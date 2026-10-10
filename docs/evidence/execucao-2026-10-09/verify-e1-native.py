#!/usr/bin/env python3
"""Offline verification of the preparatory native E1 capture, 2026-10-09."""
import argparse
import csv
import hashlib
import io
import json
import pathlib
import re
import sys

SESSION = pathlib.Path(__file__).resolve().parent
ROOT = SESSION.parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from collect_results import FIELDS, PREFIX, parse_log

EXPECTED_BIN = '7bdbf68d9827d52ff524d9e8c246ac7a4caab0af8a1af196ab09bfb7706e731a'
EXPECTED_ELF = '35ed6729ac02653ab36a296131a64636a86b775662bf0f61c80192d776305b9a'
EXPECTED_CONFIG = 'aab9531c01984b9961721b1517c9bbb9e9dcc972da418700b639be61d55906c6'
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
START = 'E1 preliminary calls: scenario=native warmup=5 measured=30; one load, not independent updates'
FINAL = re.compile(r'E1 scenario=native status=ok heap_before_runtime=(\d+) heap_after_release=(\d+)')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(text):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f'duplicate JSON key: {key}')
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=unique_object)


def verify_serial(text):
    lines = ANSI.sub('', text).splitlines()
    boots = []
    events = []
    for index, line in enumerate(lines):
        if 'WASMHOME_EVENT,' in line:
            event = load_json(line.split('WASMHOME_EVENT,', 1)[1])
            events.append(event)
            if event.get('event') == 'boot_observed':
                boots.append((index, event))
    require(sum(line.startswith('ESP-ROM:esp32s3') for line in lines) == 1, 'expected one ESP32-S3 ROM boot')
    require(sum(line.startswith('rst:') for line in lines) == 1, 'expected one reset header')
    require(len(boots) == 1 and boots[0][1].get('scenario') == 'native', 'expected one native boot event')
    boot_id = boots[0][1].get('boot_id', '')
    require(re.fullmatch(r'[0-9a-f]{16}', boot_id), 'invalid boot ID')
    require(all(event.get('boot_id') == boot_id and event.get('scenario') == 'native' for event in events), 'mixed boots/scenarios')
    require(not any('selftest' in event.get('event', '') for event in events), 'diagnostic firmware is not this native capture')
    starts = [index for index, line in enumerate(lines) if START in line]
    finals = [(index, FINAL.search(line)) for index, line in enumerate(lines) if 'E1 scenario=' in line]
    require(len(starts) == 1, 'expected one E1 startup declaring five completed warmups')
    require(len(finals) == 1 and finals[0][1] is not None, 'expected one successful native E1 final status')
    csv_lines = [(index, line) for index, line in enumerate(lines) if PREFIX in line]
    require(all(line.count(PREFIX) == 1 for _, line in csv_lines), 'multiple CSV markers on one line')
    headers = [index for index, line in csv_lines if line.split(PREFIX, 1)[1].strip() == ','.join(FIELDS)]
    require(len(headers) == 1 and len(csv_lines) == 31, 'expected one header and exactly 30 CSV records')
    require(boots[0][0] < starts[0] < headers[0] < csv_lines[-1][0] < finals[0][0], 'E1 events/records out of order')
    rows = parse_log(lines)
    require(len(rows) == 30, 'expected exactly 30 measured calls')
    require([row['repetition'] for row in rows] == [str(number) for number in range(1, 31)], 'CSV repetitions must be ordered 1..30')
    for row in rows:
        require(row['scenario'] == 'native' and row['status'] == 'ok', 'invalid/non-native CSV result')
        require(re.fullmatch(r'0x[0-9A-Fa-f]{8}', row['checksum']) and int(row['checksum'], 16) == 0xDBB2F0D4, 'checksum differs from workload reference')
        for field in FIELDS[2:9]:
            require(re.fullmatch(r'\d+', row[field]), f'invalid nonnegative metric: {field}')
        require(row['load_us'] == '0' and row['instantiate_us'] == '0', 'native CSV unexpectedly reports WAMR lifecycle')
    elf = [re.search(r'ELF file SHA256:\s*([0-9a-f]+)(?:\.\.\.)?', line) for line in lines if 'ELF file SHA256:' in line]
    require(len(elf) == 1 and elf[0] is not None and len(elf[0][1]) >= 8 and EXPECTED_ELF.startswith(elf[0][1]), 'serial ELF identity differs from approved build')
    return rows, dict(boot_id=boot_id, reset_reason=boots[0][1].get('status'),
                     serial_elf_sha256_prefix=elf[0][1], measured_calls=30,
                     checksum='0xDBB2F0D4', warmup_runs_declared_completed=5,
                     warmup_observation='startup is emitted after five successful checks; individual warmups are not serial records',
                     independent_updates=0, runner_sessions=1,
                     lifecycle_note='one runner session; native WAMR load/instantiate are not applicable and appear as zero in the preliminary CSV',
                     heap_before_runtime=int(finals[0][1][1]), heap_after_release=int(finals[0][1][2]))


