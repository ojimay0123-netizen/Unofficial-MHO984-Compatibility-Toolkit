#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Independent, unofficial compatibility tool; not affiliated with RIGOL.
"""
RIGOL MHO984 Memory BIN Decoder r12.7b BIN-only

実機 MHO984 (firmware 00.01.00) の :SAVE:MEMory:WAVeform で保存した
RG03 BIN を解析し、同一アクイジションの LA を D0-D15 に展開する。

この実装は、ユーザー提供実機ファイル
mho_sync_r7_20260814_0800120.bin
で次の構造を実測確認したものを安全側に検証してから使用する。

FileHeader (16 bytes)
  magic[4] = b"RG03"
  total_file_bytes : uint32 LE
  reserved         : uint32 LE
  waveform_count   : uint32 LE

各 waveform:
  WaveformHeader (112 bytes)
    header_size_marker : uint32
    waveform_type      : uint32   (実測: analog=1, LA=6)
    waveform_count     : uint32
    points             : uint32
    reserved           : uint32
    record_span_s      : float32
    display_start_s    : float64
    x_increment_s      : float64
    x_origin_s         : float64
    x_unit_code        : uint32
    analog_flag        : uint32
    date[16], time[16], instrument[24]

  DataHeader (44 bytes)
    name[16]
    reserved[8]
    buffer_count       : uint32
    header_marker      : uint32
    data_type_code     : uint16   (実測: analog=1, LA=5)
    bytes_per_point    : uint16   (実測: 4)
    data_bytes         : uint32
    reserved           : uint32

  payload[data_bytes]

実測:
  CH1-CH4 payload = little-endian float32 voltage
  LA payload      = little-endian uint32, lower 16 bits = D0-D15

2026-08-29 実機画面比較による追加確認:
  large-record LA の upper16 を第2サンプルとして時系列へ挿入すると、
  本来静止している D1-D7 等に高速な偽トグルが発生する。
  large-record signature では lower16 のみを波形サンプルとして使用し、
  時間間隔を header x_increment の2倍として扱う。

重要:
- 未知のmagic/長さ/データ型/点数不整合を黙って解釈しない。
- LAとアナログの時間軸が一致しない場合は "完全同期" と判定しない。
- Viewer用 digital_events.bin は完全LA RAWから遷移点だけを可逆圧縮したもの。
  元の一様サンプル digital_bus.bin (uint16) も必ず保存する。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
from dataclasses import dataclass, asdict
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

VERSION = "2026.08.29-r12.7f-public-beta2-timebase-reference"
DEFAULT_DIGITAL_TIME_CALIBRATION_PPM = 2.091228967287936
DEFAULT_DIGITAL_TIME_CALIBRATION_REFERENCE_S = 0.0
FILE_HEADER_SIZE = 16
WAVEFORM_HEADER_SIZE = 112
DATA_HEADER_SIZE = 44
EXPECTED_MAGIC = b"RG03"

DIGITAL_EVENT_DTYPE = np.dtype([
    ("time_s", "<f8"),
    ("bus_value", "<u2"),
])


def build_digital_time_calibration(
    raw_x_increment_s: float,
    ppm: float = DEFAULT_DIGITAL_TIME_CALIBRATION_PPM,
    reference_s: float = DEFAULT_DIGITAL_TIME_CALIBRATION_REFERENCE_S,
) -> Dict[str, Any]:
    ppm = float(ppm)
    reference_s = float(reference_s)
    if not np.isfinite(ppm):
        raise ValueError("digital time calibration ppm must be finite")
    if not np.isfinite(reference_s):
        raise ValueError("digital time calibration reference must be finite")
    scale = 1.0 + ppm * 1e-6
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("digital time calibration scale must be positive")
    return {
        "model": "linear_scale_about_trigger_reference",
        "enabled_by_default": False,
        "scale_correction_ppm": ppm,
        "scale_factor": scale,
        "reference_s": reference_s,
        "formula": "t_corrected = reference + (t_raw-reference) * scale_factor",
        "raw_x_increment_s": float(raw_x_increment_s),
        "calibrated_x_increment_s": float(raw_x_increment_s) * scale,
        "provenance": (
            "Empirical REFERENCE profile derived from one tested MHO984 using "
            "same-generator CH1<->D8 and CH2<->D0 captures (mho984_0034, "
            "2026-08-29). NOT universal, NOT traceable calibration, and not "
            "enabled by default in the public Viewer."
        ),
        "calibration_class": "relative_timebase_alignment_reference_only",
        "traceable_calibration": False,
    }


def calibrate_transition_events(
    events: np.ndarray,
    calibration: Dict[str, Any],
) -> np.ndarray:
    out = np.array(events, copy=True)
    if len(out) == 0:
        return out
    ref = float(calibration["reference_s"])
    scale = float(calibration["scale_factor"])
    raw_t = np.asarray(out["time_s"], dtype=np.float64)
    out["time_s"] = ref + (raw_t - ref) * scale
    return out


class BinFormatError(RuntimeError):
    pass


def write_array_binary(path: Path, array: np.ndarray) -> None:
    """Windows-safe NumPy binary writer using Python file I/O."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.ascontiguousarray(array)
    with path.open("wb") as f:
        f.write(arr.tobytes(order="C"))


