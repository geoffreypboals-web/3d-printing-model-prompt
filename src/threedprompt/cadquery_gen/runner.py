"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/cadquery_gen/runner.py
Description: The sandboxed child process for the CadQuery backend. Run
    only as `python -I runner.py <part.py> <output_dir> <cpu_s> <mem_mb>`
    by cadquery_gen/__init__.py (tests import its lockdown functions). It imports CadQuery,
    then locks itself down before running the LLM-written code:
      1. rlimits: CPU seconds, address space, 100 MB max file, no core dumps
      2. Landlock (kernel): reads only from an allow-list (Python's own
         prefix and site-packages, /usr, /lib*, a few /etc and /dev files,
         its own /proc/self, <output_dir>) -- so no /proc/<parent>/environ,
         no .env, no ~/.ssh; writes, deletes and creates only in
         <output_dir>; no execve at all; on Landlock ABI >= 4 also no TCP
         connect/bind. Covers OCCT's native code too.
      3. seccomp (kernel): socket() and io_uring_setup() fail with EACCES,
         so no network from Python or native code, on any kernel
      4. a Python audit hook: no sockets, subprocesses, fork, ctypes, or
         gc/code-object tricks aimed at the hook itself
    Then it runs part.py, which must assign `result` (a cq.Workplane or
    Shape), and exports it to <output_dir>/model.stl.
Inputs: argv as above. Deliberately imports nothing from threedprompt so
    the child never loads the app's config (or sees its secrets; the
    parent also passes a stripped environment).
Outputs: <output_dir>/model.stl; exit 0 on success. Any failure exits
    non-zero with the reason on stderr (the parent feeds it to the LLM).
Troubleshooting:
    - "sandbox: Landlock unavailable": the kernel lacks Landlock (Linux
      5.13+, enabled in the LSM list) or a seccomp profile blocks its
      syscalls. The runner refuses to run unsandboxed - fix the host,
      don't bypass it. Windows/macOS hosts: run the Docker image.
    - "sandbox: <event> blocked": the part code tried something outside
      geometry (network, processes, ctypes). Expected; the LLM retries.
    - MemoryError / "std::bad_alloc": the part exceeded
      CADQUERY_MEMORY_LIMIT_MB; raise it if real parts need more.
"""

import ctypes
import os
import resource
import runpy
import site
import sys

# Landlock uapi (linux/landlock.h); syscall numbers are the same on x86_64 and arm64.
_SYS_CREATE_RULESET, _SYS_ADD_RULE, _SYS_RESTRICT_SELF = 444, 445, 446
_PR_SET_NO_NEW_PRIVS = 38
_SECCOMP_SET_MODE_FILTER, _SECCOMP_FILTER_FLAG_TSYNC = 1, 1
_FS_EXECUTE, _FS_WRITE_FILE, _FS_READ_FILE, _FS_READ_DIR = 1 << 0, 1 << 1, 1 << 2, 1 << 3
_FS_REMOVE_DIR, _FS_REMOVE_FILE = 1 << 4, 1 << 5
_FS_MAKE_DIR, _FS_MAKE_REG = 1 << 7, 1 << 8
_FS_MAKE_ALL = sum(1 << b for b in range(6, 13))  # char, dir, reg, sock, fifo, block, sym
_FS_REFER, _FS_TRUNCATE = 1 << 13, 1 << 14  # ABI 2, ABI 3
_NET_BIND_TCP, _NET_CONNECT_TCP = 1 << 0, 1 << 1  # ABI 4

_BLOCKED_EVENTS = frozenset(
    {
        "socket.__new__", "socket.bind", "socket.connect", "socket.getaddrinfo", "socket.gethostbyname",
        "socket.gethostbyaddr", "socket.sendto", "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn",
        "os.spawn", "os.fork", "os.forkpty", "os.kill", "os.killpg", "pty.spawn", "gc.get_objects",
        "gc.get_referrers", "gc.get_referents", "sys.setprofile", "sys.settrace", "webbrowser.open",
    }
)  # fmt: skip


def _deny(event, args):
    """Audit hook: refuse the events above, all of ctypes, and swapping a function's code object."""
    if event in _BLOCKED_EVENTS or event.startswith("ctypes."):
        raise PermissionError(f"sandbox: {event} blocked")
    if event == "object.__setattr__" and len(args) > 1 and args[1] in ("__code__", "__closure__"):
        raise PermissionError("sandbox: code-object swap blocked")


# Readable after the lockdown. No EXECUTE anywhere: Landlock's execute right governs execve only, and
# shared libraries still map without it, so granting it would just re-open running /usr/bin/*.
_READ_DIRS = ("/usr", "/lib", "/lib64")
_READ_FILES = ("/etc/ld.so.cache", "/etc/localtime", "/dev/urandom")


def _read_dirs():
    """Directories part code may read: the system library dirs plus this interpreter's prefix and site-packages."""
    dirs = {*_READ_DIRS, sys.prefix, sys.base_prefix, sys.exec_prefix, *site.getsitepackages()}
    return sorted(d for d in dirs if os.path.isdir(d))