def prepare(build, output, artifact):
    require(not output.exists() and not output.is_symlink(), 'build metadata output must be a new directory')
    report_path = build / 'e1-build.json'
    report = load_json(report_path.read_text())
    allowed = {'schema', 'scenario', 'status', 'elf_sha256', 'effective_config_sha256', 'compile_commands_sha256', 'pending', 'runtime_resolved', 'workload_optimization'}
    require(set(report) <= allowed, 'unexpected build report fields; review before publishing')
    require(report.get('scenario') == 'native' and report.get('workload_optimization') == '-O3', 'unexpected build scenario/optimization')
    require(report.get('elf_sha256') == EXPECTED_ELF and sha(build / 'wasmhome_bench.elf') == EXPECTED_ELF, 'ELF changed')
    require(report.get('effective_config_sha256') == EXPECTED_CONFIG and sha(build / 'config/sdkconfig.json') == EXPECTED_CONFIG, 'effective configuration changed')
    require(sha(artifact) == EXPECTED_BIN, 'application artifact differs from approved image')
    require(sha(build / 'compile_commands.json') == report.get('compile_commands_sha256'), 'compile commands changed')
    entries = load_json((build / 'compile_commands.json').read_text())
    benchmark = [entry for entry in entries if pathlib.Path(entry['file']).resolve() == (ROOT / 'workload/benchmark.c').resolve()]
    require(len(benchmark) == 1, 'benchmark compile command missing/ambiguous')
    command = json.dumps(benchmark[0], indent=2) + '\n'
    require(not re.search(r'(?i)(?:password|secret|token|ssid)\s*[=:]', command), 'compile command needs sanitization review')
    payloads = {'e1-build.json': (json.dumps(report, indent=2) + '\n').encode('utf-8'),
                'benchmark-compile-command.json': command.encode('utf-8'),
                'benchmark.disassembly.txt': (build / 'benchmark.disassembly.txt').read_bytes()}
    # Keep explicit build result lines only, never sdkconfig values or full logs.
    build_log = (build / 'build.log').read_text(encoding='utf-8-sig')
    require('Project build complete.' in build_log, 'build completion missing')
    excerpts = [line.strip() for line in build_log.splitlines() if line.strip() == 'Project build complete.'
                or re.fullmatch(r'wasmhome_bench\.bin binary size 0x[0-9a-f]+ bytes\. Smallest app partition is 0x[0-9a-f]+ bytes\. 0x[0-9a-f]+ bytes \(\d+%\) free\.', line.strip())]
    payloads['build-result-excerpt.txt'] = ('\n'.join(excerpts) + '\n').encode('utf-8')
    files = {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()}
    inventory = dict(schema='wasmhome-native-e1-evidence-v1', scope='preparatory_local_A_only',
                     app_bin_sha256=EXPECTED_BIN, app_bin_bytes=artifact.stat().st_size,
                     elf_sha256=EXPECTED_ELF, effective_config_sha256=EXPECTED_CONFIG,
                     original_build_report_sha256=sha(report_path), files_sha256=files)
    payloads['build-inventory.json'] = (json.dumps(inventory, indent=2) + '\n').encode('utf-8')
    # Complete every input check before creating any output; old evidence is never a destination.
    output.mkdir(parents=True, exist_ok=False)
    for name, data in payloads.items():
        with (output / name).open('xb') as stream:
            stream.write(data)
    return inventory


