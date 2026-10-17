#!/usr/bin/env python3
"""Create fixed-path ENV-SIG/HASH/OLD fixtures from an accepted #15 package.

Private keys are inputs only and are never copied into the output. The output
directory is a new immutable tree suitable for manual publication to the
bench artifact server's dedicated resilience/ paths.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from signed_artifacts import (  # noqa: E402
    SCENARIOS,
    _manifest_values,
    _rename_directory_noreplace,
    build_envelope,
    key_id,
    public_der_from_key,
    verify_envelope,
    verify_set,
)


def _write_file(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _fixture_manifest(campaign_id, scenario, sequence, payload, toolchain_id):
    return _manifest_values(
        campaign_id, scenario, "v2", sequence, len(payload),
        hashlib.sha256(payload).digest(), toolchain_id if scenario == 3 else None,
    )


def _record(root: Path, case_id: str, scenario: int, private_key: Path,
            manifest: dict, signed_payload: bytes, received_payload: bytes):
    envelope = build_envelope(manifest, signed_payload, private_key)
    folder = root / "resilience" / case_id / f"scenario-{scenario}"
    _, component, payload_filename, _ = SCENARIOS[scenario]
    _write_file(folder / "manifest.cose", envelope)
    _write_file(folder / payload_filename, received_payload)
    return {
        "case_id": case_id,
        "scenario": scenario,
        "campaign_id": manifest[2],
        "release_version": manifest[6],
        "sequence": manifest[7],
        "component": component,
        "manifest_path": (Path("resilience") / case_id / f"scenario-{scenario}" / "manifest.cose").as_posix(),
        "payload_path": (Path("resilience") / case_id / f"scenario-{scenario}" / payload_filename).as_posix(),
        "manifest_bytes": len(envelope),
        "manifest_sha256": hashlib.sha256(envelope).hexdigest(),
        "signed_payload_bytes": len(signed_payload),
        "signed_payload_sha256": hashlib.sha256(signed_payload).hexdigest(),
        "received_payload_bytes": len(received_payload),
        "received_payload_sha256": hashlib.sha256(received_payload).hexdigest(),
        "expected": {
            "ENV-SIG": {"event": "update_rejected", "reason": "SIGNATURE_INVALID",
                        "stage": "envelope_signature", "payload_written": False},
            "ENV-HASH": {"event": "update_rejected", "reason": "HASH_MISMATCH",
                         "stage": "payload_hash", "payload_slot": "inactive",
                         "payload_invalidated": True},
            "ENV-OLD": {"event": "update_rejected", "reason": "SEQUENCE_NOT_NEWER",
                        "stage": "envelope_sequence", "sequence_floor": "real_nvs"},
        }[case_id],
    }


def generate(package: Path, public_key: Path, trusted_private_key: Path,
             untrusted_private_key: Path, untrusted_public_key: Path, output: Path, *, old_sequence=1,
             next_sequence=2, module_next_sequence=3, expected_campaign_id=None,
             expected_aot_toolchain_id=None):
    package = package.resolve()
    public_key = public_key.resolve()
    trusted_private_key = trusted_private_key.resolve()
    untrusted_private_key = untrusted_private_key.resolve()
    untrusted_public_key = untrusted_public_key.resolve()
    output = output.absolute()
    if (old_sequence <= 0 or next_sequence <= old_sequence or
            module_next_sequence <= old_sequence):
        raise ValueError("old_sequence must be positive and both next sequences must be greater")
    if os.path.lexists(output):
        raise ValueError("output directory already exists; refusing to overwrite")
    verified = verify_set(package, public_key,
                          expected_campaign_id=expected_campaign_id,
                          expected_aot_toolchain_id=expected_aot_toolchain_id)
    trusted_der = public_der_from_key(public_key)
    signing_der = public_der_from_key(trusted_private_key, private=True)
    untrusted_der = public_der_from_key(untrusted_private_key, private=True)
    untrusted_public_der = public_der_from_key(untrusted_public_key)
    if hashlib.sha256(trusted_der).digest() != hashlib.sha256(signing_der).digest():
        raise ValueError("trusted private key does not match the package public key")
    if key_id(untrusted_der) == key_id(trusted_der):
        raise ValueError("untrusted signing key must differ from the trusted key")
    if hashlib.sha256(untrusted_der).digest() != hashlib.sha256(untrusted_public_der).digest():
        raise ValueError("untrusted private key does not match its public key")
    campaign_id = verified["campaign_id"]
    toolchain_id = verified["aot_toolchain_id"]
    index = json.loads((package / "index.json").read_text(encoding="utf-8"))
    entries = {(item["scenario"], item["label"]): item for item in index["artifacts"]}

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.stage-", dir=output.parent))
    records = []
    try:
        for scenario in (1, 2, 3):
            scenario_next_sequence = (next_sequence if scenario == 1
                                      else module_next_sequence)
            artifact = entries[(scenario, "v2")]
            signed_payload = (package / artifact["payload_path"]).read_bytes()
            source_envelope = (package / artifact["envelope_path"]).read_bytes()
            verify_envelope(source_envelope, signed_payload, public_key,
                            expected_campaign_id=campaign_id,
                            expected_aot_toolchain_id=toolchain_id)

            sig_manifest = _fixture_manifest(campaign_id, scenario, scenario_next_sequence,
                                             signed_payload, toolchain_id)
            records.append(_record(staging, "ENV-SIG", scenario, untrusted_private_key,
                                   sig_manifest, signed_payload, signed_payload))

            hash_manifest = _fixture_manifest(campaign_id, scenario, scenario_next_sequence,
                                              signed_payload, toolchain_id)
            tampered = bytearray(signed_payload)
            tampered[-1] ^= 0x01
            records.append(_record(staging, "ENV-HASH", scenario, trusted_private_key,
                                   hash_manifest, signed_payload, bytes(tampered)))

            old_manifest = _fixture_manifest(campaign_id, scenario, old_sequence,
                                             signed_payload, toolchain_id)
            records.append(_record(staging, "ENV-OLD", scenario, trusted_private_key,
                                   old_manifest, signed_payload, signed_payload))

        # Full semantic/signature checks catch malformed generation before publishing.
        for record in records:
            scenario = record["scenario"]
            scenario_next_sequence = (next_sequence if scenario == 1
                                      else module_next_sequence)
            envelope = (staging / record["manifest_path"]).read_bytes()
            received = (staging / record["payload_path"]).read_bytes()
            if record["case_id"] == "ENV-SIG":
                try:
                    verify_envelope(envelope, received, public_key,
                                    expected_campaign_id=campaign_id,
                                    expected_aot_toolchain_id=toolchain_id)
                except ValueError:
                    pass
                else:
                    raise ValueError("ENV-SIG unexpectedly verifies with the trusted key")
                # Its ECDSA signature is well-formed and valid under the non-trusted key.
                verify_envelope(envelope, received, untrusted_public_key,
                                expected_campaign_id=campaign_id,
                                expected_aot_toolchain_id=toolchain_id)
            elif record["case_id"] == "ENV-HASH":
                try:
                    verify_envelope(envelope, received, public_key,
                                    expected_campaign_id=campaign_id,
                                    expected_aot_toolchain_id=toolchain_id)
                except ValueError as error:
                    if "SHA-256 mismatch" not in str(error):
                        raise
                else:
                    raise ValueError("ENV-HASH unexpectedly accepts the altered payload")
            else:
                manifest = verify_envelope(envelope, received, public_key,
                                           expected_campaign_id=campaign_id,
                                           expected_aot_toolchain_id=toolchain_id)
                if manifest[7] != old_sequence or manifest[7] >= scenario_next_sequence:
                    raise ValueError("ENV-OLD sequence differs from the requested floor fixture")

        fixture_index = {
            "schema": "wasmhome-issue22-resilience-fixtures-v1",
            "status": "host_generated_not_device_validated",
            "campaign_id": campaign_id,
            "trusted_public_key_sha256": hashlib.sha256(trusted_der).hexdigest(),
            "untrusted_public_key_sha256": hashlib.sha256(untrusted_public_der).hexdigest(),
            "aot_toolchain_id": toolchain_id,
            "sequence_setup": {"old_sequence": old_sequence,
                               "next_sequence": next_sequence,
                               "module_next_sequence": module_next_sequence,
                               "next_sequences_by_scenario": {
                                   "scenario-1": next_sequence,
                                   "scenario-2": module_next_sequence,
                                   "scenario-3": module_next_sequence,
                               },
                               "requires_real_nvs_floor_at_least_old_sequence": True},
            "source_package_verified": verified,
            "artifacts": records,
            "pending": [
                "install fixture files on the fixed HTTPS bench server",
                "compile firmware with matching resilience fixture paths",
                "verify real inactive-slot invalidation and NVS floor on ESP32-S3",
            ],
        }
        (staging / "fixture-index.json").write_text(
            json.dumps(fixture_index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        for path in staging.rglob("*"):
            path.chmod(0o750 if path.is_dir() else 0o640)
        staging.chmod(0o750)
        _rename_directory_noreplace(staging, output)
        return fixture_index
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--trusted-private-key", type=Path, required=True)
    parser.add_argument("--untrusted-private-key", type=Path, required=True)
    parser.add_argument("--untrusted-public-key", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--old-sequence", type=int, default=1)
    parser.add_argument("--next-sequence", type=int, default=2)
    parser.add_argument("--module-next-sequence", type=int, default=3,
                        help="next sequence for scenarios 2/3 after module v2 has floor 2")
    parser.add_argument("--expected-campaign-id")
    parser.add_argument("--expected-aot-toolchain-id")
    args = parser.parse_args(argv)
    try:
        result = generate(args.package, args.public_key, args.trusted_private_key,
                          args.untrusted_private_key, args.untrusted_public_key,
                          args.output,
                          old_sequence=args.old_sequence, next_sequence=args.next_sequence,
                          module_next_sequence=args.module_next_sequence,
                          expected_campaign_id=args.expected_campaign_id,
                          expected_aot_toolchain_id=args.expected_aot_toolchain_id)
        print(json.dumps({"fixture_count": len(result["artifacts"]),
                          "output": str(args.output),
                          "status": result["status"]}, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        parser.exit(2, f"Resilience fixture generation failed: {error}\n")


if __name__ == "__main__":
    sys.exit(main())
