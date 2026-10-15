#!/usr/bin/env python3
"""Create and verify WasmHome COSE_Sign1 artifact packages.

Signing uses the system OpenSSL CLI. The module implements only the strict,
deterministic CBOR subset required by the WasmHome envelope contract.
"""

import argparse
import hashlib
import hmac
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile


SCHEMA = "wasmhome-issue15-artifacts-v1"
SCENARIOS = {
    1: ("native", "firmware", "firmware.bin", b"\xe9"),
    2: ("wasm", "gpio-control", "control.wasm", b"\x00asm\x01\x00\x00\x00"),
    3: ("aot", "gpio-control", "control.aot", b"\x00aot"),
}
MAX_PAYLOAD = {1: 7 * 1024 * 1024, 2: 65536, 3: 65536}
MAX_ENVELOPE = 4096
UINT32_MAX = (1 << 32) - 1
UINT64_MAX = (1 << 64) - 1
P256_ORDER = int("FFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551", 16)
P256_OID = bytes.fromhex("06082a8648ce3d030107")


def require(value, message):
    if not value:
        raise ValueError(message)


class CborTag:
    def __init__(self, tag, value):
        self.tag = tag
        self.value = value

    def __eq__(self, other):
        return isinstance(other, CborTag) and self.tag == other.tag and self.value == other.value

    def __hash__(self):
        return hash((self.tag, self.value))


def _head(major, value):
    require(isinstance(value, int) and value >= 0, "invalid CBOR argument")
    if value < 24:
        return bytes([(major << 5) | value])
    if value < 1 << 8:
        return bytes([(major << 5) | 24, value])
    if value < 1 << 16:
        return bytes([(major << 5) | 25]) + value.to_bytes(2, "big")
    if value < 1 << 32:
        return bytes([(major << 5) | 26]) + value.to_bytes(4, "big")
    require(value < 1 << 64, "CBOR integer exceeds uint64")
    return bytes([(major << 5) | 27]) + value.to_bytes(8, "big")


def cbor_encode(value):
    if value is None:
        return b"\xf6"
    if isinstance(value, bool):
        raise ValueError("CBOR booleans are outside the envelope contract")
    if isinstance(value, int):
        if value >= 0:
            return _head(0, value)
        return _head(1, -1 - value)
    if isinstance(value, bytes):
        return _head(2, len(value)) + value
    if isinstance(value, str):
        data = value.encode("utf-8", errors="strict")
        return _head(3, len(data)) + data
    if isinstance(value, (tuple, list)):
        return _head(4, len(value)) + b"".join(cbor_encode(item) for item in value)
    if isinstance(value, dict):
        pairs = [(cbor_encode(key), cbor_encode(item)) for key, item in value.items()]
        pairs.sort(key=lambda pair: (len(pair[0]), pair[0]))
        return _head(5, len(pairs)) + b"".join(key + item for key, item in pairs)
    if isinstance(value, CborTag):
        return _head(6, value.tag) + cbor_encode(value.value)
    raise ValueError(f"unsupported CBOR value: {type(value).__name__}")


def _argument(data, offset, additional):
    if additional < 24:
        return additional, offset
    sizes = {24: 1, 25: 2, 26: 4, 27: 8}
    require(additional in sizes, "indefinite or reserved CBOR argument")
    size = sizes[additional]
    end = offset + size
    require(end <= len(data), "truncated CBOR argument")
    value = int.from_bytes(data[offset:end], "big")
    minimum = {1: 24, 2: 1 << 8, 4: 1 << 16, 8: 1 << 32}[size]
    require(value >= minimum, "non-minimal CBOR argument")
    return value, end


