"""Host-side codec for the WasmHome UDP observation v1 contract."""

from dataclasses import dataclass
from enum import IntEnum
import struct
import uuid


MAGIC = b"WHOB"
VERSION = 1
MAX_DATAGRAM_SIZE = 1200
HEADER = struct.Struct("!4sBBH16sQQQ")
HEADER_SIZE = HEADER.size
NIL_UUID = uuid.UUID(int=0)
INT32_MIN = -(2**31)


class MessageType(IntEnum):
    TELEMETRY = 0x01
    CONTROL_REQUEST = 0x02
    CONTROL_RESULT = 0x03
    UPDATE_TRIGGER = 0x10
    UPDATE_ACCEPTANCE = 0x11


class SensorStatus(IntEnum):
    VALID = 0
    CRC_ERROR = 1
    DISCONNECTED = 2
    NOT_READY = 3
    NO_VALID_SAMPLE = 4


class ControlStatus(IntEnum):
    APPLIED = 0
    MISMATCH = 1
    NOT_APPLICABLE = 2
    EXECUTION_ERROR = 3


class Scenario(IntEnum):
    NATIVE = 1
    INTERPRETER = 2
    AOT = 3


class Component(IntEnum):
    CONTROL = 1


class AcceptanceStatus(IntEnum):
    ACCEPTED = 1
    REJECTED = 2


class RejectionReason(IntEnum):
    NONE = 0
    WRONG_BOOT = 1
    SCENARIO_MISMATCH = 2
    UNSUPPORTED_COMPONENT = 3
    UNSUPPORTED_VERSION = 4
    SEQUENCE_NOT_NEWER = 5
    BUSY = 6
    ATTEMPT_CONFLICT = 7


BODY_STRUCTS = {
    MessageType.TELEMETRY: struct.Struct("!QiHBB"),
    MessageType.CONTROL_REQUEST: struct.Struct("!Qi"),
    MessageType.CONTROL_RESULT: struct.Struct("!QBBBB"),
    MessageType.UPDATE_TRIGGER: struct.Struct("!QBBH"),
    MessageType.UPDATE_ACCEPTANCE: struct.Struct("!QQBBH"),
}


@dataclass(frozen=True)
class Packet:
    message_type: MessageType
    attempt_id: uuid.UUID
    sequence: int
    boot_id: int
    monotonic_us: int
    body: dict[str, int]


class ProtocolError(ValueError):
    """Raised when a datagram cannot be accepted as a v1 protocol message."""


def _uint(name: str, value: int, bits: int, *, allow_zero: bool = True) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProtocolError(f"{name} must be an integer")
    minimum = 0 if allow_zero else 1
    if not minimum <= value < 2**bits:
        raise ProtocolError(f"{name} is outside uint{bits} range")
    return value


def _sint(name: str, value: int, bits: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProtocolError(f"{name} must be an integer")
    if not -(2 ** (bits - 1)) <= value < 2 ** (bits - 1):
        raise ProtocolError(f"{name} is outside int{bits} range")
    return value


def _enum(name: str, value: int, enum_type: type[IntEnum]) -> int:
    _uint(name, value, 8)
    try:
        return int(enum_type(value))
    except ValueError as exc:
        raise ProtocolError(f"unknown {name}: {value}") from exc


def _validate_header(packet: Packet) -> MessageType:
    try:
        message_type = MessageType(packet.message_type)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"unknown message type: {packet.message_type}") from exc
    if not isinstance(packet.attempt_id, uuid.UUID):
        raise ProtocolError("attempt_id must be a UUID")
    _uint("sequence", packet.sequence, 64, allow_zero=False)
    _uint("boot_id", packet.boot_id, 64, allow_zero=False)
    _uint("monotonic_us", packet.monotonic_us, 64)
    if message_type in (MessageType.UPDATE_TRIGGER, MessageType.UPDATE_ACCEPTANCE) and packet.attempt_id == NIL_UUID:
        raise ProtocolError("update messages require a non-nil attempt_id")
    return message_type


