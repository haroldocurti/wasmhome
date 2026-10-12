import pathlib,subprocess,os,json,hashlib
root=pathlib.Path('/home/wasmhome/.local/toolchains/wasmhome-e1-20261010-187ff962')
assert os.getuid()==1000 and root.resolve()==root
home=root/'runtime-home'
home.mkdir(mode=0o700)
env={'PATH':str(root/'idf526-py313/bin')+':/usr/bin:/bin','HOME':str(home),'LANG':'C.UTF-8','DOTNET_CLI_TELEMETRY_OPTOUT':'1'}
paths=[root/'wasi-sdk/wasi-sdk-27.0-x86_64-linux/bin/clang',root/'powershell-7.6.6/pwsh',root/'powershell-7.6.6/libcoreclr.so',root/'powershell-7.6.6/libSystem.Security.Cryptography.Native.OpenSsl.so']
for p in paths:
 r=subprocess.run(['ldd',str(p)],capture_output=True,text=True,env=env)
 print('LDD',p.name,r.stdout,r.stderr,flush=True)
 assert 'not found' not in r.stdout+r.stderr
clang=subprocess.check_output([str(paths[0]),'--version'],text=True,env=env)
print(clang,flush=True)
command="$ErrorActionPreference='Stop'; $data='{\"key\":1}' | ConvertFrom-Json -AsHashtable; [ordered]@{version=$PSVersionTable.PSVersion.ToString();jsonValue=$data['key'];python=(& python --version);hash=(Get-FileHash -Algorithm SHA256 -LiteralPath '/home/wasmhome/.local/toolchains/wasmhome-e1-20261010-187ff962/idf-core-frozen.lock').Hash.ToLower()} | ConvertTo-Json -Compress"
result=subprocess.check_output([str(root/'powershell-7.6.6/pwsh'),'-NoProfile','-NonInteractive','-Command',command],text=True,env=env)
print(result,flush=True)
data=json.loads(result)
assert data['version']=='7.6.6' and data['jsonValue']==1 and '3.13.5' in data['python']
assert data['hash']==hashlib.sha256((root/'idf-core-frozen.lock').read_bytes()).hexdigest()
with (root/'sdk-pwsh-validation.json').open('x') as out: json.dump({'clang':clang,'pwsh':data,'pending':['generator with real wamrc','IDF configure/build']},out,indent=2)
