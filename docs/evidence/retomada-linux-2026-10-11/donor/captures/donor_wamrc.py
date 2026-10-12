#!/usr/bin/env python3
"""Ignored donor proposal. Default is read-only; --execute requires review/bundle gates."""
import argparse
import datetime
import hashlib
import json
import os
import pathlib
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time

GIB = 1024 ** 3
REVISION = "187ff96279ed8e6d3da14b7663ce5b5e856dff49"
TREE = "70580c660a5642e9f7226c0d9813b805ee8b3c64"
LLVM_SHA = "344b928e67bfc1d07cb150609c55548e86a9bf19"
LLVM_ORIGIN = "https://github.com/espressif/llvm-project.git"
INITIAL_DISK = 20 * GIB  # Engineering gate, not an official measured minimum.
INITIAL_MEMORY = 8 * GIB
DISK_FLOOR = 6 * GIB
MEMORY_FLOOR = 2 * GIB
SWAP_CEILING = 512 * 1024 ** 2
POWERSHELL = pathlib.Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
PROFILE = "wsl"
HOST_LEASE = None


def require(condition, message):
    if not condition:
        raise ValueError(message)


def strict_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON field")
            result[key] = value
        return result
    def nonfinite(value):
        raise ValueError("non-finite JSON number: " + value)
    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=pairs, parse_constant=nonfinite)


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def read_command(command, timeout=30):
    result = subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    return result.stdout.decode("utf-8-sig").strip()


def git(path, *arguments):
    return read_command(["git", "-c", "core.autocrlf=false", "-c", "credential.helper=", "-C", str(path), *arguments])


def clean(path):
    require(not git(path, "status", "--porcelain", "--untracked-files=all") and not git(path, "clean", "-ndx"), "source dirty/untracked/ignored: " + str(path))


def safe_new_run(path):
    home = pathlib.Path.home().resolve()
    path = path.absolute()
    require(path.parent == home and path.name.startswith("wasmhome-donor-") and len(path.name) > 16, "run must be a named direct child of Linux HOME")
    require(not any(character.isspace() for character in str(path)), "donor paths must have no whitespace")
    require(not os.path.lexists(path), "run exists; preserve it and choose a new run name")
    require(not any(parent.is_symlink() for parent in path.parents), "run parent contains symlink")
    require(read_command(["findmnt", "-n", "-o", "FSTYPE", "-T", str(home)]) == "ext4", "build must reside on Linux ext4, not /mnt/c")
    return path


def resources(directory):
    memory = {}
    for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        memory[key] = int(value.split()[0]) * 1024
    if PROFILE == "lxc110":
        require(HOST_LEASE and HOST_LEASE.is_file(), "LXC execution requires a fresh external Proxmox resource guard lease")
        lease = strict_json(HOST_LEASE)
        require(lease.get("schema") == "wasmhome-proxmox-donor-lease-v1" and lease.get("ctid") == 110 and
                lease.get("hostname") == "wasmhome-toolchain-donor" and lease.get("ct_state") == "running", "CT110 guard identity differs")
        require(0 <= time.time() - lease.get("timestamp_epoch", 0) <= 30, "Proxmox resource guard lease is stale; stop own build")
        require(lease.get("configured_memory_bytes") == 8 * GIB and lease.get("configured_swap_bytes") == 0 and lease.get("ct109_state") == "stopped", "CT resource/final-state contract differs")
        require(lease.get("host_start_gate_memavailable_bytes", 0) >= 10 * GIB, "host startup must have had 8GiB plus 2GiB reserve before CT110 started")
        require(lease.get("host_memavailable_bytes", 0) >= MEMORY_FLOOR and
                lease.get("host_swap_used_bytes", 0) <= lease.get("host_swap_baseline_bytes", 0) + 128 * 1024 ** 2, "Proxmox host pressure above guard; stop own build")
        available = min(memory["MemAvailable"], 8 * GIB - lease["ct_memory_current_bytes"])
        return dict(time=datetime.datetime.now(datetime.timezone.utc).isoformat(), donor_memory_available=available,
                    donor_swap_used=lease["ct_swap_current_bytes"], donor_filesystem_available=shutil.disk_usage(directory).free,
                    host_memavailable=lease["host_memavailable_bytes"], host_swap_used=lease["host_swap_used_bytes"], host_lease=lease)
    command = "$ErrorActionPreference='Stop'; $v=Get-Volume -DriveLetter C; $o=Get-CimInstance Win32_OperatingSystem; @{c_free=[long]$v.SizeRemaining;windows_memory_free=[long]$o.FreePhysicalMemory*1024}|ConvertTo-Json -Compress"
    windows = json.loads(read_command([str(POWERSHELL), "-NoProfile", "-NonInteractive", "-Command", command]))
    return dict(time=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                donor_memory_available=memory["MemAvailable"], donor_swap_used=memory["SwapTotal"] - memory["SwapFree"],
                donor_filesystem_available=shutil.disk_usage(directory).free, **windows)


