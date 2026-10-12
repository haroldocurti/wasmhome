"""New native E1 configure/build on CT107. No hardware access or flash."""
import hashlib
import json
import os
import pathlib
import re
import subprocess

ROOT = pathlib.Path('/home/wasmhome/.local/toolchains/wasmhome-e1-20261010-187ff962')
PROJECT = ROOT / 'project'
IDF = ROOT / 'esp-idf'
BUILD = ROOT / 'build-e1-native-ct107'
VENV = ROOT / 'idf526-py313'
PYTHON = VENV / 'bin/python'
require = lambda condition, message: None if condition else (_ for _ in ()).throw(RuntimeError(message))
require(os.getuid() == 1000 and ROOT.resolve() == ROOT, 'Wrong task user/root')
require(subprocess.check_output(['hostname'], text=True).strip() == 'wasmhome-bench', 'Wrong CT')
require(not os.path.lexists(BUILD), 'Build already exists; preserve prior evidence')
environment = {'PATH': str(VENV / 'bin') + ':/usr/bin:/bin', 'HOME': '/home/wasmhome', 'LANG': 'C.UTF-8',
               'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1', 'GIT_TERMINAL_PROMPT': '0',
               'PIP_CONFIG_FILE': '/dev/null', 'IDF_PATH': str(IDF), 'IDF_TOOLS_PATH': str(ROOT / 'idf-tools'),
               'IDF_PYTHON_ENV_PATH': str(VENV), 'IDF_TARGET': 'esp32s3', 'CMAKE_BUILD_PARALLEL_LEVEL': '1',
               'TMPDIR': str(ROOT / 'tmp'), 'IDF_CCACHE_ENABLE': '0'}
exports = subprocess.check_output([str(PYTHON), '-B', str(IDF / 'tools/idf_tools.py'), 'export', '--format=key-value'], env=environment, text=True)
for line in exports.splitlines():
    if '=' in line:
        key, value = line.split('=', 1)
        require(re.fullmatch(r'[A-Z_0-9]+', key), 'Unexpected export field')
        environment[key] = value.replace('$PATH', environment['PATH']) if key == 'PATH' else value
settings = ROOT / 'native-e1.defaults'
require(not os.path.lexists(settings), 'Defaults already exist')
settings.write_text('CONFIG_WASMHOME_RUN_BENCHMARK=y\n# CONFIG_WASMHOME_PLATFORM_SELFTEST is not set\n# CONFIG_WASMHOME_OBSERVER_SELFTEST is not set\nCONFIG_WASMHOME_WIFI_SSID=""\nCONFIG_WASMHOME_WIFI_PASSWORD=""\nCONFIG_WASMHOME_OBSERVER_IPV4=""\n')
config = ROOT / 'native-e1.sdkconfig'
require(not os.path.lexists(config), 'Config already exists')
configure = [str(PYTHON), '-B', str(IDF / 'tools/idf.py'), '--no-ccache', '-B', str(BUILD),
             '-DEXPERIMENT_SCENARIO=native', '-DIDF_TARGET=esp32s3', '-DSDKCONFIG=' + str(config),
             '-DSDKCONFIG_DEFAULTS=' + str(PROJECT / 'sdkconfig.defaults') + ';' + str(settings), 'reconfigure']
compile_command = ['/usr/bin/cmake', '--build', str(BUILD), '--parallel', '1']
with (ROOT / 'native-e1-commands.json').open('x') as stream:
    json.dump(dict(configure=configure, build=compile_command, idf_path=str(IDF), tools_path=str(ROOT / 'idf-tools'),
                   python=str(PYTHON), bytecode_disabled=True), stream, indent=2)
subprocess.run(configure, cwd=PROJECT, env=environment, check=True)
subprocess.run(compile_command, cwd=PROJECT, env=environment, check=True)
nm = ROOT / 'idf-tools/tools/xtensa-esp-elf/esp-13.2.0_20250707/xtensa-esp-elf/bin/xtensa-esp32s3-elf-nm'
if not nm.is_file():
    nm = nm.with_name('xtensa-esp-elf-nm')
require(nm.is_file(), 'Native nm missing')
result = ROOT / 'native-e1-build-check.json'
require(not os.path.lexists(result), 'Evidence output exists')
subprocess.run([str(PYTHON), '-B', str(PROJECT / 'tools/validate_build_config.py'), '--build-dir', str(BUILD),
                '--scenario', 'native', '--nm', str(nm), '--output', str(result)], cwd=PROJECT, env=environment, check=True)
require(subprocess.check_output(['git', '-C', str(PROJECT), 'rev-parse', 'HEAD'], text=True).strip() == '187ff96279ed8e6d3da14b7663ce5b5e856dff49', 'Project revision changed')
print('Native CT107 build/config/source gates passed; no physical execution')
