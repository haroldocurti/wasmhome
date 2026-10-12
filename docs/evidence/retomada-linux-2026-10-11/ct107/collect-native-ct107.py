"""Read-only collection of the completed CT107 build; local exclusive outputs."""
import base64
import hashlib
import json
import pathlib
import subprocess

LOCAL = pathlib.Path(__file__).resolve().parent / 'native-final-ct107'
LOCAL.mkdir(exist_ok=False)
REMOTE = r'''
import base64,hashlib,io,json,os,pathlib,subprocess,tarfile
root=pathlib.Path('/home/wasmhome/.local/toolchains/wasmhome-e1-20261010-187ff962')
project=root/'project'; build=root/'build-e1-native-ct107'
assert os.getuid()==1000
assert subprocess.check_output(['hostname'],text=True).strip()=='wasmhome-bench'
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
assert subprocess.check_output(['git','-C',str(project),'rev-parse','HEAD'],text=True).strip()=='187ff96279ed8e6d3da14b7663ce5b5e856dff49'
assert not subprocess.check_output(['git','-C',str(project),'status','--porcelain'],text=True).strip()
lock=project/'dependencies.lock'; managed=project/'managed_components'
assert lock.is_file() and managed.is_dir()
inventory=[]
for p in sorted(managed.rglob('*')):
    assert not p.is_symlink(), 'managed component symlink'
    if p.is_file(): inventory.append(dict(path=p.relative_to(project).as_posix(),bytes=p.stat().st_size,sha256=sha(p)))
assert inventory and any(x['path'].endswith('/.component_hash') for x in inventory)
archive=io.BytesIO()
with tarfile.open(fileobj=archive,mode='w:gz') as tf:
    tf.add(lock,arcname='dependencies.lock',recursive=False)
    for item in inventory: tf.add(project/item['path'],arcname=item['path'],recursive=False)
tools=root/'idf-tools/tools/xtensa-esp-elf/esp-13.2.0_20250707/xtensa-esp-elf/bin'
objdump=tools/'xtensa-esp-elf-objdump'
disassembly=subprocess.check_output([str(objdump),'-d','--disassemble=benchmark_run',str(build/'wasmhome_bench.elf')])
assert b'<benchmark_run>' in disassembly
files={'native-e1-build-check.json':(root/'native-e1-build-check.json').read_bytes(),
       'native-e1-commands.json':(root/'native-e1-commands.json').read_bytes(),
       'native-dependencies.lock':lock.read_bytes(),
       'native-managed-components.tar.gz':archive.getvalue(),
       'native-benchmark-disassembly.txt':disassembly}
outputs=[]
for p in [build/'wasmhome_bench.bin',build/'wasmhome_bench.elf',build/'compile_commands.json',root/'native-e1.sdkconfig',build/'partition_table/partition-table.bin']:
    assert p.is_file(); outputs.append(dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p)))
receipt=dict(schema='wasmhome-native-ct107-collection-v1',revision='187ff96279ed8e6d3da14b7663ce5b5e856dff49',
    lock_sha256=sha(lock),managed_components=inventory,outputs=outputs,
    objdump=dict(path=str(objdump),sha256=sha(objdump)),
    python_package=subprocess.check_output(['dpkg-query','-W','-f=${Version}','python3.13'],text=True),
    python_sha256=sha(pathlib.Path('/usr/bin/python3.13')),
    files={name:dict(bytes=len(value),sha256=hashlib.sha256(value).hexdigest()) for name,value in files.items()},
    pending=['independent review of native build and component evidence','native checksum on ESP32-S3','real wamrc and B/C builds'])
files['native-ct107-collection.json']=json.dumps(receipt,indent=2).encode()
print(json.dumps({name:base64.b64encode(value).decode('ascii') for name,value in files.items()}))
'''
command=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile=build-session-e0/known_hosts','wasmhome@192.168.0.138','python3','-']
result=subprocess.run(command,input=REMOTE.replace('\r\n','\n').encode(),capture_output=True)
(LOCAL/'collect-native-ct107-stderr.log').write_bytes(result.stderr)
result.check_returncode()
payload=json.loads(result.stdout)
for name,encoded in payload.items():
    assert pathlib.Path(name).name==name
    with (LOCAL/name).open('xb') as output: output.write(base64.b64decode(encoded,validate=True))
receipt=json.loads((LOCAL/'native-ct107-collection.json').read_bytes())
for name,item in receipt['files'].items():
    value=(LOCAL/name).read_bytes()
    assert len(value)==item['bytes'] and hashlib.sha256(value).hexdigest()==item['sha256']
print(json.dumps(dict(outputs=receipt['outputs'],managed_files=len(receipt['managed_components']),lock_sha256=receipt['lock_sha256'],python_package=receipt['python_package']),indent=2))
