# Release Notes — v0.1.0-beta.9


## beta.9 — Capacity-aware dynamic FTP SIZE wait

- Replaced the fixed 60-second remote-SIZE wait with a capacity-aware dynamic wait.
- The RG03 header-declared size is used only to calculate an initial observation budget; it is never used as a completion criterion.
- The observed FTP SIZE growth rate is tracked with an exponential moving average and can extend the soft deadline when a large file is still growing.
- The hard upper bound is 600 seconds to prevent an infinite wait.
- Completion still requires a non-zero FTP SIZE to remain unchanged for six consecutive polls, followed by exact local/remote size equality and RG03 decoder validation.
- This change is based on a real RIGOL demo MHO984 capture where a 1,500,000,484-byte RG03 file was still growing at about 980 MB after 60 seconds.
- **Hardware validation: PASS (2026-10-04)** on a RIGOL loaner/demo MHO984 equipped with the 500 Mpts storage depth option; the tester reported successful acquisition with beta.9.

## beta.8 — Firmware save / FTP appearance compatibility

- Changed scope-side BIN filename to `mho984_YYYYMMDD_HHMMSS.bin` (26 chars including `.bin`) for current MHO900 filename-length compatibility.
- Extended/relaxed `:SAVE:STATus?` polling.
- Integrated controller can defer an inconclusive save-status result (with no SCPI error) to downstream FTP/RG03 verification.
- Added FTP file-appearance polling for up to 180 seconds before stable-SIZE checks.
- Preserved exact-size transfer validation and RG03 decoder validation; no uncertain file is treated as a successful capture.
- beta.8 hardware validation was a partial pass: delayed file appearance was handled, but the fixed 60-second SIZE wait was insufficient for a 1.5 GB-class demo-unit capture.

## beta.7 — Interactive SINGLE timeout handling

- Integrated GUI acquisitions now show a timeout decision dialog instead of immediately failing.
- Yes = one-time `:TFORce`, No = wait one more configured interval, Cancel = abort.
- CLI/headless mode remains fail-safe and aborts unless `--force-trigger-on-timeout` is explicitly supplied.
- SINGLE timeout interval is configurable from the main Settings dialog (default 30 s).
- Trigger-timeout decisions and extensions are recorded in `acquisition_log.json`.

## beta.6 — Viewer GUI / mouse interaction update

- Reordered the Viewer detail tabs around the normal workflow: Waveform/Display, Cursor, Auto Measure, Protocol, Sync, Data, Diagnostics.
- Added pixel-based hover hit testing for A-Z measurement cursor lines.
- The native mouse pointer changes to a horizontal resize pointer near a cursor line.
- Left-dragging a cursor line moves that selected cursor directly and updates measurements in real time.
- Right-dragging on the waveform pans the common time axis and shows a pan pointer while dragging.
- Existing keyboard cursor controls and protocol overlay behavior are preserved.


## Viewer protocol overlay

- Protocol Analyzer decode results can now be written to `protocol_overlays/active_protocol_overlay.json` inside the dataset.
- Viewer automatically detects changes to that file and overlays decoded frames/events directly on the original Analog/Digital waveform display.
- Event types use distinct colored translucent bands; decode errors are shown in red.
- Labels are shown adaptively according to the current zoom level, with Auto / More / Fewer / None modes.
- Overlay opacity is adjustable.
- Viewer offers direct launch of Protocol Analyzer, reload, and clear controls from the new `プロトコル帯` tab.
- Protocol Analyzer has `デコード後、Viewerへプロトコル帯を自動反映` enabled by default.
- Overlay rendering is limited to visible events and capped per refresh to preserve pan/zoom responsiveness on dense captures.

## Compatibility / safety

Acquisition, RG03 decoding, Raw timebase defaults, legal notices, and protocol-decoder limitations remain unchanged from beta.4. Protocol annotations are best-effort analysis aids and are not protocol-conformance certification.
