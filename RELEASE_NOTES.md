# MHO984 Compatibility Toolkit v0.1.0-beta.10

Independent, unofficial project; not affiliated with, endorsed by, or sponsored by RIGOL Technologies.

## Changes from beta.9

- Use mmap for RG03 Memory BIN input to reduce peak memory usage.
- Store online Memory BIN directly in `dataset/memory`, avoiding an extra local deep-memory copy.
- Add RG03 structural preflight with up to three FTP re-check/retrieval attempts when a download is structurally rejected. Record actual transfer retries.
- Exclude `protocol_overlays` from diagnostic ZIPs by default to reduce accidental sharing of decoded payloads.
- Request graceful cancellation with CTRL_BREAK/SIGINT before process termination fallback.
- Fix decoder and Viewer CSV-loader syntax defects present in the previous checked-in sources; keep short-file failures inside RG03 format handling and close mmap after decoding.
- Expand UART parity, CAN extended/RTR/CRC, GPS GGA/checksum/PPS regression tests.
- Add beta.10 P1 selftests and Windows CI for Python 3.11, 3.13 and 3.14.

## Validation

Implementation commit `b65f8282d7dc56aa122866539215762150b06aef` passed all three Windows jobs: compile all Python sources, protocol decoder, overlay, firmware/FTP compatibility and beta.10 P1 selftests.

CI evidence: https://github.com/ojimay0123-netizen/Unofficial-MHO984-Compatibility-Toolkit/actions/runs/38136634888

On 2026-10-18 the maintainer reported beta.10 checking complete and requested publication. Detailed per-test hardware results, firmware and acquisition settings were not supplied with that confirmation.

## Installation

Download the named beta.10 ZIP and matching SHA-256 file. Extract to a fresh writable local folder, run `setup_venv.bat`, then `START_MHO984_Toolkit.bat`. Do not copy an old `.venv` into the new folder.

## Limitations and defaults

MHO984 community-testing beta. Other models remain unvalidated. CAN FD is not implemented. RG03/FTP compatibility is empirical and firmware-dependent. Raw digital timing remains default; Force Trigger and broad LAN discovery are not automatic. Scope-side BIN files are not deleted automatically. Maximum capture size and RAM requirements remain unbounded by published measurements. See `docs/KNOWN_LIMITATIONS.md`.

## Full Changelog

https://github.com/ojimay0123-netizen/Unofficial-MHO984-Compatibility-Toolkit/compare/v0.1.0-beta.9...v0.1.0-beta.10
