import hashlib,json,pathlib,subprocess
local=pathlib.Path(__file__).resolve().parent/'native-final-ct107'
root='/home/wasmhome/.local/toolchains/wasmhome-e1-20261010-187ff962'
receipt=json.loads((local/'native-ct107-collection.json').read_bytes())
items=list(receipt['outputs'])+[dict(path=root+'/build-e1-native-ct107/CMakeCache.txt')]
for item in items:
    destination=local/pathlib.PurePosixPath(item['path']).name
    assert not destination.exists(), 'Preserve existing result'
    command=['scp','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile=build-session-e0/known_hosts','wasmhome@192.168.0.138:'+item['path'],str(destination)]
    subprocess.run(command,check=True)
    value=destination.read_bytes()
    if 'sha256' in item:
        assert len(value)==item['bytes'] and hashlib.sha256(value).hexdigest()==item['sha256']
    print(json.dumps(dict(file=destination.name,bytes=len(value),sha256=hashlib.sha256(value).hexdigest())))
