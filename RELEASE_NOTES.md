# Release Notes — v0.1.0-beta.7


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
