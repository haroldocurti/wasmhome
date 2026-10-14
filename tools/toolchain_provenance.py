"""Check pinned local inputs and an explicit, unsigned compiler build receipt."""
import hashlib
import json
import pathlib
import re
import subprocess
import tarfile

from evidence_json import read
from runtime_package import git, require, verify

IDF_SHA = "9ef24e3e2a2c96e720d83c574a3f8699177573da"
WAMR_SHA = "25bd7eb63e828e4bd242cc9b38d260b4b31c6605"
LLVM_SHA = "344b928e67bfc1d07cb150609c55548e86a9bf19"
WASI_SHA = "b7d4d944c88503e4f21d84af07ac293e3440b1b6210bfd7fe78e0afd92c23bc2"
WASI_URL = "https://github.com/WebAssembly/wasi-sdk/releases/download/wasi-sdk-27/wasi-sdk-27.0-x86_64-linux.tar.gz"
GENERATORS = ("tools/build-artifacts.ps1", "tools/build-module-firmware.ps1", "tools/generate_artifacts.py", "tools/toolchain_provenance.py",
              "tools/generate_service_modules.py", "tools/validate_build_config.py", "tools/embed_artifact.py", "tools/runtime_package.py",
              "workload/service_telemetry.c", "workload/service_control.c",
              "sdkconfig.module-services", "tools/evidence_json.py", "tools/record_wamrc_build.py", "runtime-port/lock.json", "runtime-port/esp-idf.patch", "runtime-port/idf_component.yml")


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def workspace(root):
    require(not git(root, "status", "--porcelain", "--untracked-files=all"), "WasmHome source revision must be clean, including untracked")
    return dict(revision=git(root, "rev-parse", "HEAD"), tree=git(root, "rev-parse", "HEAD^{tree}"),
                generator_sha256={name: sha(root / name) for name in GENERATORS})


def idf_identity(path):
    require(git(path, "remote", "get-url", "origin") == "https://github.com/espressif/esp-idf.git", "IDF origin differs")
    require(git(path, "rev-parse", "HEAD") == IDF_SHA, "IDF must use pinned 5.2.6 Git SHA")
    require(not git(path, "status", "--porcelain", "--untracked-files=all") and not git(path, "clean", "-ndx"), "IDF source is dirty/untracked/ignored")
    require(git(path, "describe", "--tags", "--exact-match") == "v5.2.6", "IDF tag differs")
    modules = git(path, "submodule", "status", "--recursive", binary=True).decode().rstrip()
    require(all(line.startswith(" ") for line in modules.splitlines()), "IDF submodules are missing or differ")
    for line in modules.splitlines():
        module = path / line.split()[1]
        require(not git(module, "status", "--porcelain", "--untracked-files=all") and not git(module, "clean", "-ndx"), "IDF submodule is dirty/untracked/ignored")
    return dict(version="5.2.6", revision=IDF_SHA, origin="https://github.com/espressif/esp-idf.git",
                tree=git(path, "rev-parse", "HEAD^{tree}"), submodules=modules)


def wasi_identity(sdk, package):
    require(package.is_file() and sha(package) == WASI_SHA, "WASI-SDK 27 official package missing or digest differs")
    expected = set()
    with tarfile.open(package, "r:gz") as archive:
        for entry in archive:
            parts = pathlib.PurePosixPath(entry.name).parts
            require(parts and parts[0] == "wasi-sdk-27.0-x86_64-linux" and ".." not in parts, "unexpected WASI package path/version")
            relative = pathlib.Path(*parts[1:])
            path = sdk / relative
            require(path.resolve().is_relative_to(sdk.resolve()), "WASI SDK path escapes package")
            if entry.isdir():
                continue
            require(relative.as_posix() not in expected, "duplicate WASI package path")
            expected.add(relative.as_posix())
            if entry.issym():
                require(path.is_symlink() and path.readlink().as_posix() == entry.linkname, "WASI package symlink differs")
            else:
                require(entry.isfile() or entry.islnk(), "unsupported WASI package entry")
                require(path.is_file() and not path.is_symlink(), "WASI package file missing/redirected")
                require(sha(path) == hashlib.file_digest(archive.extractfile(entry), "sha256").hexdigest(), "WASI extracted file differs from official package")
    actual = {p.relative_to(sdk).as_posix() for p in sdk.rglob("*") if p.is_file() or p.is_symlink()}
    require(actual == expected, "WASI SDK has missing/extra files")
    return dict(version="27.0", package_sha256=WASI_SHA, package_url=WASI_URL,
                release_source_revision="fbdec30656d4099da5c7fd7e8275e4af777ca97e")


def cache_values(text):
    result = {}
    for line in text.splitlines():
        match = re.match(r"^([^#/:][^:]*):[^=]*=(.*)$", line)
        if match:
            require(match[1] not in result, "duplicate CMake cache field")
            result[match[1]] = match[2]
    return result


