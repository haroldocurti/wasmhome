#!/usr/bin/env python3
"""Freeze #22 app/bootloader/config artifacts from completed ESP-IDF builds."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import issue22_hil_orchestrator as hil


def build_manifest(builds: dict[str, Path], source_commit: str) -> dict:
    profiles = {}
    common = None
    for name, directory in builds.items():
        directory = directory.expanduser().resolve()
        description = hil.load_json(directory / "project_description.json", "IDF build description")
        hil.require(description.get("target") == "esp32s3"
                    and Path(description["build_dir"]).resolve() == directory,
                    f"{name} build description is stale or targets another chip")
        artifacts = {
            "v1_app": directory / description["app_bin"],
            "bootloader": directory / "bootloader/bootloader.bin",
            "sdkconfig": Path(description["config_file"]),
            "bootloader_config": directory / "bootloader/config/sdkconfig.h",
        }
        for path in artifacts.values():
            hil.require(path.is_file() and not path.is_symlink(),
                        f"{name} completed build artifact is missing or symlinked")
        marker = hil._embedded_issue22_build_marker(artifacts["v1_app"].read_bytes())
        hil.require(marker["profile"] == name, f"{name} build contains another profile")
        endpoint = {"ipv4": marker["server_ipv4"], "port": marker["server_port"],
                    "server_name": marker["server_name"], "ca_sha256": marker["ca_sha256"]}
        update_endpoint = {"ipv4": marker["update_server_ipv4"], "port": marker["update_server_port"],
                           "server_name": marker["update_server_name"], "ca_sha256": marker["ca_sha256"]}
        hil.require(update_endpoint == endpoint,
                    f"{name} signed provisioning and fixture endpoints differ")
        settings = (marker["functional_timeout_ms"], endpoint)
        hil.require(common is None or common == settings,
                    "profile build endpoints or functional timeouts differ")
        common = settings
        profiles[name] = {kind: {"path": str(path.absolute()), "sha256": hil.sha256_file(path)}
                          for kind, path in artifacts.items()}
    hil.require(set(profiles) == {"native", "interp", "aot"}, "all three completed builds are required")
    return {"schema": hil.PROFILE_SCHEMA, "resilience_test_mode": True,
            "source_commit": source_commit, "functional_timeout_ms": common[0],
            "endpoint": common[1], "update_endpoint": common[1], "profiles": profiles}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("native", "interp", "aot"):
        parser.add_argument(f"--{name}-build", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--tls-ca", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    builds = {name: getattr(options, f"{name}_build") for name in ("native", "interp", "aot")}
    manifest = build_manifest(builds, options.source_commit)
    # Validate the exact frozen record before making it available to run/check.
    output = options.output.expanduser().absolute()
    hil.private_directory(output.parent)
    temporary = output.with_name(output.name + ".validate")
    hil.write_private(temporary, (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode())
    try:
        endpoint = manifest["endpoint"]
        hil.validate_profile_manifest(temporary, manifest["functional_timeout_ms"],
            endpoint["ipv4"], endpoint["port"], endpoint["server_name"], options.tls_ca)
        hil.write_private(output, temporary.read_bytes())
    finally:
        temporary.unlink()
    print(json.dumps({"status": "validated", "schema": hil.PROFILE_SCHEMA,
                      "profiles": 3, "manifest_sha256": hil.sha256_file(output)}))


if __name__ == "__main__":
    main()
