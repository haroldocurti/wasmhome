"""Freeze resolver artifacts, build pure-Python esptool offline, install in venv."""
import concurrent.futures
import hashlib
import importlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
import zipfile

ROOT = pathlib.Path('/home/wasmhome/.local/toolchains/wasmhome-e1-20261010-187ff962')
VENV = ROOT / 'idf526-py313'
PYTHON = VENV / 'bin/python'

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

require(os.getuid() == 1000 and ROOT.resolve() == ROOT and ROOT.is_dir(), 'Wrong task identity')
require(subprocess.check_output(['hostname'], text=True).strip() == 'wasmhome-bench', 'Wrong CT')
report_path = ROOT / 'core-resolution-first.json'
report = json.loads(report_path.read_text())
require(report['environment']['python_full_version'] == '3.13.5', 'Resolver Python differs')
sources = ROOT / 'core-distributions'
sources.mkdir(mode=0o700)
wheelhouse = ROOT / 'core-wheelhouse'
wheelhouse.mkdir(mode=0o700)
environment = {'PATH': str(VENV / 'bin') + ':/usr/bin:/bin', 'HOME': '/home/wasmhome', 'LANG': 'C.UTF-8',
               'PYTHONNOUSERSITE': '1', 'PIP_CONFIG_FILE': '/dev/null', 'PIP_DISABLE_PIP_VERSION_CHECK': '1',
               'TMPDIR': str(ROOT / 'tmp'), 'PYTHONDONTWRITEBYTECODE': '1', 'SOURCE_DATE_EPOCH': '1785801600'}
records = []
for entry in report['install']:
    metadata, info = entry['metadata'], entry['download_info']
    name, version = metadata['name'], metadata['version']
    require(re.fullmatch(r'[A-Za-z0-9_.-]+', name) and re.fullmatch(r'[A-Za-z0-9_.+!-]+', version), 'Unexpected distribution identity')
    url = info['url']
    require(url.startswith('https://files.pythonhosted.org/'), 'Unexpected distribution origin')
    filename = urllib.parse.unquote(urllib.parse.urlparse(url).path.rsplit('/', 1)[1])
    require(re.fullmatch(r'[A-Za-z0-9_.+-]+', filename), 'Unexpected distribution filename')
    digest = info['archive_info']['hashes']['sha256']
    require(re.fullmatch(r'[a-f0-9]{64}', digest), 'Missing distribution SHA-256')
    require(filename.endswith('.whl') or (name.lower() == 'esptool' and version == '4.12.0' and
            digest == 'd797f6529d582173508baf0572542ddadf53aae8c92c84ea9f420f242464103c'), 'Unreviewed source distribution')
    records.append(dict(name=name, version=version, url=url, filename=filename, sha256=digest))
require(len(records) == 57 and len({r['name'].lower() for r in records}) == 57, 'Unexpected closure')

def download(record):
    target = sources / record['filename']
    with urllib.request.urlopen(record['url'], timeout=60) as response, target.open('xb') as output:
        require(response.geturl().startswith('https://files.pythonhosted.org/'), 'Unexpected download redirect')
        while block := response.read(1024 * 1024):
            output.write(block)
    require(sha(target) == record['sha256'], 'Downloaded distribution digest differs')
    record['bytes'] = target.stat().st_size
    print('Verified', record['filename'], flush=True)

with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    list(pool.map(download, records))
(ROOT / 'core-source-inventory.json').write_text(json.dumps(records, indent=2) + '\n')
setuptools = next(r for r in records if r['name'] == 'setuptools')
setuptools_lock = ROOT / 'setuptools-bootstrap.lock'
setuptools_lock.write_text(f"setuptools=={setuptools['version']} --hash=sha256:{setuptools['sha256']}\n")
common = [str(PYTHON), '-I', '-m', 'pip']
subprocess.run(common + ['install', '--no-index', '--no-deps', '--no-cache-dir', '--require-hashes',
                         '--find-links', str(sources), '-r', str(setuptools_lock)], env=environment, check=True)
esptool = next(r for r in records if r['name'] == 'esptool')
subprocess.run(common + ['wheel', '--no-index', '--no-deps', '--no-build-isolation', '--no-cache-dir',
                         '--wheel-dir', str(wheelhouse), str(sources / esptool['filename'])], env=environment, check=True)
produced = list(wheelhouse.glob('esptool-4.12.0-*.whl'))
require(len(produced) == 1 and produced[0].name.endswith('-py3-none-any.whl'), 'esptool wheel is not pure Python')
with zipfile.ZipFile(produced[0]) as archive:
    require(not any(n.endswith(('.so', '.dll', '.pyd', '.a')) for n in archive.namelist()), 'Unexpected native esptool payload')
    wheel_metadata = archive.read('esptool-4.12.0.dist-info/WHEEL').decode()
    require('Root-Is-Purelib: true' in wheel_metadata and 'Tag: py3-none-any' in wheel_metadata, 'esptool wheel metadata differs')
install_records = []
for record in records:
    if record['name'] == 'esptool':
        selected = dict(record, source_sha256=record['sha256'], filename=produced[0].name,
                        sha256=sha(produced[0]), bytes=produced[0].stat().st_size, built_locally=True)
    else:
        shutil.copyfile(sources / record['filename'], wheelhouse / record['filename'])
        selected = dict(record, built_locally=False)
    install_records.append(selected)
lock = ROOT / 'idf-core-frozen.lock'
lock.write_text(''.join(f"{r['name']}=={r['version']} --hash=sha256:{r['sha256']}\n" for r in install_records))
(ROOT / 'core-wheel-inventory.json').write_text(json.dumps(install_records, indent=2) + '\n')
subprocess.run(common + ['install', '--no-index', '--no-deps', '--no-cache-dir', '--require-hashes',
                         '--find-links', str(wheelhouse), '-r', str(lock)], env=environment, check=True)
subprocess.run(common + ['check'], env=environment, check=True)
imports = ['ssl', 'ctypes', 'lzma', 'cryptography.hazmat.bindings._rust', '_cffi_backend', 'kconfiglib',
           'esptool', 'idf_component_manager', 'esp_coredump', 'serial', 'psutil', 'pydantic_core', 'pyclang']
probe = 'import importlib,json; modules=' + repr(imports) + '; [importlib.import_module(n) for n in modules]; print(json.dumps(modules))'
subprocess.run([str(PYTHON), '-I', '-c', probe], env=environment, check=True)
freeze = subprocess.check_output(common + ['freeze', '--all'], env=environment, text=True)
(ROOT / 'core-freeze.txt').write_text(freeze)
require(subprocess.check_output(['dpkg-query', '-W', '-f=${Version}', 'python3.13'], text=True) == '3.13.5-2', 'System Python package changed')
print(json.dumps(dict(schema='wasmhome-idf-core-install-v1', distributions=len(records), resolver_sha256=sha(report_path),
                     lock_sha256=sha(lock), freeze_sha256=sha(ROOT / 'core-freeze.txt'), imports=imports,
                     pending=['IDF configure/native build', 'runtime B/C builds and physical proof'])))
