# Network Architecture Project - BHTTP/1

A small binary protocol over TCP, with a persistent static-file server (`bserve`) and binary-safe client (`bcurl`). The protocol is specified independently of the Python implementation.

## Submission deliverables

1. **[Two-page protocol specification (PDF)](docs/spec.pdf)** and its [editable Markdown source](docs/spec.md).
2. **Working programs:** [server](bhttp/server.py), [client](bhttp/client.py), and [wire codec](bhttp/protocol.py).
3. **[Annotated complete request and response](docs/annotated-hexdump.md)** with [raw request](docs/request.bin), [raw response](docs/response.bin), and [verbose capture](docs/session.hex).
4. **[Requirements checklist and verification](docs/verification.md)**, [independent-peer tests](tests/test_project.py), sample files, and CI.

## Run

Requires **Python 3.10 or newer**, using only its standard library. No runtime package installation is needed. On macOS/Linux, from the repository root:

```sh
chmod +x bserve bcurl
./bserve ./www 9000
```

In a second terminal:

```sh
./bcurl -v localhost:9000/index.html
./bcurl localhost:9000/hello.txt localhost:9000/index.html
./bcurl localhost:9000/missing.txt
```

The second command fetches both files over **one connected TCP socket**, concatenating their bodies on stdout. The third prints the 404 body and exits 1. `-v` prints every transmitted/received frame as hexadecimal and ASCII to stderr, keeping stdout suitable for binary redirection:

```sh
./bcurl -v localhost:9000/hello.txt > downloaded.txt 2> capture.hex
cmp www/hello.txt downloaded.txt
```

On Windows, or if executable permissions are unavailable, use `python bserve ./www 9000` and `python bcurl -v localhost:9000/index.html`. The server binds to `127.0.0.1` by default. `--host 0.0.0.0` explicitly enables network access. Stop it with Ctrl+C. Paths containing spaces should be quoted or percent-encoded. Scheme form `bhttp://localhost:9000/path` is also supported; this custom protocol is not browser HTTP.

## Test and reproduce

```sh
python3 -m unittest discover -s tests -v
python3 tools/capture.py
```

Tests include independent raw-wire peers that do not use the production codec to construct integration messages, fragmented and coalesced TCP writes, binary/large/empty files, persistent recovery after 400/404, safe paths, literal header names, unknown-frame skipping at both peers, malformed responses, and a listener that counts the client's connections. This supports interoperability from the written specification; it does not claim a human partner implementation was tested.

`tools/capture.py` runs the real server/client on loopback, uses a fixed sample modification time, and regenerates every hexdump artifact. See [the capture annotation](docs/annotated-hexdump.md) for offsets and field interpretations.

To rebuild only the PDF, install the optional documentation dependency and run:

```sh
python3 -m pip install -r tools/requirements-docs.txt
python3 tools/build_spec.py
```

The submitted PDF is already included, so ReportLab is unnecessary to run or test the programs. CI tests Python 3.10, 3.12 and 3.14 on Linux, plus Python 3.12 on Windows.

## Design boundaries

BHTTP/1 supports GET and sequential request/response exchanges, not multiplexing, TLS, authentication, uploads, redirects, or full HTTP/2. The frame header is 8 bytes (32-bit length, 8-bit type, 8-bit flags, 16-bit request ID). Ten frequently sent names have fixed IDs; all other names are length-prefixed literals. Unknown frame types are drained without changing the session state. See the specification for every field, limit, error rule and design tradeoff.

Use a trusted, stable serving root. Network timeouts bound inactivity, and frame/file streaming bounds memory; this teaching server does not implement production connection quotas or protection against an untrusted local writer changing symlinks while files are opened.
