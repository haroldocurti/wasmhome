"""Read-only portable verifier. Never calls infrastructure or changes captures."""
import hashlib
import json
import pathlib


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(root):
    root = root.resolve()
    checked = 0
    for line in (root / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        expected, name = line.split("  ", 1)
        path = (root / name).resolve()
        if not path.is_relative_to(root) or digest(path) != expected:
            raise ValueError("preservation checksum mismatch: " + name)
        checked += 1
    records = json.loads((root / "path-map.json").read_text(encoding="utf-8"))["entries"]
    mapping = {item["source_absolute"]: item for item in records}
    for item in records:
        path = root / item["target_relative"]
        if path.stat().st_size != item["bytes"] or digest(path) != item["sha256"]:
            raise ValueError("original-copy binding mismatch: " + item["target_relative"])
    index = json.loads((root / "captures/proposal-index.json").read_text(encoding="utf-8"))
    for name, item in index.items():
        if "sha256" in item:
            path = root / "captures" / name if "path" not in item else root / mapping[item["path"]]["target_relative"]
            if digest(path) != item["sha256"] or ("bytes" in item and path.stat().st_size != item["bytes"]):
                raise ValueError("R3 frozen index mismatch: " + name)
        elif name == "host_resource_snapshots":
            for historic, expected in item.items():
                path = root / mapping[historic]["target_relative"]
                if digest(path) != expected:
                    raise ValueError("R3 portable external snapshot mismatch")
    return dict(status="preservation_hashes_and_R3_index_verified", checksum_files=checked,
                copied_original_files=len(records), copied_original_bytes=sum(item["bytes"] for item in records),
                note="R1/R2 indexes are historical, and reconstructed R2 review is not its lost original. Hashes are unsigned.")


if __name__ == "__main__":
    print(json.dumps(verify(pathlib.Path(__file__).parent), indent=2))