def _decode_one(data, offset=0):
    require(offset < len(data), "truncated CBOR item")
    initial = data[offset]
    major, additional = initial >> 5, initial & 31
    offset += 1
    if major == 7:
        require(additional == 22, "unsupported CBOR simple or floating value")
        return None, offset
    value, offset = _argument(data, offset, additional)
    if major == 0:
        return value, offset
    if major == 1:
        return -1 - value, offset
    if major in (2, 3):
        end = offset + value
        require(end <= len(data), "truncated CBOR string")
        raw = data[offset:end]
        if major == 2:
            return raw, end
        try:
            return raw.decode("utf-8", errors="strict"), end
        except UnicodeDecodeError as error:
            raise ValueError("invalid UTF-8 in CBOR text") from error
    if major == 4:
        result = []
        for _ in range(value):
            item, offset = _decode_one(data, offset)
            result.append(item)
        return result, offset
    if major == 5:
        result = {}
        for _ in range(value):
            key, offset = _decode_one(data, offset)
            try:
                duplicate = key in result
            except TypeError as error:
                raise ValueError("unsupported unhashable CBOR map key") from error
            require(not duplicate, "duplicate CBOR map key")
            item, offset = _decode_one(data, offset)
            result[key] = item
        return result, offset
    if major == 6:
        tagged, offset = _decode_one(data, offset)
        return CborTag(value, tagged), offset
    raise ValueError("unsupported CBOR major type")


def cbor_decode(data):
    value, offset = _decode_one(data)
    require(offset == len(data), "trailing bytes after CBOR item")
    require(cbor_encode(value) == data, "CBOR is not deterministically encoded")
    return value


def _run(args, *, input_data=None):
    try:
        result = subprocess.run(args, input=input_data, capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError("OpenSSL operation failed") from error
    return result.stdout


def _key_text(path, private):
    args = ["openssl", "pkey"]
    if not private:
        args.append("-pubin")
    if pathlib.Path(path).suffix.lower() == ".der":
        args += ["-inform", "DER"]
    args += ["-in", str(path), "-noout", "-text"]
    return _run(args).decode("utf-8", errors="replace")


def public_der_from_key(path, private=False):
    path = pathlib.Path(path)
    require(path.is_file() and not path.is_symlink(), "key file is missing or is a symlink")
    if private:
        require(path.stat().st_mode & 0o077 == 0, "private key permissions must exclude group and other")
    text = _key_text(path, private)
    require("prime256v1" in text or "P-256" in text, "key must use NIST P-256")
    args = ["openssl", "pkey"]
    if not private:
        args.append("-pubin")
    if path.suffix.lower() == ".der":
        args += ["-inform", "DER"]
    args += ["-in", str(path), "-pubout", "-outform", "DER"]
    der = _run(args)
    require(P256_OID in der, "key SubjectPublicKeyInfo is not P-256")
    return der


def key_id(public_der):
    return hashlib.sha256(public_der).digest()[:8]


def _der_length(length):
    if length < 128:
        return bytes([length])
    raw = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def _read_der_length(data, offset):
    require(offset < len(data), "truncated ECDSA DER")
    first = data[offset]
    offset += 1
    if first < 128:
        return first, offset
    width = first & 0x7f
    require(1 <= width <= 2 and offset + width <= len(data), "invalid ECDSA DER length")
    raw = data[offset:offset + width]
    require(raw[0] != 0, "non-minimal ECDSA DER length")
    return int.from_bytes(raw, "big"), offset + width


def _der_integer_value(data, offset):
    require(offset < len(data) and data[offset] == 2, "invalid ECDSA DER integer")
    length, offset = _read_der_length(data, offset + 1)
    end = offset + length
    require(length > 0 and end <= len(data), "truncated ECDSA DER integer")
    raw = data[offset:end]
    require(raw[0] & 0x80 == 0, "negative ECDSA DER integer")
    require(not (len(raw) > 1 and raw[0] == 0 and raw[1] & 0x80 == 0), "non-minimal ECDSA DER integer")
    return int.from_bytes(raw, "big"), end


def _der_to_raw(signature):
    require(signature and signature[0] == 0x30, "invalid ECDSA DER sequence")
    length, offset = _read_der_length(signature, 1)
    require(offset + length == len(signature), "invalid ECDSA DER sequence length")
    r, offset = _der_integer_value(signature, offset)
    s, offset = _der_integer_value(signature, offset)
    require(offset == len(signature), "trailing bytes in ECDSA signature")
    require(0 < r < P256_ORDER and 0 < s < P256_ORDER, "ECDSA integer outside P-256 range")
    if s > P256_ORDER // 2:
        s = P256_ORDER - s
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def _raw_to_der(signature):
    require(len(signature) == 64, "COSE ES256 signature must be exactly 64 bytes")
    r, s = int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")
    require(0 < r < P256_ORDER and 0 < s <= P256_ORDER // 2, "COSE signature is not normalized low-S P-256")

    def integer(value):
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        if raw[0] & 0x80:
            raw = b"\0" + raw
        return b"\x02" + _der_length(len(raw)) + raw

    body = integer(r) + integer(s)
    return b"\x30" + _der_length(len(body)) + body


def _signature_structure(protected, manifest_bytes):
    return cbor_encode(["Signature1", protected, b"", manifest_bytes])


def sign_message(message, private_key):
    key = pathlib.Path(private_key)
    public_der_from_key(key, private=True)
    with tempfile.TemporaryDirectory(prefix="wasmhome-cose-") as directory:
        message_path = pathlib.Path(directory) / "message.bin"
        signature_path = pathlib.Path(directory) / "signature.der"
        message_path.write_bytes(message)
        _run(["openssl", "dgst", "-sha256", "-sign", str(key), "-out", str(signature_path), str(message_path)])
        return _der_to_raw(signature_path.read_bytes())


def verify_message(message, signature, public_key):
    key = pathlib.Path(public_key)
    public_der_from_key(key)
    der_signature = _raw_to_der(signature)
    with tempfile.TemporaryDirectory(prefix="wasmhome-cose-verify-") as directory:
        message_path = pathlib.Path(directory) / "message.bin"
        signature_path = pathlib.Path(directory) / "signature.der"
        message_path.write_bytes(message)
        signature_path.write_bytes(der_signature)
        try:
            subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(key), "-signature", str(signature_path), str(message_path)], capture_output=True, check=True)
        except (OSError, subprocess.CalledProcessError) as error:
            raise ValueError("invalid COSE ES256 signature") from error


