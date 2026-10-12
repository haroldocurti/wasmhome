import base64,gzip,hashlib,json,pathlib,subprocess
local=pathlib.Path(__file__).resolve().parent/'native-final-ct107'/'metadata-r3'
local.mkdir(exist_ok=False)
root='/home/wasmhome/.local/toolchains/wasmhome-e1-20261010-187ff962'
receipt=json.loads((local.parent/'native-ct107-collection.json').read_bytes())
items=[i for i in receipt['outputs'] if pathlib.PurePosixPath(i['path']).name in ('compile_commands.json','native-e1.sdkconfig')]+[dict(path=root+'/build-e1-native-ct107/CMakeCache.txt')]
for item in items:
    destination=local/pathlib.PurePosixPath(item['path']).name
    compressed=bytearray(); offset=0
    while True:
        program='import base64,gzip,pathlib\ndata=gzip.compress(pathlib.Path('+repr(item['path'])+').read_bytes(),mtime=0)\nprint(base64.b64encode(data['+str(offset)+':'+str(offset+16384)+']).decode())\n'
        command=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile=build-session-e0/known_hosts','wasmhome@192.168.0.138','python3','-']
        result=subprocess.run(command,input=program.encode(),capture_output=True,timeout=30)
        if result.returncode:
            with (local/(destination.name+'.failure.log')).open('xb') as log: log.write(result.stderr)
            result.check_returncode()
        chunk=base64.b64decode(result.stdout.strip(),validate=True); compressed.extend(chunk); offset+=len(chunk)
        if len(chunk)<16384: break
    value=gzip.decompress(compressed)
    if 'sha256' in item: assert len(value)==item['bytes'] and hashlib.sha256(value).hexdigest()==item['sha256']
    with destination.open('xb') as output: output.write(value)
    print(json.dumps(dict(file=destination.name,bytes=len(value),sha256=hashlib.sha256(value).hexdigest())),flush=True)
