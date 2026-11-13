#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import struct
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_decoder():
    path = ROOT / "mho984_bin_decoder_r12_7.py"
    spec = importlib.util.spec_from_file_location("rg03_tail_hotfix_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


dec = load_decoder()


def record(name: str, declared_points: int, payload_points: int, *, xinc=1e-9):
    values = np.linspace(-1.0, 1.0, payload_points, dtype="<f4")
    payload = values.tobytes()

    wh = bytearray(112)
    struct.pack_into(
        "<IIIII",
        wh,
        0,
        112,
        1,
        2,
        declared_points,
        0,
    )
    struct.pack_into("<f", wh, 20, declared_points * xinc)
    struct.pack_into("<d", wh, 24, 0.0)
    struct.pack_into("<d", wh, 32, xinc)
    struct.pack_into("<d", wh, 40, -declared_points * xinc / 2)
    struct.pack_into("<II", wh, 48, 2, 1)
    wh[88:94] = b"MHO984"

    dh = bytearray(44)
    encoded = name.encode("ascii")
    dh[: len(encoded)] = encoded
    struct.pack_into("<I", dh, 24, 1)
    struct.pack_into("<I", dh, 28, 44)
    struct.pack_into("<HH", dh, 32, 1, 4)
    # Important: header still claims declared_points * 4.
    struct.pack_into("<I", dh, 36, declared_points * 4)
    struct.pack_into("<I", dh, 40, 0)
    return bytes(wh), bytes(dh), payload


def build_two_channel(
    path: Path,
    *,
    declared_points=1000,
    ch2_payload_points=992,
    file_header_declares_actual=True,
):
    ch1 = record("CH1", declared_points, declared_points)
    ch2 = record("CH2", declared_points, ch2_payload_points)
    body = b"".join(ch1 + ch2)
    actual_total = 16 + len(body)
    full_total = (
        16
        + 2 * (112 + 44)
        + declared_points * 4
        + declared_points * 4
    )
    declared_total = actual_total if file_header_declares_actual else full_total
    header = b"RG03" + struct.pack("<III", declared_total, 0, 2)
    path.write_bytes(header + body)
    return actual_total, full_total


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    dataset = root / "mho984_tail"
    memory = dataset / "memory"
    memory.mkdir(parents=True)
    source = memory / "tail.bin"

    actual_total, full_total = build_two_channel(source)
    assert full_total - actual_total == 32  # 8 float32 points in synthetic case

    fh, records, blob = dec.parse_rg03(source)
    try:
        assert fh["length_field_matches_actual"] is True
        assert [r.data.name for r in records] == ["CH1", "CH2"]
        assert records[0].data.data_size_policy == "header_exact"
        assert records[1].data.data_size_policy == (
            "final_analog_payload_short_at_declared_rg03_eof"
        )
        assert records[0].header.points == 1000
        assert dec.record_effective_points(records[0]) == 1000
        assert records[1].header.points == 1000
        assert dec.record_effective_points(records[1]) == 992

        sync = dec.validate_common_timebase(records)
        assert sync["synchronized"] is True
        assert sync["partial_common_overlap"] is True
        assert sync["common_overlap_points"] == 992
    finally:
        blob.close()

    result = dec.decode_to_dataset(source, dataset, copy_memory_bin=False)
    assert result["success"] is True
    assert result["partial_recovery"] is True
    assert result["common_overlap_points"] == 992
    assert result["analog_points"] == {"CH1": 992, "CH2": 992}

    ch1 = np.fromfile(dataset / "analog" / "chan1.bin", dtype="<f4")
    ch2 = np.fromfile(dataset / "analog" / "chan2.bin", dtype="<f4")
    assert len(ch1) == len(ch2) == 992

    report = json.loads(
        (dataset / "digital" / "digital_parse_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["synchronization"]["partial_common_overlap"] is True
    assert report["analog"]["common_overlap_points"] == 992

    # Safety regression: if FileHeader still declares the larger expected file,
    # the same physical truncation must NOT be accepted as a completed RG03.
    incomplete = root / "incomplete.bin"
    build_two_channel(
        incomplete,
        file_header_declares_actual=False,
    )
    try:
        dec.parse_rg03(incomplete)
    except dec.BinFormatError as exc:
        message = str(exc)
        assert "effective payload exceeds file" in message
        assert "file_header_matches_actual=False" in message
    else:
        raise AssertionError(
            "incomplete file was incorrectly accepted as tail quirk"
        )

print("rg03_analog_tail_hotfix_selftest: PASS")