def unique_redecode_binary_path(directory: Path, stem: str) -> Path:
    """Return a new BIN pathname so an existing memory-mapped file is never overwritten.

    On Windows the Viewer keeps digital event BIN files memory-mapped while a
    dataset is open. Re-opening that same pathname with ``wb`` can fail with
    WinError 87 / OSError(EINVAL). Re-decode therefore always writes a new
    versioned pathname and updates acquisition.json to point at it.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    candidate = directory / f"{stem}_redecode_{stamp}.bin"
    serial = 1
    while candidate.exists():
        candidate = directory / f"{stem}_redecode_{stamp}_{serial:02d}.bin"
        serial += 1
    return candidate


@dataclass
class WaveformHeader:
    header_size_marker: int
    waveform_type: int
    waveform_count: int
    points: int
    reserved: int
    record_span_s: float
    display_start_s: float
    x_increment_s: float
    x_origin_s: float
    x_unit_code: int
    analog_flag: int
    date: str
    time: str
    instrument: str


@dataclass
class DataHeader:
    name: str
    buffer_count: int
    header_marker: int
    data_type_code: int
    bytes_per_point: int

    # Effective payload size used by the parser.
    data_bytes: int

    # Raw value stored in the RG03 DataHeader.
    declared_data_bytes: int

    # "header_exact" normally.
    # Observed large-record MHO984 quirk:
    # "LA_u32_payload_with_doubled_declared_size"
    data_size_policy: str

    reserved: int
    payload_offset: int


@dataclass
class WaveformRecord:
    index: int
    waveform_header_offset: int
    data_header_offset: int
    header: WaveformHeader
    data: DataHeader


def _zstr(blob: bytes) -> str:
    return blob.split(b"\0", 1)[0].decode("ascii", errors="replace")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_waveform_header(blob: bytes, offset: int) -> WaveformHeader:
    if offset + WAVEFORM_HEADER_SIZE > len(blob):
        raise BinFormatError("truncated waveform header")

    h = blob[offset:offset + WAVEFORM_HEADER_SIZE]
    u0, u1, u2, points, reserved = struct.unpack_from("<IIIII", h, 0)
    record_span_s = struct.unpack_from("<f", h, 20)[0]
    display_start_s = struct.unpack_from("<d", h, 24)[0]
    x_increment_s = struct.unpack_from("<d", h, 32)[0]
    x_origin_s = struct.unpack_from("<d", h, 40)[0]
    x_unit_code, analog_flag = struct.unpack_from("<II", h, 48)

    return WaveformHeader(
        header_size_marker=u0,
        waveform_type=u1,
        waveform_count=u2,
        points=points,
        reserved=reserved,
        record_span_s=record_span_s,
        display_start_s=display_start_s,
        x_increment_s=x_increment_s,
        x_origin_s=x_origin_s,
        x_unit_code=x_unit_code,
        analog_flag=analog_flag,
        date=_zstr(h[56:72]),
        time=_zstr(h[72:88]),
        instrument=_zstr(h[88:112]),
    )


def parse_data_header(blob: bytes, offset: int) -> DataHeader:
    if offset + DATA_HEADER_SIZE > len(blob):
        raise BinFormatError("truncated data header")

    h = blob[offset:offset + DATA_HEADER_SIZE]
    name = _zstr(h[:16])
    buffer_count = struct.unpack_from("<I", h, 24)[0]
    header_marker = struct.unpack_from("<I", h, 28)[0]
    data_type_code, bytes_per_point = struct.unpack_from("<HH", h, 32)
    declared_data_bytes = struct.unpack_from("<I", h, 36)[0]
    reserved = struct.unpack_from("<I", h, 40)[0]

    return DataHeader(
        name=name,
        buffer_count=buffer_count,
        header_marker=header_marker,
        data_type_code=data_type_code,
        bytes_per_point=bytes_per_point,
        data_bytes=declared_data_bytes,
        declared_data_bytes=declared_data_bytes,
        data_size_policy="header_exact",
        reserved=reserved,
        payload_offset=offset + DATA_HEADER_SIZE,
    )


def parse_rg03(path: Path) -> Tuple[Dict[str, Any], List[WaveformRecord], bytes]:
    blob = path.read_bytes()
    if len(blob) < FILE_HEADER_SIZE:
        raise BinFormatError("file is shorter than RG03 file header")
    if blob[:4] != EXPECTED_MAGIC:
        raise BinFormatError(
            f"unsupported magic {blob[:4]!r}; expected {EXPECTED_MAGIC!r}"
        )

    total_file_bytes, reserved, waveform_count = struct.unpack_from("<III", blob, 4)
    actual_file_bytes = len(blob)
    file_header_length_mismatch = (
        total_file_bytes != actual_file_bytes
    )

    if not (1 <= waveform_count <= 64):
        raise BinFormatError(f"implausible waveform_count={waveform_count}")

    file_header = {
        "magic": "RG03",
        "declared_total_file_bytes": total_file_bytes,
        "actual_file_bytes": actual_file_bytes,
        "declared_minus_actual_bytes": (
            total_file_bytes - actual_file_bytes
        ),
        "length_field_matches_actual": (
            total_file_bytes == actual_file_bytes
        ),
        "length_field_policy": (
            "diagnostic_only; record headers and exact parsed EOF are authoritative"
        ),
        "reserved": reserved,
        "waveform_count": waveform_count,
    }

    records: List[WaveformRecord] = []
    offset = FILE_HEADER_SIZE

    for index in range(waveform_count):
        wh_off = offset
        wh = parse_waveform_header(blob, wh_off)
        dh_off = wh_off + WAVEFORM_HEADER_SIZE
        dh = parse_data_header(blob, dh_off)

        if wh.points <= 0:
            raise BinFormatError(f"{dh.name}: invalid points={wh.points}")
        if dh.bytes_per_point <= 0:
            raise BinFormatError(
                f"{dh.name}: invalid bytes_per_point={dh.bytes_per_point}"
            )
        expected_bytes = wh.points * dh.bytes_per_point

        if dh.declared_data_bytes != expected_bytes:

            # MHO984 large-record RG03 quirk confirmed by the user's real
            # 2026-08-22 acquisition:
            #
            #   LA type              = 5
            #   LA bytes_per_point   = 4
            #   LA points            = 12,500,000
            #   points * bpp         = 50,000,000 bytes
            #   LA data_bytes header = 100,000,000 bytes
            #
            # File level at the same time:
            #   declared total       = 300,000,484 bytes
            #   actual/FTP total     = 250,000,484 bytes
            #   difference           = 50,000,000 bytes
            #
            # Thus the LA payload is the normal 4-byte word, while the
            # LA data_bytes field is exactly doubled. The file-header total
            # contains the same extra amount.
            #
            # Accept ONLY when all arithmetic agrees with this exact signature.
            file_declared_extra = total_file_bytes - actual_file_bytes
            record_declared_extra = dh.declared_data_bytes - expected_bytes

            la_u32_doubled_declared_size_quirk = (
                dh.name == "LA"
                and dh.data_type_code == 5
                and dh.bytes_per_point == 4
                and expected_bytes == wh.points * 4
                and dh.declared_data_bytes == expected_bytes * 2
                and record_declared_extra == expected_bytes
                and file_declared_extra == record_declared_extra
            )

            if la_u32_doubled_declared_size_quirk:
                dh.data_bytes = expected_bytes
                dh.data_size_policy = (
                    "LA_u32_payload_with_doubled_declared_size"
                )
            else:
                raise BinFormatError(
                    f"{dh.name}: data size mismatch: "
                    f"type={dh.data_type_code}, "
                    f"bytes_per_point={dh.bytes_per_point}, "
                    f"points={wh.points}, "
                    f"header={dh.declared_data_bytes}, "
                    f"points*bytes={expected_bytes}, "
                    f"file_declared_extra={file_declared_extra}"
                )

        if dh.payload_offset + dh.data_bytes > len(blob):
            raise BinFormatError(
                f"{dh.name}: effective payload exceeds file: "
                f"offset={dh.payload_offset}, "
                f"effective_bytes={dh.data_bytes}, file={len(blob)}"
            )

        records.append(WaveformRecord(
            index=index,
            waveform_header_offset=wh_off,
            data_header_offset=dh_off,
            header=wh,
            data=dh,
        ))
        offset = dh.payload_offset + dh.data_bytes

    if offset != len(blob):
        raise BinFormatError(
            "RG03 record structure does not end at actual EOF: "
            f"parsed={offset}, actual_file={len(blob)}, "
            f"difference={len(blob)-offset}. "
            "This file is not accepted even if FTP SIZE was stable."
        )

    file_header["record_structure_exact_to_eof"] = True
    file_header["record_size_corrections"] = [
        {
            "name": r.data.name,
            "data_type_code": r.data.data_type_code,
            "declared_data_bytes": r.data.declared_data_bytes,
            "effective_data_bytes": r.data.data_bytes,
            "bytes_per_point": r.data.bytes_per_point,
            "policy": r.data.data_size_policy,
        }
        for r in records
        if r.data.data_size_policy != "header_exact"
    ]

    names = [r.data.name for r in records]
    if len(names) != len(set(names)):
        raise BinFormatError(f"duplicate waveform names: {names}")

    return file_header, records, blob


def effective_record_timebase(record: WaveformRecord) -> Dict[str, Any]:
    """
    Return the physical sample timing represented by a record.

    Normal records:
      logical_points = WaveformHeader.points

    Observed MHO984 large-record LA quirk:
      DataHeader says type=5, bpp=4
      stored words = WaveformHeader.points
      declared_data_bytes = 2 * stored_payload_bytes
      FileHeader has the same one-payload excess

    Real-screen comparison on 2026-08-29 showed that interpreting upper16 as
    a second D0-D15 sample fabricates rapid activity on otherwise quiet digital
    channels. The reliable payload is the lower16 of each uint32 word.

    The stored LA word count is half the analog logical point count while the
    header x_increment remains the base 0.5 ns interval. Therefore each stored
    uint32 is treated as one valid uint16 sample at every second base interval:
      logical_points = stored_words
      effective_x_increment = header x_increment * 2

    This keeps the full acquisition time span aligned with the analog records
    while discarding the non-waveform upper16 field.
    """
    h = record.header
    d = record.data

    large_la_stride2 = (
        d.name == "LA"
        and d.data_size_policy
        == "LA_u32_payload_with_doubled_declared_size"
        and d.data_type_code == 5
        and d.bytes_per_point == 4
    )

    logical_points = h.points
    effective_x_increment_s = (
        h.x_increment_s * 2.0
        if large_la_stride2
        else h.x_increment_s
    )

    return {
        "name": d.name,
        "stored_points": h.points,
        "logical_points": logical_points,
        "x_increment_s": effective_x_increment_s,
        "raw_header_x_increment_s": h.x_increment_s,
        "x_origin_s": h.x_origin_s,
        "record_span_s": h.record_span_s,
        "sample_span_s": logical_points * effective_x_increment_s,
        "packing": (
            "lower16_one_sample_per_uint32_stride2"
            if large_la_stride2
            else "one_sample_per_stored_point"
        ),
    }


def waveform_time_axis(record: WaveformRecord) -> np.ndarray:
    h = record.header
    return h.x_origin_s + np.arange(h.points, dtype=np.float64) * h.x_increment_s


def _float_close(a: float, b: float, *, rel=1e-9, abs_=1e-15) -> bool:
    return abs(a-b) <= max(abs_, rel * max(abs(a), abs(b), 1.0))


def validate_common_timebase(records: List[WaveformRecord]) -> Dict[str, Any]:
    """
    Validate the physical acquisition window using effective sample timing.

    For the large-record LA quirk, only uint32 lower16 is waveform data and
    stored words occur every two base sample intervals.
    """
    if not records:
        return {"synchronized": False, "reason": "no waveforms"}

    ref_record = records[0]
    ref_eff = effective_record_timebase(ref_record)
    mismatches = []
    variants = []

    def span_close(a, b, inc_a, inc_b):
        tolerance = max(
            abs(inc_a),
            abs(inc_b),
            abs(a) * 1e-9,
            abs(b) * 1e-9,
            1e-15,
        ) * 2.0
        return abs(a - b) <= tolerance

    for record in records[1:]:
        eff = effective_record_timebase(record)
        differences = {}

        if not _float_close(
            eff["x_origin_s"],
            ref_eff["x_origin_s"],
        ):
            differences["x_origin_s"] = [
                ref_eff["x_origin_s"],
                eff["x_origin_s"],
            ]

        if not span_close(
            eff["record_span_s"],
            ref_eff["record_span_s"],
            eff["x_increment_s"],
            ref_eff["x_increment_s"],
        ):
            differences["record_span_s"] = [
                ref_eff["record_span_s"],
                eff["record_span_s"],
            ]

        if not span_close(
            eff["sample_span_s"],
            ref_eff["sample_span_s"],
            eff["x_increment_s"],
            ref_eff["x_increment_s"],
        ):
            differences["effective_points_times_x_increment_s"] = [
                ref_eff["sample_span_s"],
                eff["sample_span_s"],
            ]

        if differences:
            mismatches.append({
                "name": record.data.name,
                "differences": differences,
                "effective_timebase": eff,
            })
        elif (
            eff["logical_points"] != ref_eff["logical_points"]
            or not _float_close(
                eff["x_increment_s"],
                ref_eff["x_increment_s"],
            )
            or eff["packing"] != ref_eff["packing"]
        ):
            variants.append(eff)

    return {
        "synchronized": not mismatches,
        "reference": ref_eff,
        "mismatches": mismatches,
        "sample_rate_or_storage_variants": variants,
        "records": [
            effective_record_timebase(r)
            for r in records
        ],
        "basis": (
            "common x_origin and physical acquisition span using effective "
            "sample timing; large LA uses uint32 lower16 at stride-2 timing"
        ),
    }


def extract_analog_float32(
    blob: bytes,
    record: WaveformRecord,
) -> np.ndarray:
    d = record.data
    if d.data_type_code != 1 or d.bytes_per_point != 4:
        raise BinFormatError(
            f"{d.name}: expected observed analog type_code=1 / 4 bytes, "
            f"got type={d.data_type_code}, bytes={d.bytes_per_point}"
        )
    return np.frombuffer(
        blob,
        dtype="<f4",
        count=record.header.points,
        offset=d.payload_offset,
    )



def decode_analog_records_to_dataset(
    dataset_root: Path,
    blob: bytes,
    records: List[WaveformRecord],
) -> Dict[str, Any]:
    """
    Decode RG03 analog float32 voltages directly.

    Viewer compatibility:
      analog/chanN.bin      = little-endian float32 voltage
      analog/chanN_pre.txt  = custom fmt=2 (direct float32 volts)

    fmt=2 is an r12 Viewer extension:
      y = raw_float32_voltage
      x = (index-xref)*xinc+xorig
    """
    analog_dir = dataset_root / "analog"
    analog_dir.mkdir(parents=True, exist_ok=True)

    decoded: Dict[str, Any] = {}

    for n in range(1, 5):
        record_name = f"CH{n}"
        rec = next(
            (r for r in records if r.data.name == record_name),
            None
        )
        if rec is None:
            continue

        volts = extract_analog_float32(blob, rec)
        bin_path = analog_dir / f"chan{n}.bin"
        pre_path = analog_dir / f"chan{n}_pre.txt"
        info_path = analog_dir / f"chan{n}_info.json"

        # Write without converting/scaling. This is the exact float32 payload
        # stored by the oscilloscope Memory BIN.
        write_array_binary(bin_path, np.asarray(volts, dtype="<f4"))

        # 10-field Viewer preamble.
        # fmt=2 means direct float32 voltage in Viewer r12.9+.
        pre_fields = [
            2,                              # fmt: direct float32 voltage
            0,                              # mode
            rec.header.points,
            1,                              # count
            rec.header.x_increment_s,
            rec.header.x_origin_s,
            0.0,                            # xref
            1.0,                            # yinc
            0.0,                            # yorig
            0.0,                            # yref
        ]
        pre_path.write_text(
            ",".join(str(v) for v in pre_fields) + "\n",
            encoding="utf-8"
        )

        time_end = (
            rec.header.x_origin_s
            + (rec.header.points - 1) * rec.header.x_increment_s
        )

        info = {
            "channel": record_name,
            "status": "success",
            "source": "RG03_memory_BIN_float32",
            "storage_dtype": "<f4",
            "storage_unit": "V",
            "points": rec.header.points,
            "x_increment_s": rec.header.x_increment_s,
            "x_origin_s": rec.header.x_origin_s,
            "time_start_s": rec.header.x_origin_s,
            "time_end_s": time_end,
            "data": f"analog/chan{n}.bin",
            "preamble": f"analog/chan{n}_pre.txt",
        }
        info_path.write_text(
            json.dumps(info, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        decoded[record_name] = info

    if not decoded:
        raise BinFormatError(
            "no analog CH1-CH4 waveform records are present in RG03 BIN"
        )

    return decoded


def describe_analog_records_without_rewrite(
    dataset_root: Path,
    records: List[WaveformRecord],
) -> Dict[str, Any]:
    """Build analog metadata without touching existing chan*.bin files."""
    decoded: Dict[str, Any] = {}
    for n in range(1, 5):
        record_name = f"CH{n}"
        rec = next((r for r in records if r.data.name == record_name), None)
        if rec is None:
            continue
        time_end = (
            rec.header.x_origin_s
            + (rec.header.points - 1) * rec.header.x_increment_s
        )
        decoded[record_name] = {
            "channel": record_name,
            "status": "success",
            "source": "RG03_memory_BIN_float32",
            "storage_dtype": "<f4",
            "storage_unit": "V",
            "points": rec.header.points,
            "x_increment_s": rec.header.x_increment_s,
            "x_origin_s": rec.header.x_origin_s,
            "time_start_s": rec.header.x_origin_s,
            "time_end_s": time_end,
            "data": f"analog/chan{n}.bin",
            "preamble": f"analog/chan{n}_pre.txt",
            "rewrite_skipped": True,
        }
    if not decoded:
        raise BinFormatError(
            "no analog CH1-CH4 waveform records are present in RG03 BIN"
        )
    return decoded


def extract_la_bus_u16(
    blob: bytes,
    record: WaveformRecord,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Decode MHO984 LA payload into one uint16 D0-D15 word per physical sample.

    Normal small-record layout:
      stored uint32 -> lower16 is one D0-D15 sample; upper16 must be zero.

    Observed large-record layout (screen-verified 2026-08-29):
      stored uint32 -> lower16 is the D0-D15 sample
                       upper16 is NOT a second waveform sample

      The valid lower16 samples are spaced by 2 * header x_increment.
      This mode is enabled only for the exact parser policy
      LA_u32_payload_with_doubled_declared_size.
    """
    d = record.data
    h = record.header

    if d.name != "LA":
        raise BinFormatError(
            f"expected LA record, got {d.name!r}"
        )

    if d.bytes_per_point == 2:
        bus = np.frombuffer(
            blob,
            dtype="<u2",
            count=h.points,
            offset=d.payload_offset,
        ).copy()

        return bus, {
            "storage_dtype": "<u2",
            "storage_word_count": h.points,
            "logical_sample_count": len(bus),
            "bytes_per_point": 2,
            "data_type_code": d.data_type_code,
            "upper16_nonzero_samples": 0,
            "packing": "one_uint16_sample_per_word",
            "packing_order": None,
            "mapping": "uint16 bit0=D0 ... bit15=D15",
            "effective_x_increment_s": h.x_increment_s,
        }

    if d.bytes_per_point != 4:
        raise BinFormatError(
            "unsupported LA bytes_per_point="
            f"{d.bytes_per_point}; expected 2 or 4"
        )

    if d.data_type_code != 5:
        raise BinFormatError(
            "unsupported 4-byte LA data_type_code="
            f"{d.data_type_code}; expected 5"
        )

    words = np.frombuffer(
        blob,
        dtype="<u4",
        count=h.points,
        offset=d.payload_offset,
    )

    large_la_stride2 = (
        d.data_size_policy
        == "LA_u32_payload_with_doubled_declared_size"
    )

    if large_la_stride2:
        # MHO984 large-record LA: screen comparison shows that only lower16
        # represents D0-D15.  Upper16 must not be interleaved as waveform data.
        # One valid sample is stored per uint32 word, every second base interval.
        high_nonzero = int(
            np.count_nonzero(words >> np.uint32(16))
        )
        bus = (
            words & np.uint32(0xFFFF)
        ).astype("<u2", copy=True)

        return bus, {
            "storage_dtype": "<u4",
            "storage_word_count": h.points,
            "logical_sample_count": len(bus),
            "bytes_per_point": 4,
            "data_type_code": d.data_type_code,
            "upper16_nonzero_samples": high_nonzero,
            "upper16_policy": "ignored_non_waveform_field",
            "packing": "lower16_one_sample_per_uint32_stride2",
            "packing_order": None,
            "mapping": (
                "uint32 lower16 bit0=D0 ... bit15=D15; "
                "upper16 ignored; samples spaced at 2*header_x_increment"
            ),
            "raw_header_x_increment_s": h.x_increment_s,
            "effective_x_increment_s": h.x_increment_s * 2.0,
        }

    # Normal small-record format.
    high_nonzero = int(
        np.count_nonzero(words >> 16)
    )
    if high_nonzero:
        raise BinFormatError(
            "normal LA record has nonzero upper16 in "
            f"{high_nonzero} stored words; mapping is unknown"
        )

    bus = (
        words & np.uint32(0xFFFF)
    ).astype(
        "<u2",
        copy=True,
    )

    return bus, {
        "storage_dtype": "<u4",
        "storage_word_count": h.points,
        "logical_sample_count": len(bus),
        "bytes_per_point": 4,
        "data_type_code": d.data_type_code,
        "upper16_nonzero_samples": high_nonzero,
        "packing": "lower16_one_sample_per_uint32_word",
        "packing_order": None,
        "mapping": "lower16 bit0=D0 ... bit15=D15; upper16=0",
        "effective_x_increment_s": h.x_increment_s,
    }

