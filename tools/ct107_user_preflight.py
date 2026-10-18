#!/usr/bin/env python3
"""Read-only, pinned-SSH preflight for the CT107 user-owned HTTPS server.

Run before claiming an HIL slot. The probe does not open the serial port,
change remote files, or start/stop a process.
"""

import argparse
import ipaddress
import json
from pathlib import Path
import re
import shlex
import subprocess

try:
    from . import remote_observer
except ImportError:
    import remote_observer


class PreflightError(ValueError):
    pass


REMOTE_PROBE = r'''
import json, os, pathlib, pwd, socket, sys

bind_ip, expected_log, campaign_id, expected_port, remote_root = sys.argv[1:]
home = pathlib.Path.home().resolve()
log = pathlib.Path(expected_log)
if os.geteuid() == 0 or pwd.getpwuid(os.geteuid()).pw_name != 'wasmhome':
    raise SystemExit(2)
if sys.version_info < (3, 10) or not log.is_absolute() or log.is_symlink():
    raise SystemExit(2)
try:
    log.relative_to(home)
except ValueError:
    raise SystemExit(2)
if not log.is_file() or log.stat().st_uid != os.geteuid() or not os.access(log, os.R_OK):
    raise SystemExit(2)
root = pathlib.Path(remote_root)
if not root.is_absolute():
    root = home / root
try:
    root.relative_to(home)
except ValueError:
    raise SystemExit(2)
if root == home or root.is_symlink():
    raise SystemExit(2)
for ancestor in root.parents:
    if ancestor == home:
        break
    if ancestor.is_symlink():
        raise SystemExit(2)
if root.exists():
    if not root.is_dir() or root.stat().st_uid != os.geteuid() or root.stat().st_mode & 0o077:
        raise SystemExit(2)
else:
    parent = root
    while not parent.exists():
        if parent.is_symlink():
            raise SystemExit(2)
        parent = parent.parent
    if not parent.is_dir() or not os.access(parent, os.W_OK):
        raise SystemExit(2)

matches = []
for entry in pathlib.Path('/proc').iterdir():
    if not entry.name.isdecimal():
        continue
    try:
        if entry.stat().st_uid != os.geteuid():
            continue
        args = [item.decode('utf-8', 'replace') for item in
                (entry / 'cmdline').read_bytes().split(b'\0') if item]
        if not any(item.endswith('/artifact_https_server.py') for item in args):
            continue
        flags = {}
        for flag in ('--bind', '--port', '--log', '--expected-campaign-id'):
            if flag not in args or args.index(flag) + 1 >= len(args):
                continue
            flags[flag] = args[args.index(flag) + 1]
        if flags.get('--bind') == bind_ip and flags.get('--expected-campaign-id') == campaign_id:
            matches.append(flags)
    except (OSError, ValueError):
        continue
if len(matches) != 1 or matches[0].get('--log') != str(log):
    raise SystemExit(2)
try:
    port = int(matches[0]['--port'])
    if not 1024 <= port <= 65535 or port != int(expected_port):
        raise ValueError('privileged or invalid port')
    with socket.create_connection((bind_ip, port), timeout=2):
        pass
except (KeyError, OSError, ValueError):
    raise SystemExit(2)
print(json.dumps({'normal_user': True, 'pinned_ssh': True,
                  'campaign_server': True, 'server_log': True,
                  'listener_reachable': True, 'server_port': port}))
'''


def verify_server(options, *, bind_ip, server_log, campaign_id, expected_port):
    """Confirm the exact campaign server before the ESP is touched."""
    options = remote_observer.validate_options(dict(options))
    address = ipaddress.IPv4Address(bind_ip)
    if address.is_unspecified or address.is_loopback or address.is_multicast:
        raise PreflightError("CT107 bind address must be a LAN unicast IPv4")
    if not Path(server_log).is_absolute():
        raise PreflightError("CT107 server log must have an absolute path")
    if not isinstance(campaign_id, str) or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", campaign_id) is None:
        raise PreflightError("invalid signed artifact campaign ID")
    if type(expected_port) is not int or not 1024 <= expected_port <= 65535:
        raise PreflightError("expected HTTPS server port must be an unprivileged integer")
    command = ["ssh", "-T", "-p", str(options.get("ssh_port", 22)),
               "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
               "-o", "UserKnownHostsFile=" + options["known_hosts"],
               "-o", "GlobalKnownHostsFile=/dev/null", "-o", "IdentitiesOnly=yes",
               "-o", "IdentityAgent=none", "-o", "PasswordAuthentication=no",
               "-o", "KbdInteractiveAuthentication=no", "-o", "ConnectTimeout=3",
               "-i", options["identity"], "--", options["target"],
               shlex.join(["python3", "-c", REMOTE_PROBE, str(address),
                           str(server_log), campaign_id, str(expected_port), options["root"]])]
    try:
        response = subprocess.run(command, capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PreflightError("CT107 user preflight did not complete") from exc
    if response.returncode != 0:
        raise PreflightError("CT107 user server, campaign, log, or pinned SSH is not ready")
    try:
        result = json.loads(response.stdout)
    except (UnicodeError, ValueError) as exc:
        raise PreflightError("CT107 user preflight returned invalid data") from exc
    required = {"normal_user", "pinned_ssh", "campaign_server", "server_log",
                "listener_reachable", "server_port"}
    if (not isinstance(result, dict) or set(result) != required
            or any(result[k] is not True for k in required - {"server_port"})
            or type(result["server_port"]) is not int
            or result["server_port"] != expected_port):
        raise PreflightError("CT107 user preflight returned invalid readiness")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote-observer", required=True)
    parser.add_argument("--remote-known-hosts", required=True)
    parser.add_argument("--remote-identity", required=True)
    parser.add_argument("--remote-ssh-port", type=int, default=22)
    parser.add_argument("--remote-root", default=".local/share/wasmhome-hil")
    parser.add_argument("--bind-ip", required=True)
    parser.add_argument("--remote-server-log", required=True)
    parser.add_argument("--artifact-campaign-id", required=True)
    parser.add_argument("--expected-port", type=int, required=True)
    args = parser.parse_args()
    options = {"target": args.remote_observer, "known_hosts": args.remote_known_hosts,
               "identity": args.remote_identity, "ssh_port": args.remote_ssh_port,
               "root": args.remote_root}
    try:
        print(json.dumps(verify_server(options, bind_ip=args.bind_ip,
                                       server_log=args.remote_server_log,
                                       campaign_id=args.artifact_campaign_id,
                                       expected_port=args.expected_port), sort_keys=True))
    except (PreflightError, remote_observer.RemoteObserverError, OSError, ValueError) as exc:
        parser.exit(1, f"CT107 preflight failed: {exc}\n")


if __name__ == "__main__":
    main()
