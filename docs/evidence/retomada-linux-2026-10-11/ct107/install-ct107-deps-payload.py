"""Install reviewed exact new packages only. Run as root inside CT107."""
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

def query(arguments):
    return subprocess.check_output(arguments, text=True)

def sha(path):
    with pathlib.Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def dpkg_snapshot():
    text = query(['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\t${db:Status-Abbrev}\n'])
    records = {}
    for line in text.splitlines():
        name, version, status = line.split('\t')
        require(name not in records, 'Duplicate dpkg snapshot identity')
        records[name] = dict(version=version, status=status)
    return text, records

plan = json.loads(pathlib.Path(sys.argv[1]).read_text()) if len(sys.argv) > 1 else {'schema': 'wasmhome-ct107-apt-pins-v1', 'simulation_sha256': '5b4b0259e7f959593b566dba610b84366bf4258f3f57ff5834e6bab54df67276', 'packages': {'m4': '1.4.19-8', 'flex': '2.6.4-8.2+b4', 'bison': '2:3.8.2+dfsg-1+b2', 'libfmt10': '10.1.1+ds1-4', 'libhiredis1.1.0': '1.2.0-6+b3', 'ccache': '4.11.2-2', 'cmake-data': '3.31.6-2', 'libarchive13t64': '3.7.4-4+deb13u1', 'libjsoncpp26': '1.9.6-3', 'librhash1': '1.4.5-1', 'cmake': '3.31.6-2', 'libngtcp2-16': '1.11.0-1+deb13u1', 'libngtcp2-crypto-gnutls8': '1.11.0-1+deb13u1', 'libcurl3t64-gnutls': '8.14.1-2+deb13u5', 'liberror-perl': '0.17030-1', 'git-man': '1:2.47.3-0+deb13u1', 'git': '1:2.47.3-0+deb13u1', 'gperf': '3.2.1-1', 'libunwind8': '1.8.1-0.1', 'libusb-1.0-0': '2:1.0.28-1', 'ninja-build': '1.12.1-1'}}
require(plan['schema'] == 'wasmhome-ct107-apt-pins-v1' and len(plan['packages']) == 21, 'Unknown dependency plan')
require(os.getuid() == 0 and query(['hostname']).strip() == 'wasmhome-bench', 'Wrong root/CT identity')
interfaces = json.loads(query(['ip', '-j', 'addr', 'show']))
require(any(i.get('address', '').lower() == 'bc:24:11:b6:36:f7' and
            any(a.get('local') == '192.168.0.138' for a in i.get('addr_info', [])) for i in interfaces), 'Wrong CT IP/MAC')
require(shutil.disk_usage('/').free > 5 * 1024**3, 'Insufficient CT package disk margin')
python_before = dict(package=query(['dpkg-query', '-W', '-f=${Version}', 'python3.13']), sha256=sha('/usr/bin/python3.13'))
require(python_before['package'] == '3.13.5-2', 'Python package drifted')
receipt_root = pathlib.Path('/var/tmp/wasmhome-e1-deps-20261010-187ff962')
require(not os.path.lexists(receipt_root) and not pathlib.Path('/var/tmp').is_symlink(), 'Receipt destination exists or redirects')
specs = [f'{name}={version}' for name, version in sorted(plan['packages'].items())]
base = ['apt-get', '--no-install-recommends', '--no-upgrade', '--no-remove']
simulation = query(base + ['--simulate', 'install', *specs])
actual = {}
for line in simulation.splitlines():
    require(not line.startswith('Remv '), 'Simulation removes packages')
    if line.startswith('Inst '):
        match = re.fullmatch(r'Inst (\S+) \((\S+) .+\)', line)
        require(match is not None, 'Simulation has upgrade/unknown install form')
        require(match[1] not in actual, 'Duplicate install item')
        actual[match[1]] = match[2]
require(actual == plan['packages'] and '0 upgraded, 21 newly installed, 0 to remove' in simulation, 'APT delta differs from reviewed plan')
receipt_root.mkdir(mode=0o700)
(receipt_root / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
(receipt_root / 'simulation.txt').write_text(simulation)
before_text, before_records = dpkg_snapshot()
(receipt_root / 'dpkg-before.txt').write_text(before_text)
print(simulation, flush=True)
environment = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/root', 'LANG': 'C.UTF-8', 'DEBIAN_FRONTEND': 'noninteractive'}
with (receipt_root / 'install.log').open('x') as log:
    result = subprocess.run(base + ['--assume-yes', 'install', *specs], env=environment, stdout=log, stderr=subprocess.STDOUT)
after_text, after_records = dpkg_snapshot()
(receipt_root / 'dpkg-after.txt').write_text(after_text)
native_arch = query(['dpkg', '--print-architecture']).strip()
expected_keys = set()
for name in plan['packages']:
    matches = [key for key in after_records if key == name or key == name + ':' + native_arch]
    require(len(matches) == 1, 'Final dependency identity missing/ambiguous')
    expected_keys.add(matches[0])
changed = {name for name in before_records.keys() | after_records.keys() if before_records.get(name) != after_records.get(name)}
delta = dict(changed=sorted(changed), expected=sorted(expected_keys),
             missing_or_unexpected=sorted(changed ^ expected_keys), install_returncode=result.returncode)
(receipt_root / 'delta-verification.json').write_text(json.dumps(delta, indent=2) + '\n')
print((receipt_root / 'install.log').read_text(), flush=True)
require(result.returncode == 0, 'APT install failed; preserve partial state and logs')
require(changed == expected_keys, 'Final dpkg delta differs from the 21 reviewed package identities')
for name, version in plan['packages'].items():
    require(query(['dpkg-query', '-W', '-f=${Version} ${db:Status-Abbrev}', name]) == version + ' ii ', 'Installed dependency differs')
python_after = dict(package=query(['dpkg-query', '-W', '-f=${Version}', 'python3.13']), sha256=sha('/usr/bin/python3.13'))
require(python_after == python_before, 'System Python changed')
receipt = dict(schema='wasmhome-ct107-apt-result-v1', packages=plan['packages'], python_before=python_before,
               python_after=python_after, receipt_root=str(receipt_root), install_returncode=result.returncode,
               dpkg_delta=delta)
(receipt_root / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps(receipt))