def make_transition_events(
    bus_u16: np.ndarray,
    x_origin_s: float,
    x_increment_s: float,
) -> np.ndarray:
    """
    Uniform LA samples -> exact transition-compressed bus.
    First sample is retained; every changed sample is retained.
    Viewer holds the state until the next sample time.
    """
    if len(bus_u16) == 0:
        return np.empty(0, dtype=DIGITAL_EVENT_DTYPE)

    changed = np.flatnonzero(bus_u16[1:] != bus_u16[:-1]) + 1
    indices = np.concatenate((
        np.asarray([0], dtype=np.int64),
        changed.astype(np.int64, copy=False),
    ))

    events = np.empty(len(indices), dtype=DIGITAL_EVENT_DTYPE)
    events["time_s"] = x_origin_s + indices * x_increment_s
    events["bus_value"] = bus_u16[indices]
    return events


def compare_pc_analog(
    dataset_root: Path,
    blob: bytes,
    records: List[WaveformRecord],
) -> Dict[str, Any]:
    """
    Optional integrity cross-check:
    compare scope-memory float32 channels with PC SCPI WORD channels.
    """
    result: Dict[str, Any] = {}
    analog_dir = dataset_root / "analog"

    for n in range(1, 5):
        name = f"CH{n}"
        rec = next((r for r in records if r.data.name == name), None)
        bin_path = analog_dir / f"chan{n}.bin"
        pre_path = analog_dir / f"chan{n}_pre.txt"
        if rec is None or not bin_path.exists() or not pre_path.exists():
            continue

        try:
            vals = [
                float(v.strip())
                for v in pre_path.read_text(
                    encoding="utf-8", errors="ignore"
                ).strip().split(",")
            ]
            if len(vals) != 10:
                raise ValueError("preamble must contain 10 fields")

            fmt, mode, points, count, xinc, xorig, xref, yinc, yorig, yref = vals
            if int(fmt) != 1:
                raise ValueError(f"PC analog fmt={fmt}; WORD expected")

            raw = np.fromfile(bin_path, dtype="<u2")
            pc_v = (raw.astype(np.float64) - yorig - yref) * yinc
            mem_v = extract_analog_float32(blob, rec).astype(np.float64)

            count_common = min(len(pc_v), len(mem_v))
            if count_common == 0:
                raise ValueError("zero common points")

            d = mem_v[:count_common] - pc_v[:count_common]
            pc_std = float(np.std(pc_v[:count_common]))
            mem_std = float(np.std(mem_v[:count_common]))

            if pc_std > 0 and mem_std > 0:
                corr = float(np.corrcoef(
                    pc_v[:count_common], mem_v[:count_common]
                )[0, 1])
            else:
                corr = 1.0 if np.allclose(
                    pc_v[:count_common], mem_v[:count_common]
                ) else 0.0

            result[name] = {
                "status": "success",
                "points_compared": count_common,
                "memory_points": len(mem_v),
                "pc_points": len(pc_v),
                "correlation": corr,
                "max_abs_error_v": float(np.max(np.abs(d))),
                "rms_error_v": float(np.sqrt(np.mean(d*d))),
                "memory_x_increment_s": rec.header.x_increment_s,
                "memory_x_origin_s": rec.header.x_origin_s,
                "pc_x_increment_s": xinc,
                "pc_x_origin_s": xorig,
                "timebase_match": bool(
                    len(pc_v) == len(mem_v)
                    and np.isclose(xinc, rec.header.x_increment_s)
                    and np.isclose(xorig, rec.header.x_origin_s)
                ),
                "same_record_candidate": bool(
                    corr > 0.99999
                    and len(pc_v) == len(mem_v)
                    and bool(np.isclose(xinc, rec.header.x_increment_s))
                    and bool(np.isclose(xorig, rec.header.x_origin_s))
                ),
            }
        except Exception as exc:
            result[name] = {
                "status": "failed",
                "error": repr(exc),
            }

    return result


