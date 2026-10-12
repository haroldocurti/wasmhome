"""Collect the already hashed ELF through the working bounded ASCII transport."""
import base64,gzip,hashlib,json,pathlib,subprocess
local=pathlib.Path(__file__).resolve().parent/'native-final-ct107'/'elf-r4'
local.mkdir(exist_ok=False)
root='/home/wasmhome/.local/toolchains/wasmhome-e1-20261010-187ff962'
receipt=json.loads((local.parent/'native-ct107-collection.json').read_bytes())
item=next(i for i in receipt['outputs'] if i['path'].endswith('.elf'))
ssh=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile=build-session-e0/known_hosts','wasmhome@192.168.0.138','python3','-']
def call(program):
    result=subprocess.run(ssh,input=program.encode(),capture_output=True,timeout=30)
    if result.returncode:
        with (local/'failure.log').open('xb') as log: log.write(result.stderr)
        result.check_returncode()
    return result.stdout
remote=root+'/native-elf-transfer-r4.gz'
program='import gzip,hashlib,json,pathlib\np=pathlib.Path('+repr(item['path'])+')\ndata=p.read_bytes()\nassert hashlib.sha256(data).hexdigest()=='+repr(item['sha256'])+'\nblob=gzip.compress(data,compresslevel=1,mtime=0)\nwith pathlib.Path('+repr(remote)+').open("xb") as f: f.write(blob)\nprint(json.dumps(dict(bytes=len(blob),sha256=hashlib.sha256(blob).hexdigest())))\n'
compressed_identity=json.loads(call(program)); compressed=bytearray()
for offset in range(0,compressed_identity['bytes'],16384):
    program='import base64,pathlib\nwith pathlib.Path('+repr(remote)+').open("rb") as f:\n f.seek('+str(offset)+')\n print(base64.b64encode(f.read(16384)).decode())\n'
    compressed.extend(base64.b64decode(call(program).strip(),validate=True))
assert len(compressed)==compressed_identity['bytes'] and hashlib.sha256(compressed).hexdigest()==compressed_identity['sha256']
value=gzip.decompress(compressed)
assert len(value)==item['bytes'] and hashlib.sha256(value).hexdigest()==item['sha256']
with (local/'wasmhome_bench.elf').open('xb') as output: output.write(value)
with (local/'transfer-receipt.json').open('x') as output: json.dump(dict(elf=item,compressed=compressed_identity,transport='LF stdin and ASCII chunks16KiB'),output,indent=2)
print(json.dumps(dict(bytes=len(value),sha256=item['sha256'],local=str(local/'wasmhome_bench.elf'))))
