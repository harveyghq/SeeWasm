from z3 import *
import re

from . exceptions import *

class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


# this is a mapping, which maps the data type to the corresponding BitVec
def getConcreteBitVec(type, name):
    if type == 'i32':
        return BitVec(name, 32)
    elif type == 'i64':
        return BitVec(name, 64)
    elif type == 'f32':
        return FP(name, Float32())
    elif type == 'f64':
        return FP(name, Float64())
    else:
        raise UnsupportZ3TypeError

# 该函数用于抽取 C 通过 -g3 等级编译得到的 wat 文件中的对应的 function index 和 function 名称之间的关系
# This script will maintain a *map* structure, consisting of the function index and the corresponding 
# function name that obtained from the compiler from C to Wasm with -g3 debuggability
def extract_mapping(file_path):
    with open(file_path) as fp:
        text = fp.read()

    # index to func name
    mapper = {}
    matches = re.findall(r' \(func (.*) \(type', text)
    for i, func_name in enumerate(matches):
        mapper[i] = func_name if func_name[0] != '$' else func_name[1:]

    return mapper

def show_state_info(state_index, states):
    state = states[state_index]
    state_infos = state.items() if isinstance(state, dict) else [('fallthrough', state)]
    for _, info in state_infos:
        print(f'''
PC:\t\t{info.pc}
Current Func:\t{info.current_func_name}
Stack:\t\t{info.symbolic_stack}
Local Var:\t{info.local_var}
Global Var:\t{info.globals}
Memory:\t\t{info.symbolic_memory}
Constraints:\t{info.constraints[:-1]}\n''')


def show_branch_info(branch, branches, state):
    bb_name = branches[branch]
    if branch in ['conditional_true', 'conditional_false']:
        print(f'[!] The constraint: {bcolors.WARNING}"{state[branch].constraints[-1]}"{bcolors.ENDC} will be appended')
    print(f'[!] You choose to go to basic block: {bcolors.WARNING}{bb_name}{bcolors.ENDC}')
    # commented, TODO, need revise, uncomment if neccessary
    # print(f'[!] Its instruction begins at offset {cls.bb_to_instructions[bb_name][0].offset}')
    # print(f'[!] The leading instructions are showed as follows:')
    # instructions = cls.bb_to_instructions[bb_name]
    # for i, instr in enumerate(instructions):
    #     if i >= 10:
    #         break
    #     print(f'\t{instr.operand_interpretation}')

def ask_user_input(emul_states, isbr, onlyone=False, branches=None, state_item=None):
    # the flag can be 0 or 1,
    # 0 means state, 1 means branch
    # `concerned_variable` is state_index or branch, depends on the flag value
    branch_mapping = {
        'T': 'conditional_true',
        'F': 'conditional_false',
        'f': 'fallthrough',
        'u': 'unconditional',
        'conditional_true': 'T',
        'conditional_false': 'F',
        'fallthrough': 'f',
        'unconditional': 'u',
    }

    while True:
        user_input = input("[!] Please input the command: ")
        try:
            ask_for_info = False

            # if there is only one possible state
            if onlyone and not isbr:
                user_input = ("1 " + user_input) if user_input == 'i' else "1"
            elif onlyone and isbr: # if there is only one possible branch
                branch_symbol = branch_mapping[list(branches.keys())[0]]
                user_input = branch_symbol + " " + user_input if user_input == 'i' else branch_symbol

            if ' ' in user_input:
                concerned_variable, ask_for_info = user_input.split(' ')
                assert ask_for_info == 'i'
                ask_for_info = True
            else:
                concerned_variable = user_input

            concerned_variable = branch_mapping[concerned_variable] if isbr else int(concerned_variable) - 1
            if not ask_for_info:
                break
            if isbr:
                show_branch_info(concerned_variable, branches, state_item)
            else:
                show_state_info(concerned_variable, emul_states)
            print('')
        except:
            print(f"{bcolors.FAIL}[!] Invalid input, please try again{bcolors.ENDC}")

    return concerned_variable