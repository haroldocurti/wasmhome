import datetime,json,pathlib,subprocess
program='''import json,pathlib,subprocess
assert subprocess.check_output(['hostname'],text=True).strip()=='haroldo'
m={line.split(':')[0]:int(line.split()[1])*1024 for line in pathlib.Path('/proc/meminfo').read_text().splitlines()}
print(json.dumps(dict(hostname='haroldo',mem_available_bytes=m['MemAvailable'],swap_used_bytes=m['SwapTotal']-m['SwapFree'],ct110_config_exists=pathlib.Path('/etc/pve/lxc/110.conf').exists())))
'''
command=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=yes','root@192.168.0.99','python3','-']
result=subprocess.run(command,input=program.encode(),capture_output=True,timeout=25)
root=pathlib.Path(__file__).resolve().parent
with (root/'proxmox-donor-final-stderr-r2.log').open('xb') as output: output.write(result.stderr)
result.check_returncode()
data=json.loads(result.stdout)
data['captured_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
with (root/'proxmox-donor-final-resources.json').open('x') as output: json.dump(data,output,indent=2)
print(json.dumps(dict(mem_available_gib=round(data['mem_available_bytes']/2**30,2),swap_used_gib=round(data['swap_used_bytes']/2**30,2),ct110_exists=data['ct110_config_exists'])))
