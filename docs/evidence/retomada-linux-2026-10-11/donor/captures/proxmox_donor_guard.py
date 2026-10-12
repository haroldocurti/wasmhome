#!/usr/bin/env python3
"""Read-only host guard proposal. Never creates, starts, stops or changes a guest."""
import argparse
import hashlib
import json
import pathlib
import platform
import re
import subprocess
import time

GIB = 1024 ** 3
CT = 110
NAME = "wasmhome-toolchain-donor"
TEMPLATE = pathlib.Path("/var/lib/vz/template/cache/debian-13-standard_13.1-2_amd64.tar.zst")
TEMPLATE_SHA256 = "ca6dffb91c3239fedc30af2c11389198140de1f1fc3d58082f3d9f4239b92e1b"
TEMPLATE_BYTES = 129710398


def require(value, message):
    if not value:
        raise ValueError(message)


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=20).stdout.strip()


def meminfo():
    info = {}
    for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        info[key] = int(value.split()[0]) * 1024
    return info["MemAvailable"], info["SwapTotal"] - info["SwapFree"]


def template_identity():
    require(TEMPLATE.is_file() and not TEMPLATE.is_symlink(), "template missing/redirected")
    require(TEMPLATE.stat().st_size == TEMPLATE_BYTES, "template byte count changed")
    with TEMPLATE.open("rb") as source:
        actual = hashlib.file_digest(source, "sha256").hexdigest()
    require(actual == TEMPLATE_SHA256, "template digest changed; do not create/start CT110")
    return dict(path=str(TEMPLATE), bytes=TEMPLATE_BYTES, sha256=actual)


def ct_state(identifier):
    return run("pct", "status", str(identifier)).split()[-1]


def guest_states():
    states = {}
    for command, prefix in (("pct", "ct"), ("qm", "vm")):
        for line in run(command, "list").splitlines()[1:]:
            fields = line.split()
            if fields and fields[0].isdigit() and int(fields[0]) != CT:
                states[prefix + fields[0]] = ct_state(fields[0]) if command == "pct" else run("qm", "status", fields[0]).split()[-1]
    return states


def configuration():
    path = pathlib.Path("/etc/pve/lxc/110.conf")
    text = path.read_text()
    result = {}
    allowed = {"arch", "cores", "hostname", "memory", "net0", "onboot", "ostype", "rootfs", "swap", "unprivileged"}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        require(not line[0].isspace() and ": " in line, "unsupported CT110 configuration syntax")
        key, value = line.split(": ", 1)
        require(key in allowed, "unapproved extra CT110 configuration: " + key)
        require(key not in result, "ambiguous CT110 configuration")
        result[key] = value
    for key, value in dict(hostname=NAME, memory="8192", swap="0", cores="2", unprivileged="1").items():
        require(result.get(key) == value, "CT110 identity/resource configuration differs: " + key)
    require(result.get("arch") == "amd64", "CT110 must be amd64")
    require(result.get("onboot", "0") == "0", "CT110 must have onboot0")
    require(result.get("ostype") == "debian", "CT110 must use the verified Debian template")
    rootfs = result.get("rootfs", "").split(",")
    require(len(rootfs) == 2 and re.fullmatch(r"DevServer:vm-110-disk-[0-9]+", rootfs[0]) and rootfs[1] == "size=32G", "CT110 must use only its own DevServer32GiB root volume, without extra mount options")
    net = {}
    for part in result.get("net0", "").split(","):
        require("=" in part, "invalid CT110 net0 field")
        key, value = part.split("=", 1)
        require(key in {"name", "bridge", "ip", "hwaddr", "type", "firewall", "link_down"} and key not in net, "unapproved/duplicate CT110 net0 option: " + key)
        net[key] = value
    mac = net.get("hwaddr", "")
    require(net.get("bridge") == "vmbr0" and net.get("ip") == "dhcp" and net.get("name") == "eth0"
            and re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", mac) and int(mac[:2], 16) & 1 == 0,
            "CT110 network identity differs/missing assigned unicast MAC")
    require(net.get("type", "veth") == "veth" and net.get("firewall", "0") == "0" and net.get("link_down", "0") == "0", "CT110 net0 differs from documented default veth/up/firewall0")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def devserver_free():
    lines = run("pvesm", "status", "--storage", "DevServer").splitlines()
    require(len(lines) == 2, "DevServer storage result ambiguous")
    fields = lines[1].split()
    require(fields[:3] == ["DevServer", "lvmthin", "active"], "DevServer storage identity/status differs")
    return int(fields[5]) * 1024


