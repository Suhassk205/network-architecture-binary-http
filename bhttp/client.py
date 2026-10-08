"""Binary-safe CLI client; one socket for all URL paths in an invocation."""
import argparse
import socket
import sys
from urllib.parse import urlsplit, quote

from .protocol import (REQUEST, RESPONSE, DATA, END, ProtocolError, receive,
                       send, request_payload, decode_response, hexdump)


def parse_url(value):
    parsed = urlsplit(value if '://' in value else 'bhttp://' + value)
    if parsed.scheme != 'bhttp' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('expected bhttp://host:port/path or host:port/path')
    if parsed.query or parsed.fragment:
        raise ValueError('queries and fragments are unsupported')
    port = parsed.port if parsed.port is not None else 9000
    if not 1 <= port <= 65535:
        raise ValueError('port must be 1..65535')
    return parsed.hostname, port, quote(parsed.path or '/', safe='/%!$&\'()*+,;=:@-._~')


def fetch(sock, host, port, path, rid, output, trace=None):
    host_value = f'[{host}]:{port}' if ':' in host else f'{host}:{port}'
    headers = {'host': host_value, 'user-agent': 'bcurl/1', 'accept': '*/*', 'x-request-id': str(rid)}
    send(sock, REQUEST, END, rid, request_payload(path, headers), trace)
    status = None
    expected = None
    received = 0
    while True:
        frame = receive(sock, trace)
        if frame is None:
            raise ProtocolError('connection closed before END')
        if frame.kind not in (REQUEST, RESPONSE, DATA):
            continue  # Unknown types do not change the response state machine.
        if frame.request_id != rid or frame.flags & ~END or frame.payload is None:
            raise ProtocolError('invalid response frame envelope')
        if status is None:
            if frame.kind != RESPONSE:
                raise ProtocolError('expected RESPONSE before DATA')
            status, response_headers = decode_response(frame.payload)
            length = response_headers.get('content-length', '')
            if not length.isascii() or not length.isdecimal():
                raise ProtocolError('missing or invalid content-length')
            expected = int(length)
            if 'x-request-id' in response_headers and response_headers['x-request-id'] != str(rid):
                raise ProtocolError('x-request-id disagrees with frame')
        else:
            if frame.kind != DATA or not frame.payload:
                raise ProtocolError('expected non-empty DATA')
            received += len(frame.payload)
            if received > expected:
                raise ProtocolError('body exceeds content-length')
            output.write(frame.payload)
        if frame.flags & END:
            if received != expected:
                raise ProtocolError('body length differs from content-length')
            return status


def main():
    parser = argparse.ArgumentParser(description='Fetch BHTTP/1 files; body only on stdout')
    parser.add_argument('-v', '--verbose', action='store_true', help='hexdump every TX/RX frame to stderr')
    parser.add_argument('urls', nargs='+', help='one or more URLs on the same host and port')
    args = parser.parse_args()
    try:
        targets = [parse_url(url) for url in args.urls]
        host, port, _ = targets[0]
        if any((h, p) != (host, port) for h, p, _ in targets):
            raise ValueError('all URLs must use the same host and port')
        trace = (lambda raw, direction: hexdump(raw, direction, sys.stderr)) if args.verbose else None
        failed = False
        # Exactly one connected socket. No retries, redirects, or reconnects.
        with socket.create_connection((host, port), timeout=10) as sock:
            for i, (_, _, path) in enumerate(targets):
                rid = i % 65535 + 1
                status = fetch(sock, host, port, path, rid, sys.stdout.buffer, trace)
                if status >= 400:
                    failed = True
                    print(f'bcurl: status {status} for {path}', file=sys.stderr)
        sys.stdout.buffer.flush()
        return 1 if failed else 0
    except (OSError, EOFError, ValueError) as exc:
        print(f'bcurl: {exc}', file=sys.stderr)
        return 2
