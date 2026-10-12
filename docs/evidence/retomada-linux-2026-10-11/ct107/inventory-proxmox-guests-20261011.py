import datetime
import json
import pathlib
import subprocess

program = r'''
import json,pathlib,subprocess
assert subprocess.check_output(['hostname'],text=True).strip()=='haroldo'
resources=json.loads(subprocess.check_output(['pvesh','get','/cluster/resources','--type','vm','--output-format','json'],text=True))
guests=[]
for r in sorted(resources,key=lambda x:int(x['vmid'])):
    item={k:r.get(k) for k in ('vmid','name','type','node','status','mem','maxmem','maxcpu')}
    if r['type']=='lxc':
        cfg=json.loads(subprocess.check_output(['pvesh','get',f"/nodes/{r['node']}/lxc/{r['vmid']}/config",'--output-format','json'],text=True))
        item['configured_swap_mib']=cfg.get('swap')
        cg=pathlib.Path(f"/sys/fs/cgroup/lxc/{r['vmid']}")
        for name in ('memory.current','memory.swap.current'):
            path=cg/name
            item[name]=int(path.read_text().strip()) if path.exists() else None
    guests.append(item)
m={line.split(':')[0]:int(line.split()[1])*1024 for line in pathlib.Path('/proc/meminfo').read_text().splitlines()}
print(json.dumps({'host':'haroldo','mem_total_bytes':m['MemTotal'],'mem_available_bytes':m['MemAvailable'],'swap_used_bytes':m['SwapTotal']-m['SwapFree'],'guests':guests}))
'''
result = subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=yes','root@192.168.0.99','python3','-'],input=program.encode(),capture_output=True,timeout=55)
root=pathlib.Path(__file__).resolve().parent
stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
with (root/f'proxmox-inventory-{stamp}.stderr.log').open('xb') as output:
    output.write(result.stderr)
result.check_returncode()
data=json.loads(result.stdout)
data['captured_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
path=root/f'proxmox-inventory-{stamp}.json'
with path.open('x',encoding='utf-8') as output:
    json.dump(data,output,indent=2)
print(json.dumps(data,indent=2))
print('Evidence: '+str(path))