def check(phase, baseline_path=None, baseline=None):
    require(platform.node() == "haroldo", "unexpected Proxmox host")
    available, swap = meminfo()
    others = guest_states()
    require(others.get("ct109") == "stopped", "CT109 must remain stopped; guard does not stop it")
    require(not (baseline_path and baseline), "use one baseline input")
    base = baseline if baseline is not None else (json.loads(baseline_path.read_text()) if baseline_path else None)
    if base:
        expected_phase = "before-create" if phase == "before-start" else "before-start"
        require(base.get("ctid") == CT and base.get("hostname") == NAME and base.get("phase") == expected_phase, "baseline phase/CT identity differs")
        require(base["other_guest_states"] == others, "another guest changed state; pause and review, never change it automatically")
    result = dict(schema="wasmhome-proxmox-donor-lease-v1", timestamp_epoch=time.time(), ctid=CT, hostname=NAME,
                  phase=phase, host_memavailable_bytes=available, host_swap_used_bytes=swap, other_guest_states=others, ct109_state=others["ct109"])
    if phase == "before-create":
        require(not pathlib.Path("/etc/pve/lxc/110.conf").exists() and not pathlib.Path("/etc/pve/qemu-server/110.conf").exists(), "ID110 already exists; no replacement")
        require(run("pvesh", "get", "/cluster/nextid") == "110", "cluster free ID changed")
        require(available >= 10 * GIB and devserver_free() >= 48 * GIB, "host needs 8GiB plus2GiB RAM and32GiB plus16GiB disk reserve before creation")
        result["template"] = template_identity()
    else:
        require(base, "previous-phase baseline is mandatory; preserve other guests' states")
        config_sha = configuration()
        state = ct_state(CT)
        result.update(config_sha256=config_sha, ct_state=state, configured_memory_bytes=8 * GIB, configured_swap_bytes=0)
        if phase == "before-start":
            require(state == "stopped" and available >= 10 * GIB, "CT110 must be stopped and host have10GiB available before start")
            result["template"] = template_identity()
            require(base.get("template") == result["template"], "template changed since before-create")
            result.update(host_start_gate_memavailable_bytes=available, host_swap_baseline_bytes=swap)
        else:
            require(base and base["config_sha256"] == config_sha, "CT110 config changed or before-start baseline missing")
            if phase == "final":
                require(state == "stopped", "final contract: only new CT110 stopped, outputs preserved")
            elif phase == "identity":
                require(state == "running", "CT110 identity guard requires running state")
            else:
                require(state == "running" and available >= 2 * GIB and swap <= base["host_swap_used_bytes"] + 128 * 1024 ** 2, "host under pressure: stop only owned build processes, no guest action by guard")
                result.update(ct_memory_current_bytes=int(pathlib.Path("/sys/fs/cgroup/lxc/110/memory.current").read_text()),
                              ct_swap_current_bytes=int(pathlib.Path("/sys/fs/cgroup/lxc/110/memory.swap.current").read_text()),
                              host_start_gate_memavailable_bytes=base["host_start_gate_memavailable_bytes"], host_swap_baseline_bytes=base["host_swap_baseline_bytes"])
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("before-create", "before-start", "running", "identity", "final"), required=True)
    parser.add_argument("--baseline", type=pathlib.Path)
    args = parser.parse_args()
    try:
        print(json.dumps(check(args.phase, args.baseline), indent=2))
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, "Read-only donor guard rejected: " + str(error) + "\n")