def _manifest_values(campaign_id, scenario, release_version, sequence, size, digest, toolchain_id):
    require(isinstance(campaign_id, str) and re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", campaign_id) is not None,
            "campaign_id has invalid form")
    require(isinstance(scenario, int) and not isinstance(scenario, bool) and scenario in SCENARIOS,
            "scenario must be 1, 2, or 3")
    require(isinstance(release_version, str) and
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}", release_version) is not None,
            "release_version has invalid form")
    require(isinstance(sequence, int) and not isinstance(sequence, bool) and 0 <= sequence <= UINT64_MAX,
            "sequence must be uint64")
    require(0 < size <= min(MAX_PAYLOAD[scenario], UINT32_MAX), "payload_size exceeds scenario limit")
    name, component, _, _ = SCENARIOS[scenario]
    if scenario == 3:
        require(isinstance(toolchain_id, str) and
                re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}", toolchain_id) is not None,
                "AOT toolchain_id has invalid form")
    else:
        require(toolchain_id is None, "toolchain_id must be null outside AOT")
    return {
        1: 1,
        2: campaign_id,
        3: scenario,
        4: scenario,
        5: component,
        6: release_version,
        7: sequence,
        8: size,
        9: digest,
        10: "esp32s3",
        11: 1,
        12: toolchain_id,
    }


def _semantic_check(manifest):
    require(set(manifest) == set(range(1, 13)), "manifest fields differ from the closed contract")
    require(isinstance(manifest[1], int) and not isinstance(manifest[1], bool) and manifest[1] == 1,
            "unsupported manifest_version")
    require(isinstance(manifest[3], int) and not isinstance(manifest[3], bool) and manifest[3] in SCENARIOS and
            isinstance(manifest[4], int) and not isinstance(manifest[4], bool) and manifest[4] == manifest[3],
            "scenario/artifact_kind pair is invalid")
    _, component, _, _ = SCENARIOS[manifest[3]]
    require(manifest[5] == component, "component does not match scenario")
    require(isinstance(manifest[10], str) and manifest[10] == "esp32s3", "unsupported target")
    require(isinstance(manifest[11], int) and not isinstance(manifest[11], bool) and manifest[11] == 1,
            "unsupported base_api_version")
    if manifest[3] == 3:
        require(isinstance(manifest[12], str) and
                re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}", manifest[12]) is not None,
                "AOT toolchain_id has invalid form")
    else:
        require(manifest[12] is None, "toolchain_id must be null outside AOT")
    require(isinstance(manifest[2], str) and re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", manifest[2]) is not None and
            isinstance(manifest[6], str) and
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}", manifest[6]) is not None,
            "manifest text fields are invalid")
    require(isinstance(manifest[7], int) and not isinstance(manifest[7], bool) and 0 <= manifest[7] <= UINT64_MAX,
            "sequence must be uint64")
    require(isinstance(manifest[8], int) and not isinstance(manifest[8], bool) and
            0 < manifest[8] <= min(MAX_PAYLOAD[manifest[3]], UINT32_MAX), "payload_size exceeds scenario limit")
    require(isinstance(manifest[9], bytes) and len(manifest[9]) == 32, "payload_sha256 must be 32 bytes")


