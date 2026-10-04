# Public Release Audit — v0.1.0-beta.9

This file records the technical/publication checks performed when preparing this Beta package. It is not a legal opinion.

## Technical regression

- Python syntax compilation: PASS
- Acquisition compatibility regression: immediate `:TRIGger:SWEep? == AUTO` after `:SINGle` is non-fatal; mocked RUN->STOP path: PASS
- Acquisition compatibility regression: immediate `:TRIGger:SWEep? == SING`; mocked WAIT->STOP path: PASS
- Real-user observation motivating beta.2: MHO984 returned immediate sweep `AUTO` after accepted `:SINGle`; beta.1 aborted before trigger-status polling. No instrument serial/IP is retained in this audit.
- Public default `FORCE_TRIGGER_ON_TIMEOUT == False`: PASS
- Public default broad LAN fallback discovery disabled: PASS
- Fresh public controller has no preset oscilloscope IP: PASS
- Non-GUI acquisition requires explicit `--accept-risk`: PASS
- Diagnostics exporter excludes `.bin` waveform files and redacts representative IP/serial/user-path data in synthetic test: PASS
- Real MHO984 RG03 regression using the project's `mho984_0034` test capture: PASS
  - D0-D15 points: 12,500,000
  - Raw Digital x increment: 1 ns
  - Reference-aligned x increment: 1.0000020912289673 ns
  - Raw and optional reference-aligned event files both generated
  - Reference profile metadata: `enabled_by_default=false`
  - Reference profile metadata: `traceable_calibration=false`
- Public Viewer source default Digital mode: Raw

The real test capture itself is **not** included in this public release.


## beta.3 unified-GUI regression

- `START_MHO984_Toolkit.bat` is the single normal entry point: PASS
- Unified GUI source imports without side effects: PASS
- Tk main-window smoke test under virtual display: PASS
- IPv4 validation helpers: PASS
- Normal acquisition invokes the existing beta.2-compatible engine with `--no-gui --accept-risk` and safe defaults: REVIEWED
- Saved Memory BIN offline flow uses no oscilloscope/IP/FTP path: REVIEWED
- Offline import does not record the original full source path in `offline_import.json`: PASS
- Real MHO984 RG03 offline decode using the project's mho984_0034 test capture: PASS
  - records: CH1, CH2, LA
  - D0-D15 points: 12,500,000
  - raw Digital x increment: 1 ns
  - source BIN copied into the new self-contained dataset
- Root-level direct component launchers removed; troubleshooting wrappers moved to `advanced_launchers/`: PASS
- Python syntax compilation after integration: PASS

## Publication / rights posture

- RIGOL firmware: not included
- RIGOL official software binaries: not included
- RIGOL manuals/PDFs: not included
- RIGOL logos/website graphics: not included
- NumPy/Matplotlib binaries: not included; installed separately into local venv
- Project license file: MIT
- Independent/unofficial/non-affiliation notice: included
- Trademark ownership notice: included
- Model support limitation: MHO984 validated; MHO934/MHO954 not validated
- RG03/FTP/timebase mechanisms identified as empirical interoperability behavior rather than official vendor format guarantees

## Remaining publisher action

The package preparation process cannot establish the copyright provenance of every line that existed in user-supplied base scripts before this release work. Before publishing, the repository owner should complete the source-rights review described in `SOURCE_PROVENANCE.md`.

For commercial distribution or organization-level legal approval, obtain appropriate legal review if required by the publisher's jurisdiction/policies.


## beta.4 protocol-analysis regression

- Protocol decoder module syntax/import: PASS
- UART 8N1 synthetic decode: PASS
- RS-232 polarity inversion path: PASS
- RS-485 Analog A-B differential -> UART synthetic decode: PASS
- Analog threshold/hysteresis -> UART synthetic decode: PASS
- I2C address/data/ACK synthetic decode: PASS
- SPI Mode 0 word decode: PASS
- LIN BREAK/SYNC/PID/enhanced checksum synthetic decode: PASS
- Classic CAN standard-ID frame, stuffing, CRC-15, ACK/EOF synthetic decode: PASS
- GPS NMEA RMC checksum/field parsing: PASS
- GPS UBX checksum decode: PASS
- Real MHO984 digital event file load through protocol signal provider: PASS
- Raw and optional relative-aligned Digital time source loading: PASS
- CAN FD: intentionally not implemented in this beta
- Protocol decoding documented as non-certifying best-effort analysis: PASS


## beta.5 Viewer protocol-overlay regression

- Shared overlay persistence module syntax/import: PASS
- Atomic write/read/clear of `protocol_overlays/active_protocol_overlay.json`: PASS
- Protocol Analyzer publish-to-Viewer smoke test under virtual display: PASS
- Viewer overlay load/render/toggle smoke test under virtual display: PASS
- Event coloring and decode-error red path: implemented
- Adaptive labels and adjustable opacity: implemented
- Pan/zoom refresh uses only events intersecting the visible x-range: PASS
- Dense-display safety cap with 20 waveform axes and 10,000 visible events: PASS
- Waveform BIN files are not rewritten by overlay operations: PASS by design
- beta.4 acquisition/RG03 decoding code paths were not modified for this feature

## beta.7 trigger-timeout safety

Integrated-GUI acquisition may show a local decision dialog after the configured SINGLE wait interval. Force Trigger is never automatic in the public GUI: the user must explicitly choose it for that acquisition. CLI/headless behavior remains abort-on-timeout unless `--force-trigger-on-timeout` is explicitly supplied.

## beta.9 capacity-aware FTP wait audit

- Fixed 60 s stable-SIZE timeout removed from the normal FTP path: PASS.
- Initial wait budget scales with RG03 header-declared size and is capped at 600 s: PASS.
- Observed SIZE growth rate can extend the soft deadline: PASS.
- Header size remains diagnostic/timing-only and is not a completion target: PASS.
- Six consecutive stable non-zero SIZE polls remain mandatory: PASS.
- Exact downloaded-size validation and RG03 decoder validation remain mandatory: PASS.
- Synthetic 1.5 GB / ~16 MiB/s growth regression exceeds 60 s and completes successfully: PASS.
- Synthetic header/final-size mismatch regression completes from stable FTP SIZE rather than header equality: PASS.
- beta.9 hardware validation on the RIGOL loaner/demo MHO984 with 500 Mpts storage option: PASS (tester-reported successful acquisition, 2026-10-04).

## beta.8 firmware-save / FTP compatibility audit

- Scope-side Memory BIN filename shortened to `mho984_YYYYMMDD_HHMMSS.bin`: PASS (26 characters including `.bin`).
- Capture code refuses future generated instrument filenames longer than 26 characters: PASS.
- `:SAVE:STATus?` wait extended to 180 s with 1 s polling: PASS.
- A save-status timeout with no SCPI error is recorded as `pending_ftp_verification`, never as confirmed success: PASS.
- Integrated controller opts into deferred downstream verification; standalone capture remains conservative by default: PASS.
- Anonymous FTP waits for the expected file to appear for up to 180 s instead of failing after one directory listing: PASS.
- Stable remote SIZE and exact downloaded size validation remain required: PASS.
- RG03 decoder validation remains required before the integrated workflow reports completion: PASS.
- beta.8 hardware validation: PARTIAL PASS — file-appearance wait worked; fixed 60 s SIZE wait was insufficient for a 1.5 GB-class demo-unit capture.
