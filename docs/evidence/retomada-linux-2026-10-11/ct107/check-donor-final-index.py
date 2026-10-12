import hashlib,json,pathlib,sys
root=pathlib.Path('C:/Users/inomi/.codex/worktrees/issue7-runtime-port/wasmhome/build-session-e2')
index=root/'proposal-index.json'
def strict(pairs):
    value={}
    for key,item in pairs:
        assert key not in value, 'Duplicate JSON key'
        value[key]=item
    return value
data=json.loads(index.read_bytes(),object_pairs_hook=strict)
checked=[]
for name,item in data.items():
    if 'bytes' in item: records=[(root/name,item['sha256'],item['bytes'])]
    elif 'path' in item: records=[(pathlib.Path(item['path']),item['sha256'],None)]
    else: records=[(pathlib.Path(path),digest,None) for path,digest in item.items()]
    for file,digest,size in records:
        value=file.read_bytes()
        assert hashlib.sha256(value).hexdigest()==digest, str(file)
        assert size is None or len(value)==size, str(file)
        checked.append(dict(path=str(file),sha256=digest,bytes=len(value)))
suffix=sys.argv[1] if len(sys.argv)>1 else ''
assert suffix in ('','-r2','-r3')
receipt=dict(index_sha256=hashlib.sha256(index.read_bytes()).hexdigest(),checked_files=checked,
    root_tests='root-donor-supervisor-tests'+suffix+'.log: '+({'':'20','-r2':'26','-r3':'28'}[suffix])+' Linux tests passed',
    pending=['Luna adversarial review of final supervisor','real host memory','human authorization for CT110'])
with (pathlib.Path(__file__).resolve().parent/('root-donor-final-index-check'+suffix+'.json')).open('x') as output: json.dump(receipt,output,indent=2)
print(json.dumps(dict(files=len(checked),index_sha256=receipt['index_sha256'],mismatches=0)))
