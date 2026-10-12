import datetime,json,pathlib,subprocess
program=r'''
import json,pathlib,subprocess
assert subprocess.check_output(['hostname'],text=True).strip()=='haroldo'
out=[]
for vmid in (102,103,104):
    pid=int(pathlib.Path(f'/run/qemu-server/{vmid}.pid').read_text())
    status=pathlib.Path(f'/proc/{pid}/status').read_text().splitlines()
    mem={s.split(':')[0]:int(s.split()[1])*1024 for s in status if s.startswith(('VmRSS:','VmSwap:','RssAnon:','RssFile:','RssShmem:'))}
    out.append(dict(vmid=vmid,pid=pid,**mem))
print(json.dumps(out))
'''
result=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=yes','root@192.168.0.99','python3','-'],input=program.encode(),capture_output=True,timeout=30)
result.check_returncode()
data={'captured_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'vms':json.loads(result.stdout)}
stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
path=pathlib.Path(__file__).resolve().parent/f'proxmox-vm-resident-{stamp}.json'
with path.open('x',encoding='utf-8') as f: json.dump(data,f,indent=2)
print(json.dumps(data,indent=2))
print('Evidence: '+str(path))
