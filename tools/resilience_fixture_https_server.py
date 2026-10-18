#!/usr/bin/env python3
"""Serve only the immutable, closed issue #22 resilience fixture catalog over HTTPS."""
import argparse
import hashlib
import http.server
import ipaddress
import json
import os
import pathlib
import re
import socket
import ssl
import stat
import threading
import tempfile
import time
import urllib.parse

try:
    from .signed_artifacts import (
        MAX_ENVELOPE, MAX_PAYLOAD, _json_load, _read_limited, public_der_from_key,
        verify_envelope, verify_set,
    )
except ImportError:
    from signed_artifacts import (
        MAX_ENVELOPE, MAX_PAYLOAD, _json_load, _read_limited, public_der_from_key,
        verify_envelope, verify_set,
    )

CASES = ("ENV-SIG", "ENV-HASH", "ENV-OLD")
PAYLOADS = {1: "firmware.bin", 2: "control.wasm", 3: "control.aot"}
SCENARIOS = (1, 2, 3)
MAX_PAYLOAD = {1: 7 * 1024 * 1024, 2: 65536, 3: 65536}
RESERVED_CAMPAIGN_PORTS = frozenset({8443, 8444})
DEFAULT_PORT = 18445


def require(condition, message):
    if not condition:
        raise ValueError(message)


def write_server_pid_receipt(path):
    """Durably record this server process after it adopts its reserved socket."""
    receipt = pathlib.Path(path)
    require(receipt.is_absolute() and ".." not in receipt.parts,
            "server PID receipt must be an absolute canonical path")
    parent = receipt.parent
    require(parent.is_dir() and not parent.is_symlink(),
            "server PID receipt directory is missing or symlinked")
    parent_info = parent.stat()
    require(parent_info.st_uid == os.getuid() and stat.S_IMODE(parent_info.st_mode) == 0o700,
            "server PID receipt directory must be private and user-owned")
    try:
        receipt.lstat()
    except FileNotFoundError:
        pass
    else:
        raise ValueError("server PID receipt already exists; verify/stop the prior campaign process first")
    raw_stat = pathlib.Path("/proc/self/stat").read_text(encoding="ascii")
    fields_after_comm = raw_stat.rsplit(")", 1)[1].split()
    start_time = fields_after_comm[19]
    require(start_time.isdigit(), "cannot read server process start-time")
    payload = f"{os.getpid()} {start_time}\n".encode("ascii")
    fd, temporary = tempfile.mkstemp(prefix=".server.pid.", dir=parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            fd = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # Hard-link gives an atomic no-replace commit. A concurrent or stale
        # receipt is a hard gate; this server must not overwrite it.
        os.link(temporary, receipt, follow_symlinks=False)
        os.unlink(temporary)
        directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return os.getpid(), start_time


def adopt_fixture_listener(descriptor, bind_ip, port):
    """Take ownership of the manager-reserved listening socket without rebinding."""
    require(type(descriptor) is int and descriptor == 3,
            "managed fixture listener must arrive on descriptor 3")
    listener = socket.socket(fileno=descriptor)
    try:
        address = listener.getsockname()
        require(listener.family == socket.AF_INET
                and listener.type & socket.SOCK_STREAM
                and address == (bind_ip, port)
                and listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 1
                and listener.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) == 0,
                "inherited fixture listener identity differs")
        if hasattr(socket, "SO_REUSEPORT"):
            require(listener.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT) == 0,
                    "inherited fixture listener permits port sharing")
        listener.setblocking(True)
        return listener
    except BaseException:
        listener.close()
        raise


def validate_fixture_port(port):
    require(isinstance(port, int) and 1024 <= port <= 65535,
            "port must be unprivileged")
    require(port not in RESERVED_CAMPAIGN_PORTS,
            "ports 8443 and 8444 are reserved for existing campaigns")
    return port


