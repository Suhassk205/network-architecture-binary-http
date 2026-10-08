"""Independent wire peers: no production codec used for integration messages."""
import io
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from bhttp.server import Server
from bhttp.client import fetch, parse_url
from bhttp import protocol as p

ROOT = Path(__file__).resolve().parents[1]


def wire(kind, flags, rid, payload=b''):
    return struct.pack('!IBBH', len(payload), kind, flags, rid) + payload


def exact(sock, n):
    result = b''
    while len(result) < n:
        part = sock.recv(n - len(result))
        if not part:
            raise EOFError('independent peer saw EOF')
        result += part
    return result


def frame(sock):
    size, kind, flags, rid = struct.unpack('!IBBH', exact(sock, 8))
    return kind, flags, rid, exact(sock, size)


def request(path='/hello.txt', rid=1):
    raw = path.encode('utf-8')
    return wire(1, 1, rid, b'\x01' + len(raw).to_bytes(2, 'big') + raw + b'\x00')


def response(sock):
    kind, flags, rid, payload = frame(sock)
    if kind != 2:
        raise AssertionError('first frame must be RESPONSE')
    status = int.from_bytes(payload[:2], 'big')
    body = b''
    while not flags & 1:
        kind, flags, body_rid, data = frame(sock)
        if kind != 3 or body_rid != rid:
            raise AssertionError('unexpected DATA envelope')
        body += data
    return status, body, payload


