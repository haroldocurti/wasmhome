"""Acquire/check the independent WAMR port; no SDK, compiler or remote installation."""
import argparse
import hashlib
import io
import json
import pathlib
import shutil
import subprocess
import tarfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = pathlib.Path("build-runtime-port/component/wasm-micro-runtime")


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def local_path(root, relative):
    path = root / relative
    require(path.resolve().is_relative_to(root), "runtime path escapes workspace")
    require(not any(parent.is_symlink() for parent in (path, *path.parents) if parent.is_relative_to(root)), "runtime path contains symlink")
    return path


def git(repo, *args, binary=False):
    result = subprocess.run(["git", "-c", "core.autocrlf=false", "-C", str(repo), *args], check=True, capture_output=True)
    return result.stdout if binary else result.stdout.decode().strip()


def load_lock(root):
    port = root / "runtime-port"
    lock = json.loads((port / "lock.json").read_text())
    require(lock["schema"] == "wasmhome-runtime-port-v1", "invalid port schema")
    for patch in lock["patches"]:
        path = (port / patch["path"]).resolve()
        require(path.is_relative_to(port.resolve()), "patch escapes port directory")
        require(digest(path.read_bytes()) == patch["sha256"], "port patch digest differs")
    require(digest((port / "idf_component.yml").read_bytes()) == lock["metadata_sha256"], "port metadata differs")
    return lock


def source(root, name, pin, acquire=False):
    repo = local_path(root, pathlib.Path("build-runtime-port/sources") / name)
    if not repo.exists() and acquire:
        repo.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--filter=blob:none", "--no-checkout", pin["url"], str(repo)], check=True)
        git(repo, "checkout", "--detach", pin["revision"])
    require(repo.is_dir(), f"source cache missing: {repo}; run --acquire")
    require(git(repo, "remote", "get-url", "origin") == pin["url"], "source origin differs")
    require(git(repo, "rev-parse", "HEAD") == pin["revision"], "source revision differs")
    require(not git(repo, "status", "--porcelain", "--untracked-files=all"), "source cache is dirty/untracked")
    require(not git(repo, "clean", "-ndx"), "source cache has ignored contamination")
    return repo


def tree(repo, revision):
    records = {}
    for entry in git(repo, "ls-tree", "-rz", "--full-tree", revision, binary=True).split(b"\0"):
        if not entry:
            continue
        info, name = entry.split(b"\t", 1)
        mode, kind, oid = info.decode().split()
        require(kind == "blob", "unexpected submodule: explicit pin required")
        records[name.decode()] = (mode, oid)
    return records