def read_file(path, limit):
    require(path.is_file() and not path.is_symlink(), f"file missing or symlink: {path.name}")
    require(path.stat().st_size <= limit, f"file exceeds size limit: {path.name}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        with os.fdopen(fd, "rb") as stream:
            data = stream.read(limit + 1)
    except BaseException:
        raise
    require(len(data) <= limit, f"file exceeds size limit: {path.name}")
    return data


def validate_file(root, relative, expected_bytes, expected_sha256, limit):
    rel = pathlib.PurePosixPath(relative)
    require(not rel.is_absolute() and ".." not in rel.parts and rel.as_posix() == relative,
            "fixture path is not canonical")
    path = root.joinpath(*rel.parts)
    for parent in (path, *path.parents):
        if parent == root.parent:
            break
        require(not parent.is_symlink(), "fixture path contains symlink")
    data = read_file(path, limit)
    require(len(data) == expected_bytes and hashlib.sha256(data).hexdigest() == expected_sha256,
            f"fixture digest/size differs: {relative}")
    return path, data


def load_allowlist(root, trusted_public_path, untrusted_public_path,
                   expected_campaign_id, expected_aot_toolchain_id,
                   ota_package_root=None, ota_public_key=None):
    root = pathlib.Path(root)
    require(root.is_dir() and not root.is_symlink(), "fixture root missing or symlink")
    root = root.resolve()
    index_path = root / "fixture-index.json"
    index = _json_load(index_path, max_bytes=65536)
    require(index.get("schema") == "wasmhome-issue22-resilience-fixtures-v1" and
            index.get("status") == "host_generated_not_device_validated",
            "fixture catalog schema/status differs")
    campaign_id = index.get("campaign_id")
    require(campaign_id == expected_campaign_id and
            re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", campaign_id or "") is not None,
            "fixture campaign id differs")
    require(index.get("expected_aot_toolchain_id") == expected_aot_toolchain_id,
            "fixture AOT toolchain differs")
    trusted = pathlib.Path(trusted_public_path)
    untrusted = pathlib.Path(untrusted_public_path)
    trusted_der = public_der_from_key(trusted)
    untrusted_der = public_der_from_key(untrusted)
    require(hashlib.sha256(trusted_der).hexdigest() == index.get("trusted_fixture_public_key_sha256"),
            "trusted fixture key fingerprint differs")
    require(hashlib.sha256(untrusted_der).hexdigest() == index.get("untrusted_fixture_public_key_sha256"),
            "untrusted fixture key fingerprint differs")
    records = index.get("artifacts")
    require(isinstance(records, list) and len(records) == 9, "fixture catalog must contain nine records")
    expected_identities = {(case, scenario) for case in CASES for scenario in SCENARIOS}
    seen = set()
    allowed = {}
    route_files = set()
    verification_files = {"verification/trusted-public.der", "verification/untrusted-public.der"}
    for record in records:
        require(isinstance(record, dict), "fixture record is not an object")
        case = record.get("case_id")
        scenario = record.get("scenario")
        identity = (case, scenario)
        require(identity in expected_identities and identity not in seen,
                "fixture catalog has a duplicate or unsupported case")
        seen.add(identity)
        require(record.get("campaign_id") == campaign_id and record.get("release_version") == "v2",
                "fixture record campaign/release differs")
        expected_sequence = 1 if case == "ENV-OLD" else 3
        require(record.get("sequence") == expected_sequence,
                "fixture sequence differs from the registered sequence plan")
        base = pathlib.PurePosixPath("resilience") / case / f"scenario-{scenario}"
        payload_rel = (base / PAYLOADS[scenario]).as_posix()
        manifest_rel = (base / "manifest.cose").as_posix()
        require(record.get("payload_path") == payload_rel and record.get("manifest_path") == manifest_rel,
                "fixture route differs from the closed path catalog")
        payload_path, payload = validate_file(root, payload_rel,
            record.get("payload_bytes"), record.get("payload_sha256"), MAX_PAYLOAD[scenario])
        manifest_path, envelope = validate_file(root, manifest_rel,
            record.get("manifest_bytes"), record.get("manifest_sha256"), 4096)
        signed_rel = record.get("verification_payload_path")
        require(signed_rel == (pathlib.PurePosixPath("verification") / f"scenario-{scenario}" / PAYLOADS[scenario]).as_posix(),
                "verification payload path differs")
        signed_path, signed_payload = validate_file(root, signed_rel,
            record.get("verification_payload_bytes"), record.get("verification_payload_sha256"), MAX_PAYLOAD[scenario])
        require(len(signed_payload) == record.get("signed_payload_bytes") and
                hashlib.sha256(signed_payload).hexdigest() == record.get("signed_payload_sha256"),
                "signed payload metadata differs")
        if case == "ENV-SIG":
            manifest = verify_envelope(envelope, payload, untrusted,
                expected_campaign_id=campaign_id, expected_aot_toolchain_id=expected_aot_toolchain_id)
            require(manifest[7] == expected_sequence, "ENV-SIG sequence differs")
            try:
                verify_envelope(envelope, payload, trusted,
                    expected_campaign_id=campaign_id, expected_aot_toolchain_id=expected_aot_toolchain_id)
            except ValueError:
                pass
            else:
                raise ValueError("ENV-SIG unexpectedly validates under the firmware trust key")
        elif case == "ENV-HASH":
            manifest = verify_envelope(envelope, signed_payload, trusted,
                expected_campaign_id=campaign_id, expected_aot_toolchain_id=expected_aot_toolchain_id)
            require(manifest[7] == expected_sequence and payload != signed_payload,
                    "ENV-HASH is not a signed, tampered payload")
            try:
                verify_envelope(envelope, payload, trusted,
                    expected_campaign_id=campaign_id, expected_aot_toolchain_id=expected_aot_toolchain_id)
            except ValueError as error:
                require("SHA-256 mismatch" in str(error), "ENV-HASH failed outside the hash check")
            else:
                raise ValueError("ENV-HASH unexpectedly verifies the downloaded payload")
        else:
            manifest = verify_envelope(envelope, payload, trusted,
                expected_campaign_id=campaign_id, expected_aot_toolchain_id=expected_aot_toolchain_id)
            require(manifest[7] == 1 and manifest[6] == "v2",
                    "ENV-OLD does not carry the old sequence")
        allowed[payload_rel] = (payload_path, len(payload), hashlib.sha256(payload).hexdigest())
        allowed[manifest_rel] = (manifest_path, len(envelope), hashlib.sha256(envelope).hexdigest())
        route_files.update((payload_rel, manifest_rel))
        verification_files.add(signed_rel)
    require(seen == expected_identities, "fixture catalog is incomplete")
    actual_route_files = {path.relative_to(root).as_posix() for path in (root / "resilience").rglob("*") if path.is_file()}
    require(actual_route_files == route_files, "fixture route tree has unexpected or missing files")
    actual_verification_files = {path.relative_to(root).as_posix() for path in (root / "verification").rglob("*") if path.is_file()}
    require(actual_verification_files == verification_files, "fixture verification tree differs")
    top_level = {path.name for path in root.iterdir()}
    require(top_level == {"fixture-index.json", "resilience", "verification"},
            "fixture package contains unexpected top-level entries")

    # The ordinary signed sequence-2 updater uses a separate, immutable package
    # layout and signing key. Keep it opt-in so a fixture-only server cannot
    # accidentally become a release server. The orchestrator supplies both
    # inputs and requires all three scenario payloads to be v2/sequence 2.
    require((ota_package_root is None) == (ota_public_key is None),
            "OTA package root and signing key must be supplied together")
    if ota_package_root is not None:
        ota_root = pathlib.Path(ota_package_root)
        require(ota_root.is_dir() and not ota_root.is_symlink(),
                "OTA package root missing or symlink")
        ota_root = ota_root.resolve()
        verification = verify_set(ota_root, ota_public_key,
            expected_campaign_id=expected_campaign_id,
            expected_aot_toolchain_id=expected_aot_toolchain_id)
        require(verification.get("campaign_id") == campaign_id,
                "OTA package and fixture campaign differ")
        ota_index = _json_load(ota_root / "index.json", max_bytes=65536)
        names = {1: "firmware.bin", 2: "control.wasm", 3: "control.aot"}
        records = ota_index.get("artifacts")
        require(isinstance(records, list), "OTA package artifact index is invalid")
        selected = [record for record in records if isinstance(record, dict)
                    and record.get("label") == "v2"]
        require(len(selected) == 3, "OTA package must contain exactly three v2 artifacts")
        for record in selected:
            scenario = record.get("scenario")
            require(scenario in SCENARIOS and record.get("sequence") == 2
                    and record.get("release_version") == "v2",
                    "OTA package release must be signed sequence 2")
            expected_component = "firmware" if scenario == 1 else "gpio-control"
            base = pathlib.PurePosixPath(f"scenario-{scenario}") / expected_component / "v2"
            payload_rel = (base / names[scenario]).as_posix()
            envelope_rel = (base / "manifest.cose").as_posix()
            require(record.get("component") == expected_component
                    and record.get("payload_path") == payload_rel
                    and record.get("envelope_path") == envelope_rel,
                    "OTA package route differs from the normal signed-update contract")
            for relative, byte_count_field, digest_field, limit in (
                    (payload_rel, "payload_bytes", "payload_sha256", MAX_PAYLOAD[scenario]),
                    (envelope_rel, "envelope_bytes", "envelope_sha256", MAX_ENVELOPE)):
                path = ota_root.joinpath(*pathlib.PurePosixPath(relative).parts)
                data = _read_limited(path, limit, "OTA package artifact")
                require(len(data) == record.get(byte_count_field)
                        and hashlib.sha256(data).hexdigest() == record.get(digest_field),
                        "OTA package artifact digest/size differs from its signed index")
                require(relative not in allowed, "OTA package route collides with a fixture route")
                allowed[relative] = (path, len(data), hashlib.sha256(data).hexdigest())
    return root, campaign_id, allowed, index


class FixtureHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "WasmHomeIssue22Fixture/1"
    sys_version = ""

    def log_message(self, _format, *_args):
        return

    def do_GET(self):
        self._serve("GET")

    def do_HEAD(self):
        self._serve("HEAD")

    def do_POST(self):
        self.close_connection = True
        self._reply(405, b"method not allowed\n", "POST")

    def send_error(self, code, message=None, explain=None):
        if code == 501:
            self.close_connection = True
            return self._reply(405, b"method not allowed\n", self.command or "UNKNOWN")
        return super().send_error(code, message, explain)

    def _record(self, entry):
        encoded = (json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        with self.server.log_lock:
            descriptor = os.open(self.server.log_path,
                os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                self.server.log_mode)
            try:
                os.fchmod(descriptor, self.server.log_mode)
                view = memoryview(encoded)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError("short write to private HTTPS event log")
                    view = view[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def _reply(self, status, body, method, *, relative=None, expected_size=None, digest=None, started=None):
        server = self.server
        tls_protocol = self.connection.version() if isinstance(self.connection, ssl.SSLSocket) else None
        tls_cipher = self.connection.cipher()[0] if isinstance(self.connection, ssl.SSLSocket) and self.connection.cipher() else None
        bytes_sent = 0
        try:
            self.send_response(status)
            self.send_header("X-Content-Type-Options", "nosniff")
            if status == 200:
                self.send_header("Content-Type", {".aot":"application/vnd.wasmhome.aot", ".bin":"application/octet-stream", ".cose":'application/cose; cose-type="cose-sign1"', ".wasm":"application/wasm"}.get(pathlib.PurePosixPath(relative).suffix, "application/octet-stream"))
                self.send_header("Content-Length", str(expected_size))
                self.send_header("Cache-Control", "no-store")
                self.send_header("ETag", f'"sha256-{digest}"')
            else:
                if status == 405:
                    self.send_header("Allow", "GET, HEAD")
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if method != "HEAD" and body:
                self.wfile.write(body)
                bytes_sent = expected_size if status == 200 else len(body)
        finally:
            self._record({"schema":"wasmhome-issue22-fixture-http-v1", "time_unix_ns":time.time_ns(),
                "peer":self.client_address[0], "host":self.headers.get("Host", ""), "method":method,
                "path":relative, "status":status, "bytes_sent":bytes_sent,
                "sha256":digest if status == 200 else None, "tls_protocol":tls_protocol,
                "tls_cipher":tls_cipher, "duration_ns":max(0, time.monotonic_ns()-started) if started else 0})

    def _serve(self, method):
        started = time.monotonic_ns()
        try:
            parsed = urllib.parse.urlsplit(self.path)
        except ValueError:
            return self._reply(404, b"not found\n", method, started=started)
        host_values = self.headers.get_all("Host", [])
        if (parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or "%" in parsed.path or
                len(host_values) != 1 or not self.server.host_header_allowed(host_values[0])):
            return self._reply(404, b"not found\n", method, started=started)
        prefix = "/" + self.server.campaign_id + "/"
        if not parsed.path.startswith(prefix) or parsed.path.startswith("//"):
            return self._reply(404, b"not found\n", method, started=started)
        relative = parsed.path[len(prefix):]
        path = pathlib.PurePosixPath(relative)
        if not relative or path.is_absolute() or any(part in ("", ".", "..") for part in path.parts) or path.as_posix() != relative:
            return self._reply(404, b"not found\n", method, started=started)
        record = self.server.allowed.get(relative)
        if record is None:
            return self._reply(404, b"not found\n", method, relative=relative, started=started)
        file_path, expected_size, expected_digest = record
        try:
            body = read_file(file_path, MAX_PAYLOAD.get(next((s for s, name in PAYLOADS.items() if name == file_path.name), 0), 4096) if file_path.suffix != ".cose" else 4096)
            if len(body) != expected_size or hashlib.sha256(body).hexdigest() != expected_digest:
                return self._reply(409, b"published fixture changed\n", method, relative=relative, started=started)
        except (OSError, ValueError):
            return self._reply(404, b"not found\n", method, relative=relative, started=started)
        return self._reply(200, body, method, relative=relative, expected_size=expected_size,
                           digest=expected_digest, started=started)


class FixtureHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    request_timeout = 10

    def __init__(self, address, root, certificate, private_key, log_path, server_name,
                 server_ip, trusted_public, untrusted_public, campaign_id, aot_toolchain_id,
                 ota_package_root=None, ota_public_key=None, log_mode=0o640,
                 listener=None):
        validate_fixture_port(address[1])
        package_root, campaign, allowed, index = load_allowlist(
            root, trusted_public, untrusted_public, campaign_id, aot_toolchain_id,
            ota_package_root=ota_package_root, ota_public_key=ota_public_key)
        self.package_root = package_root
        self.campaign_id = campaign
        self.expected_server_name = server_name
        self.server_ip = server_ip
        self.allowed = allowed
        self.log_path = pathlib.Path(log_path).absolute()
        require(log_mode in (0o600, 0o640), "HTTP log mode must be 0600 or 0640")
        self.log_mode = log_mode
        require(self.log_path.parent.is_dir() and not self.log_path.parent.is_symlink(),
                "HTTP log directory is missing or symlink")
        require(not self.log_path.is_symlink(), "HTTP log path is symlink")
        self.log_lock = threading.Lock()
        cert_path, key_path = pathlib.Path(certificate), pathlib.Path(private_key)
        require(cert_path.is_file() and not cert_path.is_symlink(), "TLS certificate missing or symlink")
        require(key_path.is_file() and not key_path.is_symlink(), "TLS key missing or symlink")
        require(key_path.stat().st_mode & 0o077 == 0, "TLS private key permissions must exclude group and other")
        if listener is None:
            super().__init__(address, FixtureHandler)
        else:
            # Wrap the HTTP server without bind/listen and adopt the socket held
            # throughout staging. The manager remains the only process owner.
            super().__init__(address, FixtureHandler, bind_and_activate=False)
            self.socket.close()
            self.socket = listener
            self.server_address = listener.getsockname()
        self.server_port = self.server_address[1]
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        self.socket = context.wrap_socket(self.socket, server_side=True, do_handshake_on_connect=False)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(self.request_timeout)
        return connection, address

    def process_request_thread(self, request, client_address):
        try:
            request.do_handshake()
        except OSError:
            self.shutdown_request(request)
            return
        super().process_request_thread(request, client_address)

    def host_header_allowed(self, value):
        if not value or any(character.isspace() for character in value) or any(character in "/?#@%\\" for character in value):
            return False
        authority = value.lower()
        hosts = {self.expected_server_name.lower().rstrip("."), self.server_ip.lower()}
        return authority in hosts or authority in {f"{host}:{self.server_port}" for host in hosts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-root", type=pathlib.Path, required=True)
    parser.add_argument("--trusted-public-key", type=pathlib.Path, required=True)
    parser.add_argument("--untrusted-public-key", type=pathlib.Path, required=True)
    parser.add_argument("--certificate", type=pathlib.Path, required=True)
    parser.add_argument("--private-key", type=pathlib.Path, required=True)
    parser.add_argument("--log", type=pathlib.Path, required=True)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--server-name", required=True)
    parser.add_argument("--server-ip", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--aot-toolchain-id", required=True)
    parser.add_argument("--ota-package-root", type=pathlib.Path,
                        help="optional verified signed-artifacts package; only v2/sequence-2 routes are served")
    parser.add_argument("--ota-public-key", type=pathlib.Path,
                        help="trusted public key for the optional signed OTA package")
    parser.add_argument("--log-mode", type=lambda value: int(value, 8), default=0o640,
                        help="private append log mode: 600 or 640")
    parser.add_argument("--pid-file", type=pathlib.Path,
                        help="private receipt written after adopting the inherited HTTPS socket")
    parser.add_argument("--listen-fd", type=int,
                        help="manager-only inherited listener descriptor (always 3)")
    parser.add_argument("--check", action="store_true", help="validate immutable catalog/TLS config without binding")
    args = parser.parse_args()
    try:
        validate_fixture_port(args.port)
    except ValueError as error:
        parser.error(str(error))
    require(args.log_mode in (0o600, 0o640), "HTTP log mode must be 0600 or 0640")
    require(ipaddress.ip_address(args.server_ip).version == 4, "server IP must be IPv4")
    # Constructing the server also opens the socket; --check performs the same
    # package and TLS validations without opening a listener.
    if args.check:
        root, campaign, allowed, index = load_allowlist(args.fixture_root,
            args.trusted_public_key, args.untrusted_public_key, args.campaign_id,
            args.aot_toolchain_id, ota_package_root=args.ota_package_root,
            ota_public_key=args.ota_public_key)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certfile=str(args.certificate), keyfile=str(args.private_key))
        require(args.log.parent.is_dir() and not args.log.parent.is_symlink(), "HTTP log directory missing")
        require(not args.log.is_symlink(), "HTTP log path is a symlink")
        print(json.dumps({"status":"validated", "campaign_id":campaign, "fixtures":len(allowed)//2,
            "bind":args.bind, "port":args.port, "server_name":args.server_name,
            "server_ip":args.server_ip, "trusted_public_key_sha256":index["trusted_fixture_public_key_sha256"]}, sort_keys=True))
        return 0
    require(args.listen_fd == 3 and args.pid_file is not None,
            "managed fixture startup requires inherited listener descriptor 3 and PID receipt")
    listener = adopt_fixture_listener(args.listen_fd, args.bind, args.port)
    server = None
    try:
        server = FixtureHTTPServer((args.bind, args.port), args.fixture_root,
            args.certificate, args.private_key, args.log, args.server_name, args.server_ip,
            args.trusted_public_key, args.untrusted_public_key, args.campaign_id,
            args.aot_toolchain_id, ota_package_root=args.ota_package_root,
            ota_public_key=args.ota_public_key, log_mode=args.log_mode, listener=listener)
        listener = None  # FixtureHTTPServer owns the adopted descriptor now.
        write_server_pid_receipt(args.pid_file)
    except BaseException:
        if server is not None:
            server.server_close()
        elif listener is not None:
            listener.close()
        raise
    print(json.dumps({"status":"listening", "bind":args.bind, "port":args.port,
        "campaign_id":server.campaign_id, "fixtures":len(server.allowed)//2}, sort_keys=True), flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
