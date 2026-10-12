"""Fetch immutable files in bounded SSH reads; preserve the interrupted SCP files."""
import hashlib,json,pathlib,subprocess
local=pathlib.Path(__file__).resolve().parent/'native-final-ct107'/'ssh-r2'
local.mkdir(exist_ok=False)
root='/home/wasmhome/.local/toolchains/wasmhome-e1-20261010-187ff962'
receipt=json.loads((local.parent/'native-ct107-collection.json').read_bytes())
items=[i for i in receipt['outputs'] if not i['path'].endswith('.bin')]+[dict(path=root+'/build-e1-native-ct107/CMakeCache.txt')]
for item in items:
    destination=local/pathlib.PurePosixPath(item['path']).name
    offset=0
    with destination.open('xb') as output:
        while True:
            program='import pathlib,sys\np=pathlib.Path('+repr(item['path'])+')\nwith p.open("rb") as f:\n f.seek('+str(offset)+')\n sys.stdout.buffer.write(f.read(1048576))\n'
            command=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile=build-session-e0/known_hosts','wasmhome@192.168.0.138','python3','-']
            result=subprocess.run(command,input=program.encode(),capture_output=True)
            if result.returncode:
                with (local/(destination.name+'.failure.log')).open('xb') as log: log.write(result.stderr)
                result.check_returncode()
            output.write(result.stdout)
            offset+=len(result.stdout)
            if len(result.stdout)<1048576: break
    value=destination.read_bytes()
    if 'sha256' in item:
        assert len(value)==item['bytes'] and hashlib.sha256(value).hexdigest()==item['sha256']
    print(json.dumps(dict(file=destination.name,bytes=len(value),sha256=hashlib.sha256(value).hexdigest())),flush=True)
