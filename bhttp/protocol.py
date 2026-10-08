"""Wire codec. All multi-byte integers use network byte order."""
from dataclasses import dataclass
import re
import struct

HEADER = struct.Struct('!IBBH')
REQUEST, RESPONSE, DATA = 1, 2, 3
END = 1
MAX_PAYLOAD = 65536
CHUNK = 16384
NAMES = ('host', 'user-agent', 'accept', 'content-type', 'content-length',
         'server', 'cache-control', 'last-modified', 'etag', 'x-request-id')
TOKENS = {name: i + 1 for i, name in enumerate(NAMES)}
NAME_RE = re.compile(r"[!#$%&'*+.^_`|~0-9a-z-]+\Z")


class ProtocolError(ValueError):
    pass


@dataclass
class Frame:
    kind: int
    flags: int
    request_id: int
    payload: bytes | None


def read_exact(sock, count, *, clean_eof=False):
    out = bytearray()
    while len(out) < count:
        part = sock.recv(count - len(out))
        if not part:
            if clean_eof and not out:
                return None
            raise EOFError('truncated frame')
        out.extend(part)
    return bytes(out)


def hexdump(raw, direction, out):
    print(f'{direction} ({len(raw)} bytes)', file=out)
    for i in range(0, len(raw), 16):
        part = raw[i:i + 16]
        readable = ''.join(chr(b) if 32 <= b < 127 else '.' for b in part)
        print(f'{i:08x}  {part.hex(" "):<47}  |{readable}|', file=out)


def receive(sock, trace=None):
    raw = read_exact(sock, HEADER.size, clean_eof=True)
    if raw is None:
        return None
    length, kind, flags, rid = HEADER.unpack(raw)
    # Drain unknown/oversized frames in bounded pieces, preserving alignment.
    if kind not in (REQUEST, RESPONSE, DATA) or length > MAX_PAYLOAD:
        if trace:
            trace(raw, 'RX header (skipped payload follows)')
        remaining = length
        while remaining:
            part = read_exact(sock, min(remaining, CHUNK))
            if trace:
                trace(part, 'RX skipped payload chunk')
            remaining -= len(part)
        return Frame(kind, flags, rid, None)
    payload = read_exact(sock, length)
    if trace:
        trace(raw + payload, 'RX')
    return Frame(kind, flags, rid, payload)


def pack(kind, flags, rid, payload=b''):
    if len(payload) > MAX_PAYLOAD:
        raise ProtocolError('payload exceeds 65536 bytes')
    return HEADER.pack(len(payload), kind, flags, rid) + payload


def send(sock, kind, flags, rid, payload=b'', trace=None):
    raw = pack(kind, flags, rid, payload)
    sock.sendall(raw)
    if trace:
        trace(raw, 'TX')


class Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def take(self, size):
        if self.pos + size > len(self.data):
            raise ProtocolError('truncated payload field')
        value = self.data[self.pos:self.pos + size]
        self.pos += size
        return value

    def u8(self):
        return self.take(1)[0]

    def u16(self):
        return int.from_bytes(self.take(2), 'big')

    def text(self, size):
        try:
            return self.take(size).decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ProtocolError('invalid UTF-8') from exc

    def finish(self):
        if self.pos != len(self.data):
            raise ProtocolError('trailing payload bytes')


def validate_header(name, value):
    if not NAME_RE.fullmatch(name):
        raise ProtocolError('header names must be lowercase ASCII tokens')
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ProtocolError('control character in header value')


def encode_headers(headers):
    if len(headers) > 32:
        raise ProtocolError('more than 32 headers')
    out = bytearray([len(headers)])
    for name, value in headers.items():
        validate_header(name, value)
        token = TOKENS.get(name, 0)
        out.append(token)
        if token == 0:
            raw_name = name.encode('ascii')
            if len(raw_name) > 255:
                raise ProtocolError('header name too long')
            out.append(len(raw_name))
            out.extend(raw_name)
        raw_value = value.encode('utf-8')
        if len(raw_value) > 65535:
            raise ProtocolError('header value too long')
        out.extend(struct.pack('!H', len(raw_value)))
        out.extend(raw_value)
    return bytes(out)


def decode_headers(reader):
    count = reader.u8()
    if count > 32:
        raise ProtocolError('more than 32 headers')
    headers = {}
    for _ in range(count):
        token = reader.u8()
        if token == 0:
            try:
                name = reader.take(reader.u8()).decode('ascii')
            except UnicodeDecodeError as exc:
                raise ProtocolError('non-ASCII header name') from exc
        elif token <= len(NAMES):
            name = NAMES[token - 1]
        else:
            raise ProtocolError('unassigned header token')
        value = reader.text(reader.u16())
        validate_header(name, value)
        if name in headers:
            raise ProtocolError('duplicate header name')
        headers[name] = value
    return headers


def request_payload(path, headers):
    raw = path.encode('utf-8')
    if not 1 <= len(raw) <= 4096:
        raise ProtocolError('path must be 1..4096 bytes')
    return b'\x01' + struct.pack('!H', len(raw)) + raw + encode_headers(headers)


def decode_request(payload):
    r = Reader(payload)
    if r.u8() != 1:
        raise ProtocolError('unsupported method (only GET=1)')
    size = r.u16()
    if not 1 <= size <= 4096:
        raise ProtocolError('path must be 1..4096 bytes')
    path = r.text(size)
    headers = decode_headers(r)
    r.finish()
    return path, headers


def response_payload(status, headers):
    return struct.pack('!H', status) + encode_headers(headers)


def decode_response(payload):
    r = Reader(payload)
    status = r.u16()
    if not 100 <= status <= 599:
        raise ProtocolError('invalid status')
    headers = decode_headers(r)
    r.finish()
    return status, headers
