# This file will initiate a Wasm Symbolic Execution Engine
# Also, it implements the Instruction Dispatcher

import copy
import logging
import re
import sys
from collections import defaultdict
from datetime import datetime
from queue import Queue

from eunomia.arch.wasm.analyzer import WasmModuleAnalyzer
from eunomia.arch.wasm.cfg import WasmCFG
from eunomia.arch.wasm.configuration import Configuration
from eunomia.arch.wasm.exceptions import UnsupportGlobalTypeError
from eunomia.arch.wasm.instructions import (ArithmeticInstructions,
                                            BitwiseInstructions,
                                            ConstantInstructions,
                                            ControlInstructions,
                                            ConversionInstructions,
                                            LogicalInstructions,
                                            MemoryInstructions,
                                            ParametricInstructions,
                                            VariableInstructions)
from eunomia.arch.wasm.instructions.ControlInstructions import C_LIBRARY_FUNCS
from eunomia.arch.wasm.utils import (getConcreteBitVec,
                                     readable_internal_func_name)
from eunomia.arch.wasm.vmstate import WasmVMstate
from eunomia.engine.emulator import EmulatorEngine
from z3 import BitVec, BitVecVal, BoolRef

sys.setrecursionlimit(4096)

# config the logger
logging_config = {
    'filename': f'./log/log/{Configuration.get_file_name()}_{Configuration.get_start_time()}.log',
    'filemode': 'w',
    'format': '%(asctime)s | %(levelname)s | %(message)s',
}
if 'debug' == Configuration.get_verbose_flag():
    logging_config['level'] = logging.DEBUG
elif 'info' == Configuration.get_verbose_flag():
    logging_config['level'] = logging.INFO
else:
    logging_config['level'] = logging.WARNING
logging.basicConfig(**logging_config)


# =======================================
# #         WASM Emulator               #
# =======================================