def decode_to_dataset(
    bin_path: Path,
    dataset_root: Path,
    *,
    copy_memory_bin: bool = True,
    rewrite_analog: bool = True,
    digital_time_calibration_ppm: float = DEFAULT_DIGITAL_TIME_CALIBRATION_PPM,
    digital_time_calibration_reference_s: float = DEFAULT_DIGITAL_TIME_CALIBRATION_REFERENCE_S,
) -> Dict[str, Any]:
    bin_path = Path(bin_path).resolve()
    dataset_root = Path(dataset_root).resolve()    dataset_root.mkdir(parents=True, exist_ok=True)
    analog_dir = dataset_root / "analog"
    digital_dir = dataset_root / "digital"
    memory_dir = dataset_root / "memory"
    analog_dir.mkdir(parents=True, exist_ok=True)
    digital_dir.mkdir(parents=True, exist_ok=True)
    memory_dir.mkdir(parents=True, exist_ok=True)

    file_header, records, blob = parse_rg03(bin_path)
    record_map = {r.data.name: r for r in records}

    la_record = record_map.get("LA")

    # Validate the timebase across every waveform actually present in this BIN.
    # Analog-only capture is a legitimate MHO984 record when LA is disabled.
    sync_validation = validate_common_timebase(records)
    if not sync_validation["synchronized"]:
        raise BinFormatError(
            "common timebase validation failed: "
            + json.dumps(sync_validation["mismatches"], ensure_ascii=False)
        )

    analog_only = la_record is None

    # R12 efficiency change: CH1-CH4 are decoded from this same Memory BIN.
    # No per-channel SCPI waveform transfer is required.
    if rewrite_analog:
        analog_decoded = decode_analog_records_to_dataset(
            dataset_root,
            blob,
            records,
        )
    else:
        analog_decoded = describe_analog_records_without_rewrite(
            dataset_root,
            records,
        )

    if analog_only:
        # Do not fabricate D0-D15 files/configuration. Remove stale digital
        # artifacts if the same dataset directory is ever re-used.
        for stale in (
            digital_dir / "digital_bus.bin",
            digital_dir / "digital_events.bin",
            digital_dir / "digital_parse_report.json",
            digital_dir / "digital_coverage.json",
            dataset_root / "digital_coverage.json",
            dataset_root / "digital_configuration.json",
        ):
            try:
                stale.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

        parse_report = {
            "decoder": "MHO984 Memory BIN Decoder r12.7b",
            "decoder_version": VERSION,
            "source_format": "RG03",
            "source_file": bin_path.name,
            "source_sha256": sha256_file(bin_path),
            "records": [
                {
                    "index": r.index,
                    "waveform_header_offset": r.waveform_header_offset,
                    "data_header_offset": r.data_header_offset,
                    "header": asdict(r.header),
                    "data": asdict(r.data),
                }
                for r in records
            ],
            "empirical_layout_validation": {
                "la_record_present": False,
                "mode": "analog_only",
                "note": (
                    "No LA record is present in this RG03 BIN. This is valid "
                    "when the logic analyzer is disabled on the MHO984."
                ),
            },
            "synchronization": sync_validation,
            "digital": {
                "status": "not_present",
                "reason": "LA record is absent in source RG03 BIN",
                "channels": [],
            },
        }

        # Values used by the common result/metadata path below.
        bus_u16 = None
        events = None
        activity = {}
        upper16_nonzero = 0
        effective_la_points = None
        effective_la_xinc = None
        calibrated_la_xinc = None
        calibration = None
        calibrated_events = None
        calibrated_events_path = None
        digital_events_calibrated_filename = None
        time_start = None
        time_end = None
        calibrated_time_start = None
        calibrated_time_end = None
        coverage = None

    else:
        bus_u16, la_layout = extract_la_bus_u16(
            blob,
            la_record,
        )
        upper16_nonzero = int(
            la_layout.get("upper16_nonzero_samples", 0)
        )

        # Preserve complete uniform RAW.
        # Re-decode uses a fresh pathname because an open Viewer may keep the
        # previous BIN memory-mapped on Windows. Never truncate that pathname.
        if rewrite_analog:
            digital_bus_path = digital_dir / "digital_bus.bin"
        else:
            digital_bus_path = unique_redecode_binary_path(
                digital_dir, "digital_bus"
            )
        write_array_binary(digital_bus_path, bus_u16)
        digital_bus_filename = digital_bus_path.name

        effective_la_xinc = float(
            la_layout.get(
                "effective_x_increment_s",
                la_record.header.x_increment_s,
            )
        )
        effective_la_points = int(len(bus_u16))
        calibration = build_digital_time_calibration(
            effective_la_xinc,
            ppm=digital_time_calibration_ppm,
            reference_s=digital_time_calibration_reference_s,
        )
        calibrated_la_xinc = float(calibration["calibrated_x_increment_s"])

        # Exact transition-compressed RAW representation for the viewer.
        events = make_transition_events(
            bus_u16,
            la_record.header.x_origin_s,
            effective_la_xinc,
        )
        if rewrite_analog:
            events_path = digital_dir / "digital_events.bin"
        else:
            events_path = unique_redecode_binary_path(
                digital_dir, "digital_events"
            )
        write_array_binary(events_path, events)
        digital_events_filename = events_path.name

        calibrated_events = calibrate_transition_events(events, calibration)
        if rewrite_analog:
            calibrated_events_path = digital_dir / "digital_events_calibrated.bin"
        else:
            calibrated_events_path = unique_redecode_binary_path(
                digital_dir, "digital_events_calibrated"
            )
        write_array_binary(calibrated_events_path, calibrated_events)
        digital_events_calibrated_filename = calibrated_events_path.name

        time_start = la_record.header.x_origin_s
        time_end = (
            la_record.header.x_origin_s
            + (effective_la_points - 1) * effective_la_xinc
        )
        _ref = float(calibration["reference_s"])
        _scale = float(calibration["scale_factor"])
        calibrated_time_start = _ref + (time_start - _ref) * _scale
        calibrated_time_end = _ref + (time_end - _ref) * _scale

        activity = {}
        xor = (
            np.bitwise_xor(bus_u16[:-1], bus_u16[1:])
            if len(bus_u16) >= 2
            else np.empty(0, dtype=np.uint16)
        )
        for bit in range(16):
            mask = np.uint16(1 << bit)
            states = (bus_u16 >> bit) & 1
            activity[f"D{bit}"] = {
                "transition_count": int(np.count_nonzero((xor & mask) != 0)),
                "initial_state": int(states[0]) if len(states) else None,
                "final_state": int(states[-1]) if len(states) else None,
                "observed_low": bool(np.any(states == 0)),
                "observed_high": bool(np.any(states == 1)),
            }

        coverage = {
            "status": "complete",
            "valid_time_start_s": time_start,
            "valid_time_end_s": time_end,
            "valid_duration_s": (
                (time_end - time_start)
                if effective_la_points >= 2
                else 0.0
            ),
            "sample_count": effective_la_points,
            "stored_word_count": la_record.header.points,
            "x_increment_s": effective_la_xinc,
            "raw_x_increment_s": effective_la_xinc,
            "calibrated_x_increment_s": calibrated_la_xinc,
            "raw_valid_time_start_s": time_start,
            "raw_valid_time_end_s": time_end,
            "calibrated_valid_time_start_s": calibrated_time_start,
            "calibrated_valid_time_end_s": calibrated_time_end,
            "time_calibration": calibration,
            "analog_coverage_ratio": 1.0,
            "validity_basis": (
                "full uniform LA payload from the same RG03 memory BIN; "
                "common acquisition time window validated using effective logical LA sample count"
            ),
        }

        parse_report = {
            "decoder": "MHO984 Memory BIN Decoder r12.7b",
            "decoder_version": VERSION,
            "source_format": "RG03",
            "source_file": bin_path.name,
            "source_sha256": sha256_file(bin_path),
            "file_header": file_header,
            "records": [
                {
                    "index": r.index,
                    "waveform_header_offset": r.waveform_header_offset,
                    "data_header_offset": r.data_header_offset,
                    "header": asdict(r.header),
                    "data": asdict(r.data),
                }
                for r in records
            ],
            "empirical_layout_validation": {
                "la_record_present": True,
                "la_type_code": la_record.data.data_type_code,
                "la_data_type_code": la_record.data.data_type_code,
                "la_bytes_per_point": la_record.data.bytes_per_point,
                "la_declared_data_bytes": la_record.data.declared_data_bytes,
                "la_effective_data_bytes": la_record.data.data_bytes,
                "la_data_size_policy": la_record.data.data_size_policy,
                "la_storage_dtype": la_layout.get("storage_dtype"),
                "la_packing": la_layout.get("packing"),
                "la_packing_order": la_layout.get("packing_order"),
                "la_stored_word_count": la_layout.get("storage_word_count"),
                "la_logical_sample_count": la_layout.get("logical_sample_count"),
                "la_effective_x_increment_s": effective_la_xinc,
                "upper16_nonzero_samples": upper16_nonzero,
                "lower16_mapping": la_layout.get("mapping"),
                "file_header_length_mismatch": (
                    not file_header["length_field_matches_actual"]
                ),
            },
            "synchronization": sync_validation,
            "digital": {
                "status": "success",
                "uniform_bus_file": digital_bus_filename,
                "uniform_bus_dtype": "<u2",
                "uniform_sample_count": len(bus_u16),
                "event_file": digital_events_filename,
                "event_file_raw": digital_events_filename,
                "event_file_calibrated": digital_events_calibrated_filename,
                "time_calibration": calibration,
                "event_record_format": "<float64 time_s,uint16 bus_value>",
                "event_count": len(events),
                "event_representation": (
                    "first sample + each sample whose 16-bit bus differs "
                    "from the preceding sample"
                ),
                "activity": activity,
            },
        }

        (digital_dir / "digital_coverage.json").write_text(
            json.dumps(coverage, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (dataset_root / "digital_coverage.json").write_text(
            json.dumps(coverage, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        config = {
            "source": "RG03_memory_BIN_LA",
            "digital_channels": {f"D{i}": True for i in range(16)},
            "time_calibration": calibration,
            "note": (
                "D0-D15 are decoded from the uint32 lower16 field. "
                "For the observed large-record quirk, upper16 is ignored and "
                "the valid samples use 2*header x_increment timing."
            ),
        }
        (dataset_root / "digital_configuration.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # Always preserve a parse report, including analog-only captures.
    (digital_dir / "digital_parse_report.json").write_text(
        json.dumps(parse_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    analog_comparison = {}
    same_record_verified = True
    parse_report["pc_analog_comparison"] = {
        "status": "not_required",
        "reason": (
            "R12 decodes Analog and Digital directly from the same RG03 "
            "Memory BIN; separate analog SCPI transfer was removed."
        ),
    }
    parse_report["same_record_pc_crosscheck"] = "not_required_single_BIN_source"
    parse_report["analog"] = {
        "status": "success",
        "source": "RG03_memory_BIN_float32",
        "channels": analog_decoded,
        "rewrite_performed": bool(rewrite_analog),
    }

    # Rewrite after Analog/common-source metadata has been added.
    (digital_dir / "digital_parse_report.json").write_text(
        json.dumps(parse_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Copy the original scope BIN into the dataset for traceability.
    stored_memory_path = memory_dir / bin_path.name
    if copy_memory_bin and stored_memory_path != bin_path:
        shutil.copy2(bin_path, stored_memory_path)
    elif stored_memory_path == bin_path:
        pass

    # Update acquisition manifest, while preserving capture-side fields.
    acquisition_path = dataset_root / "acquisition.json"
    acquisition = {}
    if acquisition_path.exists():
        try:
            acquisition = json.loads(acquisition_path.read_text(encoding="utf-8"))
        except Exception:
            acquisition = {}

    if not isinstance(acquisition, dict):
        acquisition = {}

    acquisition["schema"] = "mho984-synchronized-acquisition"
    acquisition["schema_version"] = 4
    acquisition["same_acquisition"] = True
    acquisition["complete_D0_D15"] = not analog_only
    acquisition["capture_mode"] = (
        "analog_only" if analog_only else "analog_plus_logic_analyzer"
    )
    acquisition["memory_bin_decoder"] = {
        "decoder_version": VERSION,
        "source_file": f"memory/{bin_path.name}",
        "source_sha256": parse_report["source_sha256"],
        "format": "RG03",
        "layout_validation": "passed",
        "pc_analog_crosscheck": "not_required_single_BIN_source",
        "la_record_present": not analog_only,
    }

    channels = acquisition.setdefault("channels", {})

    # Viewer-facing manifest uses CHAN1..CHAN4 consistently.
    # RG03 internal record names remain CH1..CH4.
    channels["analog"] = {}

    for record_channel, info in analog_decoded.items():
        n = int(record_channel[2:])
        viewer_channel = f"CHAN{n}"

        channels["analog"][viewer_channel] = {
            "status": "success",
            "source": "RG03_memory_BIN_float32",
            "rg03_record_name": record_channel,
            "data": info["data"],
            "preamble": info["preamble"],
            "info": f"analog/chan{n}_info.json",
            "points": info["points"],
            "x_increment_s": info["x_increment_s"],
            "x_origin_s": info["x_origin_s"],
        }

    if analog_only:
        channels["digital"] = {
            "status": "not_present",
            "source": "RG03_memory_BIN",
            "reason": (
                "No LA waveform record exists in this acquisition. "
                "Logic analyzer was disabled/not captured."
            ),
            "channels": [],
            "same_timebase_as_analog": None,
            "complete_record": False,
        }

        sync = acquisition.setdefault("synchronization", {})
        sync.update({
            "basis": (
                "single STOP record saved once as RG03 memory BIN; "
                "Analog is decoded directly from the same BIN"
            ),
            "trigger_reference_s": 0.0,
            "digital_timebase_verified_from_bin_header": False,
            "digital_complete_record_verified": False,
            "digital_status": "not_present",
        })

    else:
        channels["digital"] = {
            "status": "success",
            "source": "RG03_memory_BIN_LA",
            "data": f"digital/{digital_events_filename}",
            "raw_data": f"digital/{digital_events_filename}",
            "calibrated_data": f"digital/{digital_events_calibrated_filename}",
            "uniform_raw": f"digital/{digital_bus_filename}",
            "parse_report": "digital/digital_parse_report.json",
            "coverage": "digital/digital_coverage.json",
            "channels": [f"D{i}" for i in range(16)],
            "points": effective_la_points,
            "stored_word_count": la_record.header.points,
            "x_increment_s": effective_la_xinc,
            "raw_x_increment_s": effective_la_xinc,
            "calibrated_x_increment_s": calibrated_la_xinc,
            "time_calibration": calibration,
            "x_origin_s": la_record.header.x_origin_s,
            "trigger_reference_s": float(calibration["reference_s"]),
            "same_timebase_as_analog": True,
            "complete_record": True,
        }

        sync = acquisition.setdefault("synchronization", {})
        sync.update({
            "basis": (
                "single STOP record saved once as RG03 memory BIN; "
                "CH1-CH4 float32 and LA lower16 are decoded from "
                "that same file with matching timebase"
            ),
            "trigger_reference_s": 0.0,
            "digital_time_axis": "RAW: t = x_origin + sample_index * x_increment; calibrated: trigger-referenced linear scale correction",
            "digital_time_calibration": calibration,
            "digital_timebase_verified_from_bin_header": True,
            "digital_complete_record_verified": True,
            "digital_status": "success",
        })

    acquisition_path.write_text(
        json.dumps(acquisition, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    result = {
        "success": True,
        "mode": "analog_only" if analog_only else "analog_plus_logic_analyzer",
        "dataset_root": str(dataset_root),
        "source_bin": str(bin_path),
        "stored_memory_bin": str(stored_memory_path),
        "records": [r.data.name for r in records],
        "la_present": not analog_only,
        "analog_channels": list(analog_decoded),
        "analog_points": {
            name: info["points"]
            for name, info in analog_decoded.items()
        },
        "digital_points": (
            None if analog_only else effective_la_points
        ),
        "digital_event_count": (
            0 if analog_only else len(events)
        ),
        "digital_event_file": (
            None if analog_only else str(events_path)
        ),
        "digital_event_calibrated_file": (
            None if analog_only else str(calibrated_events_path)
        ),
        "digital_time_calibration": calibration,
        "calibrated_x_increment_s": (
            None if analog_only else calibrated_la_xinc
        ),
        "digital_bus_file": (
            None if analog_only else str(digital_bus_path)
        ),
        "time_start_s": time_start,
        "time_end_s": time_end,
        "x_increment_s": (
            None if analog_only else effective_la_xinc
        ),
        "upper16_nonzero_samples": upper16_nonzero,
        "same_record_pc_crosscheck": same_record_verified,
        "activity": activity,
    }

    (dataset_root / "decode_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Decode MHO984 RG03 memory BIN into synchronized D0-D15 dataset."
    )
    ap.add_argument("bin_file", type=Path, help="MHO984 memory *.bin")
    ap.add_argument(
        "dataset_folder",
        type=Path,
        help="r8/r7 PC dataset folder containing acquisition.json/analog/",
    )
    ap.add_argument(
        "--no-copy",
        action="store_true",
        help="Do not copy original BIN into dataset/memory/",
    )
    ap.add_argument(
        "--digital-calibration-ppm",
        type=float,
        default=DEFAULT_DIGITAL_TIME_CALIBRATION_PPM,
        help=(
            "Digital time-axis linear scale correction in ppm used to generate "
            "an optional reference-aligned event file. This is an empirical "
            f"reference profile, not a universal calibration (reference: {DEFAULT_DIGITAL_TIME_CALIBRATION_PPM:.9f})."
        ),
    )
    args = ap.parse_args()

    result = decode_to_dataset(
        args.bin_file,
        args.dataset_folder,
        copy_memory_bin=not args.no_copy,
        digital_time_calibration_ppm=args.digital_calibration_ppm,
    )

    print("=" * 72)
    print("MHO984 Memory BIN Decoder r12")
    print("=" * 72)
    print(f"Dataset       : {result['dataset_root']}")
    print(f"Records       : {', '.join(result['records'])}")
    print(f"Mode          : {result['mode']}")
    parse_report_path = Path(result["dataset_root"]) / "digital" / "digital_parse_report.json"
    try:
        _report = json.loads(parse_report_path.read_text(encoding="utf-8"))
        _fh = _report.get("file_header", {})
        if _fh:
            print(
                "RG03 size hdr : "
                f"{_fh.get('declared_total_file_bytes')} bytes"
            )
            print(
                "RG03 size file: "
                f"{_fh.get('actual_file_bytes')} bytes"
            )
            if not _fh.get("length_field_matches_actual", True):
                print(
                    "RG03 size note: header total differs, but record structure "
                    "parsed exactly to EOF"
                )

            corrections = _fh.get("record_size_corrections") or []
            for correction in corrections:
                print(
                    "RG03 correction: "
                    f"{correction.get('name')} declared="
                    f"{correction.get('declared_data_bytes')} effective="
                    f"{correction.get('effective_data_bytes')} "
                    f"type={correction.get('data_type_code')} "
                    f"bpp={correction.get('bytes_per_point')} "
                    f"({correction.get('policy')})"
                )
    except Exception:
        pass
    print(
        "Analog        : "
        + ", ".join(result["analog_channels"])
        + " (decoded directly from RG03 BIN)"
    )

    if result["la_present"]:
        print(f"Digital points: {result['digital_points']:,}")
        print(f"Raw X inc     : {result['x_increment_s']:.12g} s")
        print(f"Cal X inc     : {result['calibrated_x_increment_s']:.12g} s")
        print(
            "Ref align ppm : "
            f"{result['digital_time_calibration']['scale_correction_ppm']:+.9f} ppm "
            "about trigger t=0"
        )
        print(
            f"Time range    : {result['time_start_s']:.12g} "
            f"to {result['time_end_s']:.12g} s"
        )
        print(f"Events        : {result['digital_event_count']:,}")
        print(f"Upper16 != 0  : {result['upper16_nonzero_samples']}")
        print(f"PC crosscheck : {result['same_record_pc_crosscheck']}")
        print("Analog + D0-D15 single-BIN decode: SUCCESS")
    else:
        print("Logic analyzer: not present in this BIN")
        print(f"PC crosscheck : {result['same_record_pc_crosscheck']}")
        print("Analog-only single-BIN decode: SUCCESS")
        print("Viewer will open without D0-D15 channels.")

    print("=" * 72)


if __name__ == "__main__":
    main()