def _payload_check(scenario, payload):
    require(isinstance(payload, bytes) and payload, "payload is empty")
    _, _, _, magic = SCENARIOS[scenario]
    require(payload.startswith(magic), f"payload magic does not match scenario {scenario}")
    require(len(payload) <= MAX_PAYLOAD[scenario], "payload exceeds scenario limit")


def build_envelope(manifest, payload, private_key):
    _semantic_check(manifest)
    _payload_check(manifest[3], payload)
    require(manifest[8] == len(payload) and hmac.compare_digest(manifest[9], hashlib.sha256(payload).digest()),
            "manifest payload size/hash differs")
    public_der = public_der_from_key(private_key, private=True)
    protected = cbor_encode({1: -7, 4: key_id(public_der)})
    encoded_manifest = cbor_encode(manifest)
    signature = sign_message(_signature_structure(protected, encoded_manifest), private_key)
    envelope = cbor_encode(CborTag(18, [protected, {}, encoded_manifest, signature]))
    require(len(envelope) <= MAX_ENVELOPE, "COSE envelope exceeds configured limit")
    return envelope


def verify_envelope(envelope, payload, public_key, *, accepted_floor=None,
                    expected_campaign_id=None, expected_aot_toolchain_id=None):
    require(len(envelope) <= MAX_ENVELOPE, "COSE envelope exceeds configured limit")
    outer = cbor_decode(envelope)
    require(isinstance(outer, CborTag) and outer.tag == 18, "COSE_Sign1 tag 18 is required")
    require(isinstance(outer.value, list) and len(outer.value) == 4, "COSE_Sign1 must contain four elements")
    protected, unprotected, encoded_manifest, signature = outer.value
    require(isinstance(protected, bytes) and isinstance(unprotected, dict) and not unprotected,
            "COSE headers differ from the contract")
    require(isinstance(encoded_manifest, bytes) and isinstance(signature, bytes) and len(signature) == 64,
            "COSE payload/signature encoding is invalid")
    protected_map = cbor_decode(protected)
    require(protected_map == {1: -7, 4: key_id(public_der_from_key(public_key))}, "protected alg/kid differs")
    manifest = cbor_decode(encoded_manifest)
    require(isinstance(manifest, dict), "manifest must be a CBOR map")
    require(set(manifest) == set(range(1, 13)), "manifest fields differ from the closed contract")
    _semantic_check(manifest)
    verify_message(_signature_structure(protected, encoded_manifest), signature, public_key)
    if expected_campaign_id is not None:
        require(manifest[2] == expected_campaign_id, "campaign_id differs from expected campaign")
    if expected_aot_toolchain_id is not None and manifest[3] == 3:
        require(manifest[12] == expected_aot_toolchain_id,
                "AOT toolchain_id differs from expected toolchain")
    require(accepted_floor is None or (isinstance(accepted_floor, int) and not isinstance(accepted_floor, bool) and
                                       0 <= accepted_floor <= UINT64_MAX),
            "accepted_floor must be uint64")
    if accepted_floor is not None:
        require(manifest[7] > accepted_floor, "sequence is not newer than accepted_floor")
    _payload_check(manifest[3], payload)
    require(manifest[8] == len(payload), "payload_size mismatch")
    require(hmac.compare_digest(manifest[9], hashlib.sha256(payload).digest()), "payload SHA-256 mismatch")
    return manifest


def _json_load(path, max_bytes=65536):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result

    try:
        path = pathlib.Path(path)
        require(path.is_file() and not path.is_symlink(), "JSON input is missing or a symlink")
        data = _read_limited(path, max_bytes, "JSON input")
        return json.loads(data.decode("utf-8", errors="strict"), object_pairs_hook=unique,
                          parse_constant=lambda value: (_ for _ in ()).throw(
                              ValueError(f"invalid JSON constant: {value}")))
    except json.JSONDecodeError as error:
        raise ValueError("invalid JSON input") from error


