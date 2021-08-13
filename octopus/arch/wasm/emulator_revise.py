import copy
import logging
from datetime import datetime

from z3 import *

import gvar
from octopus.arch.wasm.cfg import WasmCFG
from octopus.arch.wasm.vmstate import WasmVMstate
from octopus.engine.emulator import EmulatorEngine
from octopus.arch.wasm.helper_c import *
from .instructions import *
from octopus.arch.wasm.type2z3 import getConcreteBitVec

sys.setrecursionlimit(4096)

if gvar.logging_level_verbose:
    logging.basicConfig(level=logging.DEBUG)
else:
    logging.basicConfig(level=logging.INFO)
MAX = 42


# =======================================
# #         WASM Emulator               #
# =======================================

class WasmSSAEmulatorEngine(EmulatorEngine):

    def __init__(self, bytecode, timeout, func_index2func_name=None):
        self.cfg = WasmCFG(bytecode)
        self.ana = self.cfg.analyzer

        self.data_section = dict()
        # init memory section with data section
        for _, data_section_value in enumerate(self.ana.datas):
            data = data_section_value['data']
            offset = data_section_value['offset']
            size = data_section_value['size']
            # print(offset, size, data)
            if offset == '4':
                exit("The offset of data section is 4, please check")
                self.data_section[(offset, offset + size)] = BitVecVal(int.from_bytes(data, byteorder='little'), size * 8)
            else:
                # the original implementation, but it will stuck when the data section is huge, so I comment this implementation
                # self.data_section[(offset, offset + size)] = BitVecVal(int.from_bytes(data, byteorder='big'), size * 8)
                self.data_section[(offset, offset + size)] = data
        # func index to func real name
        # like func 4 is $main function in C
        self.func_index2func_name = func_index2func_name

    def get_signature(self, func_name):
        # extract param and return str
        for func_info in self.ana.func_prototypes:
            if func_info[0] == func_name:
                param_str, return_str = func_info[1], func_info[2]
                break
        return param_str, return_str

    def init_globals(self, state):
        for i, item in enumerate(self.ana.globals):
            op_type, op_val = item[0], BitVecVal(item[1], 32)
            state.globals[i] = op_val

    def init_state(self, func_name, param_str, return_str, has_ret):
        state = WasmVMstate()

        for i, local in enumerate(param_str.split(' ')):
            state.local_var[i] = getConcreteBitVec(local, func_name + '_loc_' + str(i) + '_' + local)

        # deal with the globals
        self.init_globals(state)

        if return_str:
            has_ret.append(True)
        else:
            has_ret.append(False)

        return state, has_ret

    def emulate_basic_block(self, state, has_ret, instructions):
        pre_instr = None
        states = [state]
        halt = False
        for instruction in instructions:
            next_states = []
            for state in states:
                state.instr = instruction
                state.pc += 1
                halt, ret = self.emulate_one_instruction(instruction, state, 0, has_ret, 0)
                if ret is not None:
                    next_states.extend(ret)
                else:
                    next_states.append(copy.deepcopy(state))
            states = next_states
        return halt, states

    def emulate_one_instruction(self, instr, state, depth, has_ret, call_depth):
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

        logging.debug(f'''
PC:\t\t{state.pc}
Current Func:\t{state.current_func_name}
Instruction:\t{instr.operand_interpretation}
Stack:\t\t{state.symbolic_stack}
Local Var:\t{state.local_var}
Global Var:\t{state.globals}
Memory:\t\t{state.symbolic_memory}\n''')

        for c in state.constraints:
            if type(c) != BoolRef:
                state.constraints.remove(c)
                # logging.warning(state.constraints)
                # exit()

        instr_obj = instruction_map[instr.group](instr.name, instr.operand, instr.operand_interpretation)
        if instr.group == 'Memory':
            return instr_obj.emulate(state, self.data_section), None
        elif instr.group == 'Control':
            return instr_obj.emulate(state, has_ret, self.ana.func_prototypes, self.func_index2func_name, self.data_section)
        elif instr.group == 'Parametric':
            return instr_obj.emulate(state, depth, has_ret, call_depth)
        else:
            instr_obj.emulate(state)
            return False, None