def resource_gate(sample, initial=False):
    if PROFILE == "wsl":
        require(sample["c_free"] >= (INITIAL_DISK if initial else DISK_FLOOR), "physical C: free space below donor gate; do not expand or clean automatically")
        require(sample["windows_memory_free"] >= MEMORY_FLOOR, "Windows host available memory below donor gate")
    require(sample["donor_filesystem_available"] >= (INITIAL_DISK if initial else DISK_FLOOR), "Linux filesystem free space below donor gate")
    startup_memory = 6 * GIB if PROFILE == "lxc110" else INITIAL_MEMORY
    require(sample["donor_memory_available"] >= (startup_memory if initial else MEMORY_FLOOR), "donor available memory below gate")
    require(sample["donor_swap_used"] <= (0 if PROFILE == "lxc110" else SWAP_CEILING), "donor swap consumption above conservative gate")


def preflight():
    require(platform.system() == "Linux" and platform.machine() == "x86_64" and os.getuid() == 1000, "expected unprivileged UID1000 x86_64 Linux donor")
    if PROFILE == "lxc110":
        require(platform.node() == "wasmhome-toolchain-donor" and 'VERSION_ID="13"' in pathlib.Path("/etc/os-release").read_text(), "expected CT110 Debian13 donor")
    else:
        require("microsoft" in platform.release().lower() and 'VERSION_ID="24.04"' in pathlib.Path("/etc/os-release").read_text(), "expected Ubuntu24.04 WSL donor")
        require(POWERSHELL.is_file(), "Windows physical-disk guard unavailable")
    tools = {tool: shutil.which(tool) for tool in ("git", "python3", "cmake", "ninja", "gcc", "g++", "ar", "ranlib", "tar", "xz", "curl", "findmnt")}
    versions = {tool: read_command([path, "--version"]).splitlines()[0] for tool, path in tools.items() if path}
    sample = resources(pathlib.Path.home())
    blockers = []
    try:
        resource_gate(sample, initial=True)
    except ValueError as error:
        blockers.append(str(error))
    blockers += ["missing tool: " + tool for tool, path in tools.items() if not path]
    network = subprocess.run([tools["curl"] or "curl", "--disable", "--head", "--fail", "--silent", "--show-error", "--connect-timeout", "5", "--max-time", "15", "https://github.com/espressif/llvm-project"], capture_output=True)
    if network.returncode:
        blockers.append("official source network HEAD failed: " + network.stderr.decode(errors="replace").strip())
    return dict(schema="wasmhome-donor-preflight-v1", profile=PROFILE, candidate_revision=REVISION, candidate_tree=TREE,
                platform=platform.platform(), os_release=pathlib.Path("/etc/os-release").read_text(), uid=os.getuid(), tools=tools, tool_versions=versions, resources=sample, blockers=blockers,
                gates=dict(initial_disk=INITIAL_DISK, initial_memory=6 * GIB if PROFILE == "lxc110" else INITIAL_MEMORY,
                           disk_floor=DISK_FLOOR, memory_floor=MEMORY_FLOOR, swap_ceiling=0 if PROFILE == "lxc110" else SWAP_CEILING))


