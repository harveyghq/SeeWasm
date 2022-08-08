# These functions are imported from WASI
# we will emulate their behaviors

import logging
from datetime import datetime

from eunomia.arch.wasm.configuration import Configuration
from eunomia.arch.wasm.lib.utils import _extract_params, _loadN, _storeN
from eunomia.arch.wasm.solver import SMTSolver
from eunomia.arch.wasm.utils import getConcreteBitVec, str_to_little_endian_int
from z3 import (And, BitVec, BitVecVal, Concat, Extract, If, is_bv, sat,
                simplify)


class WASIImportFunction:
    """
    Emulate the behavior of import functioons.

    Basically composed of WASI import functions
    """

    def __init__(self, name, cur_func_name):
        self.name = name
        self.cur_func = cur_func_name

    def emul(self, state, param_str, return_str, data_section):
        # if the return value is dependent on the library function, we will manually contruct it
        # and jump over the process in which it append a symbol according to the signature of the function
        if self.name == 'args_sizes_get':
            arg_buf_size_addr, argc_addr = _extract_params(param_str, state)
            logging.info(
                f"\targs_sizes_get, argc_addr: {argc_addr}, arg_buf_size_addr: {arg_buf_size_addr}")

            def _iterate_sym_args(args_list):
                if not args_list:
                    return BitVecVal(len(state.args), 32)
                return If(And([i == 0 for i in args_list]),
                          BitVecVal(len(state.args) - len(args_list), 32),
                          _iterate_sym_args(args_list[1:]))
            argc = _iterate_sym_args(state.args[1:])

            # insert the `argc` into the corresponding addr
            _storeN(state, argc_addr, argc, 4)
            # the length of `argv` into the corresponding addr
            # the `+ 1` is defined in the source code
            argv_len = 0
            for arg_i in state.args:
                if isinstance(arg_i, str):
                    argv_len += len(arg_i) + 1
                elif is_bv(arg_i):
                    argv_len += arg_i.size() // 8 + 1
            _storeN(state, arg_buf_size_addr, argv_len, 4)

            # append a 0 as return value, means success
            state.symbolic_stack.append(BitVecVal(0, 32))
            return
        elif self.name == 'args_get':
            # this is not the complete version
            # ref: https://github.com/WebAssembly/wasm-jit-prototype/blob/65ca25f8e6578ffc3bcf09c10c80af4f1ba443b2/Lib/WASI/WASIArgsEnvs.cpp
            arg_buf_addr, argv_addr = _extract_params(param_str, state)
            logging.info(
                f"\targs_get, argv_addr: {argv_addr}, arg_buf_addr: {arg_buf_addr}")

            # emulate the official implementation
            args = state.args
            next_arg_buf_addr = arg_buf_addr
            for arg_index in range(len(args)):
                arg = args[arg_index]

                if isinstance(arg, str):
                    num_arg_bytes = len(arg) + 1
                    # insert the arg
                    _storeN(
                        state, next_arg_buf_addr,
                        str_to_little_endian_int(arg),
                        num_arg_bytes)

                elif is_bv(arg):
                    num_arg_bytes = arg.size() // 8 + 1
                    # insert the arg
                    _storeN(state, next_arg_buf_addr, simplify(
                        Concat(BitVecVal(0, 8), arg)), num_arg_bytes)

                # insert the next_arg_buf_addr
                _storeN(state, argv_addr + 4 * arg_index,
                        next_arg_buf_addr, 4)
                # update the next_arg_buf_addr
                next_arg_buf_addr += num_arg_bytes

            # append a 0 as return value, means success
            state.symbolic_stack.append(BitVecVal(0, 32))
            return
        elif self.name == 'environ_sizes_get':
            env_buf_size_addr, env_count_addr = _extract_params(
                param_str, state)
            logging.info(
                f"\tenviron_sizes_get, env_count_addr: {env_count_addr}, env_buf_size_addr: {env_buf_size_addr}")

            _storeN(state, env_count_addr, 0, 4)
            _storeN(state, env_buf_size_addr, 0, 4)

            state.symbolic_stack.append(BitVecVal(0, 32))
            return
        elif self.name == 'fd_advise':
            # ref: https://man7.org/linux/man-pages/man2/posix_fadvise.2.html
            advice, length, offset, fd = _extract_params(param_str, state)
            logging.info(
                f"\tfd_advise, fd: {fd}, offset: {offset}, length: {length}, advice: {advice}")

            # append a 0 as return value, means success
            state.symbolic_stack.append(BitVecVal(0, 32))
            return
        elif self.name == 'fd_fdstat_get':
            # ref: https://github.com/WebAssembly/wasm-jit-prototype/blob/65ca25f8e6578ffc3bcf09c10c80af4f1ba443b2/Lib/WASI/WASIFile.cpp#L717
            fd_stat_addr, fd = _extract_params(param_str, state)
            logging.info(
                f"\tfd_fdstat_get, fd: {fd}, fd_stat_addr: {fd_stat_addr}")
            # fs_filetype is 1 byte, possible 0-7
            # fs_filetype = BitVec(
            #     f'fs_filetype_{datetime.timestamp(datetime.now()):.0f}', 8)
            # TODO we temporarily to concretize the fs_filetype as 1, i.e., __WASI_FILETYPE_CHARACTER_DEVICE
            fs_filetype = 2
            _storeN(state, fd_stat_addr, fs_filetype, 1)
            # TODO the fs_filetype could be 0-7, jump over temporarily
            # state.constraints.append(
            #     Or(
            #         fs_filetype == 0, fs_filetype == 1,
            #         fs_filetype == 2, fs_filetype == 3,
            #         fs_filetype == 4, fs_filetype == 5,
            #         fs_filetype == 6, fs_filetype == 7))
            # align
            _storeN(state, fd_stat_addr + 1, 0, 1)

            # fs_flags is 2 bytes, possible from {0, 1, 2, 3, 4, 5, 6, 7, 10, 11, 14, 15}
            # fs_flags = BitVec(
            #     f'fs_flags_{datetime.timestamp(datetime.now()):.0f}', 16)
            # TODO we temporarily to concretize the fs_flags as 0, i.e., no flags are set
            fs_flags = 0
            _storeN(state, fd_stat_addr + 2, fs_flags, 2)
            # TODO the fs_flags could be the following values, jump over temporarily
            # state.constraints.append(
            #     Or(
            #         fs_flags == 0, fs_flags == 1,
            #         fs_flags == 2, fs_flags == 3,
            #         fs_flags == 4, fs_flags == 5,
            #         fs_flags == 6, fs_flags == 7,
            #         fs_flags == 10, fs_flags == 11,
            #         fs_flags == 14, fs_flags == 15))
            # align
            _storeN(state, fd_stat_addr + 4, 0, 4)

            # fs_rights_base and fs_rights_inheriting is 0, 8 bytes for each
            _storeN(state, fd_stat_addr + 8, 0, 8)
            _storeN(state, fd_stat_addr + 16, 0, 8)

            # append a 0 as return value, means success
            state.symbolic_stack.append(BitVecVal(0, 32))
            return
        elif self.name == 'fd_tell':
            # TODO, do not precisely emulate this function, just insert 0 temporarily
            # ref: https://github.com/WebAssembly/wasm-jit-prototype/blob/65ca25f8e6578ffc3bcf09c10c80af4f1ba443b2/Lib/WASI/WASIFile.cpp#L695
            offset_addr, fd = _extract_params(param_str, state)
            logging.info(
                f"\tfd_tell, fd: {fd}, offset_addr: {offset_addr}")
            fd_tell_var = BitVec(
                f"fd_tell_{datetime.timestamp(datetime.now()):.0f}", 32)
            _storeN(state, offset_addr, fd_tell_var, 4)

            # append a 0 as return value, means success
            state.symbolic_stack.append(BitVecVal(0, 32))
            return
        elif self.name == 'fd_seek':
            # TODO, similar to fd_tell, do not precisely emulate this function
            # ref: https://github.com/WebAssembly/wasm-jit-prototype/blob/65ca25f8e6578ffc3bcf09c10c80af4f1ba443b2/Lib/WASI/WASIFile.cpp#L657
            new_offset_addr, whence, offset, fd = _extract_params(
                param_str, state)
            logging.info(
                f"\tfd_seek, fd: {fd}, offset: {offset}, whence: {whence}, new_offset_addr: {new_offset_addr}")
            fd_seek_var = BitVec(
                f"fd_seek_{datetime.timestamp(datetime.now()):.0f}", 32)
            _storeN(state, new_offset_addr, fd_seek_var, 4)

            # append a 0 as return value, means success
            state.symbolic_stack.append(BitVecVal(0, 32))
            return
        elif self.name == 'fd_close':
            # I did not emulate the fdMap, just return the success flag here
            # ref: https://github.com/WebAssembly/wasm-jit-prototype/blob/65ca25f8e6578ffc3bcf09c10c80af4f1ba443b2/Lib/WASI/WASIFile.cpp#L322
            fd, = _extract_params(param_str, state)
            logging.info(f"\tfd_close, fd: {fd}")
            state.file_sys[fd]["name"] = ""
            state.file_sys[fd]["status"] = False
            state.file_sys[fd]["flag"] = ""
            state.file_sys[fd]["content"] = []

            # append a 0 as return value, means success
            state.symbolic_stack.append(BitVecVal(0, 32))
            return
        elif self.name == 'fd_read':
            # ref: https://github.com/WebAssembly/wasm-jit-prototype/blob/65ca25f8e6578ffc3bcf09c10c80af4f1ba443b2/Lib/WASI/WASIFile.cpp#L554
            num_bytes_read_addr, num_iovs, iovs_addr, fd = _extract_params(
                param_str,
                state)
            logging.info(
                f"\tfd_read, fd: {fd}, iovs_addr: {iovs_addr}, num_iovs: {num_iovs}, num_bytes_read_addr: {num_bytes_read_addr}")

            if fd not in state.file_sys:
                exit(f"fd ({fd}) not in file_sys, please give more sym files")
            assert state.file_sys[fd]["status"], f"fd ({fd}) is not opened yet, can't be read"

            # if there is no input chars
            # just set the num_bytes_read_addr as 0 and return 0 immediately
            if (isinstance(state.file_sys[fd]["content"], bytes) and not state.file_sys[fd]["content"]) or (is_bv(state.file_sys[fd]["content"]) and state.file_sys[fd]["content"].size() < 8):
                _storeN(state, num_bytes_read_addr, 0, 4)
                # append a 0 as return value, means success
                state.symbolic_stack.append(BitVecVal(0, 32))
                return

            char_read_cnt = 0
            out_chars = []
            for i in range(num_iovs):
                # the buffer where to store data
                buffer_ptr = _loadN(state, data_section, iovs_addr + 8 * i, 4)
                # the buffer capacity
                buffer_len = _loadN(state, data_section,
                                    iovs_addr + (8 * i + 4), 4)

                if isinstance(state.file_sys[fd]["content"], bytes):
                    stdin_length = len(state.file_sys[fd]["content"])
                else:
                    stdin_length = state.file_sys[fd]["content"].size() // 8

                for j in range(min(stdin_length, buffer_len)):
                    if isinstance(state.file_sys[fd]["content"], bytes):
                        data_to_read = state.file_sys[fd]["content"][0]
                        state.file_sys[fd]["content"] = state.file_sys[fd][
                            "content"][
                            1:]
                    else:
                        data_to_read = simplify(
                            Extract(7, 0, state.file_sys[fd]["content"]))
                        if (stdin_length - char_read_cnt) == 1:
                            state.file_sys[fd]["content"] = BitVec('dummy', 1)
                        else:
                            state.file_sys[fd]["content"] = simplify(
                                Extract(
                                    state.file_sys[fd]["content"].size() - 1,
                                    8,
                                    state.file_sys[fd]["content"]))

                    out_chars.append(data_to_read)
                    char_read_cnt += 1
                    _storeN(state, buffer_ptr + j, data_to_read, 1)

                # if there are more bytes to read, and the buffer is filled
                # update the cursor and move to the next buffer
                if (isinstance(state.file_sys[fd]["content"], bytes) and len(state.file_sys[fd]["content"]) > 0) or (is_bv(state.file_sys[fd]["content"]) and state.file_sys[fd]["content"].size() > 1):
                    continue
                else:
                    # or the stdin buffer is drained out, break out
                    break

            all_char = True
            for ele in out_chars:
                if not isinstance(ele, int):
                    all_char = False
                    break
            if all_char:
                out_chars = [chr(i).encode() for i in out_chars]
                logging.info(
                    f"\tInput a fd_read string: {b''.join(out_chars)}")
            else:
                logging.info(
                    f"\tInput a fd_read string: {out_chars}")
            # set num_bytes_read_addr to bytes_read_cnt
            logging.info(f"\t{char_read_cnt} chars read")
            _storeN(state, num_bytes_read_addr, char_read_cnt, 4)

            # append a 0 as return value, means success
            state.symbolic_stack.append(BitVecVal(0, 32))
            return
        elif self.name == 'fd_write':
            # ref: https://github.com/WebAssembly/wasm-jit-prototype/blob/65ca25f8e6578ffc3bcf09c10c80af4f1ba443b2/Lib/WASI/WASIFile.cpp#L583
            num_bytes_written_addr, num_iovs, iovs_addr, fd = _extract_params(
                param_str,
                state)
            logging.info(
                f"\tfd_write. fd: {fd}, iovs_addr: {iovs_addr}, num_iovs: {num_iovs}, num_bytes_written_addr: {num_bytes_written_addr}")
            assert fd in state.file_sys, f"fd ({fd}) not in file_sys"
            assert state.file_sys[fd]["status"], f"fd ({fd}) is not opened yet"
            assert 'w' in state.file_sys[fd][
                "flag"], f"fd ({fd}) mode is {state.file_sys[fd]['flag']}, can't be written"
            assert isinstance(
                state.file_sys[fd]["content"], list), f"fd ({fd}) content is not a list, please check the init process"

            bytes_written_cnt = 0
            for i in range(num_iovs):
                data_ptr = _loadN(state, data_section, iovs_addr + 8 * i, 4)
                data_len = _loadN(state, data_section,
                                  iovs_addr + (8 * i + 4), 4)

                # data_len could be BitVec
                # if it is, try to concretize it with the current constraints
                if is_bv(data_len):
                    s = SMTSolver(Configuration.get_solver())
                    s += state.constraints
                    tmp_data_len = BitVec('tmp_data_len', data_len.size())
                    s.add(tmp_data_len == data_len)
                    if sat == s.check():
                        m = s.model()
                        data_len = m[tmp_data_len].as_long()
                    else:
                        raise Exception("the data_len cannot be solved")
                out_str = []
                for j in range(data_len):
                    c = _loadN(state, data_section, data_ptr + j, 1)
                    if isinstance(c, int):
                        c = chr(c)
                    elif is_bv(c):
                        c = c
                    else:
                        raise Exception(
                            f"The loaded char: {c} is with type: {type(c)}")
                    out_str.append(c)

                state.file_sys[fd]["content"] += out_str

                all_char = True
                for ele in out_str:
                    if not isinstance(ele, str):
                        all_char = False
                        break
                if all_char:
                    out_str = [ele.encode() for ele in out_str]
                    logging.info(
                        f"\tOutput a fd_write string: {b''.join(out_str)}")
                else:
                    logging.info(
                        f"\tOutput a fd_write string: {out_str}")
                bytes_written_cnt += data_len

            _storeN(state, num_bytes_written_addr, bytes_written_cnt, 4)

            # append a 0 as return value, means success
            state.symbolic_stack.append(BitVecVal(0, 32))
            return
        elif self.name == 'proc_exit':
            return_val, = _extract_params(param_str, state)
            logging.info(
                f"\tproc_exit: return_val: {return_val}")

            proc_exit = BitVec('proc_exit', 32)
            state.constraints.append(proc_exit == return_val)
            return
            # if return_val == 0:
            #     raise ProcSuccessTermination(return_val)
            # else:
            #     raise ProcFailTermination(return_val)
        elif self.name == 'fd_prestat_get':
            prestat_addr, fd = _extract_params(param_str, state)
            logging.info(
                f"\tfd_prestat_get: fd: {fd}, prestat_addr: {prestat_addr}")

            # we assume there are only two input files, like "demo.wasm a.txt b.txt"
            # if we do not return 8, the loop in `__wasilibc_populate_preopens` will never end
            if fd >= 5:
                state.symbolic_stack.append(BitVecVal(8, 32))
                return

            # the first byte means '__WASI_PREOPENTYPE_DIR', the other three are for align
            _storeN(state, prestat_addr, 0, 4)
            # store the length of file's path in 4 bytes, like 'a.txt' is 5 bytes
            _storeN(state, prestat_addr + 4, 5, 4)

            # append a 0 as return value, means success
            state.symbolic_stack.append(BitVecVal(0, 32))
            return
        elif self.name == 'fd_prestat_dir_name':
            buffer_len, buffer_addr, fd = _extract_params(
                param_str, state)
            logging.info(
                f"\tfd_prestat_dir_name, fd: {fd}, buffer_addr: {buffer_addr}, buffer_len: {buffer_len}")

            # copy the file path into the buffer
            _storeN(
                state, buffer_addr, str_to_little_endian_int('a.txt'),
                buffer_len)

            # append a 0 as return value, means success
            state.symbolic_stack.append(BitVecVal(0, 32))
            return
        elif self.name == 'path_open':
            fd_addr, _, _, _, _, _, _, _, dir_fd = _extract_params(
                param_str,
                state)
            logging.info(f"\tpath_open, fd: {dir_fd}")

            _storeN(state, fd_addr, dir_fd, 4)

            # append a 0 as return value, means success
            state.symbolic_stack.append(BitVecVal(0, 32))
            return
        else:
            logging.error(f"{self.name}")
            logging.error(f"{state.symbolic_stack}")
            logging.error(f"{state.symbolic_memory}")
            exit()

        if return_str:
            tmp_bitvec = getConcreteBitVec(
                return_str,
                f'{self.name}_ret_{return_str}_{self.cur_func}_{str(state.instr.offset)}')
            state.symbolic_stack.append(tmp_bitvec)
