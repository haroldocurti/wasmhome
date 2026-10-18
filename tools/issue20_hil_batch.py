#!/usr/bin/env python3
"""Detached user-level supervisor: exactly one HIL slot per child process."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time
import uuid

try:
    from . import issue20_hil_orchestrator as hil
except ImportError:
    import issue20_hil_orchestrator as hil


def _read_private(path):
    path = Path(path)
    hil.require(path.is_file() and not path.is_symlink()
                and path.stat().st_uid == os.getuid()
                and stat.S_IMODE(path.stat().st_mode) == 0o600,
                "batch input must be a private regular 0600 file")
    return hil.campaign._read_json(path, "batch input")


def _identity(pid):
    try:
        info = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
        return {"pid": pid, "start_ticks": info[19],
                "host_boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}
    except (OSError, IndexError):
        return None


@contextmanager
def _lock(root):
    descriptor = os.open(root / "batch.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        hil.require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                    and stat.S_IMODE(info.st_mode) == 0o600, "unsafe batch lock")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise hil.HilError("another batch supervisor is active") from exc
        yield
    finally:
        os.close(descriptor)


def child_command(journal):
    options = journal["options"]
    fields = ("campaign_root", "profiles", "baseline", "expected_mac", "device_ip", "serial_port",
              "python", "esptool", "flash_baud", "read_timeout_seconds", "serial_baud", "bind_ip",
              "remote_observer", "remote_known_hosts", "remote_identity", "remote_root",
              "remote_ssh_port", "remote_server_log")
    hil.require(all(key in options for key in fields), "journal lacks frozen child options")
    command = [options["python"], str(Path(__file__).resolve().with_name("issue20_hil_orchestrator.py")), "run"]
    for key in fields:
        command.extend(("--" + key.replace("_", "-"), str(options[key])))
    return command + ["--max-slots", "1"]


def _group_alive(pid):
    if not pid:
        return False
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False


def _install_stop_handlers(state, status_path):
    def stop(signum, _frame):
        state.update(state="stopping", received_signal=signum)
        try:
            hil.write_private(status_path, state)
        except OSError:
            pass  # Forward even when recording the signal encounters a full disk.
        group = state.get("child_group_pid")
        if group:
            try:
                os.killpg(group, signum)
            except ProcessLookupError:
                pass
        elif not state.get("launching_child"):
            raise InterruptedError("supervisor interrupted without an active child")
        # During Popen, defer forwarding until its process group is recorded.
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    for sig in previous:
        signal.signal(sig, stop)
    return previous


def _run_child(command, log, state, status_path):
    descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        state["launching_child"] = True
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stream,
                                       stderr=subprocess.STDOUT, start_new_session=True,
                                       cwd=Path(__file__).resolve().parents[1])
            state.update(child_process=_identity(process.pid), child_group_pid=process.pid, launching_child=False)
            hil.write_private(status_path, state)
            if state.get("received_signal"):
                handler = signal.getsignal(state["received_signal"])
                handler(state["received_signal"], None)
            return process.wait()
        finally:
            state["launching_child"] = False


def run_batch(campaign_root, *, run_child=_run_child):
    os.umask(0o077)
    hil.require(os.geteuid() != 0, "batch supervisor must run as an ordinary user")
    root = hil.private_directory(Path(campaign_root).absolute() / "hil-orchestrator")
    journal_path, status_path = root / "journal.json", root / "batch.json"
    with _lock(root):
        previous = None
        if status_path.exists():
            old = _read_private(status_path)
            child = old.get("child_process")
            hil.require((not child or _identity(child["pid"]) != child)
                        and not _group_alive(old.get("child_group_pid")),
                        "previous batch child is still alive; do not start another")
            history = root / ("batch-history-" + uuid.uuid4().hex + ".json")
            hil._snapshot(status_path, history, hil.digest(status_path))
            previous = {"path": history.name, "sha256": hil.digest(history)}
        state = {"schema_version": 1, "state": "running", "process": _identity(os.getpid()),
                 "tooling_sha256": hil.tooling_snapshot(), "previous_batch": previous, "events": []}
        hil.write_private(status_path, state)
        previous_signals = _install_stop_handlers(state, status_path)
        try:
            for _ in range(90):
                journal = _read_private(journal_path)
                before, total = journal["next_index"], len(journal["slots"])
                hil.require(type(before) is int and 0 <= before <= total <= 90,
                            "invalid batch schedule index")
                state.update(next_index=before, planned_slots=total,
                             restore_required=journal["restore_required"])
                hil.require(journal["restore_required"] is False
                            and journal["state"] in {"ready", "complete", "recovered_interruption"},
                            "journal needs recovery or adjudication; supervisor stopped")
                hil.require(hil.tooling_snapshot() == state["tooling_sha256"],
                            "tooling changed while supervisor was active")
                hil.require(not state.get("received_signal"), "supervisor was interrupted; no next child")
                if before == total:
                    hil.require(journal["state"] != "recovered_interruption", "unresolved recovered claim")
                    state.update(state="complete", child_process=None)
                    hil.write_private(status_path, state)
                    return 0
                command = child_command(journal)
                hil.require(Path(journal["options"]["campaign_root"]).absolute()
                            == Path(campaign_root).absolute(), "journal belongs to another campaign")
                log = root / (f"batch-child-{before:04d}-" + uuid.uuid4().hex + ".log")
                state.update(child_log=log.name, child_process=None, child_group_pid=None)
                hil.write_private(status_path, state)
                code = run_child(command, log, state, status_path)
                after = _read_private(journal_path)
                state["events"].append({"before_index": before, "after_index": after["next_index"],
                                        "exit_code": code, "log": log.name})
                state.update(next_index=after["next_index"], restore_required=after["restore_required"],
                             child_process=None if code == 0 else state.get("child_process"),
                             child_group_pid=None if code == 0 else state.get("child_group_pid"))
                hil.require(not state.get("received_signal"), "supervisor interrupted; stopped without retry")
                hil.require(code == 0, "child failed; supervisor stopped without retry")
                advance = 2 if journal["state"] == "recovered_interruption" else 1
                hil.require(after["next_index"] == min(before + advance, total)
                            and after["state"] == "complete" and after["restore_required"] is False,
                            "child did not finish the expected single slot safely")
                if after["next_index"] == total:
                    state["state"] = "complete"
                    hil.write_private(status_path, state)
                    return 0
                hil.write_private(status_path, state)
            raise hil.HilError("supervisor exceeded the frozen schedule bound")
        except BaseException as exc:
            state.update(state="blocked", error=type(exc).__name__, reason=str(exc))
            hil.write_private(status_path, state)
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return 1
        finally:
            for sig, handler in previous_signals.items():
                signal.signal(sig, handler)


def batch_status(campaign_root):
    root = Path(campaign_root).absolute() / "hil-orchestrator"
    journal = _read_private(root / "journal.json")
    result = {"journal_state": journal["state"], "restore_required": journal["restore_required"],
              "next_index": journal["next_index"], "planned_slots": len(journal["slots"])}
    if (root / "batch.json").exists():
        state = _read_private(root / "batch.json")
        identity = state.get("process")
        child = state.get("child_process")
        result.update(batch_state=state["state"], supervisor_alive=bool(identity)
                      and _identity(identity["pid"]) == identity,
                      child_alive=(bool(child) and _identity(child["pid"]) == child)
                      or _group_alive(state.get("child_group_pid")))
        if state.get("received_signal"):
            result["stop_signal"] = signal.Signals(state["received_signal"]).name
    return result


def start_background(campaign_root):
    hil.require(os.geteuid() != 0, "batch supervisor must run as an ordinary user")
    root = hil.private_directory(Path(campaign_root).absolute() / "hil-orchestrator")
    _read_private(root / "journal.json")
    descriptor = os.open(root / "batch-supervisor.log",
                         os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    info = os.fstat(descriptor)
    hil.require(info.st_uid == os.getuid() and stat.S_ISREG(info.st_mode)
                and stat.S_IMODE(info.st_mode) == 0o600, "unsafe supervisor log")
    with os.fdopen(descriptor, "ab") as stream:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "run",
                                    "--campaign-root", str(Path(campaign_root).absolute())],
                                   stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                                   start_new_session=True, cwd=Path(__file__).resolve().parents[1])
    for _ in range(100):
        if (root / "batch.json").exists():
            state = _read_private(root / "batch.json")
            if (state.get("process") or {}).get("pid") == process.pid:
                return {"state": state["state"], "supervisor_pid": process.pid}
        hil.require(process.poll() is None, "supervisor could not start; inspect its private log")
        time.sleep(0.05)
    return {"state": "starting", "supervisor_pid": process.pid}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "run", "status"))
    parser.add_argument("--campaign-root", required=True)
    options = parser.parse_args()
    try:
        if options.action == "run":
            return run_batch(options.campaign_root)
        result = (start_background(options.campaign_root) if options.action == "start"
                  else batch_status(options.campaign_root))
        print(json.dumps(result, sort_keys=True))
        return 0
    except (hil.HilError, hil.campaign.CampaignError, OSError, ValueError) as exc:
        print(f"Batch stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