def reference_response(body=b'OK', rid=1, status=200):
    value = str(len(body)).encode()
    # One indexed content-length (ID 5), encoded without the production codec.
    payload = status.to_bytes(2, 'big') + b'\x01\x05' + len(value).to_bytes(2, 'big') + value
    return wire(2, 0 if body else 1, rid, payload) + (wire(3, 1, rid, body) if body else b'')


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        (cls.root / 'hello.txt').write_bytes(b'Hello\x00\xff\n')
        (cls.root / 'index.html').write_bytes(b'INDEX')
        (cls.root / 'empty').write_bytes(b'')
        (cls.root / 'caf\u00e9.txt').write_bytes(b'UTF8')
        (cls.root / 'big.bin').write_bytes(bytes(range(256)) * 1100)
        (cls.root / 'directory').mkdir()
        (cls.root / 'escape').symlink_to(cls.root.parent)
        cls.server = Server(('127.0.0.1', 0), cls.root)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def connect(self):
        return socket.create_connection(self.server.server_address, timeout=3)

    def test_binary_file_and_all_response_header_ids(self):
        with self.connect() as sock:
            sock.sendall(request())
            status, body, payload = response(sock)
        self.assertEqual((status, body), (200, b'Hello\x00\xff\n'))
        reader = p.Reader(payload[2:])
        headers = p.decode_headers(reader)
        self.assertEqual(set(headers), set(p.NAMES[3:]))

    def test_persistent_success_missing_malformed_success(self):
        with self.connect() as sock:
            for message, status in [(request(), 200), (request('/missing'), 404),
                                    (wire(1, 1, 2, b'\x01'), 400), (request('/', 3), 200)]:
                sock.sendall(message)
                self.assertEqual(response(sock)[0], status)

    def test_fragmented_request_one_byte_at_a_time(self):
        with self.connect() as sock:
            for byte in request():
                sock.sendall(bytes([byte]))
            self.assertEqual(response(sock)[0], 200)

    def test_coalesced_requests(self):
        with self.connect() as sock:
            sock.sendall(request(rid=1) + request('/', 2))
            self.assertEqual(response(sock)[:2], (200, b'Hello\x00\xff\n'))
            self.assertEqual(response(sock)[:2], (200, b'INDEX'))

    def test_unknown_frames_skip_payload_and_ignore_flags_id(self):
        with self.connect() as sock:
            sock.sendall(wire(255, 255, 0, b'\x00' * 70000) + request())
            self.assertEqual(response(sock)[0], 200)

    def test_oversized_known_frame_400_then_recover(self):
        with self.connect() as sock:
            sock.sendall(wire(1, 1, 1, b'\x00' * 65537) + request('/', 2))
            self.assertEqual(response(sock)[0], 400)
            self.assertEqual(response(sock)[0], 200)

    def test_malformed_payloads_and_envelopes(self):
        path = b'/hello.txt'
        base = b'\x01' + len(path).to_bytes(2, 'big') + path
        cases = [wire(1, 0, 1, base + b'\x00'), wire(1, 3, 1, base + b'\x00'),
                 wire(1, 1, 0, base + b'\x00'), wire(3, 1, 1, b'x'),
                 wire(1, 1, 1, b'\x02' + base[1:] + b'\x00'),
                 wire(1, 1, 1, base + b'\x00EXTRA'),
                 wire(1, 1, 1, base + b'\x01\x0b\x00\x00'),
                 wire(1, 1, 1, base + b'\x21'),
                 wire(1, 1, 1, base + b'\x01\x01\x00\x01\xff'),
                 wire(1, 1, 1, b'\x01\x00\x01\xff\x00'),
                 wire(1, 1, 1, base + b'\x02\x01\x00\x00\x01\x00\x00')]
        with self.connect() as sock:
            for message in cases:
                with self.subTest(message=message.hex()):
                    sock.sendall(message)
                    self.assertEqual(response(sock)[0], 400)
            sock.sendall(request())
            self.assertEqual(response(sock)[0], 200)

    def test_paths(self):
        cases = {'/': 200, '/empty': 200, '/caf%C3%A9.txt': 200,
                 '/missing': 404, '/directory': 404, '/%2e%2e/private': 403,
                 '/../private': 403, '/escape/anything': 403,
                 '/bad%': 400, '/bad%00': 400, '/bad%ff': 400,
                 '/a\\b': 400, 'relative': 400, '/a?b': 400}
        with self.connect() as sock:
            for path, status in cases.items():
                with self.subTest(path=path):
                    sock.sendall(request(path))
                    self.assertEqual(response(sock)[0], status)

    def test_large_file_multiple_data_frames(self):
        with self.connect() as sock:
            sock.sendall(request('/big.bin'))
            status, body, _ = response(sock)
        self.assertEqual(status, 200)
        self.assertEqual(body, bytes(range(256)) * 1100)

    def test_empty_file_finishes_in_response(self):
        with self.connect() as sock:
            sock.sendall(request('/empty'))
            kind, flags, _, _ = frame(sock)
            self.assertEqual((kind, flags), (2, 1))
            sock.sendall(request('/', 2))
            self.assertEqual(response(sock)[0], 200)

    def test_literal_extension_header(self):
        raw_path = b'/hello.txt'
        block = b'\x01\x00\x06x-test\x00\x03yes'
        with self.connect() as sock:
            sock.sendall(wire(1, 1, 1, b'\x01\x00\x0a' + raw_path + block))
            self.assertEqual(response(sock)[0], 200)

    def test_partial_frame_disconnect_does_not_break_listener(self):
        with self.connect() as sock:
            sock.sendall(request()[:5])
        with self.connect() as sock:
            sock.sendall(request())
            self.assertEqual(response(sock)[0], 200)

    def test_an_idle_connection_does_not_block_another(self):
        with self.connect(), self.connect() as active:
            active.sendall(request())
            self.assertEqual(response(active)[0], 200)

    def test_cli_body_exit_codes_and_verbose(self):
        host, port = self.server.server_address
        prefix = f'{host}:{port}'
        good = subprocess.run([sys.executable, str(ROOT / 'bcurl'), '-v', prefix + '/hello.txt', prefix + '/'], capture_output=True)
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertEqual(good.stdout, b'Hello\x00\xff\nINDEX')
        self.assertEqual(good.stderr.count(b'TX ('), 2)
        self.assertIn(b'00000000', good.stderr)
        missing = subprocess.run([sys.executable, str(ROOT / 'bcurl'), prefix + '/missing'], capture_output=True)
        self.assertEqual(missing.returncode, 1)
        self.assertEqual(missing.stdout, b'404 Not Found\n')