class Runner:
    def __init__(self, root):
        self.root = root
        self.commands = {}

    def sample(self, initial=False):
        sample = resources(self.root)
        with (self.root / "resources.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(sample) + "\n")
        resource_gate(sample, initial)

    @staticmethod
    def interrupt(child):
        # Only the new session/process group created by this Runner is signalled.
        for signum, timeout in ((signal.SIGINT, 15), (signal.SIGTERM, 10), (signal.SIGKILL, 5)):
            if child.poll() is not None:
                return
            require(os.getpgid(child.pid) == child.pid, "child process group identity differs; no signal sent")
            os.killpg(child.pid, signum)
            try:
                child.wait(timeout=timeout)
                return
            except subprocess.TimeoutExpired:
                pass

    def run(self, name, command, cwd=None):
        self.sample()
        self.commands[name] = command
        with (self.root / (name + ".log")).open("xb") as log:
            child = None
            try:
                # This Linux coordinator is single-threaded. Verify the
                # premise before using preexec_fn to restore the child's mask.
                require(threading.current_thread() is threading.main_thread() and
                        len(list(pathlib.Path("/proc/self/task").iterdir())) == 1,
                        "Runner spawn requires its existing single-threaded Linux coordinator")
                previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
                try:
                    # INT/TERM remain blocked until child is assigned. Pending
                    # handlers run during restoration inside this outer try.
                    child = subprocess.Popen(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                        start_new_session=True,
                        preexec_fn=lambda: signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask))
                finally:
                    signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
                while child.poll() is None:
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        self.sample()
                require(child.returncode == 0, name + " failed; preserve this run, no automatic retry/cleanup")
                self.sample()
            except BaseException:
                if child is not None:
                    self.interrupt(child)
                raise


