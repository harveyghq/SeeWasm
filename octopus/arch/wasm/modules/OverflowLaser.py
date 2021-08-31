from z3 import *
import logging
from copy import deepcopy
from collections import defaultdict

from octopus.arch.wasm.utils import bcolors

overflow_group = {'bvadd', 'bvmul'}


class OverflowLaser:
    def __init__(self):
        pass

    def fire(self, expr, original_constraints):
        # two operands
        op1, op2 = expr.arg(0), expr.arg(1)
        # copy the original_constraints
        new_cond = deepcopy(original_constraints)

        # we only consider the instructions in `overflow_group`
        if expr.decl().name() not in overflow_group:
            return

        # step 1:
        # if two BitVecNumRef, return directly
        # as only the symbol can be manipulated
        if isinstance(op1, BitVecNumRef) and isinstance(op2, BitVecNumRef):
            return

        def contain_op(cons, op):
            for sub_cons in cons.children():
                if sub_cons.get_id() == op.get_id():
                    return True
                return contain_op(sub_cons, op)

        # step 2:
        # if both of op1 and op2 are free, overflow may happen
        free_variable = True
        op2con = defaultdict(list)
        for op in [op1, op2]:
            for constraint in new_cond:
                if contain_op(constraint, op):
                    free_variable = False
                    op2con[(op, op.get_id())].append(constraint)
            if not free_variable:
                break
        if free_variable:
            logging.warning(
                f'{bcolors.WARNING}op1 ({op1}) or op2 ({op2}) is free which may result in overflow!{bcolors.ENDC}')
        else:
            # step 3:
            # infer the data type according to its passed instruction
            # print(op2con)
            logging.warning(
                f'{bcolors.WARNING}Cannot determine overflow problem{bcolors.ENDC}')