def _encode_body(message_type: MessageType, body: dict[str, int]) -> bytes:
    try:
        if message_type == MessageType.TELEMETRY:
            sample_id = _uint("sample_id", body["sample_id"], 64)
            temperature = _sint("temperature_mC", body["temperature_mC"], 32)
            age = _uint("age_ms", body["age_ms"], 16)
            status = _enum("sensor_status", body["sensor_status"], SensorStatus)
            if sample_id == 0:
                if temperature != INT32_MIN or age != 0xFFFF or status == SensorStatus.VALID:
                    raise ProtocolError("no-sample telemetry requires INT32_MIN and age 0xFFFF")
            elif not -55_000 <= temperature <= 125_000 or age == 0xFFFF or status == SensorStatus.NO_VALID_SAMPLE:
                raise ProtocolError("sample telemetry has invalid temperature or age")
            return BODY_STRUCTS[message_type].pack(sample_id, temperature, age, status, 0)

        if message_type == MessageType.CONTROL_REQUEST:
            sample_id = _uint("sample_id", body["sample_id"], 64, allow_zero=False)
            temperature = _sint("input_temperature_mC", body["input_temperature_mC"], 32)
            if not -55_000 <= temperature <= 125_000:
                raise ProtocolError("input_temperature_mC is outside the DS18B20 range")
            return BODY_STRUCTS[message_type].pack(sample_id, temperature)

        if message_type == MessageType.CONTROL_RESULT:
            sample_id = _uint("sample_id", body["sample_id"], 64, allow_zero=False)
            version = _uint("module_version", body["module_version"], 8, allow_zero=False)
            if version not in (1, 2):
                raise ProtocolError("module_version must be 1 or 2")
            requested = _uint("requested_gpio", body["requested_gpio"], 8)
            observed = _uint("observed_gpio", body["observed_gpio"], 8)
            status = _enum("control_status", body["status"], ControlStatus)
            allowed = (0, 1, 255)
            if requested not in allowed or observed not in allowed:
                raise ProtocolError("GPIO states must be 0, 1, or 255 (not applicable)")
            if status == ControlStatus.APPLIED and (requested not in (0, 1) or observed != requested):
                raise ProtocolError("applied result requires matching binary GPIO states")
            if status == ControlStatus.MISMATCH and (requested not in (0, 1) or observed not in (0, 1) or observed == requested):
                raise ProtocolError("mismatch result requires different binary GPIO states")
            if status == ControlStatus.NOT_APPLICABLE and (requested, observed) != (255, 255):
                raise ProtocolError("not-applicable result requires both GPIO states to be 255")
            if status == ControlStatus.EXECUTION_ERROR and observed != 255:
                raise ProtocolError("execution-error result requires observed GPIO 255")
            return BODY_STRUCTS[message_type].pack(sample_id, version, requested, observed, status)

        if message_type == MessageType.UPDATE_TRIGGER:
            target_sequence = _uint("target_sequence", body["target_sequence"], 64, allow_zero=False)
            scenario = _enum("scenario", body["scenario"], Scenario)
            component = _uint("component", body["component"], 8, allow_zero=False)
            target_version = _uint("target_version", body["target_version"], 16, allow_zero=False)
            return BODY_STRUCTS[message_type].pack(target_sequence, scenario, component, target_version)

        if message_type == MessageType.UPDATE_ACCEPTANCE:
            request_sequence = _uint("request_sequence", body["request_sequence"], 64, allow_zero=False)
            target_sequence = _uint("target_sequence", body["target_sequence"], 64, allow_zero=False)
            status = _enum("acceptance_status", body["status"], AcceptanceStatus)
            reason = _enum("rejection_reason", body["reason"], RejectionReason)
            if status == AcceptanceStatus.ACCEPTED and reason != RejectionReason.NONE:
                raise ProtocolError("accepted message requires rejection reason NONE")
            if status == AcceptanceStatus.REJECTED and reason == RejectionReason.NONE:
                raise ProtocolError("rejected message requires a rejection reason")
            return BODY_STRUCTS[message_type].pack(request_sequence, target_sequence, status, reason, 0)

    except KeyError as exc:
        raise ProtocolError(f"missing {message_type.name} field: {exc.args[0]}") from exc
    except struct.error as exc:
        raise ProtocolError(f"invalid {message_type.name} body") from exc
    raise ProtocolError(f"unsupported message type: {message_type}")


