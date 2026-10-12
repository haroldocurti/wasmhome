import hashlib,json,pathlib,tarfile
root=pathlib.Path(__file__).resolve().parent/'native-final-ct107'
receipt=json.loads((root/'native-ct107-collection.json').read_bytes())
with tarfile.open(root/'native-managed-components.tar.gz') as archive:
    names=archive.getnames()
    assert len(names)==len(set(names))
    members={name:archive.extractfile(name).read() for name in names}
    for item in receipt['managed_components']:
        data=members[item['path']]
        assert len(data)==item['bytes'] and hashlib.sha256(data).hexdigest()==item['sha256']
    prefix='managed_components/espressif__onewire_bus/'
    checks=json.loads(members[prefix+'CHECKSUMS.json'])
    assert checks['algorithm']=='sha256'
    assert members[prefix+'.component_hash'].decode()=='c4c781940d8bd988432b47ac3ee68479c69e14e01fdfba0b61365b233a3ba5d6'
    checked=[]
    for item in checks['files']:
        name=prefix+item['path']; data=members[name]
        assert len(data)==item['size'] and hashlib.sha256(data).hexdigest()==item['hash']
        checked.append(name)
    assert len(checked)==len(set(checked))
    assert set(checked)==set(names)-{'dependencies.lock',prefix+'.component_hash',prefix+'CHECKSUMS.json'}
assert hashlib.sha256(members['dependencies.lock']).hexdigest()==receipt['lock_sha256']
commands=json.loads((root/'metadata-r3/compile_commands.json').read_bytes())
workload=[item for item in commands if item['file'].endswith('/workload/benchmark.c')]
assert len(workload)==1 and '-O3' in workload[0]['command'].split()
managed=[item['file'] for item in commands if '/managed_components/' in item['file']]
assert managed and all('/espressif__onewire_bus/' in name for name in managed)
assert all(name.split('/project/')[-1] in members for name in managed)
disassembly=(root/'native-benchmark-disassembly.txt').read_text()
assert '<benchmark_run>' in disassembly and 'loop\t' in disassembly and 'mull\t' in disassembly and 'xor\t' in disassembly
assert receipt['python_package']=='3.13.5-2' and receipt['python_sha256']=='e59d0124ff06c248546876e01fcfb1ea3cda63534949f94a9372bfcfe3bfc3f5'
result=dict(schema='wasmhome-native-collection-check-v1',managed_files=len(receipt['managed_components']),registry_file_hashes=len(checked),managed_compiled_sources=managed,native_workload_compile_command=workload[0],disassembly='actual 2000-iteration loop with arithmetic retained',pending=['independent review','physical checksum','wamrc and B/C production'])
with (root/'root-collection-check.json').open('x') as output: json.dump(result,output,indent=2)
print(json.dumps(dict(managed_files=len(receipt['managed_components']),registry_files=len(checked),compiled_managed_sources=len(managed),workload_o3=True)))
