"""Acquire exact official userspace packages into exclusive directories."""
import hashlib
import json
import os
import pathlib
import subprocess
import tarfile
import urllib.request

ROOT = pathlib.Path('/home/wasmhome/.local/toolchains/wasmhome-e1-20261010-187ff962')
PACKAGES = [
    dict(name='wasi-sdk-27.0-x86_64-linux.tar.gz', url='https://github.com/WebAssembly/wasi-sdk/releases/download/wasi-sdk-27/wasi-sdk-27.0-x86_64-linux.tar.gz',
         sha256='b7d4d944c88503e4f21d84af07ac293e3440b1b6210bfd7fe78e0afd92c23bc2', bytes=124790235, destination='wasi-sdk'),
    dict(name='powershell-7.6.6-linux-x64.tar.gz', url='https://github.com/PowerShell/PowerShell/releases/download/v7.6.6/powershell-7.6.6-linux-x64.tar.gz',
         sha256='ddbc4a2d113bbd46d283cfedcbcd117a70caefd7673f41f2b4e0000badf103bc', bytes=75876271, destination='powershell-7.6.6'),
]

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

require(os.getuid() == 1000 and ROOT.is_dir() and ROOT.resolve() == ROOT, 'Wrong task identity')
require(subprocess.check_output(['hostname'], text=True).strip() == 'wasmhome-bench', 'Wrong CT')
for record in PACKAGES:
    package = ROOT / 'downloads' / record['name']
    destination = ROOT / record['destination']
    require(not os.path.lexists(package) and not os.path.lexists(destination), 'Package/destination exists; preserve previous run')
    with urllib.request.urlopen(record['url'], timeout=60) as response, package.open('xb') as output:
        while block := response.read(1024 * 1024):
            output.write(block)
    require(package.stat().st_size == record['bytes'] and sha(package) == record['sha256'], 'Official package digest/size differs')
    destination.mkdir(mode=0o700)
    with tarfile.open(package, 'r:gz') as archive:
        archive.extractall(destination, filter='data')
    record['local_package'] = str(package)
    record['installed_root'] = str(destination)
    print('Verified/extracted', record['name'], flush=True)
with (ROOT / 'sdk-pwsh-acquisition.json').open('x') as stream:
    json.dump(PACKAGES, stream, indent=2)
    stream.write('\n')
print(json.dumps(PACKAGES))
