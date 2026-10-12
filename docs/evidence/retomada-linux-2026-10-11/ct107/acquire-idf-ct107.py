"""Acquire fixed IDF source/submodules and official target tools, outside source."""
import hashlib
import json
import os
import pathlib
import subprocess

ROOT = pathlib.Path('/home/wasmhome/.local/toolchains/wasmhome-e1-20261010-187ff962')
IDF = ROOT / 'esp-idf'
PIN = '9ef24e3e2a2c96e720d83c574a3f8699177573da'

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

require(os.getuid() == 1000 and ROOT.resolve() == ROOT and ROOT.is_dir(), 'Wrong task identity')
require(subprocess.check_output(['hostname'], text=True).strip() == 'wasmhome-bench', 'Wrong CT')
require(not os.path.lexists(IDF) and not os.path.lexists(ROOT / 'idf-tools'), 'Source/tool destination exists; preserve it')
environment = {'PATH': str(ROOT / 'idf526-py313/bin') + ':/usr/bin:/bin', 'HOME': '/home/wasmhome', 'LANG': 'C.UTF-8',
               'GIT_TERMINAL_PROMPT': '0', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
               'IDF_PATH': str(IDF), 'IDF_TOOLS_PATH': str(ROOT / 'idf-tools'),
               'IDF_PYTHON_ENV_PATH': str(ROOT / 'idf526-py313'), 'PIP_CONFIG_FILE': '/dev/null'}
subprocess.run(['git', 'clone', '--depth', '1', '--branch', 'v5.2.6', '--recurse-submodules',
                '--shallow-submodules', '--jobs', '1', 'https://github.com/espressif/esp-idf.git', str(IDF)], env=environment, check=True)
def git(*arguments):
    return subprocess.check_output(['git', '-C', str(IDF), *arguments], env=environment, text=True).strip()
require(git('rev-parse', 'HEAD') == PIN and git('describe', '--tags', '--exact-match') == 'v5.2.6', 'IDF pin/tag differs')
require(git('remote', 'get-url', 'origin') == 'https://github.com/espressif/esp-idf.git', 'IDF origin differs')
require(not git('status', '--porcelain', '--untracked-files=all') and not git('clean', '-ndx'), 'IDF source not clean')
modules = subprocess.check_output(['git', '-C', str(IDF), 'submodule', 'status', '--recursive'], env=environment, text=True).rstrip()
require(modules and all(line.startswith(' ') for line in modules.splitlines()), 'Missing/divergent submodule')
for line in modules.splitlines():
    path = IDF / line.split()[1]
    for arguments in [('status', '--porcelain', '--untracked-files=all'), ('clean', '-ndx')]:
        require(not subprocess.check_output(['git', '-C', str(path), *arguments], env=environment, text=True).strip(), 'Dirty submodule')
tools_root = ROOT / 'idf-tools'
tools_root.mkdir(mode=0o700)
constraints = (ROOT / 'core-inputs/espidf.constraints.v5.2.preflight.txt').read_bytes()
require(hashlib.sha256(constraints).hexdigest() == '082c1e98d4f950e853dc9c773dfcd21bcfe8a95cbe8cd1b0412e25150d421069', 'Constraints drifted')
(tools_root / 'espidf.constraints.v5.2.txt').write_bytes(constraints)
subprocess.run([str(ROOT / 'idf526-py313/bin/python'), '-B', str(IDF / 'tools/idf_tools.py'), 'install', '--targets=esp32s3'], env=environment, check=True)
require(not git('status', '--porcelain', '--untracked-files=all') and not git('clean', '-ndx'), 'Installer contaminated IDF source')
subprocess.run([str(ROOT / 'idf526-py313/bin/python'), '-B', str(IDF / 'tools/idf_tools.py'), 'check-python-dependencies'], env=environment, check=True)
subprocess.run([str(ROOT / 'idf526-py313/bin/python'), '-B', str(IDF / 'tools/idf.py'), '--version'], env=environment, check=True)
inventory = []
for package in (tools_root / 'dist').glob('*'):
    if package.is_file():
        with package.open('rb') as stream:
            inventory.append(dict(name=package.name, bytes=package.stat().st_size, sha256=hashlib.file_digest(stream, 'sha256').hexdigest()))
receipt = dict(schema='wasmhome-idf-acquisition-v1', revision=PIN, tree=git('rev-parse', 'HEAD^{tree}'),
               origin=git('remote', 'get-url', 'origin'), submodules=modules,
               official_tools_json_sha256=hashlib.sha256((IDF / 'tools/tools.json').read_bytes()).hexdigest(),
               packages=inventory, source_clean=True, pending=['IDF configure/native build', 'runtime B/C builds and physical proof'])
with (ROOT / 'idf-acquisition.json').open('x') as stream:
    json.dump(receipt, stream, indent=2)
    stream.write('\n')
print(json.dumps(receipt))