def execute(args, report):
    require(not report["blockers"], "; ".join(report["blockers"]))
    require(args.review_approval and args.review_receipt and args.source_bundle and args.run, "--execute requires review approval/receipt, complete project Git bundle, and new run")
    approval = strict_json(args.review_approval)
    require(approval.get("schema") == "wasmhome-donor-review-gate-v1" and approval.get("status") == "approved" and
            approval.get("scope") == "donor_build_only" and approval.get("revision") == REVISION and approval.get("tree") == TREE,
            "review approval must name this exact reviewed candidate and donor-only scope")
    require(args.review_receipt.is_file() and approval.get("review_receipt_sha256") == digest(args.review_receipt), "actual review receipt digest differs")
    require(args.source_bundle.is_file() and not args.source_bundle.is_symlink(), "project bundle missing/redirected")
    heads = read_command(["git", "bundle", "list-heads", str(args.source_bundle)])
    require(any(line.split()[0] == REVISION for line in heads.splitlines()), "bundle must include the exact candidate revision")
    root = safe_new_run(args.run)
    root.mkdir()  # First mutation; all identity/resource/approval guards precede it.
    (root / "preflight.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    provenance = dict(script_sha256=digest(pathlib.Path(__file__).resolve()), project_bundle_sha256=digest(args.source_bundle), review_approval_sha256=digest(args.review_approval), approval=approval)
    (root / "execution-inputs.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    runner = Runner(root)
    project, llvm, llvm_build, wamrc_build = (root / name for name in ("wasmhome", "llvm-source", "llvm-build", "wamrc-build"))
    runner.run("project_clone", ["git", "-c", "core.autocrlf=false", "clone", "--no-checkout", str(args.source_bundle), str(project)])
    runner.run("project_checkout", ["git", "-C", str(project), "checkout", "--detach", REVISION])
    require(git(project, "rev-parse", "HEAD") == REVISION and git(project, "rev-parse", "HEAD^{tree}") == TREE, "project bundle source identity differs")
    clean(project)
    runner.run("runtime_acquire", [sys.executable, str(project / "tools/runtime_package.py"), "--acquire"], cwd=project)
    runner.run("llvm_init", ["git", "init", "--quiet", str(llvm)])
    runner.run("llvm_origin", ["git", "-C", str(llvm), "remote", "add", "origin", LLVM_ORIGIN])
    runner.run("llvm_fetch", ["git", "-c", "credential.helper=", "-C", str(llvm), "fetch", "--depth", "1", "--filter=blob:none", "origin", LLVM_SHA])
    runner.run("llvm_checkout", ["git", "-c", "credential.helper=", "-C", str(llvm), "checkout", "--detach", LLVM_SHA])
    require(git(llvm, "remote", "get-url", "origin") == LLVM_ORIGIN and git(llvm, "rev-parse", "HEAD") == LLVM_SHA, "LLVM official origin/pin differs")
    clean(llvm)
    for directory in (llvm_build, wamrc_build):
        require(not os.path.lexists(directory), "build directory exists; no CMake reuse")
    tools = report["tools"]
    cmake, gcc, gxx, ninja = (tools[x] for x in ("cmake", "gcc", "g++", "ninja"))
    llvm_command = [cmake, "-S", str(llvm / "llvm"), "-B", str(llvm_build), "-G", "Ninja",
        "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON", "-DCMAKE_C_COMPILER=" + gcc, "-DCMAKE_CXX_COMPILER=" + gxx,
        "-DCMAKE_MAKE_PROGRAM=" + ninja, "-DCMAKE_INTERPROCEDURAL_OPTIMIZATION=OFF", "-DLLVM_TARGETS_TO_BUILD=X86", "-DLLVM_EXPERIMENTAL_TARGETS_TO_BUILD=Xtensa",
        "-DLLVM_ENABLE_PROJECTS=", "-DLLVM_ENABLE_RUNTIMES=", "-DLLVM_INCLUDE_TOOLS=OFF", "-DLLVM_BUILD_TOOLS=OFF", "-DLLVM_INCLUDE_TESTS=OFF",
        "-DLLVM_INCLUDE_EXAMPLES=OFF", "-DLLVM_INCLUDE_BENCHMARKS=OFF", "-DLLVM_INCLUDE_DOCS=OFF", "-DLLVM_ENABLE_BINDINGS=OFF",
        "-DLLVM_ENABLE_LIBEDIT=OFF", "-DLLVM_ENABLE_TERMINFO=OFF", "-DLLVM_ENABLE_FFI=OFF", "-DLLVM_ENABLE_LIBXML2=OFF", "-DLLVM_ENABLE_ZLIB=OFF",
        "-DLLVM_ENABLE_ZSTD=OFF", "-DLLVM_ENABLE_LIBPFM=OFF", "-DLLVM_ENABLE_CURL=OFF", "-DLLVM_BUILD_LLVM_DYLIB=OFF", "-DLLVM_LINK_LLVM_DYLIB=OFF",
        "-DLLVM_ENABLE_ASSERTIONS=OFF", "-DLLVM_ENABLE_LTO=OFF", "-DLLVM_PARALLEL_COMPILE_JOBS=1", "-DLLVM_PARALLEL_LINK_JOBS=1"]
    wamrc_source = project / "build-runtime-port/component/wasm-micro-runtime/wamr-compiler"
    wamrc_command = [cmake, "-S", str(wamrc_source), "-B", str(wamrc_build), "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        "-DCMAKE_C_COMPILER=" + gcc, "-DCMAKE_CXX_COMPILER=" + gxx, "-DCMAKE_MAKE_PROGRAM=" + ninja, "-DCMAKE_INTERPROCEDURAL_OPTIMIZATION=OFF",
        "-DWAMR_BUILD_PLATFORM=linux", "-DWAMR_BUILD_TARGET=X86_64", "-DWAMR_BUILD_WITH_CUSTOM_LLVM=ON", "-DLLVM_DIR=" + str(llvm_build / "lib/cmake/llvm")]
    commands = dict(llvm_configure=llvm_command, llvm_build=[cmake, "--build", str(llvm_build), "--parallel", "1"],
                    wamrc_configure=wamrc_command, wamrc_build=[cmake, "--build", str(wamrc_build), "--parallel", "1"])
    (root / "commands.json").write_text(json.dumps(dict(fresh_build_directories=True, commands=commands), indent=2) + "\n", encoding="utf-8")
    for name, command in commands.items():
        runner.run(name, command)
        clean(llvm)
        if name.endswith("_configure"):
            # Inspect the actual newly generated cache before spending the build.
            sys.path.insert(0, str(project / "tools"))
            from toolchain_provenance import cache_values
            build = llvm_build if name == "llvm_configure" else wamrc_build
            cache = cache_values((build / "CMakeCache.txt").read_text(encoding="utf-8"))
            expected = dict(CMAKE_BUILD_TYPE="Release", CMAKE_GENERATOR="Ninja", CMAKE_C_COMPILER=gcc, CMAKE_CXX_COMPILER=gxx,
                            CMAKE_COMMAND=cmake, CMAKE_MAKE_PROGRAM=ninja, CMAKE_CACHEFILE_DIR=str(build),
                            CMAKE_HOME_DIRECTORY=str(llvm / "llvm") if name == "llvm_configure" else str(wamrc_source))
            if name == "llvm_configure":
                expected.update(LLVM_TARGETS_TO_BUILD="X86", LLVM_EXPERIMENTAL_TARGETS_TO_BUILD="Xtensa", LLVM_ENABLE_PROJECTS="", LLVM_ENABLE_RUNTIMES="",
                                LLVM_PARALLEL_COMPILE_JOBS="1", LLVM_PARALLEL_LINK_JOBS="1", LLVM_BUILD_LLVM_DYLIB="OFF", LLVM_LINK_LLVM_DYLIB="OFF")
                for flag in ("LIBEDIT", "TERMINFO", "FFI", "LIBXML2", "ZLIB", "ZSTD", "LIBPFM", "CURL", "ASSERTIONS", "LTO", "BINDINGS"):
                    expected["LLVM_ENABLE_" + flag] = "OFF"
            else:
                expected.update(WAMR_BUILD_WITH_CUSTOM_LLVM="ON", LLVM_DIR=str(llvm_build / "lib/cmake/llvm"))
            require(all(cache.get(key) == value for key, value in expected.items()), "actual CMake cache differs from proposed identity/flags; stop before build")
    runner.run("runtime_verify_after_build", [sys.executable, str(project / "tools/runtime_package.py")], cwd=project)
    runner.run("project_workspace_after_build", [sys.executable, "-c", "import sys;sys.path.insert(0,'tools');from toolchain_provenance import workspace;print(workspace(__import__('pathlib').Path.cwd()))"], cwd=project)
    binary = wamrc_build / "wamrc"
    runner.run("record_receipt", [sys.executable, str(project / "tools/record_wamrc_build.py"), "--llvm-source", str(llvm), "--llvm-build", str(llvm_build),
                "--wamrc-build", str(wamrc_build), "--binary", str(binary), "--commands", str(root / "commands.json"), "--output", str(root / "wamrc-build-receipt.json")], cwd=project)
    for name, command in (("wamrc_version", [str(binary), "--version"]), ("wamrc_targets", [str(binary), "--target=help"]),
                          ("wamrc_xtensa_cpu", [str(binary), "--target=xtensa", "--cpu=help"]), ("wamrc_dynamic_deps", ["ldd", str(binary)])):
        # Some help queries may return nonzero. Preserve output and code;
        # target/CPU selection and ABI require review, no inferred default override.
        result = subprocess.run(command, capture_output=True, timeout=30)
        (root / (name + ".log")).write_bytes(result.stdout + result.stderr)
        (root / (name + ".status.json")).write_text(json.dumps(dict(argv=command, returncode=result.returncode)) + "\n")
    index = {path.name: dict(sha256=digest(path), bytes=path.stat().st_size) for path in root.iterdir() if path.is_file()}
    index["wamrc"] = dict(sha256=digest(binary), bytes=binary.stat().st_size)
    (root / "donor-output-index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(run=str(root), status="built_unsigned_receipt_pending_target_abi_review", binary=str(binary))))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--profile", choices=("wsl", "lxc110"), default="wsl")
    parser.add_argument("--host-lease", type=pathlib.Path)
    parser.add_argument("--run", type=pathlib.Path)
    parser.add_argument("--source-bundle", type=pathlib.Path)
    parser.add_argument("--review-approval", type=pathlib.Path)
    parser.add_argument("--review-receipt", type=pathlib.Path)
    args = parser.parse_args()
    PROFILE, HOST_LEASE = args.profile, args.host_lease
    # Let an identified external supervisor request cleanup of the Runner's
    # own active process group instead of orphaning it by terminating Python.
    def interrupted(signum, frame):
        raise KeyboardInterrupt("external donor supervisor requested interruption")
    signal.signal(signal.SIGTERM, interrupted)
    try:
        report = preflight()
        if args.execute:
            execute(args, report)
        else:
            print(json.dumps(report, indent=2))
    except (ValueError, OSError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        parser.exit(1, "Donor stopped without cleanup/resource changes: " + str(error) + "\n")
