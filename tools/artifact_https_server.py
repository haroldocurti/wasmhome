#!/usr/bin/env python3
"""Serve one verified, immutable issue #15 package over HTTPS."""

import argparse
import hashlib
import http.server
import ipaddress
import json
import os
import pathlib
import ssl
import threading
import time
import urllib.parse

from signed_artifacts import _json_load, verify_set


MIME_TYPES = {
    ".aot": "application/vnd.wasmhome.aot",
    ".bin": "application/octet-stream",
    ".cose": 'application/cose; cose-type="cose-sign1"',
    ".der": "application/octet-stream",
    ".json": "application/json",
    ".wasm": "application/wasm",
}


def require(value, message):
    if not value:
        raise ValueError(message)


def load_allowlist(root, signing_public_key, expected_campaign_id, expected_aot_toolchain_id):
    root = pathlib.Path(root)
    require(root.is_dir() and not root.is_symlink(), "package root is missing or is a symlink")
    root = root.resolve()
    index = _json_load(root / "index.json", max_bytes=65536)
    verify_set(root, signing_public_key, expected_campaign_id=expected_campaign_id,
               expected_aot_toolchain_id=expected_aot_toolchain_id)
    allowed = {"index.json", "signing-public.der"}
    for record in index["artifacts"]:
        for name in (record["payload_path"], record["envelope_path"]):
            path = pathlib.PurePosixPath(name)
            require(not path.is_absolute() and ".." not in path.parts, "artifact path escapes package root")
            allowed.add(path.as_posix())
    result = {}
    for relative in sorted(allowed):
        path = root.joinpath(*pathlib.PurePosixPath(relative).parts)
        require(path.is_file() and not path.is_symlink(), "allowlisted artifact is missing or a symlink")
        result[relative] = (path, path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
    return root, index, result


def _record(log_path, lock, entry):
    encoded = (json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    with lock:
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(log_path, flags, 0o640)
        try:
            os.write(descriptor, encoded)
        finally:
            os.close(descriptor)


class ArtifactHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "WasmHomeArtifact/1"
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
        # BaseHTTPRequestHandler otherwise emits an unlogged HTML 501 response
        # for methods outside the explicit GET/HEAD/POST handlers.
        if code == 501:
            self.close_connection = True
            return self._reply(405, b"method not allowed\n", self.command or "UNKNOWN")
        return super().send_error(code, message, explain)

    def _reply(self, status, body, method, *, relative=None, expected_size=None, digest=None, started=None):
        host = self.headers.get("Host", "")
        server = self.server
        tls_protocol = self.connection.version() if isinstance(self.connection, ssl.SSLSocket) else None
        tls_cipher = self.connection.cipher()[0] if isinstance(self.connection, ssl.SSLSocket) and self.connection.cipher() else None
        bytes_sent = 0
        complete = False
        try:
            self.send_response(status)
            self.send_header("X-Content-Type-Options", "nosniff")
            if status == 200:
                self.send_header("Content-Type", MIME_TYPES.get(pathlib.PurePosixPath(relative).suffix, "application/octet-stream"))
                self.send_header("Content-Length", str(expected_size))
                self.send_header("Cache-Control", "public, max-age=31536000, immutable")
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
            complete = True
        finally:
            _record(server.log_path, server.log_lock, {
                "schema": "wasmhome-issue15-http-event-v1",
                "time_unix_ns": time.time_ns(),
                "peer": self.client_address[0],
                "host": host,
                "method": method,
                "path": relative,
                "status": status,
                "bytes_sent": bytes_sent,
                "complete": complete,
                "sha256": digest if status == 200 else None,
                "tls_protocol": tls_protocol,
                "tls_cipher": tls_cipher,
                "duration_ns": max(0, time.monotonic_ns() - started) if started is not None else 0,
            })

    def _serve(self, method):
        started = time.monotonic_ns()
        raw_path = self.path
        try:
            parsed = urllib.parse.urlsplit(raw_path)
        except ValueError:
            return self._reply(404, b"not found\n", method, started=started)
        if parsed.scheme or parsed.netloc or not parsed.path.startswith("/") or parsed.path.startswith("//"):
            return self._reply(404, b"not found\n", method, started=started)
        host_values = self.headers.get_all("Host", [])
        if len(host_values) != 1 or not self.server.host_header_allowed(host_values[0]):
            return self._reply(421, b"misdirected request\n", method, started=started)
        prefix = "/" + self.server.campaign_id + "/"
        if parsed.query or parsed.fragment or "%" in parsed.path or not parsed.path.startswith(prefix):
            return self._reply(404, b"not found\n", method, started=started)
        relative = parsed.path[len(prefix):]
        path = pathlib.PurePosixPath(relative)
        if not relative or path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
            return self._reply(404, b"not found\n", method, started=started)
        if path.as_posix() != relative:
            return self._reply(404, b"not found\n", method, started=started)
        relative = path.as_posix()
        record = self.server.allowed.get(relative)
        if record is None:
            return self._reply(404, b"not found\n", method, relative=relative, started=started)
        file_path, expected_size, expected_digest = record
        try:
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(file_path, flags)
            with os.fdopen(descriptor, "rb") as stream:
                body = stream.read(expected_size + 1)
            if len(body) != expected_size or hashlib.sha256(body).hexdigest() != expected_digest:
                return self._reply(409, b"published artifact changed\n", method, relative=relative, started=started)
        except OSError:
            return self._reply(404, b"not found\n", method, relative=relative, started=started)
        if self.headers.get("If-None-Match") == f'"sha256-{expected_digest}"':
            self.send_response(304)
            self.send_header("ETag", f'"sha256-{expected_digest}"')
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return _record(self.server.log_path, self.server.log_lock, {
                "schema": "wasmhome-issue15-http-event-v1", "time_unix_ns": time.time_ns(),
                "peer": self.client_address[0], "host": self.headers.get("Host", ""), "method": method,
                "path": relative, "status": 304, "bytes_sent": 0, "sha256": expected_digest,
                "complete": True,
                "tls_protocol": self.connection.version(), "tls_cipher": self.connection.cipher()[0],
                "duration_ns": max(0, time.monotonic_ns() - started),
            })
        return self._reply(200, body, method, relative=relative, expected_size=expected_size,
                           digest=expected_digest, started=started)


class ArtifactHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, root, cert, key, log_path, server_name, server_ip,
                 signing_public_key, expected_campaign_id, expected_aot_toolchain_id):
        package_root, index, allowed = load_allowlist(root, signing_public_key,
                                                      expected_campaign_id, expected_aot_toolchain_id)
        self.package_root = package_root
        self.campaign_id = index["campaign_id"]
        self.server_name = server_name
        self.server_ip = server_ip
        self.allowed = allowed
        self.artifact_count = len(index["artifacts"])
        self.log_path = pathlib.Path(log_path).absolute()
        require(self.log_path.parent.is_dir(), "HTTP log directory is missing")
        require(not self.log_path.is_symlink(), "HTTP log path is a symlink")
        self.log_lock = threading.Lock()
        cert_path, key_path = pathlib.Path(cert), pathlib.Path(key)
        require(cert_path.is_file() and not cert_path.is_symlink(), "TLS certificate is missing or a symlink")
        require(key_path.is_file() and not key_path.is_symlink(), "TLS private key is missing or a symlink")
        require(key_path.stat().st_mode & 0o077 == 0, "TLS private key permissions must exclude group and other")
        super().__init__(address, ArtifactHandler)
        self.server_port = self.server_address[1]
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        self.socket = context.wrap_socket(self.socket, server_side=True)

    def host_header_allowed(self, value):
        if not value or any(character.isspace() for character in value) or any(
                character in "/?#@%\\" for character in value):
            return False
        authority = value.lower()
        hosts = {self.server_name.lower().rstrip("."), self.server_ip.lower()}
        return authority in hosts or authority in {f"{host}:{self.server_port}" for host in hosts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=pathlib.Path, required=True)
    parser.add_argument("--certificate", type=pathlib.Path, required=True)
    parser.add_argument("--private-key", type=pathlib.Path, required=True)
    parser.add_argument("--signing-public-key", type=pathlib.Path, required=True)
    parser.add_argument("--log", type=pathlib.Path, required=True)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--server-name", required=True)
    parser.add_argument("--server-ip", required=True)
    parser.add_argument("--expected-campaign-id", required=True)
    parser.add_argument("--expected-aot-toolchain-id", required=True)
    args = parser.parse_args()
    require(1024 <= args.port <= 65535, "server port must be unprivileged")
    require(ipaddress.ip_address(args.server_ip).version == 4, "server-ip must be IPv4")
    server = ArtifactHTTPServer((args.bind, args.port), args.package, args.certificate,
                                args.private_key, args.log, args.server_name, args.server_ip,
                                args.signing_public_key, args.expected_campaign_id,
                                args.expected_aot_toolchain_id)
    print(json.dumps({"status": "listening", "bind": args.bind, "port": args.port,
                      "campaign_id": server.campaign_id, "artifacts": server.artifact_count}), flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