class ClientTests(unittest.TestCase):
    def reference_peer(self, replies, paths=('/a',)):
        left, right = socket.socketpair()
        left.settimeout(2)
        right.settimeout(2)
        failures = []

        def peer():
            try:
                with right:
                    for reply in replies:
                        kind, flags, rid, body = frame(right)
                        self.assertEqual((kind, flags), (1, 1))
                        self.assertEqual(body[0], 1)
                        # Deliberately fragment response headers and bodies.
                        for byte in reply:
                            right.sendall(bytes([byte]))
            except Exception as exc:
                failures.append(exc)

        thread = threading.Thread(target=peer)
        thread.start()
        out = io.BytesIO()
        statuses = []
        try:
            with left:
                for i, path in enumerate(paths):
                    statuses.append(fetch(left, 'localhost', 9000, path, i + 1, out))
        finally:
            thread.join(3)
        if failures:
            raise failures[0]
        return statuses, out.getvalue()

    def test_independent_peer_unknown_before_and_inside_response(self):
        reply = reference_response(b'abc')
        split = 8 + int.from_bytes(reply[:4], 'big')
        unknown = wire(77, 255, 0, b'future')
        self.assertEqual(self.reference_peer([unknown + reply[:split] + unknown + reply[split:]]), ([200], b'abc'))

    def test_independent_peer_persistent_two_requests(self):
        result = self.reference_peer([reference_response(b'A'), reference_response(b'B', rid=2)], ('/a', '/b'))
        self.assertEqual(result, ([200, 200], b'AB'))

    def test_empty_and_500_responses(self):
        self.assertEqual(self.reference_peer([reference_response(b'')]), ([200], b''))
        self.assertEqual(self.reference_peer([reference_response(b'error', status=500)]), ([500], b'error'))

    def test_bad_server_messages(self):
        cases = [wire(3, 1, 1, b'x'), reference_response(rid=2),
                 wire(2, 1, 1, b'\x00\xc8\x00'),
                 wire(2, 1, 1, b'\x00\xc8\x01\x05\x00\x01' + b'2'),
                 wire(2, 2, 1, b'\x00\xc8\x00'),
                 reference_response(b'OK')[:-1]]
        for message in cases:
            with self.subTest(message=message.hex()), self.assertRaises((p.ProtocolError, EOFError)):
                self.reference_peer([message])

    def test_cli_exactly_one_connection_and_no_retry(self):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        listener.settimeout(3)
        port = listener.getsockname()[1]
        accepted = []
        errors = []

        def peer():
            try:
                sock, _ = listener.accept()
                accepted.append(sock)
                with sock:
                    for rid in (1, 2):
                        self.assertEqual(frame(sock)[2], rid)
                        sock.sendall(reference_response(bytes([64 + rid]), rid))
                listener.settimeout(0.2)
                try:
                    extra, _ = listener.accept()
                    accepted.append(extra)
                    extra.close()
                except socket.timeout:
                    pass
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=peer)
        thread.start()
        result = subprocess.run([sys.executable, str(ROOT / 'bcurl'), f'127.0.0.1:{port}/a', f'127.0.0.1:{port}/b'], capture_output=True, timeout=5)
        thread.join(4)
        listener.close()
        self.assertFalse(errors, errors)
        self.assertEqual((result.returncode, result.stdout, len(accepted)), (0, b'AB', 1))

    def test_client_never_reconnects_after_drop(self):
        left, right = socket.socketpair()
        right.close()
        with left, patch('bhttp.client.socket.create_connection', return_value=left) as connect:
            with patch.object(sys, 'argv', ['bcurl', 'localhost:9000/a']), patch('sys.stderr', io.StringIO()):
                from bhttp.client import main
                self.assertEqual(main(), 2)
            connect.assert_called_once()

    def test_url_validation(self):
        self.assertEqual(parse_url('localhost:9000/index.html'), ('localhost', 9000, '/index.html'))
        self.assertEqual(parse_url('bhttp://[::1]:9000/'), ('::1', 9000, '/'))
        for value in ('http://localhost/a', 'bhttp://u:p@localhost/a', 'localhost:9000/a?b', 'localhost:9000/a#b', 'localhost:99999/a', 'localhost:0/a'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_url(value)


class CodecTests(unittest.TestCase):
    def test_all_ten_names_and_literal_roundtrip(self):
        headers = {name: 'value' for name in p.NAMES}
        headers['x-custom'] = 'caf\u00e9'
        r = p.Reader(p.encode_headers(headers))
        self.assertEqual(p.decode_headers(r), headers)
        r.finish()

    def test_literal_wire_vector(self):
        self.assertEqual(p.encode_headers({'x-test': 'yes'}).hex(), '010006782d746573740003796573')

    def test_encode_rejects_bad_headers(self):
        for headers in ({'Upper': 'x'}, {'x': 'bad\nvalue'}, {'': 'x'}, {'x': '\x7f'}):
            with self.subTest(headers=headers), self.assertRaises(p.ProtocolError):
                p.encode_headers(headers)

    def test_pack_wire_vector(self):
        self.assertEqual(p.pack(1, 1, 0x1234, b'abc').hex(), '0000000301011234616263')

    def test_length_boundaries(self):
        self.assertEqual(len(p.pack(3, 1, 1, b'x' * 65536)), 65544)
        with self.assertRaises(p.ProtocolError):
            p.pack(3, 1, 1, b'x' * 65537)


if __name__ == '__main__':
    unittest.main()