def _safe_relative(root, relative):
    require(isinstance(relative, str) and relative, "artifact path must be a non-empty string")
    path = pathlib.Path(relative)
    require(not path.is_absolute() and ".." not in path.parts, "artifact path must be relative and contained")
    full = root / path
    require(full.resolve().is_relative_to(root.resolve()), "artifact path escapes package directory")
    for item in (full, *full.parents):
        if item.is_relative_to(root) and os.path.lexists(item):
            require(not item.is_symlink(), "artifact package contains a symlink")
    return full


def _read_limited(path, limit, description):
    path = pathlib.Path(path)
    metadata = path.stat()
    require(metadata.st_size <= limit, f"{description} exceeds configured limit")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    require(len(data) <= limit and len(data) == metadata.st_size,
            f"{description} changed or exceeded limit while reading")
    return data


def sign_set(config_path, output_root, private_key):
    config_path = pathlib.Path(config_path).resolve()
    output = pathlib.Path(output_root).absolute()
    require(not os.path.lexists(output), "output directory already exists; refusing to overwrite")
    config = _json_load(config_path)
    require(config.get("schema") == SCHEMA, "artifact input schema differs")
    entries = config.get("artifacts")
    require(isinstance(entries, list) and len(entries) == 6, "exactly six artifacts (v1/v2 for A/B/C) are required")
    seen = set()
    prepared = []
    campaign_id = config.get("campaign_id")
    toolchain_id = config.get("aot_toolchain_id")
    for entry in entries:
        require(isinstance(entry, dict), "artifact entry must be an object")
        scenario, label = entry.get("scenario"), entry.get("label")
        require((scenario, label) in {(s, v) for s in SCENARIOS for v in ("v1", "v2")},
                "artifact scenario/label is invalid")
        require((scenario, label) not in seen, "duplicate scenario/release pair")
        seen.add((scenario, label))
        sequence = entry.get("sequence")
        require(sequence == (1 if label == "v1" else 2), "v1/v2 sequences must be 1/2 per namespace")
        payload_name = entry.get("payload", "")
        require(isinstance(payload_name, str) and payload_name, "artifact payload path is required")
        source = _safe_relative(config_path.parent, payload_name)
        require(source.is_file(), "artifact payload file is missing")
        payload = _read_limited(source, MAX_PAYLOAD[scenario], "artifact payload")
        _payload_check(scenario, payload)
        _, component, filename, _ = SCENARIOS[scenario]
        values = _manifest_values(campaign_id, scenario, label, sequence, len(payload),
                                  hashlib.sha256(payload).digest(), toolchain_id if scenario == 3 else None)
        prepared.append((scenario, label, component, filename, payload, values))
    require(seen == {(s, v) for s in SCENARIOS for v in ("v1", "v2")}, "artifact matrix is incomplete")
    public_der = public_der_from_key(private_key, private=True)

    output.parent.mkdir(parents=True, exist_ok=True)
    stage = pathlib.Path(tempfile.mkdtemp(prefix=f".{output.name}.stage-", dir=output.parent))
    try:
        records = []
        for scenario, label, component, filename, payload, manifest in prepared:
            folder = pathlib.Path(f"scenario-{scenario}") / component / label
            package_dir = stage / folder
            package_dir.mkdir(parents=True)
            (package_dir / filename).write_bytes(payload)
            envelope = build_envelope(manifest, payload, private_key)
            (package_dir / "manifest.cose").write_bytes(envelope)
            record = {
                "scenario": scenario,
                "scenario_name": SCENARIOS[scenario][0],
                "component": component,
                "label": label,
                "release_version": manifest[6],
                "sequence": manifest[7],
                "payload_path": (folder / filename).as_posix(),
                "payload_bytes": len(payload),
                "payload_sha256": hashlib.sha256(payload).hexdigest(),
                "envelope_path": (folder / "manifest.cose").as_posix(),
                "envelope_bytes": len(envelope),
                "envelope_sha256": hashlib.sha256(envelope).hexdigest(),
                "toolchain_id": manifest[12],
            }
            records.append(record)
        (stage / "signing-public.der").write_bytes(public_der)
        index = {
            "schema": "wasmhome-issue15-index-v1",
            "campaign_id": campaign_id,
            "target": "esp32s3",
            "base_api_version": 1,
            "max_envelope_bytes": MAX_ENVELOPE,
            "payload_limits_bytes": {str(k): v for k, v in MAX_PAYLOAD.items()},
            "aot_toolchain_id": toolchain_id,
            "signing_public_key_sha256": hashlib.sha256(public_der).hexdigest(),
            "signing_kid": key_id(public_der).hex(),
            "artifacts": records,
        }
        (stage / "index.json").write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        for path in stage.rglob("*"):
            path.chmod(0o750 if path.is_dir() else 0o640)
        stage.chmod(0o750)
        verify_set(stage, stage / "signing-public.der")
        require(not os.path.lexists(output), "output directory was created during artifact generation")
        os.replace(stage, output)
        return index
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def verify_set(package_root, public_key, *, expected_campaign_id=None,
               expected_aot_toolchain_id=None):
    root = pathlib.Path(package_root).resolve()
    index = _json_load(root / "index.json")
    require(index.get("schema") == "wasmhome-issue15-index-v1", "artifact index schema differs")
    public_der = public_der_from_key(public_key)
    bundled_key = root / "signing-public.der"
    require(bundled_key.is_file() and not bundled_key.is_symlink(),
            "bundled signing public key is missing or a symlink")
    require(_read_limited(bundled_key, 1024, "bundled signing public key") == public_der,
            "bundled signing public key differs from trusted key")
    require(hashlib.sha256(public_der).hexdigest() == index.get("signing_public_key_sha256"),
            "artifact index public-key fingerprint differs")
    require(key_id(public_der).hex() == index.get("signing_kid"), "artifact index kid differs")
    campaign_id = index.get("campaign_id")
    require(isinstance(campaign_id, str) and
            re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", campaign_id) is not None,
            "artifact index campaign_id has invalid form")
    base_api_version = index.get("base_api_version")
    require(index.get("target") == "esp32s3" and isinstance(base_api_version, int) and
            not isinstance(base_api_version, bool) and base_api_version == 1,
            "artifact index target/base API differs from contract")
    require(index.get("max_envelope_bytes") == MAX_ENVELOPE and
            index.get("payload_limits_bytes") == {str(k): v for k, v in MAX_PAYLOAD.items()},
            "artifact index size limits differ from contract")
    aot_toolchain_id = index.get("aot_toolchain_id")
    require(isinstance(aot_toolchain_id, str) and
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}", aot_toolchain_id) is not None,
            "artifact index AOT toolchain_id has invalid form")
    if expected_campaign_id is not None:
        require(campaign_id == expected_campaign_id,
                "artifact index campaign differs from expected campaign")
    if expected_aot_toolchain_id is not None:
        require(aot_toolchain_id == expected_aot_toolchain_id,
                "artifact index AOT toolchain differs from expected toolchain")
    artifacts = index.get("artifacts")
    require(isinstance(artifacts, list) and len(artifacts) == 6, "artifact index must contain six records")
    seen = set()
    verified = []
    for record in artifacts:
        require(isinstance(record, dict), "artifact index record must be an object")
        payload_path = _safe_relative(root, record.get("payload_path", ""))
        envelope_path = _safe_relative(root, record.get("envelope_path", ""))
        scenario = record.get("scenario")
        require(isinstance(scenario, int) and not isinstance(scenario, bool) and scenario in SCENARIOS,
                "artifact index scenario is invalid")
        payload = _read_limited(payload_path, MAX_PAYLOAD[scenario], "artifact payload")
        envelope = _read_limited(envelope_path, MAX_ENVELOPE, "COSE envelope")
        manifest = verify_envelope(envelope, payload, public_key,
                                   expected_campaign_id=campaign_id,
                                   expected_aot_toolchain_id=aot_toolchain_id)
        identity = (manifest[3], manifest[6])
        require(identity not in seen, "duplicate scenario/release in artifact index")
        seen.add(identity)
        require(record.get("scenario") == manifest[3] and record.get("component") == manifest[5] and
                isinstance(record.get("sequence"), int) and not isinstance(record.get("sequence"), bool) and
                record.get("sequence") == manifest[7] and record.get("release_version") == manifest[6],
                "artifact index identity differs from signed manifest")
        require(record.get("label") == manifest[6] and record.get("toolchain_id") == manifest[12],
                "artifact index release/toolchain identity differs from signed manifest")
        label = record.get("label")
        require(label in ("v1", "v2") and record.get("scenario_name") == SCENARIOS[manifest[3]][0] and
                manifest[2] == campaign_id and manifest[7] == (1 if label == "v1" else 2),
                "artifact campaign or v1/v2 sequence order differs")
        expected_folder = pathlib.Path(f"scenario-{manifest[3]}") / manifest[5] / label
        require(record.get("payload_path") == (expected_folder / SCENARIOS[manifest[3]][2]).as_posix() and
                record.get("envelope_path") == (expected_folder / "manifest.cose").as_posix(),
                "artifact path differs from immutable layout")
        require(record.get("payload_bytes") == len(payload) and
                record.get("payload_sha256") == hashlib.sha256(payload).hexdigest(),
                "artifact index payload digest/size differs")
        require(record.get("envelope_bytes") == len(envelope) and
                record.get("envelope_sha256") == hashlib.sha256(envelope).hexdigest(),
                "artifact index envelope digest/size differs")
        verified.append({"scenario": manifest[3], "component": manifest[5], "version": manifest[6],
                         "sequence": manifest[7], "payload_bytes": manifest[8],
                         "payload_sha256": manifest[9].hex()})
    require(seen == {(s, v) for s in SCENARIOS for v in ("v1", "v2")}, "artifact index matrix is incomplete")
    return {"schema": index["schema"], "campaign_id": campaign_id,
            "signing_public_key_sha256": hashlib.sha256(public_der).hexdigest(),
            "signing_kid": key_id(public_der).hex(), "aot_toolchain_id": aot_toolchain_id,
            "verified": verified}


