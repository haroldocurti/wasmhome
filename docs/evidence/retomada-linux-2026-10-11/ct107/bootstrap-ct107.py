"""Exclusive userspace pip bootstrap. No apt or global Python writes."""
import hashlib
import json
import os
import pathlib
import stat
import subprocess
import sys
import urllib.request

ROOT = pathlib.Path('/home/wasmhome/.local/toolchains/wasmhome-e1-20261010-187ff962')
WHEEL_URL = 'https://files.pythonhosted.org/packages/f3/6e/1736e5b4ae2b778ef2f81c47d797de9f891d4d8acb047a24ca37a60294dd/pip-26.2.1-py3-none-any.whl'
WHEEL_SHA = '71138adf1f4ca900cdb7d289c21b7494329f2332b6d85f0e1c42108c0384ed3e'

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

def sha(path):
    with pathlib.Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def package():
    return subprocess.check_output(['dpkg-query', '-W', '-f=${Version}', 'python3.13'], text=True)

require(os.getuid() == 1000 and pathlib.Path.home() == pathlib.Path('/home/wasmhome'), 'Wrong user/home')
require(subprocess.check_output(['hostname'], text=True).strip() == 'wasmhome-bench', 'Wrong CT hostname')
require(sys.version_info[:3] == (3, 13, 5), 'Unexpected system Python')
require(not os.path.lexists(ROOT), 'Destination already exists; preserve previous run')
for parent in reversed(ROOT.parents):
    if parent.exists():
        require(not parent.is_symlink() and parent.resolve() == parent, 'Redirected task parent')
        if parent.is_relative_to(pathlib.Path('/home/wasmhome')):
            require(parent.stat().st_uid == 1000 and not parent.stat().st_mode & stat.S_IWOTH, 'Unsafe task parent ownership/mode')
before = dict(version=sys.version, package=package(), binary_sha256=sha('/usr/bin/python3.13'))
ROOT.parent.mkdir(parents=True, exist_ok=True)
ROOT.mkdir(mode=0o700)
downloads = ROOT / 'downloads'
downloads.mkdir(mode=0o700)
wheel = downloads / 'pip-26.2.1-py3-none-any.whl'
with urllib.request.urlopen(WHEEL_URL, timeout=60) as response, wheel.open('xb') as output:
    require(response.geturl().startswith('https://files.pythonhosted.org/'), 'Unexpected download origin')
    while block := response.read(1024 * 1024):
        output.write(block)
require(wheel.stat().st_size == 1816632 and sha(wheel) == WHEEL_SHA, 'Wheel digest/size differs')
venv = ROOT / 'idf526-py313'
clean_env = {'PATH': '/usr/bin:/bin', 'HOME': '/home/wasmhome', 'LANG': 'C.UTF-8',
             'PYTHONNOUSERSITE': '1', 'PIP_CONFIG_FILE': '/dev/null', 'PIP_DISABLE_PIP_VERSION_CHECK': '1'}
subprocess.run(['/usr/bin/python3.13', '-I', '-m', 'venv', '--without-pip', str(venv)], env=clean_env, check=True)
python = venv / 'bin/python'
probe = "import sys,json,site; print(json.dumps(dict(prefix=sys.prefix,base_prefix=sys.base_prefix,executable=sys.executable,usersite=site.ENABLE_USER_SITE)))"
identity = json.loads(subprocess.check_output([str(python), '-I', '-c', probe], env=clean_env, text=True))
require(identity['prefix'] == str(venv) and identity['base_prefix'] != identity['prefix'] and identity['usersite'] is False, 'Venv isolation failed')
require('include-system-site-packages = false' in (venv / 'pyvenv.cfg').read_text(), 'System packages enabled')
bootstrap = "import sys,runpy; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module('pip',run_name='__main__')"
subprocess.run([str(python), '-I', '-c', bootstrap, str(wheel), 'install', '--no-index', '--no-deps',
                '--no-cache-dir', '--disable-pip-version-check', str(wheel)], env=clean_env, check=True)
installed = json.loads(subprocess.check_output([str(python), '-I', '-c',
    "import pip,json; print(json.dumps(dict(version=pip.__version__,file=pip.__file__)))"], env=clean_env, text=True))
require(installed['version'] == '26.2.1' and pathlib.Path(installed['file']).is_relative_to(venv), 'pip installed outside venv')
subprocess.run([str(python), '-I', '-m', 'pip', 'check'], env=clean_env, check=True)
after = dict(version=sys.version, package=package(), binary_sha256=sha('/usr/bin/python3.13'))
require(before == after, 'System Python changed')
receipt = dict(schema='wasmhome-userspace-bootstrap-v1', task_root=str(ROOT), venv=identity,
               pip=installed, bootstrap_wheel=dict(url=WHEEL_URL, sha256=WHEEL_SHA, bytes=wheel.stat().st_size),
               system_python_before=before, system_python_after=after,
               pending=['IDF core resolution/imports', 'IDF configure/native build', 'runtime B/C builds and physical proof'])
with (ROOT / 'bootstrap-receipt.json').open('x', encoding='utf-8') as stream:
    json.dump(receipt, stream, indent=2)
    stream.write('\n')
print(json.dumps(receipt))
