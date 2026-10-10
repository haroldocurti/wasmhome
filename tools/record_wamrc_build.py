"""Record an existing donor build. Does not configure/build/install LLVM or wamrc."""
import argparse
import hashlib
import json
import pathlib
import re
import subprocess

from evidence_json import read
from runtime_package import git, require, verify
from toolchain_provenance import LLVM_SHA, cache_values, check_compiler_receipt, sha


def record(root, llvm_source, llvm_build, wamrc_build, binary, commands, output):
    require(not output.exists(), "receipt output exists; preserve it")
    runtime, materialized, _ = verify(root)
    require(git(llvm_source, "remote", "get-url", "origin") == "https://github.com/espressif/llvm-project.git" and
            git(llvm_source, "rev-parse", "HEAD") == LLVM_SHA, "LLVM checkout origin/pin differs")
    require(not git(llvm_source, "status", "--porcelain", "--untracked-files=all") and not git(llvm_source, "clean", "-ndx"), "LLVM checkout dirty/untracked/ignored")
    def snapshot(path):
        text = path.read_bytes().decode("utf-8")
        return dict(text=text, sha256=hashlib.sha256(text.encode()).hexdigest())
    snapshots = {label: snapshot(path) for label, path in
                 (("llvm_cache", llvm_build / "CMakeCache.txt"), ("wamrc_cache", wamrc_build / "CMakeCache.txt"),
                  ("llvm_commands", llvm_build / "compile_commands.json"), ("wamrc_commands", wamrc_build / "compile_commands.json"))}
    llvm_cache = cache_values(snapshots["llvm_cache"]["text"])
    wamrc_cache = cache_values(snapshots["wamrc_cache"]["text"])
    require(pathlib.Path(llvm_cache["CMAKE_HOME_DIRECTORY"]).resolve() == llvm_source.resolve() / "llvm", "LLVM cache belongs to different source")
    for cache, directory in ((llvm_cache, llvm_build), (wamrc_cache, wamrc_build)):
        require(pathlib.Path(cache["CMAKE_CACHEFILE_DIR"]).resolve() == directory.resolve(), "cache belongs to different build directory")
    require(pathlib.Path(wamrc_cache["CMAKE_HOME_DIRECTORY"]).resolve() == materialized / "wamr-compiler", "wamrc cache belongs to different source")
    require(binary.resolve().is_relative_to(wamrc_build.resolve()), "binary must belong to recorded wamrc build directory")
    tools = {}
    for label, tool in (("c", llvm_cache["CMAKE_C_COMPILER"]), ("cxx", llvm_cache["CMAKE_CXX_COMPILER"]),
                        ("cmake", llvm_cache["CMAKE_COMMAND"]), ("ninja", llvm_cache["CMAKE_MAKE_PROGRAM"])):
        path = pathlib.Path(tool)
        result = subprocess.run([str(path), "--version"], check=True, capture_output=True, text=True)
        tools[label] = dict(path=str(path), sha256=sha(path), version=(result.stdout + result.stderr).strip())
    version = subprocess.run([str(binary), "--version"], check=True, capture_output=True, text=True)
    ninja = (wamrc_build / "build.ninja").read_text(encoding="utf-8")
    # Donor paths are required to have no spaces; record actual link inputs,
    # not every library that happens to exist under the LLVM build directory.
    libraries = sorted(set(re.findall(r'[^\s";]*[/\\]libLLVM[^\s";]*\.a', ninja)))
    require(libraries, "no LLVM static link inputs found in build.ninja")
    records = []
    for name in libraries:
        path = pathlib.Path(name)
        require(path.resolve().is_relative_to(llvm_build.resolve()), "LLVM link input escapes recorded build directory")
        records.append(dict(name=path.name, sha256=sha(path), bytes=path.stat().st_size))
    command_data = read(commands)
    require(command_data.get("fresh_build_directories") is True, "creation log for fresh build directories required")
    receipt = dict(schema="wasmhome-wamrc-build-v1", runtime_identity=runtime,
                   llvm_source=dict(origin="https://github.com/espressif/llvm-project.git", revision=LLVM_SHA,
                                    tree=git(llvm_source, "rev-parse", "HEAD^{tree}"), clean=True),
                   binary=dict(version=(version.stdout + version.stderr).strip(), sha256=sha(binary)),
                   fresh_build_directories=True, build_tools=tools, commands=command_data["commands"],
                   llvm_cmake_dir=wamrc_cache["LLVM_DIR"], llvm_source_dir=llvm_cache["CMAKE_HOME_DIRECTORY"],
                   wamrc_source_dir=wamrc_cache["CMAKE_HOME_DIRECTORY"], snapshots=snapshots, linked_llvm_libraries=records)
    check_compiler_receipt(receipt, sha(binary), runtime)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(receipt, indent=2) + "\n")
    return receipt["binary"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("llvm-source", "llvm-build", "wamrc-build", "binary", "commands", "output"):
        parser.add_argument("--" + name, type=pathlib.Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(record(pathlib.Path(__file__).resolve().parents[1], args.llvm_source, args.llvm_build,
                                args.wamrc_build, args.binary, args.commands, args.output)))
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Compiler receipt failed: {error}\n")