class WasmSSAEmulatorEngine(EmulatorEngine):

    def __init__(self, bytecode):
        self.cfg = WasmCFG(bytecode)
        self.ana = WasmModuleAnalyzer(self.cfg.module_bytecode)

        # be inited in `graph.py`
        self.user_dsl = None

        # all the exports function's name
        self.exported_func_names = [i["field_str"]
                                    for i in self.ana.exports if i["kind"] == 0]

        self.data_section = dict()
        # init memory section with data section
        for _, data_section_value in enumerate(self.ana.datas):
            data = data_section_value['data']
            offset = data_section_value['offset']
            size = data_section_value['size']

            # the original implementation, but it will stuck when the data section is huge, so I comment this implementation
            # self.data_section[(offset, offset + size)] = BitVecVal(int.from_bytes(data, byteorder='big'), size * 8)
            self.data_section[(offset, offset + size)] = data

        # the coverage bitmap and ratio
        self.instruction_coverage_bitmap = defaultdict(list)
        self.instruction_coverage = defaultdict(list)
        self.total_instructions = 0
        self.coverage_output_last_timestamp = datetime(1996, 12, 24)
        self.concerned_funcs = dict()

        # whether enable the coverage calculation
        if Configuration.get_coverage():
            self.count_instrs()

    def count_instrs(self):
        """
        Statically count instructions followed by the entry function
        """
        # only consider the first given func
        func = readable_internal_func_name(
            Configuration.get_func_index_to_func_name(),
            Configuration.get_entry())
        queue = Queue()
        visited = set()

        # build call graph
        self.cfg.build_call_graph(self.ana)

        # put the entry func
        queue.put(func)
        # put all elem funcs
        for elem in self.ana.elements[0]["elems"]:
            queue.put(
                readable_internal_func_name(
                    Configuration.get_func_index_to_func_name(),
                    "$func" + str(elem)))

        while not queue.empty():
            caller = queue.get()
            visited.add(caller)

            callees = self.cfg.call_graph.get(caller, {})
            for callee in callees:
                if callee not in visited:
                    queue.put(callee)

        # add the instruction number
        for f in self.cfg.functions:
            concerned_func = readable_internal_func_name(
                Configuration.get_func_index_to_func_name(), f.name)
            if concerned_func in visited:
                self.total_instructions += len(f.instructions)
                self.concerned_funcs[concerned_func] = len(f.instructions)

        logging.info(f"total instructions: {self.total_instructions}")

    def get_signature(self, func_name):
        """
        Obtain function's signature from func_prototypes
        """
        # extract param and return str
        func_index = None
        if func_name[0] == '$':
            func_index = int(re.match('\$func(.*)', func_name).group(1))
        else:
            for index, wat_func_name in Configuration.get_func_index_to_func_name().items():
                if wat_func_name == func_name:
                    func_index = index
                    break

        assert func_index is not None, f"[!] Cannot find your entry function: {func_name}"
        func_info = self.ana.func_prototypes[func_index]
        func_index_name, param_str, return_str, func_type = *func_info,

        return func_index_name, param_str, return_str, func_type

    def init_globals(self, state, is_exported):
        for i, item in enumerate(self.ana.globals):
            op_type = item[0]
            if op_type == 'i32':
                op_val = BitVecVal(item[1], 32) if is_exported else BitVec(
                    'global_' + str(i) + '_i32', 32)
            elif op_type == 'i64':
                op_val = BitVecVal(item[1], 64) if is_exported else BitVec(
                    'global_' + str(i) + '_i64', 64)
            else:
                raise UnsupportGlobalTypeError
            state.globals[i] = op_val

    def init_state(self, func_name, param_str, return_str, has_ret):
        files_buffer = {}
        for _, fd in Configuration.get_fd():
            files_buffer[fd] = Configuration.get_content(fd)

        args = Configuration.get_args()

        state = WasmVMstate()
        state.files_buffer = files_buffer
        state.args = args

        if param_str != '':
            for i, local in enumerate(param_str.split(' ')):
                state.local_var[i] = getConcreteBitVec(
                    local, func_name + '_loc_' + str(i) + '_' + local)

        state.current_func_name = func_name

        # deal with the globals
        # if the entry function is not exported, make them as symbols
        is_exported = func_name in self.exported_func_names
        # if the --concrete_globals is set, we ignore if the current function is exported
        # i.e., we can regard the current function is exported
        if Configuration.get_concrete_globals():
            is_exported = True
        self.init_globals(state, is_exported)

        if return_str:
            has_ret.append(True)
        else:
            has_ret.append(False)

        return state, has_ret

    def emulate_basic_block(self, states, has_ret, instructions):
        """
        Symbolically execute the instructions from each of state in states

        Args:
            states (list(VMstate)): From which the symbolic execution begin;
            has_ret (list(bool)): Whether the functions in calling stack returns;
            instructions (list(Instruction)): A list of instruction objects
        """
        for instruction in instructions:
            next_states = []
            for state in states:  # TODO: embarassing parallel
                state.instr = instruction
                ret_state = self.emulate_one_instruction(
                    instruction, state, has_ret)

                if ret_state is not None:
                    next_states.extend(ret_state)
                else:
                    next_states.append(copy.deepcopy(state))
            states = next_states
        return states

    def emulate_one_instruction(self, instr, state, has_ret):
        instruction_map = {
            'Control': ControlInstructions,
            'Constant': ConstantInstructions,
            'Conversion': ConversionInstructions,
            'Memory': MemoryInstructions,
            'Parametric': ParametricInstructions,
            'Variable': VariableInstructions,
            'Logical_i32': LogicalInstructions,
            'Logical_i64': LogicalInstructions,
            'Logical_f32': LogicalInstructions,
            'Logical_f64': LogicalInstructions,
            'Arithmetic_i32': ArithmeticInstructions,
            'Arithmetic_i64': ArithmeticInstructions,
            'Arithmetic_f32': ArithmeticInstructions,
            'Arithmetic_f64': ArithmeticInstructions,
            'Bitwise_i32': BitwiseInstructions,
            'Bitwise_i64': BitwiseInstructions
        }
        if instr.operand_interpretation is None:
            instr.operand_interpretation = instr.name

        # calculate instruction coverage
        if Configuration.get_coverage():
            self.calculate_coverage(
                instr,
                readable_internal_func_name(
                    Configuration.get_func_index_to_func_name(),
                    state.current_func_name))

        logging.debug(
            f"\nInstruction:\t{instr.operand_interpretation}\nOffset:\t\t{instr.offset}\n{state.__str__()}")

        for c in state.constraints:
            if not isinstance(c, BoolRef):
                state.constraints.remove(c)

        instr_obj = instruction_map[instr.group](
            instr.name, instr.operand, instr.operand_interpretation)
        if instr.group == 'Memory':
            ret_states = instr_obj.emulate(state, self.data_section)
        elif instr.group == 'Control':
            # if the instr is 'call c_lib_func', which is modeled
            # we calculate its coverage in advance
            if instr.name == 'call' and Configuration.get_coverage():
                instr_operand = instr.operand_interpretation.split(" ")[1]
                func_name = readable_internal_func_name(
                    Configuration.get_func_index_to_func_name(),
                    "$func" + instr_operand)
                if func_name in C_LIBRARY_FUNCS:
                    self.calculate_coverage(instr, func_name)

            ret_states = instr_obj.emulate(
                state, has_ret, self.data_section, self.ana)
        elif instr.group == 'Arithmetic_i32' or instr.group == 'Arithmetic_i64' or instr.group == 'Arithmetic_f32' or instr.group == 'Arithmetic_f64':
            ret_states = instr_obj.emulate(state, self.ana)
        else:
            ret_states = instr_obj.emulate(state)
        return ret_states

    def init_coverage_bitmap(self, func_name):
        """
        Init the necessary data structure
        """
        if func_name not in self.instruction_coverage_bitmap:
            self.instruction_coverage_bitmap[func_name] = [
                False] * self.concerned_funcs[func_name]
            self.instruction_coverage[func_name] = [
                0, self.concerned_funcs[func_name]]

    def calculate_coverage(self, instr, func_name):
        """
        Calculate the instruction coverage
        """
        assert func_name in self.concerned_funcs, f"{func_name} is not covered by coverage calculation"
        self.init_coverage_bitmap(func_name)

        if func_name in C_LIBRARY_FUNCS:
            # extract all its callees
            queue = Queue()
            queue.put(func_name)
            visited = set()
            while not queue.empty():
                ele = queue.get()
                visited.add(ele)
                callees = self.cfg.call_graph.get(func_name, [])
                for callee in callees:
                    if callee not in visited:
                        queue.put(callee)

            # set all instrs of these callees as True
            for callee in visited:
                assert callee in self.concerned_funcs, f"{callee} is not covered by coverage calculation"
                self.init_coverage_bitmap(callee)

                self.instruction_coverage_bitmap[callee] = [
                    True for _ in self.instruction_coverage_bitmap[callee]]
                self.instruction_coverage[callee][0] = self.instruction_coverage_bitmap[callee].count(
                    True)
        else:
            self.instruction_coverage_bitmap[func_name][
                instr.nature_offset] = True
            # the visted number of instruction
            self.instruction_coverage[func_name][0] = self.instruction_coverage_bitmap[func_name].count(
                True)

        # how many instructions are been visited
        current_visited_instrs = sum([v[0]
                                      for v in
                                      self.instruction_coverage.values()])

        # output the coverage info
        current_timestamp = datetime.now()
        if (current_timestamp - self.coverage_output_last_timestamp).total_seconds() > 1:
            # output here
            output_string = ["------------------------------------------\n"]
            for k, v in self.instruction_coverage.items():
                output_string.append(f"|{k:<30}\t\t{v[0]/v[1]:<.3f}|\n")
            output_string.append(
                "------------------------------------------\n")

            with open(f'./log/coverage-function/{Configuration.get_file_name()}_{Configuration.get_start_time()}.log', 'w') as fp:
                fp.writelines(output_string)

            with open(f'./log/coverage-instruction/{Configuration.get_file_name()}_{Configuration.get_start_time()}.log', 'a') as fp:
                fp.write(
                    f'{current_timestamp}\t\t{current_visited_instrs:<6}/{self.total_instructions:<6} ({current_visited_instrs/self.total_instructions*100:.3f}%)\n')

            self.coverage_output_last_timestamp = current_timestamp