def verify_one(envelope_path, payload_path, public_key, accepted_floor=None,
               expected_campaign_id=None, expected_aot_toolchain_id=None):
    envelope = _read_limited(envelope_path, MAX_ENVELOPE, "COSE envelope")
    payload = _read_limited(payload_path, max(MAX_PAYLOAD.values()), "artifact payload")
    manifest = verify_envelope(envelope, payload, public_key, accepted_floor=accepted_floor,
                               expected_campaign_id=expected_campaign_id,
                               expected_aot_toolchain_id=expected_aot_toolchain_id)
    return {"scenario": manifest[3], "component": manifest[5], "release_version": manifest[6],
            "sequence": manifest[7], "payload_size": manifest[8], "payload_sha256": manifest[9].hex()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    sign = subparsers.add_parser("sign-set", help="sign six A/B/C v1/v2 artifacts")
    sign.add_argument("--config", type=pathlib.Path, required=True)
    sign.add_argument("--output", type=pathlib.Path, required=True)
    sign.add_argument("--private-key", type=pathlib.Path, required=True)
    verify = subparsers.add_parser("verify-set", help="verify a published artifact set")
    verify.add_argument("--package", type=pathlib.Path, required=True)
    verify.add_argument("--public-key", type=pathlib.Path, required=True)
    verify.add_argument("--expected-campaign-id")
    verify.add_argument("--expected-aot-toolchain-id")
    one = subparsers.add_parser("verify", help="verify one envelope and payload")
    one.add_argument("--envelope", type=pathlib.Path, required=True)
    one.add_argument("--payload", type=pathlib.Path, required=True)
    one.add_argument("--public-key", type=pathlib.Path, required=True)
    one.add_argument("--accepted-floor", type=int)
    one.add_argument("--expected-campaign-id")
    one.add_argument("--expected-aot-toolchain-id")
    args = parser.parse_args()
    try:
        if args.command == "sign-set":
            result = sign_set(args.config, args.output, args.private_key)
        elif args.command == "verify-set":
            result = verify_set(args.package, args.public_key,
                                expected_campaign_id=args.expected_campaign_id,
                                expected_aot_toolchain_id=args.expected_aot_toolchain_id)
        else:
            result = verify_one(args.envelope, args.payload, args.public_key, args.accepted_floor,
                                args.expected_campaign_id, args.expected_aot_toolchain_id)
        print(json.dumps(result, indent=2, sort_keys=True))
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Signed artifact operation failed: {error}\n")


if __name__ == "__main__":
    main()