def verify(native, artifact):
    inventory = load_json((native / 'build-inventory.json').read_text())
    require(inventory.get('schema') == 'wasmhome-native-e1-evidence-v1', 'invalid evidence inventory')
    require(sha(artifact) == inventory.get('app_bin_sha256') == EXPECTED_BIN, 'application hash mismatch')
    require(inventory.get('elf_sha256') == EXPECTED_ELF and inventory.get('effective_config_sha256') == EXPECTED_CONFIG, 'build identity mismatch')
    for name in ('e1-build.json', 'benchmark-compile-command.json', 'benchmark.disassembly.txt', 'build-result-excerpt.txt'):
        require(sha(native / name) == inventory.get('files_sha256', {}).get(name), f'preserved build evidence changed: {name}')
    report = load_json((native / 'e1-build.json').read_text())
    require(report.get('elf_sha256') == EXPECTED_ELF and report.get('effective_config_sha256') == EXPECTED_CONFIG and report.get('workload_optimization') == '-O3', 'preserved build report differs')
    flash_path = SESSION / 'native-app-flash.txt'
    flash = flash_path.read_text(encoding='utf-8-sig')
    require('Hash of data verified.' in flash and re.search(rf'Wrote {inventory["app_bin_bytes"]} bytes .* at 0x00020000', flash), 'application-only transfer evidence missing')
    raw = native / 'serial.log'
    rows, result = verify_serial(raw.read_text(encoding='utf-8'))
    generic = load_json((native / 'summary.json').read_text())
    require(generic.get('boot_ids') == [result['boot_id']], 'capture summary boot differs from raw serial')
    result.update(schema='wasmhome-native-e1-physical-v1', status='preparatory_local_A_checksum_pass',
                  scope='local native computation; not three-mode E1, functional transport or campaign',
                  app_bin_sha256=EXPECTED_BIN, elf_sha256=EXPECTED_ELF, effective_config_sha256=EXPECTED_CONFIG,
                  serial_sha256=sha(raw), flash_log_sha256=sha(flash_path),
                  build_inventory_sha256=sha(native / 'build-inventory.json'),
                  preserved_build_report_sha256=sha(native / 'e1-build.json'),
                  capture_summary_sha256=sha(native / 'summary.json'),
                  transport_status='unvalidated_connection_failed', udp_packets=generic.get('udp_packets'),
                  wifi_disconnect_reasons=generic.get('wifi_disconnect_reasons'), instrument_errors=generic.get('error_counts'),
                  pending=['LXC/toolchain issue #7 acceptance', 'Wasm classic and AOT device checks', 'functional transport for this firmware', 'complete comparative campaign'])
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    csv_data = stream.getvalue().encode('utf-8')
    result['derived_csv_sha256'] = hashlib.sha256(csv_data).hexdigest()
    return result, csv_data


def check_derivation(native, result, csv_data):
    require((native / 'derived-native.csv').read_bytes() == csv_data, 'preserved CSV differs from verified raw records')
    saved = load_json((native / 'derived-native.json').read_text(encoding='utf-8'))
    require(saved == result, 'preserved JSON differs from verified evidence')


def write_derivation(output, result, csv_data):
    # A failed check never touches old evidence. Explicit output must be fresh.
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'derived-native.csv').open('xb') as stream:
        stream.write(csv_data)
    with (output / 'derived-native.json').open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(result, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', action='store_true', help='preserve reviewed build identity and safe excerpts first')
    parser.add_argument('--prepare-output', type=pathlib.Path, help='required with --prepare; write build metadata only to a new directory')
    parser.add_argument('--build-dir', type=pathlib.Path, default=ROOT / 'build-e1-native')
    parser.add_argument('--artifact', type=pathlib.Path, default=ROOT / 'build-e1-native/wasmhome_bench.bin')
    parser.add_argument('--derive-output', type=pathlib.Path, help='write derived files only to a new directory after verification; default is read-only')
    args = parser.parse_args()
    if bool(args.prepare) != bool(args.prepare_output):
        parser.error('--prepare requires --prepare-output NEW_DIRECTORY; physical verification is a separate command')
    native = SESSION / 'native'
    try:
        if args.prepare:
            inventory = prepare(args.build_dir, args.prepare_output, args.artifact)
            print(json.dumps(dict(scope='build metadata only; physical capture not assessed', inventory=inventory), indent=2))
            return
        result, csv_data = verify(native, args.artifact)
        if args.derive_output:
            write_derivation(args.derive_output, result, csv_data)
        else:
            check_derivation(native, result, csv_data)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f'Native E1 evidence rejected: {error}\n')


if __name__ == '__main__':
    main()
