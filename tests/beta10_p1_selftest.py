#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import mmap
import struct
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


dec = load("beta10_decoder_test", ROOT / "mho984_bin_decoder_r12_7.py")
diag = load("beta10_diag_test", ROOT / "export_diagnostics_for_issue.py")


def build_minimal_rg03(path: Path) -> None:
    points = 2
    payload = struct.pack("<ff", 1.25, -0.5)

    wh = bytearray(112)
    struct.pack_into("<IIIII", wh, 0, 112, 1, 1, points, 0)
    struct.pack_into("<f", wh, 20, points * 1e-9)
    struct.pack_into("<d", wh, 24, 0.0)
    struct.pack_into("<d", wh, 32, 1e-9)
    struct.pack_into("<d", wh, 40, -1e-9)
    struct.pack_into("<II", wh, 48, 2, 1)
    wh[88:94] = b"MHO984"

    dh = bytearray(44)
    dh[:3] = b"CH1"
    struct.pack_into("<I", dh, 24, 1)
    struct.pack_into("<I", dh, 28, 44)
    struct.pack_into("<HH", dh, 32, 1, 4)
    struct.pack_into("<I", dh, 36, len(payload))
    struct.pack_into("<I", dh, 40, 0)

    total = 16 + len(wh) + len(dh) + len(payload)
    header = b"RG03" + struct.pack("<III", total, 0, 1)
    path.write_bytes(header + wh + dh + payload)


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    dataset = root / "mho984_test"
    memory = dataset / "memory"
    memory.mkdir(parents=True)
    source_bin = memory / "minimal.bin"
    build_minimal_rg03(source_bin)

    # P1: parser must not duplicate the whole deep-memory BIN into Python bytes.
    fh, records, blob = dec.parse_rg03(source_bin)
    assert isinstance(blob, mmap.mmap)
    assert fh["record_structure_exact_to_eof"] is True
    assert [r.data.name for r in records] == ["CH1"]
    blob.close()

    result = dec.decode_to_dataset(
        source_bin,
        dataset,
        copy_memory_bin=True,
    )
    assert result["success"] is True
    assert result["mode"] == "analog_only"
    assert (dataset / "analog" / "chan1.bin").is_file()
    assert len(list(memory.glob("*.bin"))) == 1
    assert Path(result["stored_memory_bin"]) == source_bin.resolve()

    # P1: diagnostics must not export decoded protocol payloads by default.
    overlays = dataset / "protocol_overlays"
    overlays.mkdir()
    (overlays / "active_protocol_overlay.json").write_text(
        '{"protocol":"GPS NMEA","events":[{"summary":"$GPRMC,private"}]}',
        encoding="utf-8",
    )
    (dataset / "capture_summary.json").write_text(
        '{"scope_ip":"192.0.2.10","status":"test"}',
        encoding="utf-8",
    )
    out = root / "diagnostics.zip"
    old_argv = sys.argv[:]
    try:
        sys.argv = [
            "export_diagnostics_for_issue.py",
            str(dataset),
            "--output",
            str(out),
        ]
        assert diag.main() == 0
    finally:
        sys.argv = old_argv

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert not any(name.startswith("protocol_overlays/") for name in names)
        assert "capture_summary.json" in names

# Static integration guards for the beta.10 controller changes.
controller = (ROOT / "mho984_sync_r12_11e_gui_settings.py").read_text(encoding="utf-8")
assert 'retrieval_destination = dataset / "memory"' in controller
assert "structural_preflight_attempts" in controller
assert "max_structural_attempts = 3" in controller

main_gui = (ROOT / "mho984_toolkit_main.py").read_text(encoding="utf-8")
assert "CREATE_NEW_PROCESS_GROUP" in main_gui
assert "CTRL_BREAK_EVENT" in main_gui
assert "_terminate_if_still_running" in main_gui

decoder_source = (ROOT / "mho984_bin_decoder_r12_7.py").read_text(encoding="utf-8")
assert "blob = path.read_bytes()" not in decoder_source
assert "mmap.mmap" in decoder_source

print("beta10_p1_selftest: PASS")
