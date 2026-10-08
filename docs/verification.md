# Requirements and verification

The assignment source is the instructor's “Network Architecture Project” image, titled “Now you write the spec”. This checklist maps each stated deliverable and behavior to implementation and evidence.

| Assignment requirement | Delivered implementation / evidence |
| --- | --- |
| Fixed-size frame header; choose and defend fields and widths | `docs/spec.pdf`, sections 2 and 6: 32/8/8/16-bit fields, 8 bytes total |
| Number ten actually transmitted header names | Fixed IDs 1..10; all ten used across the sample request/response |
| Length-prefix remaining names and values | Literal ID 0 with one-byte name length; two-byte UTF-8 value length; codec and literal integration tests |
| Unknown frame types MUST skip cleanly | Both peers consume advertised payload without changing state; tests before requests and between response frames |
| Server accepts TCP, reads binary requests, serves under a root | `bserve`, `bhttp/server.py`; independent raw-wire server tests |
| Status, headers and exact file bytes | RESPONSE metadata plus DATA frames; binary, large, empty and UTF-8 path tests |
| 404 for missing file | Tested with raw peer and CLI; error response framing and stdout verified |
| 400 for malformed frame | Invalid flags, method, ID, lengths, UTF-8, duplicate/unassigned headers, and trailing bytes tested |
| Server keeps connection open | Same socket successfully handles 200, 404, 400 and another 200 |
| Client builds binary request frame | Wire-envelope assertions in independent mock server tests |
| Response body to stdout | Binary bytes compared exactly; diagnostics and verbose output only on stderr |
| `-v` hexdumps every frame | CLI test counts TX frames and checks dump offsets; full real capture included |
| Nonzero exit for 4xx/5xx | CLI 404 test; independent 500 response; exit-status implementation |
| Client never opens a second connection | Independent listener observes two requests and exactly one accepted connection; drop test proves no retry |
| Two-page spec sufficient for another implementer | Exactly two A4 pages; grammar, limits, state, paths, errors and rationale included |
| Program and supporting files | Both tracks, samples, tests, PDF source/rebuild tool, capture generator, and CI |
| Annotated complete request and response | `docs/annotated-hexdump.md`; every captured payload byte consumed by independent annotation parser |

## Reproduce verification

```sh
python3 -m unittest discover -s tests -v
python3 tools/capture.py --check
```

The test suite contains 26 test methods, including parameterized malformed-message/path cases. These passed locally on Python 3.14.7. The capture check also passed. GitHub Actions runs the same suite and capture check on the versions listed in the workflow. A CI configuration is not evidence that every remote job has passed; inspect the repository's Actions tab for actual job results.

The raw-wire integration peers construct frames independently using `struct` and literal bytes rather than the production encoder. They provide evidence of specification interoperability. No human partner implementation was supplied, so no human-pair integration is claimed.

The protocol is deliberately scoped to GET-like file transfer. Known, fully received malformed frames are recoverable; truncated frames and failed transfers close the connection because their message boundaries cannot safely be recovered. The root must be trusted against concurrent local mutation. These boundaries are stated in the specification rather than hidden behind passing happy-path tests.