def encode(packet: Packet) -> bytes:
    message_type = _validate_header(packet)
    body = _encode_body(message_type, packet.body)
    if len(body) != BODY_STRUCTS[message_type].size:
        raise ProtocolError("body length does not match v1 message schema")
    datagram = HEADER.pack(
        MAGIC,
        VERSION,
        int(message_type),
        len(body),
        packet.attempt_id.bytes,
        packet.sequence,
        packet.boot_id,
        packet.monotonic_us,
    ) + body
    if len(datagram) > MAX_DATAGRAM_SIZE:
        raise ProtocolError("datagram exceeds v1 maximum size")
    return datagram


def _decode_body(message_type: MessageType, raw: bytes) -> dict[str, int]:
    if len(raw) != BODY_STRUCTS[message_type].size:
        raise ProtocolError(f"{message_type.name} body length must be {BODY_STRUCTS[message_type].size}")
    values = BODY_STRUCTS[message_type].unpack(raw)
    if message_type == MessageType.TELEMETRY:
        sample_id, temperature, age, status, reserved = values
        if reserved != 0:
            raise ProtocolError("telemetry reserved byte must be zero")
        body = {"sample_id": sample_id, "temperature_mC": temperature, "age_ms": age, "sensor_status": status}
    elif message_type == MessageType.CONTROL_REQUEST:
        sample_id, temperature = values
        body = {"sample_id": sample_id, "input_temperature_mC": temperature}
    elif message_type == MessageType.CONTROL_RESULT:
        sample_id, version, requested, observed, status = values
        body = {"sample_id": sample_id, "module_version": version, "requested_gpio": requested, "observed_gpio": observed, "status": status}
    elif message_type == MessageType.UPDATE_TRIGGER:
        target_sequence, scenario, component, target_version = values
        body = {"target_sequence": target_sequence, "scenario": scenario, "component": component, "target_version": target_version}
    else:
        request_sequence, target_sequence, status, reason, reserved = values
        if reserved != 0:
            raise ProtocolError("update acceptance reserved field must be zero")
        body = {"request_sequence": request_sequence, "target_sequence": target_sequence, "status": status, "reason": reason}
    _encode_body(message_type, body)
    return body


def decode(datagram: bytes) -> Packet:
    if len(datagram) > MAX_DATAGRAM_SIZE:
        raise ProtocolError("datagram exceeds v1 maximum size")
    if len(datagram) < HEADER_SIZE:
        raise ProtocolError("datagram is shorter than the v1 header")
    magic, version, raw_type, body_length, attempt_bytes, sequence, boot_id, monotonic_us = HEADER.unpack_from(datagram)
    if magic != MAGIC:
        raise ProtocolError("invalid magic")
    if version != VERSION:
        raise ProtocolError(f"unsupported protocol version: {version}")
    try:
        message_type = MessageType(raw_type)
    except ValueError as exc:
        raise ProtocolError(f"unknown message type: {raw_type}") from exc
    if len(datagram) != HEADER_SIZE + body_length:
        raise ProtocolError("datagram length does not match header body_length")
    packet = Packet(
        message_type=message_type,
        attempt_id=uuid.UUID(bytes=attempt_bytes),
        sequence=sequence,
        boot_id=boot_id,
        monotonic_us=monotonic_us,
        body={},
    )
    _validate_header(packet)
    body = _decode_body(message_type, datagram[HEADER_SIZE:])
    return Packet(**{**packet.__dict__, "body": body})
