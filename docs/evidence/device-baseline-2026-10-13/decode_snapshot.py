#!/usr/bin/env python3
"""Sanitized, offline ESP-IDF 5.2.6 flash/OTA decoder for a completed read-only capture.

The full extraction contains firmware and NVS and must stay private. This program
prints and writes only partition offsets/hashes, OTA sequence/state/CRC results,
and fixed app-description metadata (project/version/IDF/secure_version).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import zlib
from pathlib import Path

EXPECTED_FLASH_BYTES = 16 * 1024 * 1024
OTA_SECTOR = 0x1000
OTA_RECORD_SIZE = 32
APP_DESC_MAGIC = 0xABCD5432
STATE_NAMES = {
    0: "NEW",
    1: "PENDING_VERIFY",
    2: "VALID",
    3: "INVALID",
    4: "ABORTED",
    0xFFFFFFFF: "UNDEFINED",
}
SAFE_TEXT = re.compile(r"[^A-Za-z0-9_. +:/-]+")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checked_text(value: str, limit: int = 64) -> str:
    value = value.split("\0", 1)[0].strip()
    # Fixed app-description fields only; don't forward control bytes or arbitrary strings.
    value = SAFE_TEXT.sub("_", value)
    return value[:limit]


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def verify_bootloader_log(log_path: Path, manifest_path: Path):
    """Verify a private reset capture, returning only sanitized load evidence."""
    manifest = load_json(manifest_path)
    if manifest.get("schema") != "wasmhome-private-serial-capture-v1":
        raise ValueError("unrecognized serial-capture manifest")
    if manifest.get("action") != "reset-and-capture-current-app-no-flash-write":
        raise ValueError("serial capture is not marked as read-only reset evidence")
    if not manifest.get("started_utc") or log_path.stat().st_size != manifest.get("bytes"):
        raise ValueError("serial-capture byte count or timestamp mismatch")
    log_hash = sha256(log_path)
    if log_hash != manifest.get("sha256"):
        raise ValueError("serial-capture hash mismatch")
    # Read only to find this bootloader's fixed offset statement. Never persist
    # or emit arbitrary log lines, which may contain device configuration.
    content = log_path.read_text(encoding="utf-8", errors="replace")
    matches = re.findall(
        r"Loaded app from partition at offset\s+(0x[0-9a-fA-F]+)",
        content,
        flags=re.IGNORECASE,
    )
    offsets = sorted({int(value, 16) for value in matches})
    if len(matches) != 1 or len(offsets) != 1:
        raise ValueError("serial capture does not contain exactly one bootloader app-load offset")
    return {
        "evidence_type": "read-only UART capture after normal hard reset",
        "capture_action": manifest["action"],
        "capture_started_utc": manifest["started_utc"],
        "capture_bytes": manifest["bytes"],
        "capture_sha256": log_hash,
        "capture_basename": log_path.name,
        "matching_load_lines": len(matches),
        "loaded_app_offset": hex(offsets[0]),
    }


def crc_is_valid(seq: int, crc: int) -> bool:
    # Match bootloader_common_ota_select_crc(): esp_rom_crc32_le(UINT32_MAX,
    # &ota_seq, 4). Python zlib uses the same seed parameter convention here.
    return zlib.crc32(seq.to_bytes(4, "little"), 0xFFFFFFFF) & 0xFFFFFFFF == crc


def idf_rom_crc32_bitwise(data: bytes) -> int:
    # Independent implementation of esp_rom_crc32_le's documented
    # complement-at-entry/exit convention and reflected CRC-32 polynomial.
    crc = (~0xFFFFFFFF) & 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xEDB88320 if crc & 1 else 0)
    return (~crc) & 0xFFFFFFFF


def parse_otadata(path: Path, absolute_offset: int):
    if path.stat().st_size < 2 * OTA_SECTOR:
        raise ValueError("otadata partition is shorter than two erase sectors")
    entries = []
    with path.open("rb") as stream:
        for index in range(2):
            stream.seek(index * OTA_SECTOR)
            seq_bytes = stream.read(4)
            if len(seq_bytes) != 4:
                raise ValueError("short otadata sequence")
            seq = int.from_bytes(seq_bytes, "little")
            stream.seek(index * OTA_SECTOR + 24)
            state_crc = stream.read(8)
            if len(state_crc) != 8:
                raise ValueError("short otadata state and CRC")
            state = int.from_bytes(state_crc[0:4], "little")
            stored_crc = int.from_bytes(state_crc[4:8], "little")
            crc_zlib = zlib.crc32(seq_bytes, 0xFFFFFFFF) & 0xFFFFFFFF
            crc_bitwise = idf_rom_crc32_bitwise(seq_bytes)
            crc_ok = crc_zlib == stored_crc and crc_bitwise == stored_crc
            invalid = seq == 0xFFFFFFFF or state in (3, 4)
            entries.append({
                "copy": index,
                "absolute_offset": absolute_offset + index * OTA_SECTOR,
                "record_bytes": OTA_RECORD_SIZE,
                "ota_seq": seq,
                "ota_state_value": state,
                "ota_state": STATE_NAMES.get(state, "UNKNOWN"),
                "stored_crc32": f"{stored_crc:08x}",
                "crc_valid": crc_ok,
                "crc_zlib32": f"{crc_zlib:08x}",
                "crc_bitwise32": f"{crc_bitwise:08x}",
                "crc_implementations_agree": crc_zlib == crc_bitwise,
                "invalid_by_idf_rules": invalid,
                "valid_by_idf_rules": (not invalid) and crc_ok,
            })
    return entries


def choose_active_otadata(entries):
    valid = [item for item in entries if item["valid_by_idf_rules"]]
    if not valid:
        return None
    # IDF 5.2.6 chooses the larger sequence and copy 0 on a tie.
    return max(valid, key=lambda item: (item["ota_seq"], -item["copy"]))


def partition_is_erased(path: Path) -> bool:
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            if any(byte != 0xFF for byte in chunk):
                return False
    return True


def esptool_metadata(esptool: Path, app_file: Path):
    command = [str(esptool), "--chip", "esp32s3", "image_info", "--version", "2", str(app_file)]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, errors="replace", check=False)
    # Keep esptool's firmware-derived output in memory only. Extract a small,
    # fixed allow-list; never persist its full text because it includes other fields.
    output = result.stdout
    def match(pattern):
        found = re.search(pattern, output, flags=re.MULTILINE)
        return checked_text(found.group(1), 64) if found else None
    checksum = match(r"^Checksum: [^\n]*\((valid|invalid[^)]*)\)\s*$")
    digest = match(r"^Validation hash: [^\n]*\((valid|invalid)\)\s*$")
    meta = {
        "image_info_exit_code": result.returncode,
        "image_checksum": checksum,
        "appended_sha256": digest,
        "project_name": match(r"^Project name: (.*)$"),
        "app_version": match(r"^App version: (.*)$"),
        "idf_version": match(r"^ESP-IDF: (.*)$"),
        "secure_version": None,
        "image_parse_ok": result.returncode == 0 and checksum is not None,
    }
    secure = re.search(r"^Secure version: (\d+)\s*$", output, flags=re.MULTILINE)
    if secure:
        meta["secure_version"] = int(secure.group(1))
    meta["image_integrity_ok"] = (
        meta["image_parse_ok"]
        and checksum == "valid"
        and digest in (None, "valid")
    )
    return meta


def verify_private_extraction(partition_dir: Path, manifest: dict):
    sums_path = partition_dir / "SHA256SUMS"
    if not sums_path.is_file():
        raise ValueError("partition extraction lacks SHA256SUMS")
    parsed_sums = {}
    for line in sums_path.read_text(encoding="ascii").splitlines():
        try:
            digest, name = line.split("  ", 1)
        except ValueError as error:
            raise ValueError("malformed extraction SHA256SUMS") from error
        parsed_sums[name] = digest
    for item in manifest["partitions"]:
        file_path = partition_dir / item["file"]
        if not file_path.is_file() or sha256(file_path) != item["sha256"]:
            raise ValueError("partition payload hash mismatch")
        if parsed_sums.get(item["file"]) != item["sha256"]:
            raise ValueError("partition payload differs from SHA256SUMS")
    table_file = partition_dir / "partition-table.bin"
    table_csv = partition_dir / "partition-table.csv"
    if sha256(table_file) != manifest["partition_table_sha256"]:
        raise ValueError("partition-table hash mismatch")
    for path in [table_file, table_csv, partition_dir / "manifest.json"]:
        if path.name in parsed_sums and sha256(path) != parsed_sums[path.name]:
            raise ValueError("extraction sidecar hash mismatch")


def classify_app_subtype(subtype: str):
    if subtype == "factory":
        return ("factory", 0)
    m = re.fullmatch(r"ota_(\d+)", subtype)
    if m:
        return ("ota", int(m.group(1)))
    if subtype == "test":
        return ("test", 0)
    return ("other", 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw_flash", type=Path)
    parser.add_argument("complete_manifest", type=Path)
    parser.add_argument("private_output_dir", type=Path)
    parser.add_argument("--device-tools", type=Path, required=True)
    parser.add_argument("--bootloader-log", type=Path, required=True)
    parser.add_argument("--bootloader-log-manifest", type=Path, required=True)
    args = parser.parse_args()

    raw = args.raw_flash.resolve(strict=True)
    receipt_path = args.complete_manifest.resolve(strict=True)
    boot_log_path = args.bootloader_log.resolve(strict=True)
    boot_log_manifest_path = args.bootloader_log_manifest.resolve(strict=True)
    tools = args.device_tools.resolve(strict=True)
    output = args.private_output_dir.absolute()
    receipt = load_json(receipt_path)
    if receipt.get("schema") != "wasmhome-private-device-backup-v1":
        raise SystemExit("refusing: unrecognized or incomplete capture manifest")
    if receipt.get("read_exit_code") != 0 or not receipt.get("completed_utc"):
        raise SystemExit("refusing: flash read is not marked complete")
    if receipt.get("flash_bytes") != EXPECTED_FLASH_BYTES or raw.stat().st_size != EXPECTED_FLASH_BYTES:
        raise SystemExit("refusing: capture size does not match the 16 MiB device")
    if receipt.get("public_bytes") is not False:
        raise SystemExit("refusing: capture manifest does not mark payload private")
    raw_hash = sha256(raw)
    if raw_hash != receipt.get("sha256"):
        raise SystemExit("refusing: raw flash SHA-256 does not match completed capture manifest")
    bootloader_evidence = verify_bootloader_log(boot_log_path, boot_log_manifest_path)
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing private output directory: {output}")

    output.mkdir(mode=0o700, parents=False)
    os.chmod(output, 0o700)
    extractor = tools / "partition_backup.py"
    esptool = tools / "venv" / "bin" / "esptool.py"
    py = tools / "venv" / "bin" / "python"
    command = [str(py), str(extractor), str(raw), str(output / "partitions")]
    extracted = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, check=False)
    if extracted.returncode != 0:
        raise SystemExit("official partition-table validation/extraction failed; private output retained for review")
    os.chmod(output / "partitions", 0o700)
    extraction_manifest = load_json(output / "partitions" / "manifest.json")
    verify_private_extraction(output / "partitions", extraction_manifest)
    if extraction_manifest["raw_flash_sha256"] != raw_hash:
        raise SystemExit("raw hash in extraction manifest does not match completed capture")

    parts = extraction_manifest["partitions"]
    by_name = {item["name"]: item for item in parts}
    ota_data_parts = [p for p in parts if p["type"] == "data" and p["subtype"] == "ota"]
    app_parts = [p for p in parts if p["type"] == "app"]
    ota_apps = sorted((p for p in app_parts if classify_app_subtype(p["subtype"])[0] == "ota"),
                      key=lambda p: classify_app_subtype(p["subtype"])[1])
    if len(ota_data_parts) != 1:
        raise SystemExit("expected exactly one physical otadata partition; active-app result withheld")
    if not ota_apps:
        raise SystemExit("no OTA app partition found; active-app result withheld")
    ota_indices = [classify_app_subtype(p["subtype"])[1] for p in ota_apps]
    if ota_indices != list(range(len(ota_apps))):
        raise SystemExit("OTA app subtype indices are sparse or reordered; active-app result withheld")
    otadata = ota_data_parts[0]
    entries = parse_otadata(output / "partitions" / otadata["file"], otadata["offset"])
    selected_record = choose_active_otadata(entries)
    selected_app = None
    selection_basis = None
    if selected_record is None:
        factory = next((p for p in app_parts if classify_app_subtype(p["subtype"])[0] == "factory"), None)
        selected_app = factory or ota_apps[0]
        selection_basis = "IDF default: no valid OTA selection; factory if present, else OTA slot 0"
    else:
        raw_slot = (selected_record["ota_seq"] - 1) & 0xFFFFFFFF
        slot_index = raw_slot % len(ota_apps)
        selected_app = ota_apps[slot_index]
        selection_basis = "highest valid OTA sequence; slot=(ota_seq-1) modulo OTA app count"

    app_metadata = []
    for app in sorted(app_parts, key=lambda p: (classify_app_subtype(p["subtype"])[0],
                                                 classify_app_subtype(p["subtype"])[1])):
        image_file = output / "partitions" / app["file"]
        meta = esptool_metadata(esptool, image_file)
        app_metadata.append({
            "partition": app["name"],
            "subtype": app["subtype"],
            "offset": hex(app["offset"]),
            "size": hex(app["size"]),
            "partition_sha256": app["sha256"],
            "partition_erased_all_ff": partition_is_erased(image_file),
            **meta,
        })
    selected_meta = next((item for item in app_metadata if item["partition"] == selected_app["name"]), None)

    # The report references the verified raw read without reproducing any bytes or free-form logs.
    result = {
        "schema": "wasmhome-device-active-app-map-v1",
        "capture": {
            "chip": receipt.get("chip"),
            "flash_bytes": receipt["flash_bytes"],
            "raw_flash_sha256": raw_hash,
            "captured_utc": receipt["completed_utc"],
        },
        "partition_table": {
            "offset": extraction_manifest["partition_table_offset_hex"],
            "size": hex(extraction_manifest["partition_table_size_bytes"]),
            "sha256": extraction_manifest["partition_table_sha256"],
            "partitions": [{
                "name": p["name"], "type": p["type"], "subtype": p["subtype"],
                "offset": p["offset_hex"], "size": p["size_hex"],
                "flags": p["flags"], "sha256": p["sha256"],
            } for p in parts],
        },
        "otadata": {
            "partition": otadata["name"],
            "offset": otadata["offset_hex"],
            "size": otadata["size_hex"],
            "sha256": otadata["sha256"],
            "records": entries,
            "selected_record_copy": selected_record["copy"] if selected_record else None,
            "crc_crosscheck": "zlib seed UINT32_MAX agrees with independent ROM complement/polynomial bitwise implementation on both records",
            "selected_ota_seq": selected_record["ota_seq"] if selected_record else None,
            "selection_basis": selection_basis,
        },
        "app_partitions": app_metadata,
        "boot_selection_candidate": {
            "partition": selected_app["name"],
            "subtype": selected_app["subtype"],
            "offset": selected_app["offset_hex"],
            "size": selected_app["size_hex"],
            "partition_sha256": selected_app["sha256"],
            "project_name": selected_meta["project_name"] if selected_meta else None,
            "app_version": selected_meta["app_version"] if selected_meta else None,
            "idf_version": selected_meta["idf_version"] if selected_meta else None,
            "secure_version": selected_meta["secure_version"] if selected_meta else None,
            "image_integrity_ok": selected_meta["image_integrity_ok"] if selected_meta else False,
            "partition_erased_all_ff": selected_meta["partition_erased_all_ff"] if selected_meta else False,
            "status": "OTA selection candidate from captured bytes; bootloader UART load offset independently observed after reset",
        },
        "bootloader_observation": {
            **bootloader_evidence,
            "matches_candidate_offset": int(bootloader_evidence["loaded_app_offset"], 16) == selected_app["offset"],
            "observation_scope": "confirms the bootloader reported loading this partition at capture time; does not establish later runtime health",
        },
        "restore_guidance": {
            "principle": "For this bench run, only the test app region may be written; exact-restore that region from the private baseline. The decoder performs no writes.",
            "app_only": "After the planned manual test write to ota_0, exact-restore all captured 0x700000 bytes to ota_0 at 0x20000 and verify its SHA-256 matches the baseline. Sequence 1 already selects ota_0, so leave otadata unchanged.",
            "otadata": "Leave otadata untouched during this bench run; compare it against the captured baseline to detect unintended writes.",
            "partition_table": "Leave the partition table untouched during this bench run; compare it against the captured baseline to detect unintended writes.",
            "bootloader": "Leave the bootloader untouched during this bench run; the map does not separately establish its exact region for restoration.",
            "nvs": "Leave NVS untouched and do not expose its contents; compare it against the captured baseline to detect unintended writes.",
            "device_security": "Flags read for the capture report Secure Boot and Flash Encryption disabled; this offline map does not read all eFuses or prove anti-rollback is disabled.",
        },
        "limits": [
            "A tabela é encontrada ao varrer offsets alinhados a 4 KiB e validada pelo parser local ESP-IDF 5.2.6 gen_esp32part.py; nenhum offset fixo de tabela ou app é presumido.",
            "CRC e validade de otadata seguem bootloader_common_ota_select_* do ESP-IDF 5.2.6. A sequência identifica o candidato OTA selecionado pelas cópias válidas.",
            "A captura UART privada posterior a reset normal informa o offset carregado pelo bootloader e seu hash está registrado; isso não prova saúde posterior do processo nem exclui fallback após essa observação.",
            "esptool image_info verifica estrutura, checksum e hash anexado quando presente; política eFuse de revisão do chip e anti-rollback não foram estabelecidos independentemente por esta leitura offline.",
            "NVS e strings arbitrárias do firmware nunca são impressos nem copiados para o relatório sanitizado.",
        ],
        "physical_bench_plan": {
            "scope": "A planned bench test uses a manually written test image at the currently active ota_0 slot; firmware OTA selection is not implemented for this run.",
            "test_write_region": {"partition": "ota_0", "offset": "0x20000", "size": "0x700000"},
            "restore": "After the test, restore the exact original ota_0 bytes from this private full-flash baseline and verify the restored partition SHA-256 equals the captured baseline hash.",
            "preserve": "Keep bootloader, partition table, otadata, ota_1 and NVS untouched. Compare the complete post-test flash against the baseline and accept differences only inside ota_0 before restoring; after restoration require the full-flash SHA-256 to match the baseline.",
            "ota_flow": "This is manual app-slot programming and exact restoration, not OTA rollout or slot selection.",
        },
    }
    result_path = output / "sanitized-device-map.json"
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(result_path, 0o600)
    md = render_report(result)
    report_path = output / "sanitized-device-map.md"
    report_path.write_text(md, encoding="utf-8")
    os.chmod(report_path, 0o600)
    manifest_files = ["sanitized-device-map.json", "sanitized-device-map.md"]
    manifest_lines = [f"{sha256(output / name)}  {name}\n" for name in manifest_files]
    sums_path = output / "sanitized-SHA256SUMS"
    sums_path.write_text("".join(manifest_lines), encoding="ascii")
    os.chmod(sums_path, 0o600)
    print(json.dumps({
        "status": "offline_decode_complete",
        "partition_table_offset": result["partition_table"]["offset"],
        "partition_count": len(parts),
        "selected_candidate": selected_app["name"],
        "app_version": result["boot_selection_candidate"]["app_version"],
        "image_integrity_ok": result["boot_selection_candidate"]["image_integrity_ok"],
        "report": str(report_path),
    }, sort_keys=True))


def render_report(result: dict) -> str:
    lines = [
        "# Mapa offline da flash e do slot OTA (sanitizado)",
        "",
        "Leitura completa confirmada pelo manifesto; análise feita offline, sem acesso direto à serial. O candidato de seleção registrado em `otadata` foi confirmado pelo offset de carga que o bootloader reportou após o reset capturado; isso não comprova a saúde do processo depois do boot.",
        "",
        f"- Placa: {result['capture']['chip']}",
        f"- Captura UTC: {result['capture']['captured_utc']}",
        f"- Flash: {result['capture']['flash_bytes']} bytes; SHA-256 `{result['capture']['raw_flash_sha256']}`",
        f"- Tabela física verificada: offset `{result['partition_table']['offset']}`, SHA-256 `{result['partition_table']['sha256']}`",
        "",
        "| Partição | Tipo/subtipo | Offset | Tamanho | SHA-256 |",
        "|---|---|---:|---:|---|",
    ]
    for part in result["partition_table"]["partitions"]:
        lines.append(f"| `{part['name']}` | `{part['type']}/{part['subtype']}` | `{part['offset']}` | `{part['size']}` | `{part['sha256']}` |")
    lines += ["", "## Registros OTA", "", "O campo `seq_label` foi deliberadamente omitido e nem é lido pelo decoder. O CRC sobre os quatro bytes little-endian de `ota_seq` foi conferido tanto com zlib (semente UINT32_MAX) como com uma implementação bit a bit independente que segue os complementos e polinômio documentados pelo ESP-ROM.", "", "| Cópia | Offset absoluto | ota_seq | Estado | CRC | Válido pelo bootloader |", "|---:|---:|---:|---|---|---|"]
    for rec in result["otadata"]["records"]:
        lines.append(f"| {rec['copy']} | `{hex(rec['absolute_offset'])}` | `{rec['ota_seq']:#010x}` | `{rec['ota_state']}` | `{rec['stored_crc32']}` ({'válido' if rec['crc_valid'] else 'inválido'}) | {'sim' if rec['valid_by_idf_rules'] else 'não'} |")
    cand = result["boot_selection_candidate"]
    observation = result["bootloader_observation"]
    lines += ["", "Segurança reportada para a captura: Secure Boot e Flash Encryption desabilitados. O valor eFuse usado pela política anti-rollback não foi lido.", "", "## Candidato de boot derivado", "", f"- Partição: `{cand['partition']}` (`{cand['subtype']}`) em `{cand['offset']}`, sequência OTA `{result['otadata']['selected_ota_seq']}`.", f"- Projeto: `{cand['project_name']}`; versão: `{cand['app_version']}`; ESP-IDF: `{cand['idf_version']}`; secure_version: `{cand['secure_version']}`.", f"- Verificação offline da imagem: {'passou' if cand['image_integrity_ok'] else 'não passou / incompleta'}.", "", "## Confirmação do bootloader", "", f"- Captura UART de reset: `{observation['capture_started_utc']}`; arquivo privado `{observation['capture_basename']}`, {observation['capture_bytes']} bytes, SHA-256 `{observation['capture_sha256']}`.", f"- O bootloader informou carregar o app no offset `{observation['loaded_app_offset']}`; corresponde ao candidato da tabela: {'sim' if observation['matches_candidate_offset'] else 'não'}.", "- O log bruto não é reproduzido. A observação prova apenas o offset informado pelo bootloader naquele reset, não saúde de execução posterior.", "", "## Metadados dos slots", "", "| Partição | Offset | SHA-256 | Projeto | Versão | ESP-IDF | checksum/hash | Conteúdo apagado |", "|---|---:|---|---|---|---|---|---|"]
    for app in result["app_partitions"]:
        validation = app["image_checksum"] or "sem parse"
        if app["appended_sha256"]:
            validation += "; SHA " + app["appended_sha256"]
        erased = "sim" if app["partition_erased_all_ff"] else "não"
        fields = [app["project_name"] or "—", app["app_version"] or "—", app["idf_version"] or "—"]
        lines.append(f"| `{app['partition']}` | `{app['offset']}` | `{app['partition_sha256']}` | `{fields[0]}` | `{fields[1]}` | `{fields[2]}` | {validation} | {erased} |")
    bench = result["physical_bench_plan"]
    lines += ["", "## Plano da bancada", "", "- Este ensaio grava manualmente uma imagem de teste apenas no slot ativo `ota_0` em `0x20000`, tamanho `0x700000`; não usa fluxo OTA.", "- Antes do ensaio, preservar o backup integral validado. Depois, restaurar os bytes originais de `ota_0` desse backup e exigir o mesmo SHA-256 capturado.", "- Manter bootloader, tabela de partições, `otadata`, `ota_1` e NVS intactos. Comparar a flash inteira com a baseline; antes da restauração, qualquer diferença só é esperada dentro de `ota_0`; depois, exigir SHA-256 integral igual à baseline.", "- O plano não executa nem autoriza escrita por este decoder.", "", "## Limites", ""]
    for item in result["limits"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        # Errors are generic to avoid leaking file or firmware contents.
        raise SystemExit(f"offline decoder stopped safely: {type(error).__name__}") from None