def export(repo, revision, destination):
    # Git archive reads committed objects, never arbitrary checkout files.
    data = git(repo, "archive", "--format=tar", revision, binary=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive:
            path = (destination / member.name).resolve()
            require(path.is_relative_to(destination.resolve()), "archive path escapes destination")
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            elif member.isfile() or member.issym():
                path.parent.mkdir(parents=True, exist_ok=True)
                # Peripheral Git symlinks are materialized as their blob text on
                # Windows and Linux alike. No core file is a symlink.
                content = member.linkname.encode() if member.issym() else archive.extractfile(member).read()
                path.write_bytes(content)
                path.chmod(0o644 if member.issym() else member.mode & 0o777)
            else:
                raise ValueError("unsupported archive member")


def verify(root=ROOT):
    root = root.resolve()
    lock = load_lock(root)
    directory = local_path(root, PACKAGE)
    expected = {}
    for key, name, prefix in (("upstream", "wamr", ""), ("app_framework", "app-framework", "wamr-app-framework/")):
        repo = source(root, name, lock[key])
        expected.update({prefix + path: oid for path, (_, oid) in tree(repo, lock[key]["revision"]).items()})
    extra = {"idf_component.yml", ".wasmhome-runtime.json"}
    require(not any(path.is_symlink() for path in directory.rglob("*")), "runtime symlink is not allowed")
    actual = {path.relative_to(directory).as_posix() for path in directory.rglob("*") if path.is_file()}
    require(actual == set(expected) | extra, "runtime files missing or extra/untracked")
    package_hash = hashlib.sha256()
    checked = set()
    for name in sorted(actual - {".wasmhome-runtime.json"}):
        path = directory / name
        require(not path.is_symlink(), "runtime symlink is not allowed")
        data = path.read_bytes()
        if name == "core/version.h":
            data = data.replace(b"\r\n", b"\n")
        if name in lock["patched_files"]:
            require(digest(data) == lock["patched_files"][name], f"patched runtime file changed: {name}")
        elif name == "idf_component.yml":
            require(digest(data) == lock["metadata_sha256"], "runtime metadata changed")
        else:
            blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            require(blob == expected[name], f"upstream runtime file changed: {name}")
        package_hash.update(name.encode())
        package_hash.update(digest(data).encode())
        checked.add(path.resolve())
    require(package_hash.hexdigest() == lock["package_sha256"], "runtime package digest differs from pinned port")
    identity = dict(component=lock["component"], version=lock["version"], component_hash=package_hash.hexdigest(),
                    repository_revision=lock["upstream"]["revision"], app_framework_revision=lock["app_framework"]["revision"],
                    port_lock_sha256=digest((root / "runtime-port/lock.json").read_bytes()), aot_version=lock["aot_version"])
    receipt = json.loads((directory / ".wasmhome-runtime.json").read_text())
    require(receipt == identity, "runtime receipt differs from checked source identity")
    return identity, directory.resolve(), checked


def acquire(root=ROOT):
    root = root.resolve()
    lock = load_lock(root)
    destination = local_path(root, PACKAGE)
    if destination.exists():
        return verify(root)[0]  # Repetition checks everything; never overwrites.
    upstream = source(root, "wamr", lock["upstream"], acquire=True)
    framework = source(root, "app-framework", lock["app_framework"], acquire=True)
    stage = destination.with_name(destination.name + ".preparing")
    require(not stage.exists(), "incomplete staging directory exists; inspect before retry")
    stage.mkdir(parents=True)
    export(upstream, lock["upstream"]["revision"], stage)
    export(framework, lock["app_framework"]["revision"], stage / "wamr-app-framework")
    for patch in lock["patches"]:
        subprocess.run(["git", "-c", "core.autocrlf=false", "--git-dir=" + str(upstream / ".git"), "--work-tree=" + str(stage), "apply", "--check", str(root / "runtime-port" / patch["path"])], cwd=stage, check=True)
        subprocess.run(["git", "-c", "core.autocrlf=false", "--git-dir=" + str(upstream / ".git"), "--work-tree=" + str(stage), "apply", str(root / "runtime-port" / patch["path"])], cwd=stage, check=True)
    shutil.copyfile(root / "runtime-port/idf_component.yml", stage / "idf_component.yml")
    package_hash = hashlib.sha256()
    for path in sorted((p for p in stage.rglob("*") if p.is_file()), key=lambda p: p.relative_to(stage).as_posix()):
        package_hash.update(path.relative_to(stage).as_posix().encode())
        package_hash.update(digest(path.read_bytes()).encode())
    require(package_hash.hexdigest() == lock["package_sha256"], "prepared runtime digest differs from pinned port")
    identity = dict(component=lock["component"], version=lock["version"], component_hash=package_hash.hexdigest(),
                    repository_revision=lock["upstream"]["revision"], app_framework_revision=lock["app_framework"]["revision"],
                    port_lock_sha256=digest((root / "runtime-port/lock.json").read_bytes()), aot_version=lock["aot_version"])
    (stage / ".wasmhome-runtime.json").write_text(json.dumps(identity, indent=2) + "\n")
    stage.rename(destination)
    return verify(root)[0]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acquire", action="store_true", help="download fixed Git sources and materialize candidate locally")
    args = parser.parse_args()
    try:
        print(json.dumps(acquire() if args.acquire else verify()[0], indent=2))
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Runtime package failed: {error}\n")
