import hashlib,json,pathlib,subprocess
root=pathlib.Path(__file__).resolve().parent/'native-final-ct107'
elf=root/'elf-r4/wasmhome_bench.elf'
tool=pathlib.Path('C:/esp/tools-task8/tools/xtensa-esp-elf/esp-13.2.0_20250707/xtensa-esp-elf/bin/xtensa-esp32s3-elf-objdump.exe')
assert hashlib.sha256(elf.read_bytes()).hexdigest()=='3ffca7b09a450e0ac63f5e0d1ea67fb3d672eb55684ad9b6d7953898f90df500'
result=subprocess.run([str(tool),'-d','--disassemble=benchmark_run',str(elf)],check=True,capture_output=True,timeout=30)
text=result.stdout.decode().replace('\r\n','\n')
remote=(root/'native-benchmark-disassembly.txt').read_text().replace('\r\n','\n')
assert text.split('<benchmark_run>:',1)[1].split()==remote.split('<benchmark_run>:',1)[1].split()
assert '0x7d0' in text and 'loop\t' in text and 'mull\t' in text and 'xor\t' in text
with (root/'elf-r4/native-local-disassembly.txt').open('x',newline='\n') as output: output.write(text)
receipt=dict(elf_bytes=elf.stat().st_size,elf_sha256=hashlib.sha256(elf.read_bytes()).hexdigest(),
    objdump_path=str(tool),objdump_sha256=hashlib.sha256(tool.read_bytes()).hexdigest(),
    objdump_version=subprocess.check_output([str(tool),'--version'],text=True,timeout=30),
    workload_disassembly_matches_remote=True,comparison='all addresses/opcode/instruction/operand tokens; ignore tool column padding',loop_iterations=2000,pending=['physical checksum','wamrc and B/C builds'])
with (root/'elf-r4/root-local-elf-check.json').open('x') as output: json.dump(receipt,output,indent=2)
print(json.dumps(dict(elf_bytes=receipt['elf_bytes'],sha256=receipt['elf_sha256'],workload_disassembly_matches_remote=True)))
