"""Temporary HTTP transport fixture. No TLS, artifacts, service or COSE claims."""
import argparse
import datetime
import ipaddress
import json
import re
import select
import socket
import time

PORT = 18080
MAX_REQUEST = 1024


def response(nonce, peer):
    body = b"WH_LXC_OK " + nonce + b" " + peer.encode("ascii") + b"\n"
    return (b"HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\nContent-Length: "
            + str(len(body)).encode("ascii") + b"\r\nConnection: close\r\n\r\n" + body)


def validate_response(raw, nonce, peer):
    """Mirror the firmware's exact response contract for local negative tests."""
    return raw == response(nonce, peer)


def handle_connection(connection, address, expected_peer, timeout=2.0):
    record = {"event": "request", "source_ipv4": address[0], "source_port": address[1],
              "server_ipv4": connection.getsockname()[0], "server_port": connection.getsockname()[1]}
    started = time.monotonic()
    deadline = started + timeout
    record["utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        if address[0] != expected_peer:
            record["result"] = "rejected_source"
            connection.settimeout(max(0.001, deadline - time.monotonic()))
            connection.sendall(b"HTTP/1.0 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            return record
        raw = bytearray()
        while b"\r\n\r\n" not in raw:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("request deadline")
            connection.settimeout(remaining)
            chunk = connection.recv(MAX_REQUEST + 1 - len(raw))
            if not chunk:
                record["result"] = "incomplete_request"
                return record
            raw.extend(chunk)
            if len(raw) > MAX_REQUEST:
                record["result"] = "oversized_request"
                return record
        first = bytes(raw).split(b"\r\n", 1)[0]
        match = re.fullmatch(rb"GET /wasmhome-lxc/([0-9a-f]{64}) HTTP/1\.0", first)
        if not match or bytes(raw).split(b"\r\n\r\n", 1)[1]:
            record["result"] = "rejected_request"
            connection.settimeout(max(0.001, deadline - time.monotonic()))
            connection.sendall(b"HTTP/1.0 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            return record
        nonce = match.group(1)
        connection.settimeout(max(0.001, deadline - time.monotonic()))
        connection.sendall(response(nonce, address[0]))
        record.update(result="response_sent", nonce=nonce.decode("ascii"))
    except TimeoutError:
        record["result"] = "timeout"
    except OSError as error:
        record.update(result="socket_error", errno=error.errno)
    finally:
        record["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        connection.close()
    return record


def serve(bind, expected_peer, duration=300, emit=print):
    for configured in (bind, expected_peer):
        address = ipaddress.IPv4Address(configured)
        if address.is_unspecified or address.is_multicast or address == ipaddress.IPv4Address("255.255.255.255"):
            raise ValueError("fixture requires concrete unicast IPv4 endpoints")
    if not 1 <= duration <= 600:
        raise ValueError("duration must be between 1 and 600 seconds")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((bind, PORT))
        listener.listen(4)
        emit(json.dumps({"event": "listening", "bind": bind, "port": PORT,
                         "expected_peer": expected_peer, "duration_seconds": duration}))
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            if not select.select([listener], [], [], max(0, deadline - time.monotonic()))[0]:
                break
            connection, address = listener.accept()
            emit(json.dumps(handle_connection(connection, address, expected_peer,
                                               min(2.0, max(0.001, deadline - time.monotonic())))))
        emit(json.dumps({"event": "stopped", "reason": "fixture_duration_complete"}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", required=True, help="LXC IPv4, not a URL")
    parser.add_argument("--expected-peer", required=True, help="ESP32 IPv4")
    parser.add_argument("--duration", type=int, default=300)
    args = parser.parse_args()
    serve(args.bind, args.expected_peer, args.duration, lambda value: print(value, flush=True))


if __name__ == "__main__":
    main()
