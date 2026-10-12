"""Bộ mã hoá và giải mã Protobuf viết tay cho giao thức Muse.

Không phụ thuộc vào thư viện google.protobuf ngoài; hỗ trợ varint, length-delimited,
int64, uint32, bool, string, bytes, enum, message nhúng và oneof.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional


# ── Kiểu dây Protobuf ──────────────────────────────────────────────────
WIRE_VARINT = 0
WIRE_64BIT = 1
WIRE_LENGTH_DELIMITED = 2
WIRE_32BIT = 5


def encode_varint(val: int) -> bytes:
    """Mã hoá số nguyên thành varint protobuf."""
    if val < 0:
        val += (1 << 64)
    out = bytearray()
    while True:
        b = val & 0x7F
        val >>= 7
        if val != 0:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def decode_varint(buf: bytes, offset: int = 0) -> tuple[int, int]:
    """Giải mã varint từ buffer bắt đầu tại offset -> (giá trị, offset_tiếp)."""
    val = 0
    shift = 0
    pos = offset
    while pos < len(buf):
        b = buf[pos]
        pos += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, pos
        shift += 7
        if shift > 70:
            raise ValueError("Varint quá dài hoặc lỗi cấu trúc")
    raise ValueError("Dữ liệu kết thúc giữa chừng khi đọc varint")


def to_signed_int64(v: int) -> int:
    """Chuyển số nguyên 64-bit không dấu thành có dấu."""
    if v >= (1 << 63):
        return v - (1 << 64)
    return v


def encode_tag(field_number: int, wire_type: int) -> bytes:
    """Mã hoá tag (field_number + wire_type)."""
    return encode_varint((field_number << 3) | wire_type)


def encode_length_delimited(field_number: int, data: bytes) -> bytes:
    """Mã hoá trường kiểu độ dài xác định (string, bytes, submessage)."""
    return encode_tag(field_number, WIRE_LENGTH_DELIMITED) + encode_varint(len(data)) + data


# ── Enum định nghĩa trong lược đồ ──────────────────────────────────────

class ServiceType(IntEnum):
    SERVICE_DAEMON = 0
    SERVICE_SENTINEL = 1
    SERVICE_VAULT = 2
    SERVICE_AUTHD = 3
    SERVICE_SSH_ACCESS = 4


class ResetCode(IntEnum):
    CODE_UNSPECIFIED = 0
    CANCELLED = 1
    TIMEOUT = 2
    PROTOCOL_ERROR = 3
    REFUSED_STREAM = 4
    INTERNAL_ERROR = 5
    SERVICE_UNAVAILABLE = 6


# ── Cấu trúc dữ liệu và logic tuần tự hoá ──────────────────────────────

@dataclass
class NoiseTransportFrame:
    chunk_id: int = 0
    chunk_index: int = 0
    total_chunks: int = 1
    payload: bytes = b""

    def encode(self) -> bytes:
        out = bytearray()
        if self.chunk_id != 0:
            out.extend(encode_tag(1, WIRE_VARINT) + encode_varint(self.chunk_id))
        if self.chunk_index != 0:
            out.extend(encode_tag(2, WIRE_VARINT) + encode_varint(self.chunk_index))
        if self.total_chunks != 0:
            out.extend(encode_tag(3, WIRE_VARINT) + encode_varint(self.total_chunks))
        if self.payload:
            out.extend(encode_length_delimited(4, self.payload))
        return bytes(out)

    @classmethod
    def decode(cls, data: bytes) -> NoiseTransportFrame:
        obj = cls()
        pos = 0
        while pos < len(data):
            tag, pos = decode_varint(data, pos)
            fn = tag >> 3
            wt = tag & 7
            if fn == 1 and wt == WIRE_VARINT:
                v, pos = decode_varint(data, pos)
                obj.chunk_id = to_signed_int64(v)
            elif fn == 2 and wt == WIRE_VARINT:
                v, pos = decode_varint(data, pos)
                obj.chunk_index = v
            elif fn == 3 and wt == WIRE_VARINT:
                v, pos = decode_varint(data, pos)
                obj.total_chunks = v
            elif fn == 4 and wt == WIRE_LENGTH_DELIMITED:
                length, pos = decode_varint(data, pos)
                obj.payload = data[pos:pos + length]
                pos += length
            else:
                pos = _skip_field(data, pos, wt)
        return obj


@dataclass
class Header:
    key: str = ""
    value: str = ""

    def encode(self) -> bytes:
        out = bytearray()
        if self.key:
            b = self.key.encode("utf-8")
            out.extend(encode_length_delimited(1, b))
        if self.value:
            b = self.value.encode("utf-8")
            out.extend(encode_length_delimited(2, b))
        return bytes(out)

    @classmethod
    def decode(cls, data: bytes) -> Header:
        obj = cls()
        pos = 0
        while pos < len(data):
            tag, pos = decode_varint(data, pos)
            fn = tag >> 3
            wt = tag & 7
            if fn == 1 and wt == WIRE_LENGTH_DELIMITED:
                length, pos = decode_varint(data, pos)
                obj.key = data[pos:pos + length].decode("utf-8", errors="replace")
                pos += length
            elif fn == 2 and wt == WIRE_LENGTH_DELIMITED:
                length, pos = decode_varint(data, pos)
                obj.value = data[pos:pos + length].decode("utf-8", errors="replace")
                pos += length
            else:
                pos = _skip_field(data, pos, wt)
        return obj


@dataclass
class ApplicationRequest:
    verb: str = "GET"
    path: str = "/"
    headers: list[Header] = field(default_factory=list)
    body: bytes = b""
    end_body: bool = True

    def encode(self) -> bytes:
        out = bytearray()
        if self.verb:
            out.extend(encode_length_delimited(1, self.verb.encode("utf-8")))
        if self.path:
            out.extend(encode_length_delimited(2, self.path.encode("utf-8")))
        for h in self.headers:
            out.extend(encode_length_delimited(3, h.encode()))
        if self.body:
            out.extend(encode_length_delimited(4, self.body))
        if self.end_body:
            out.extend(encode_tag(5, WIRE_VARINT) + encode_varint(1 if self.end_body else 0))
        return bytes(out)

    @classmethod
    def decode(cls, data: bytes) -> ApplicationRequest:
        obj = cls(verb="", path="", headers=[], body=b"", end_body=False)
        pos = 0
        while pos < len(data):
            tag, pos = decode_varint(data, pos)
            fn = tag >> 3
            wt = tag & 7
            if fn == 1 and wt == WIRE_LENGTH_DELIMITED:
                l, pos = decode_varint(data, pos)
                obj.verb = data[pos:pos + l].decode("utf-8", errors="replace")
                pos += l
            elif fn == 2 and wt == WIRE_LENGTH_DELIMITED:
                l, pos = decode_varint(data, pos)
                obj.path = data[pos:pos + l].decode("utf-8", errors="replace")
                pos += l
            elif fn == 3 and wt == WIRE_LENGTH_DELIMITED:
                l, pos = decode_varint(data, pos)
                obj.headers.append(Header.decode(data[pos:pos + l]))
                pos += l
            elif fn == 4 and wt == WIRE_LENGTH_DELIMITED:
                l, pos = decode_varint(data, pos)
                obj.body = data[pos:pos + l]
                pos += l
            elif fn == 5 and wt == WIRE_VARINT:
                v, pos = decode_varint(data, pos)
                obj.end_body = bool(v)
            else:
                pos = _skip_field(data, pos, wt)
        return obj


@dataclass
class ApplicationResponse:
    status: int = 200
    headers: list[Header] = field(default_factory=list)
    body: bytes = b""
    end_body: bool = True

    def encode(self) -> bytes:
        out = bytearray()
        if self.status != 0:
            out.extend(encode_tag(1, WIRE_VARINT) + encode_varint(self.status))
        for h in self.headers:
            out.extend(encode_length_delimited(2, h.encode()))
        if self.body:
            out.extend(encode_length_delimited(3, self.body))
        if self.end_body:
            out.extend(encode_tag(4, WIRE_VARINT) + encode_varint(1 if self.end_body else 0))
        return bytes(out)

    @classmethod
    def decode(cls, data: bytes) -> ApplicationResponse:
        obj = cls(status=0, headers=[], body=b"", end_body=False)
        pos = 0
        while pos < len(data):
            tag, pos = decode_varint(data, pos)
            fn = tag >> 3
            wt = tag & 7
            if fn == 1 and wt == WIRE_VARINT:
                v, pos = decode_varint(data, pos)
                obj.status = v
            elif fn == 2 and wt == WIRE_LENGTH_DELIMITED:
                l, pos = decode_varint(data, pos)
                obj.headers.append(Header.decode(data[pos:pos + l]))
                pos += l
            elif fn == 3 and wt == WIRE_LENGTH_DELIMITED:
                l, pos = decode_varint(data, pos)
                obj.body = data[pos:pos + l]
                pos += l
            elif fn == 4 and wt == WIRE_VARINT:
                v, pos = decode_varint(data, pos)
                obj.end_body = bool(v)
            else:
                pos = _skip_field(data, pos, wt)
        return obj


@dataclass
class BodyChunk:
    data: bytes = b""
    end_body: bool = False

    def encode(self) -> bytes:
        out = bytearray()
        if self.data:
            out.extend(encode_length_delimited(1, self.data))
        if self.end_body:
            out.extend(encode_tag(2, WIRE_VARINT) + encode_varint(1))
        return bytes(out)

    @classmethod
    def decode(cls, bdata: bytes) -> BodyChunk:
        obj = cls()
        pos = 0
        while pos < len(bdata):
            tag, pos = decode_varint(bdata, pos)
            fn = tag >> 3
            wt = tag & 7
            if fn == 1 and wt == WIRE_LENGTH_DELIMITED:
                l, pos = decode_varint(bdata, pos)
                obj.data = bdata[pos:pos + l]
                pos += l
            elif fn == 2 and wt == WIRE_VARINT:
                v, pos = decode_varint(bdata, pos)
                obj.end_body = bool(v)
            else:
                pos = _skip_field(bdata, pos, wt)
        return obj


@dataclass
class Reset:
    code: ResetCode = ResetCode.CANCELLED
    reason: str = ""

    def encode(self) -> bytes:
        out = bytearray()
        if self.code != 0:
            out.extend(encode_tag(1, WIRE_VARINT) + encode_varint(int(self.code)))
        if self.reason:
            out.extend(encode_length_delimited(2, self.reason.encode("utf-8")))
        return bytes(out)

    @classmethod
    def decode(cls, data: bytes) -> Reset:
        obj = cls()
        pos = 0
        while pos < len(data):
            tag, pos = decode_varint(data, pos)
            fn = tag >> 3
            wt = tag & 7
            if fn == 1 and wt == WIRE_VARINT:
                v, pos = decode_varint(data, pos)
                obj.code = ResetCode(v)
            elif fn == 2 and wt == WIRE_LENGTH_DELIMITED:
                l, pos = decode_varint(data, pos)
                obj.reason = data[pos:pos + l].decode("utf-8", errors="replace")
                pos += l
            else:
                pos = _skip_field(data, pos, wt)
        return obj


@dataclass
class ServiceFrame:
    stream_id: int = 1
    request: Optional[ApplicationRequest] = None
    response: Optional[ApplicationResponse] = None
    body_chunk: Optional[BodyChunk] = None
    reset: Optional[Reset] = None

    def encode(self) -> bytes:
        out = bytearray()
        if self.stream_id != 0:
            out.extend(encode_tag(1, WIRE_VARINT) + encode_varint(self.stream_id))
        if self.request is not None:
            out.extend(encode_length_delimited(2, self.request.encode()))
        elif self.response is not None:
            out.extend(encode_length_delimited(3, self.response.encode()))
        elif self.body_chunk is not None:
            out.extend(encode_length_delimited(4, self.body_chunk.encode()))
        elif self.reset is not None:
            out.extend(encode_length_delimited(5, self.reset.encode()))
        return bytes(out)

    @classmethod
    def decode(cls, data: bytes) -> ServiceFrame:
        obj = cls()
        pos = 0
        while pos < len(data):
            tag, pos = decode_varint(data, pos)
            fn = tag >> 3
            wt = tag & 7
            if fn == 1 and wt == WIRE_VARINT:
                v, pos = decode_varint(data, pos)
                obj.stream_id = to_signed_int64(v)
            elif fn == 2 and wt == WIRE_LENGTH_DELIMITED:
                l, pos = decode_varint(data, pos)
                obj.request = ApplicationRequest.decode(data[pos:pos + l])
                pos += l
            elif fn == 3 and wt == WIRE_LENGTH_DELIMITED:
                l, pos = decode_varint(data, pos)
                obj.response = ApplicationResponse.decode(data[pos:pos + l])
                pos += l
            elif fn == 4 and wt == WIRE_LENGTH_DELIMITED:
                l, pos = decode_varint(data, pos)
                obj.body_chunk = BodyChunk.decode(data[pos:pos + l])
                pos += l
            elif fn == 5 and wt == WIRE_LENGTH_DELIMITED:
                l, pos = decode_varint(data, pos)
                obj.reset = Reset.decode(data[pos:pos + l])
                pos += l
            else:
                pos = _skip_field(data, pos, wt)
        return obj


@dataclass
class ServiceRequest:
    service: ServiceType = ServiceType.SERVICE_DAEMON
    payload: bytes = b""

    def encode(self) -> bytes:
        out = bytearray()
        if self.service != 0:
            out.extend(encode_tag(1, WIRE_VARINT) + encode_varint(int(self.service)))
        if self.payload:
            out.extend(encode_length_delimited(2, self.payload))
        return bytes(out)

    @classmethod
    def decode(cls, data: bytes) -> ServiceRequest:
        obj = cls()
        pos = 0
        while pos < len(data):
            tag, pos = decode_varint(data, pos)
            fn = tag >> 3
            wt = tag & 7
            if fn == 1 and wt == WIRE_VARINT:
                v, pos = decode_varint(data, pos)
                obj.service = ServiceType(v)
            elif fn == 2 and wt == WIRE_LENGTH_DELIMITED:
                l, pos = decode_varint(data, pos)
                obj.payload = data[pos:pos + l]
                pos += l
            else:
                pos = _skip_field(data, pos, wt)
        return obj


@dataclass
class ServiceResponse:
    payload: bytes = b""

    def encode(self) -> bytes:
        out = bytearray()
        if self.payload:
            out.extend(encode_length_delimited(1, self.payload))
        return bytes(out)

    @classmethod
    def decode(cls, data: bytes) -> ServiceResponse:
        obj = cls()
        pos = 0
        while pos < len(data):
            tag, pos = decode_varint(data, pos)
            fn = tag >> 3
            wt = tag & 7
            if fn == 1 and wt == WIRE_LENGTH_DELIMITED:
                l, pos = decode_varint(data, pos)
                obj.payload = data[pos:pos + l]
                pos += l
            else:
                pos = _skip_field(data, pos, wt)
        return obj


def _skip_field(data: bytes, pos: int, wire_type: int) -> int:
    """Bỏ qua trường không rõ trong protobuf."""
    if wire_type == WIRE_VARINT:
        _, pos = decode_varint(data, pos)
        return pos
    elif wire_type == WIRE_64BIT:
        return pos + 8
    elif wire_type == WIRE_LENGTH_DELIMITED:
        length, pos = decode_varint(data, pos)
        return pos + length
    elif wire_type == WIRE_32BIT:
        return pos + 4
    else:
        raise ValueError(f"Wire type không hỗ trợ: {wire_type}")