def _landlock(output_dir):
    """Restrict this process (and anything it could start): reads from the allow-list only, writes only inside
    output_dir, no execve; return the ABI."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    abi = libc.syscall(_SYS_CREATE_RULESET, None, ctypes.c_size_t(0), ctypes.c_uint32(1))
    if abi < 1:
        raise SystemExit(f"sandbox: Landlock unavailable (errno {ctypes.get_errno()}); refusing to run")
    read = _FS_READ_FILE | _FS_READ_DIR
    handled = _FS_EXECUTE | _FS_WRITE_FILE | read | _FS_REMOVE_DIR | _FS_REMOVE_FILE | _FS_MAKE_ALL
    allowed = read | _FS_WRITE_FILE | _FS_REMOVE_FILE | _FS_MAKE_REG | _FS_MAKE_DIR
    if abi >= 2:
        handled |= _FS_REFER
    if abi >= 3:
        handled |= _FS_TRUNCATE
        allowed |= _FS_TRUNCATE
    net = _NET_BIND_TCP | _NET_CONNECT_TCP if abi >= 4 else 0  # handled with no rules = all TCP denied

    attr = (ctypes.c_uint64 * 2)(handled, net)
    size = 16 if abi >= 4 else 8
    ruleset = libc.syscall(_SYS_CREATE_RULESET, ctypes.byref(attr), ctypes.c_size_t(size), ctypes.c_uint32(0))
    if ruleset < 0:
        raise SystemExit(f"sandbox: landlock_create_ruleset failed (errno {ctypes.get_errno()})")

    class _PathBeneath(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]

    def add_rule(path, access):
        fd = os.open(path, os.O_PATH | os.O_CLOEXEC)  # /proc/self resolves here, to this pid only
        try:
            rule = _PathBeneath(access, fd)
            if libc.syscall(_SYS_ADD_RULE, ruleset, ctypes.c_uint32(1), ctypes.byref(rule), ctypes.c_uint32(0)) < 0:
                raise SystemExit(f"sandbox: landlock_add_rule {path} failed (errno {ctypes.get_errno()})")
        finally:
            os.close(fd)

    add_rule(output_dir, allowed)
    for d in _read_dirs():
        add_rule(d, read)
    add_rule("/proc/self", read)
    for f in _READ_FILES:
        if os.path.exists(f):
            add_rule(f, _FS_READ_FILE)
    if os.path.exists("/dev/null"):
        add_rule("/dev/null", _FS_READ_FILE | _FS_WRITE_FILE)
    if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise SystemExit("sandbox: prctl(NO_NEW_PRIVS) failed")
    if libc.syscall(_SYS_RESTRICT_SELF, ruleset, ctypes.c_uint32(0)) < 0:
        raise SystemExit(f"sandbox: landlock_restrict_self failed (errno {ctypes.get_errno()})")
    os.close(ruleset)
    return abi


def _seccomp_no_sockets():
    """Install a seccomp filter: socket() and io_uring_setup() return EACCES, x32 syscalls too; else allowed.

    Needs NO_NEW_PRIVS, which _landlock() sets first. TSYNC applies it to every thread, including native
    ones started during `import cadquery`. Unknown architectures fail closed (SystemExit).
    """
    arches = {"x86_64": (0xC000003E, 41, 317), "aarch64": (0xC00000B7, 198, 277)}  # audit arch, socket, seccomp
    arch, sys_socket, sys_seccomp = arches.get(os.uname().machine, (0, 0, 0))
    if not arch:
        raise SystemExit(f"sandbox: no seccomp filter for {os.uname().machine}; refusing to run")
    ld, jeq, jge, ret = 0x20, 0x15, 0x35, 0x06  # BPF_LD|W|ABS, BPF_JMP|JEQ|K, BPF_JMP|JGE|K, BPF_RET|K
    allow, deny, kill = 0x7FFF0000, 0x00050000 | 13, 0x80000000  # ALLOW, ERRNO(EACCES), KILL_PROCESS
    prog = [
        (ld, 0, 0, 4),  # seccomp_data.arch
        (jeq, 0, 6, arch),  # wrong arch -> kill
        (ld, 0, 0, 0),  # seccomp_data.nr
        (jge, 3, 0, 0x40000000),  # x32 ABI -> deny
        (jeq, 2, 0, sys_socket),
        (jeq, 1, 0, 425),  # io_uring_setup (same number on both)
        (ret, 0, 0, allow),
        (ret, 0, 0, deny),
        (ret, 0, 0, kill),
    ]

    class _Insn(ctypes.Structure):
        _fields_ = [("code", ctypes.c_uint16), ("jt", ctypes.c_uint8), ("jf", ctypes.c_uint8), ("k", ctypes.c_uint32)]

    class _Prog(ctypes.Structure):
        _fields_ = [("len", ctypes.c_uint16), ("filter", ctypes.POINTER(_Insn))]

    insns = (_Insn * len(prog))(*[_Insn(*i) for i in prog])
    fprog = _Prog(len(prog), insns)
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    if libc.syscall(sys_seccomp, _SECCOMP_SET_MODE_FILTER, _SECCOMP_FILTER_FLAG_TSYNC, ctypes.byref(fprog)) != 0:
        raise SystemExit(f"sandbox: seccomp filter failed (errno {ctypes.get_errno()}); refusing to run")


def main():
    """Lock down, run the part code, export model.stl."""
    code_path, output_dir = sys.argv[1], os.path.realpath(sys.argv[2])
    cpu_s, mem_mb = int(sys.argv[3]), int(sys.argv[4])
    import cadquery as cq  # before the lockdown: imports read and map native libraries

    resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))
    resource.setrlimit(resource.RLIMIT_AS, (mem_mb << 20, mem_mb << 20))
    resource.setrlimit(resource.RLIMIT_FSIZE, (100 << 20, 100 << 20))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    abi = _landlock(output_dir)
    _seccomp_no_sockets()
    sys.addaudithook(_deny)
    print(f"sandbox: landlock ABI {abi}, seccomp no-sockets, audit hook", file=sys.stderr)

    part = runpy.run_path(code_path, init_globals={"cq": cq}, run_name="__cadquery_part__")
    result = part.get("result")
    if result is None:
        raise SystemExit("part.py must assign the finished part to a variable named `result`")
    cq.exporters.export(result, os.path.join(output_dir, "model.stl"))


if __name__ == "__main__":
    main()
