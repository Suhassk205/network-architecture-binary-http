"""Persistent TCP binary static-file server."""
import argparse
from datetime import timezone, datetime
from email.utils import format_datetime
import hashlib
import mimetypes
import os
from pathlib import Path
import re
import socketserver
import stat
import sys
from urllib.parse import unquote_to_bytes

from .protocol import (REQUEST, RESPONSE, DATA, END, CHUNK, ProtocolError,
                       decode_request, receive, send, response_payload, hexdump)


class PathError(ValueError):
    pass


def map_path(root, target):
    if not target.startswith('/') or '?' in target or '#' in target:
        raise PathError('expected an absolute path without query or fragment')
    if re.search(r'%(?![0-9A-Fa-f]{2})', target):
        raise PathError('malformed percent escape')
    try:
        path = unquote_to_bytes(target).decode('utf-8')
    except UnicodeDecodeError as exc:
        raise PathError('invalid path UTF-8') from exc
    if '\\' in path or any(ord(c) < 32 or ord(c) == 127 for c in path):
        raise PathError('invalid path character')
    if any(part in ('.', '..') for part in path.split('/')):
        raise PermissionError('dot segments forbidden')
    if path == '/':
        path = '/index.html'
    result = (root / path.lstrip('/')).resolve()
    if not result.is_relative_to(root):
        raise PermissionError('path escapes root')
    return result


class Handler(socketserver.BaseRequestHandler):
    def reply(self, rid, status, body):
        headers = {'content-type': 'text/plain; charset=utf-8',
                   'content-length': str(len(body)), 'server': 'bserve/1',
                   'cache-control': 'no-store', 'x-request-id': str(rid)}
        send(self.request, RESPONSE, END if not body else 0, rid,
             response_payload(status, headers), self.trace)
        if body:
            send(self.request, DATA, END, rid, body, self.trace)

    def serve_file(self, rid, path):
        try:
            stream = path.open('rb')
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            self.reply(rid, 404, b'404 Not Found\n')
            return
        except PermissionError:
            self.reply(rid, 403, b'403 Forbidden\n')
            return
        except OSError:
            self.reply(rid, 500, b'500 Internal Server Error\n')
            return
        with stream:
            try:
                info = os.fstat(stream.fileno())
            except OSError:
                self.reply(rid, 500, b'500 Internal Server Error\n')
                return
            if not stat.S_ISREG(info.st_mode):
                raise FileNotFoundError('not a regular file')
            size = info.st_size
            # Metadata validator, explicitly weak: does not claim a content hash.
            tag = hashlib.sha256(f'{size}:{info.st_mtime_ns}'.encode()).hexdigest()[:16]
            headers = {'content-type': mimetypes.guess_type(str(path))[0] or 'application/octet-stream',
                       'content-length': str(size), 'server': 'bserve/1',
                       'cache-control': 'no-cache',
                       'last-modified': format_datetime(datetime.fromtimestamp(info.st_mtime, timezone.utc), usegmt=True),
                       'etag': f'W/"{tag}"', 'x-request-id': str(rid)}
            send(self.request, RESPONSE, END if size == 0 else 0, rid,
                 response_payload(200, headers), self.trace)
            remaining = size
            while remaining:
                part = stream.read(min(CHUNK, remaining))
                if not part:
                    raise EOFError('file shortened during transfer')
                remaining -= len(part)
                send(self.request, DATA, END if remaining == 0 else 0, rid, part, self.trace)

    def handle(self):
        self.request.settimeout(self.server.idle_timeout)
        self.trace = (lambda raw, direction: hexdump(raw, direction, sys.stderr)) if self.server.verbose else None
        try:
            while True:
                frame = receive(self.request, self.trace)
                if frame is None:
                    return
                if frame.kind not in (REQUEST, RESPONSE, DATA):
                    continue  # MUST skip unknown frame types, including their payload.
                rid = frame.request_id
                try:
                    if frame.kind != REQUEST or frame.flags != END or rid == 0 or frame.payload is None:
                        raise ProtocolError('invalid request frame envelope')
                    target, headers = decode_request(frame.payload)
                    if 'x-request-id' in headers and headers['x-request-id'] != str(rid):
                        raise ProtocolError('x-request-id disagrees with frame')
                    path = map_path(self.server.root, target)
                    if not path.is_file():
                        raise FileNotFoundError(target)
                except (ProtocolError, PathError, ValueError):
                    self.reply(rid, 400, b'400 Bad Request\n')
                    continue
                except PermissionError:
                    self.reply(rid, 403, b'403 Forbidden\n')
                    continue
                except (FileNotFoundError, NotADirectoryError):
                    self.reply(rid, 404, b'404 Not Found\n')
                    continue
                except OSError:
                    self.reply(rid, 500, b'500 Internal Server Error\n')
                    continue
                # Once response bytes start, an I/O failure must terminate the
                # connection, not inject a second RESPONSE into the same message.
                self.serve_file(rid, path)
        except (OSError, EOFError):
            return  # EOF/reset/idle timeout is the documented connection boundary.


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, root, *, verbose=False, idle_timeout=60):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError('root must be a directory')
        self.verbose, self.idle_timeout = verbose, idle_timeout
        super().__init__(address, Handler)


def main():
    parser = argparse.ArgumentParser(description='Serve files using BHTTP/1 over persistent TCP')
    parser.add_argument('root')
    parser.add_argument('port', type=int)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('-v', '--verbose', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port must be 1..65535')
    try:
        with Server((args.host, args.port), args.root, verbose=args.verbose) as server:
            print(f'bserve listening on {args.host}:{args.port}; root={server.root}', file=sys.stderr, flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError) as exc:
        print(f'bserve: {exc}', file=sys.stderr)
        return 1
    return 0