def snapshot(record):
    require(isinstance(record.get("text"), str), "build snapshot text required")
    require(hashlib.sha256(record["text"].encode()).hexdigest() == record.get("sha256"), "compiler build snapshot digest differs")
    return record["text"]


def check_compiler_receipt(receipt, binary_sha256, runtime_identity):
    require(receipt.get("schema") == "wasmhome-wamrc-build-v1", "complete wamrc build receipt required")
    require(receipt.get("runtime_identity") == runtime_identity, "wamrc receipt source differs from verified materialized runtime")
    require(receipt.get("llvm_source", {}).get("revision") == LLVM_SHA and
            receipt["llvm_source"].get("origin") == "https://github.com/espressif/llvm-project.git" and
            receipt["llvm_source"].get("clean") is True and re.fullmatch(r"[0-9a-f]{40}", receipt["llvm_source"].get("tree", "")), "LLVM source identity/clean snapshot missing")
    require(receipt.get("fresh_build_directories") is True, "fresh compiler build directories receipt required")
    require(receipt.get("binary", {}).get("sha256") == binary_sha256 and receipt["binary"].get("version") == "wamrc 2.4.5", "wamrc binary differs from build receipt")
    for label in ("c", "cxx", "cmake", "ninja"):
        record = receipt.get("build_tools", {}).get(label, {})
        require(bool(record.get("version")) and bool(record.get("path")) and re.fullmatch(r"[0-9a-f]{64}", record.get("sha256", "")), "missing compiler build tool identity")
    for label in ("llvm_configure", "llvm_build", "wamrc_configure", "wamrc_build"):
        command = receipt.get("commands", {}).get(label)
        require(isinstance(command, list) and command and all(isinstance(x, str) for x in command), "missing complete compiler build command")
    llvm = cache_values(snapshot(receipt["snapshots"]["llvm_cache"]))
    wamrc = cache_values(snapshot(receipt["snapshots"]["wamrc_cache"]))
    for cache in (llvm, wamrc):
        require(cache.get("CMAKE_BUILD_TYPE") == "Release" and cache.get("CMAKE_GENERATOR") == "Ninja", "compiler cache configuration differs")
        require(cache.get("CMAKE_COMMAND") == receipt["build_tools"]["cmake"]["path"] and
                cache.get("CMAKE_MAKE_PROGRAM") == receipt["build_tools"]["ninja"]["path"], "compiler cache build tools differ")
        for key, label in (("CMAKE_C_COMPILER", "c"), ("CMAKE_CXX_COMPILER", "cxx")):
            require(cache.get(key) == receipt["build_tools"][label]["path"], "compiler cache tool differs from receipt")
    require(llvm.get("LLVM_TARGETS_TO_BUILD") == "X86" and llvm.get("LLVM_EXPERIMENTAL_TARGETS_TO_BUILD") == "Xtensa"
            and llvm.get("LLVM_ENABLE_PROJECTS") == "" and llvm.get("LLVM_PARALLEL_LINK_JOBS") == "1", "LLVM cache must include pinned X86/Xtensa backend and one link job")
    require(wamrc.get("WAMR_BUILD_WITH_CUSTOM_LLVM") in ("ON", "1") and
            wamrc.get("LLVM_DIR") == receipt.get("llvm_cmake_dir"), "wamrc LLVM binding differs")
    require(wamrc.get("CMAKE_HOME_DIRECTORY") == receipt.get("wamrc_source_dir") and
            llvm.get("CMAKE_HOME_DIRECTORY") == receipt.get("llvm_source_dir"), "compiler cache source directory differs")
    for label, cache in (("llvm", llvm), ("wamrc", wamrc)):
        command = receipt["commands"][label + "_configure"]
        require(command[0] == receipt["build_tools"]["cmake"]["path"], "configure command tool identity differs")
        for option, expected in (("-S", cache.get("CMAKE_HOME_DIRECTORY")), ("-B", cache.get("CMAKE_CACHEFILE_DIR"))):
            require(command.count(option) == 1 and command.index(option) + 1 < len(command) and command[command.index(option) + 1] == expected,
                    "configure command differs from compiler cache identity")
        build = receipt["commands"][label + "_build"]
        require(build[0] == receipt["build_tools"]["cmake"]["path"] and build.count("--build") == 1 and
                build.index("--build") + 1 < len(build) and build[build.index("--build") + 1] == cache.get("CMAKE_CACHEFILE_DIR"), "build command differs from compiler cache identity")
    require(receipt["llvm_cmake_dir"] == llvm["CMAKE_CACHEFILE_DIR"].rstrip("/") + "/lib/cmake/llvm", "LLVM CMake package belongs to different build")
    require(str(receipt["wamrc_source_dir"]).replace("\\", "/").endswith("/wasm-micro-runtime/wamr-compiler"), "wamrc source must be materialized port")
    from evidence_json import loads
    for label in ("llvm_commands", "wamrc_commands"):
        commands = loads(snapshot(receipt["snapshots"][label]))
        require(isinstance(commands, list) and commands and all(isinstance(x.get("command"), str) and x.get("file") for x in commands), "compiler compile_commands snapshot missing")
    libraries = receipt.get("linked_llvm_libraries", [])
    require({"libLLVMCore.a", "libLLVMXtensaCodeGen.a", "libLLVMX86CodeGen.a"}.issubset({x.get("name") for x in libraries}), "LLVM linked library identities missing")
    require(all(re.fullmatch(r"[0-9a-f]{64}", x.get("sha256", "")) and isinstance(x.get("bytes"), int) and x["bytes"] > 0 for x in libraries), "invalid LLVM library identities")
    return receipt


def compiler_receipt(path, binary, runtime_identity):
    return check_compiler_receipt(read(path), sha(binary), runtime_identity)


def receipt_digest(receipt):
    return hashlib.sha256(json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def preflight(root, sdk, package, binary, receipt_path, idf):
    # Everything here is read-only. No destination is created until this passes.
    source = workspace(root)
    require(package is not None and receipt_path is not None and idf is not None, "WASI package, wamrc build receipt and IDF_PATH are mandatory")
    runtime = verify(root)[0]
    idf_data = idf_identity(idf)
    wasi_data = wasi_identity(sdk, package)
    compiler = compiler_receipt(receipt_path, binary, runtime)
    clang = sdk / "bin/clang"
    if not clang.is_file():
        clang = sdk / "bin/clang.exe"
    versions = {}
    for label, path in (("clang", clang), ("wamrc", binary)):
        result = subprocess.run([str(path), "--version"], check=True, capture_output=True, text=True)
        versions[label] = (result.stdout + result.stderr).strip()
    require(versions["wamrc"] == "wamrc 2.4.5" and "clang version" in versions["clang"], "tool version differs")
    return dict(runtime_identity=runtime, source_revision=source, idf_identity=idf_data, wasi_sdk_identity=wasi_data,
                wamrc_build_receipt=compiler, wamrc_build_receipt_sha256=receipt_digest(compiler), wamrc_receipt_input_file_sha256=sha(receipt_path),
                provenance_trust="unsigned_build_receipt_and_local_inputs_checked_not_independent_rebuild",
                wamrc_source_revision=WAMR_SHA, wamrc_path=str(binary.resolve()), clang_path=str(clang.resolve()),
                wamrc_sha256=sha(binary), clang_sha256=sha(clang), wamrc_version=versions["wamrc"], clang_version=versions["clang"])


def validate_manifest(manifest, root, check_workspace=True):
    require(manifest.get("provenance_trust") == "unsigned_build_receipt_and_local_inputs_checked_not_independent_rebuild", "toolchain provenance trust boundary missing")
    require(manifest.get("idf_identity", {}).get("revision") == IDF_SHA and manifest["idf_identity"].get("version") == "5.2.6", "artifact IDF pin differs")
    idf = manifest["idf_identity"]
    require(idf.get("origin") == "https://github.com/espressif/esp-idf.git" and re.fullmatch(r"[0-9a-f]{40}", idf.get("tree", "")) and
            isinstance(idf.get("submodules"), str) and bool(idf["submodules"]), "artifact IDF origin/tree/submodules receipt missing")
    require(manifest.get("wasi_sdk_identity", {}).get("version") == "27.0" and manifest["wasi_sdk_identity"].get("package_sha256") == WASI_SHA
            and manifest["wasi_sdk_identity"].get("package_url") == WASI_URL, "artifact WASI-SDK pin differs")
    require(manifest.get("wamrc_source_revision") == WAMR_SHA, "artifact wamrc source pin differs")
    receipt = manifest.get("wamrc_build_receipt", {})
    require(receipt.get("schema") == "wasmhome-wamrc-build-v1" and receipt.get("runtime_identity") == manifest.get("runtime_identity")
            and receipt.get("binary", {}).get("sha256") == manifest.get("wamrc_sha256"), "artifact compiler receipt binding differs")
    require(manifest.get("wamrc_build_receipt_sha256") == receipt_digest(receipt), "compiler receipt canonical digest differs")
    require(re.fullmatch(r"[0-9a-f]{64}", manifest.get("wamrc_receipt_input_file_sha256", "")), "compiler input receipt digest missing")
    check_compiler_receipt(receipt, manifest["wamrc_sha256"], manifest["runtime_identity"])
    if check_workspace:
        require(manifest.get("source_revision") == workspace(root), "artifact WasmHome revision/scripts differ or are dirty")
