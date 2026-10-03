# SPDX-License-Identifier: MIT
# Independent, unofficial compatibility tool; not affiliated with RIGOL.
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, colorchooser
from pathlib import Path
import csv
import json
import time
import faulthandler
import os
import platform
import sys
import traceback
import bisect
import subprocess

# ============================================================
# 実行時診断（NumPy / Matplotlibのimportより前に有効化）
# ============================================================

_RUNTIME_LOG_HANDLE = None


def _setup_runtime_diagnostics():
    """
    コンソール出力を残しつつ、ハング時のPythonスタックをログへ保存する。

    STATUS_APPLICATION_HANG (0xCFFFFFFF) は通常のPython例外ではないため、
    tracebackが表示されない。30秒以上応答が止まった場合に、その時点の
    Pythonスタックを viewer_r9_runtime.log へ周期的に書き出す。
    """
    global _RUNTIME_LOG_HANDLE
    try:
        log_path = Path(__file__).resolve().with_name("viewer_r9_runtime.log")
        _RUNTIME_LOG_HANDLE = open(
            log_path, "a", encoding="utf-8", buffering=1
        )
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        _RUNTIME_LOG_HANDLE.write(
            f"\n===== Viewer R13.5 Public Beta start {now} =====\n"
            f"Python: {sys.version}\n"
            f"Executable: {sys.executable}\n"
            f"Platform: {platform.platform()}\n"
        )
        faulthandler.enable(file=_RUNTIME_LOG_HANDLE, all_threads=True)
        faulthandler.dump_traceback_later(
            30, repeat=True, file=_RUNTIME_LOG_HANDLE
        )
    except Exception:
        _RUNTIME_LOG_HANDLE = None


_setup_runtime_diagnostics()

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.widgets import SpanSelector
from protocol_overlay import load_active_overlay, clear_active_overlay, active_overlay_mtime_ns


# ============================================================
# 表示設定
# ============================================================

VIEWER_TOOL_VERSION = "2026.09.26-r13.7-public-beta6-mouse-gui"
DEFAULT_DIGITAL_TIME_CALIBRATION_PPM = 2.091228967287936
DEFAULT_DIGITAL_TIME_CALIBRATION_REFERENCE_S = 0.0

# Matplotlib既定のDejaVu Sansには日本語グリフがない。Windows標準の
# 日本語フォントを先に指定し、U+3002などの警告を抑える。
plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = [
    "Yu Gothic",
    "Meiryo",
    "BIZ UDPGothic",
    "MS Gothic",
    "Noto Sans CJK JP",
    "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False

# 表示の目標点数係数（ピクセル幅×この係数程度に収める）
PIXEL_MULT = 2.0

# 最低でもこれだけは表示（粗すぎ防止）
MIN_TARGET = 800

# 最大でもこの程度に抑える（CPU暴走防止）
MAX_TARGET = 50_000

# Plot height priority.
# Analog traces should occupy most of the vertical display area while
# 8/16 digital channels remain readable as compact logic rows.
ANALOG_STACK_HEIGHT = 3.0
DIGITAL_STACK_HEIGHT = 0.38
OVERLAY_ANALOG_HEIGHT = 4.5
OVERLAY_DIGITAL_HEIGHT = 1.0
DIGITAL_CURSOR_LABEL_FONTSIZE = 7.5

# Dark waveform area.
PLOT_BACKGROUND = "#000000"
AXIS_FOREGROUND = "#E5E7EB"
AXIS_MUTED_FOREGROUND = "#9CA3AF"
GRID_COLOR = "#374151"
SPINE_COLOR = "#6B7280"
LEGEND_BACKGROUND = "#111111"

# High-visibility defaults. Every channel can be changed from the Viewer UI.
DEFAULT_ANALOG_COLORS = {
    "CHAN1": "#FFD400",  # yellow
    "CHAN2": "#00E5FF",  # cyan
    "CHAN3": "#FF4FD8",  # magenta
    "CHAN4": "#5CFF6B",  # green
    "CH1": "#FFD400",
    "CH2": "#00E5FF",
    "CH3": "#FF4FD8",
    "CH4": "#5CFF6B",
}

DEFAULT_DIGITAL_COLORS = [
    "#00FF88",
    "#FFB000",
    "#00C8FF",
    "#FF5C8A",
    "#B48CFF",
    "#7CFF00",
    "#FFD166",
    "#4DD0E1",
    "#F06292",
    "#64B5F6",
    "#BA68C8",
    "#81C784",
    "#FF8A65",
    "#A5D6A7",
    "#90CAF9",
    "#FFF176",
]

# アナログ多段min/maxキャッシュの最下層ブロックサイズ。
# 64点未満まで拡大した場合は元データを直接読み、広域表示では
# 64, 128, 256...点単位の階層から適切なものを選ぶ。
ANALOG_CACHE_BASE_BLOCK = 64

# 小さい波形ではキャッシュ構築の方が高コストになるため、元データを
# 直接間引く。この点数以上のチャンネルだけ多段キャッシュを使用する。
ANALOG_CACHE_MIN_POINTS = 200_000

# 4K最大化ではTkAggの巨大PhotoImage生成自体がUIスレッドを長時間占有する。
# 標準TkAggを安全な最大サイズのホストへ配置し、ホストの連続Configureは
# 最後の1回だけキャンバス寸法へ反映する。
PLOT_RESIZE_DEBOUNCE_MS = 500
PLOT_INITIAL_WIDTH = 1000
PLOT_INITIAL_HEIGHT = 650

# Standard safety mode retained for machines where a large TkAgg PhotoImage
# causes sluggish resizing/redraw.
PLOT_SAFE_MAX_WIDTH = 1600
PLOT_SAFE_MAX_HEIGHT = 900

# R13.1: 4K displays get a substantially larger waveform canvas.
# 2400x1350 = 3.24M pixels. This is 2.25x the old 1600x900 area, but still
# deliberately below full 4K so TkAgg does not allocate a 3840x2160 canvas.
PLOT_4K_MAX_WIDTH = 2400
PLOT_4K_MAX_HEIGHT = 1350

# Detect a 4K-class desktop by usable screen resolution.
PLOT_4K_SCREEN_MIN_WIDTH = 3000
PLOT_4K_SCREEN_MIN_HEIGHT = 1700

HIGH_RES_PIXEL_AREA = 1_200_000
HIGH_RES_PIXEL_MULT = 0.60
HIGH_RES_MIN_TARGET = 800
HIGH_RES_MAX_TARGET = 2_500


# ============================================================
# PRE / BIN 読み込み
# ============================================================

DIGITAL_EVENT_DTYPE = np.dtype([
    ("time_s", "<f8"),
    ("bus_value", "<u2")
])


class UniformTimeAxis:
    """
    等間隔時間軸を必要な範囲だけ生成する。

    25 M点級の波形で巨大なfloat64時間配列を常駐させないための
    軽量な配列互換オブジェクト。
    """

    def __init__(self, length, xinc, xorig, xref):
        self.length = int(length)
        self.xinc = float(xinc)
        self.xorig = float(xorig)
        self.xref = float(xref)

        if self.length < 0:
            raise ValueError("時間軸の点数が負です")

        if not np.isfinite(self.xinc) or self.xinc <= 0:
            raise ValueError(
                f"XINCrementは正の有限値が必要です: {self.xinc}"
            )

    def __len__(self):
        return self.length

    def __getitem__(self, key):
        if isinstance(key, slice):
            start, stop, step = key.indices(self.length)
            indices = np.arange(
                start,
                stop,
                step,
                dtype=np.float64
            )

            return (
                indices - self.xref
            ) * self.xinc + self.xorig

        index = int(key)

        if index < 0:
            index += self.length

        if index < 0 or index >= self.length:
            raise IndexError("時間軸の範囲外です")

        return (
            (index - self.xref)
            * self.xinc
            + self.xorig
        )

    def searchsorted(self, value, side="left"):
        """np.searchsorted相当（単一値）。"""

        if side not in ("left", "right"):
            raise ValueError(f"未対応のside={side}")

        value = float(value)
        low = 0
        high = self.length

        # 式から直接丸めると、1 ns刻みなどで境界が1点ずれる
        # 場合がある。配列を生成せず二分探索して同じ比較結果にする。
        while low < high:
            middle = (low + high) // 2
            middle_value = self[middle]

            if (
                middle_value < value
                or (
                    side == "right"
                    and middle_value == value
                )
            ):
                low = middle + 1
            else:
                high = middle

        return low


class ScaledAnalogSamples:
    """
    ADCコードを保持し、参照された範囲だけ電圧へ変換する。
    """

    def __init__(self, raw, pre):
        self.raw = raw
        self.length = len(raw)
        self.yorig = float(pre["yorig"])
        self.yref = float(pre["yref"])
        self.yinc = float(pre["yinc"])

    def __len__(self):
        return self.length

    def __getitem__(self, key):
        values = np.asarray(
            self.raw[key],
            dtype=np.float64
        )

        scaled = (
            values
            - self.yorig
            - self.yref
        ) * self.yinc

        if np.ndim(scaled) == 0:
            return float(scaled)

        return scaled


class AnalogMinMaxPyramid:
    """
    アナログ波形の多段min/maxキャッシュ。

    元ADCコードを最下層で一定点数ごとに集約し、以降は2ブロックずつ
    統合する。広域表示では元の数千万点ではなく、画面解像度に近い
    階層だけを参照する。キャッシュ値はADCコードのまま保持するため、
    BYTE/WORDのメモリ使用量を抑え、電圧変換は描画点だけに行う。
    """

    def __init__(
        self,
        samples,
        base_block=ANALOG_CACHE_BASE_BLOCK
    ):
        if not isinstance(samples, ScaledAnalogSamples):
            raise TypeError(
                "AnalogMinMaxPyramidにはScaledAnalogSamplesが必要です"
            )

        self.samples = samples
        self.base_block = max(2, int(base_block))
        self.levels = []
        self.ready = False
        self.build_error = None
        self.build_seconds = 0.0
        self.cache_bytes = 0

    def ensure_built(self):
        """必要時に一度だけ階層を構築する。失敗時は再試行しない。"""

        if self.ready:
            return True
        if self.build_error is not None:
            return False

        started = time.perf_counter()

        try:
            raw = self.samples.raw
            point_count = len(raw)

            if point_count == 0:
                self.levels = []
                self.ready = True
                return True

            starts = np.arange(
                0,
                point_count,
                self.base_block,
                dtype=np.int64
            )
            raw_min = np.minimum.reduceat(raw, starts)
            raw_max = np.maximum.reduceat(raw, starts)

            block_size = self.base_block
            levels = [
                {
                    "block_size": block_size,
                    "raw_min": np.asarray(raw_min),
                    "raw_max": np.asarray(raw_max),
                }
            ]

            # 上位階層は直下のmin/maxだけを2個ずつまとめるため、
            # 元波形を再走査しない。
            while len(raw_min) > 1:
                pair_starts = np.arange(
                    0,
                    len(raw_min),
                    2,
                    dtype=np.int64
                )
                raw_min = np.minimum.reduceat(
                    raw_min,
                    pair_starts
                )
                raw_max = np.maximum.reduceat(
                    raw_max,
                    pair_starts
                )
                block_size *= 2
                levels.append(
                    {
                        "block_size": block_size,
                        "raw_min": np.asarray(raw_min),
                        "raw_max": np.asarray(raw_max),
                    }
                )

            self.levels = levels
            self.cache_bytes = sum(
                level["raw_min"].nbytes
                + level["raw_max"].nbytes
                for level in levels
            )
            self.ready = True
            return True

        except Exception as exc:
            # キャッシュ失敗だけでViewerを止めず、従来の直接間引きへ
            # フォールバックする。
            self.levels = []
            self.cache_bytes = 0
            self.build_error = f"{type(exc).__name__}: {exc}"
            return False

        finally:
            self.build_seconds = time.perf_counter() - started

    def choose_level(self, visible_points, target_bins):
        """要求表示密度を超えない、最も細かい階層を選ぶ。"""

        if not self.ensure_built() or not self.levels:
            return None

        desired_block = max(
            1,
            int(np.ceil(visible_points / max(1, target_bins)))
        )

        for level in self.levels:
            if level["block_size"] >= desired_block:
                return level

        return self.levels[-1]

    def _scale_extrema(self, raw_min, raw_max):
        offset = self.samples.yorig + self.samples.yref
        scaled_a = (
            np.asarray(raw_min, dtype=np.float64) - offset
        ) * self.samples.yinc
        scaled_b = (
            np.asarray(raw_max, dtype=np.float64) - offset
        ) * self.samples.yinc

        return (
            np.minimum(scaled_a, scaled_b),
            np.maximum(scaled_a, scaled_b)
        )

    def downsample_window(self, x, i0, i1, target_bins):
        """
        選択階層の完全ブロックと、両端の元データを組み合わせる。

        両端を元データから求めることで、表示範囲外のピークが境界へ
        混入しない。返却点のmin/maxは指定範囲の厳密な値を保つ。
        """

        visible_points = max(0, int(i1) - int(i0))
        if visible_points == 0:
            return (
                np.empty(0, dtype=np.float64),
                np.empty(0, dtype=np.float64)
            )

        level = self.choose_level(visible_points, target_bins)
        if level is None:
            return None

        block_size = int(level["block_size"])
        first_full_block = (i0 + block_size - 1) // block_size
        after_full_block = i1 // block_size

        # 表示範囲がブロック境界をまたがない場合でも、従来方式なら
        # 対象範囲だけを走査するため十分軽い。
        if after_full_block <= first_full_block:
            return None

        x_parts = []
        y_parts = []

        def append_raw_edge(start, stop):
            if stop <= start:
                return
            raw_view = self.samples.raw[start:stop]
            raw_min = np.min(raw_view)
            raw_max = np.max(raw_view)
            y_min, y_max = self._scale_extrema(raw_min, raw_max)
            x_value = float(x[start])
            x_parts.append(
                np.asarray([x_value, x_value], dtype=np.float64)
            )
            y_parts.append(
                np.asarray(
                    [float(y_max), float(y_min)],
                    dtype=np.float64
                )
            )

        left_stop = min(i1, first_full_block * block_size)
        append_raw_edge(i0, left_stop)

        middle_start = first_full_block
        middle_stop = min(
            after_full_block,
            len(level["raw_min"])
        )
        if middle_stop > middle_start:
            raw_min = level["raw_min"][middle_start:middle_stop]
            raw_max = level["raw_max"][middle_start:middle_stop]
            y_min, y_max = self._scale_extrema(raw_min, raw_max)

            source_indices = (
                np.arange(
                    middle_start,
                    middle_stop,
                    dtype=np.float64
                )
                * block_size
            )
            if isinstance(x, UniformTimeAxis):
                x_middle = (
                    source_indices - x.xref
                ) * x.xinc + x.xorig
            else:
                x_middle = np.asarray(
                    x[source_indices.astype(np.int64)],
                    dtype=np.float64
                )

            x_parts.append(np.repeat(x_middle, 2))
            y_middle = np.empty(
                len(x_middle) * 2,
                dtype=np.float64
            )
            y_middle[0::2] = y_max
            y_middle[1::2] = y_min
            y_parts.append(y_middle)

        right_start = max(i0, after_full_block * block_size)
        append_raw_edge(right_start, i1)

        if not x_parts:
            return None

        return np.concatenate(x_parts), np.concatenate(y_parts)


def parse_preamble(pre_text: str):
    parts = [
        p.strip()
        for p in pre_text.strip().strip(';').split(',')
        if p.strip() != ""
    ]

    if len(parts) < 10:
        raise ValueError(
            f"PREが10項目ではありません（{len(parts)}項目）: {parts}"
        )

    fmt = int(float(parts[0]))
    mode = int(float(parts[1]))
    points = int(float(parts[2]))
    count = int(float(parts[3]))
    xinc = float(parts[4])
    xorig = float(parts[5])
    xref = float(parts[6])
    yinc = float(parts[7])
    yorig = float(parts[8])
    yref = float(parts[9])

    return dict(
        fmt=fmt,
        mode=mode,
        points=points,
        count=count,
        xinc=xinc,
        xorig=xorig,
        xref=xref,
        yinc=yinc,
        yorig=yorig,
        yref=yref
    )


def _binary_payload_region(path: Path):
    """
    ファイル内の実ペイロード位置と長さを返す。

    取得ツールが保存するヘッダなしBINと、旧Viewerが扱っていた
    IEEE 488.2/TMCブロックの両方に対応する。
    """

    file_size = path.stat().st_size

    with path.open("rb") as f:
        prefix = f.read(2)

        if (
            len(prefix) == 2
            and prefix[0:1] == b"#"
            and prefix[1:2].isdigit()
        ):
            ndigits = int(prefix[1:2])

            if ndigits == 0:
                # ヘッダなしRAWの先頭2 byteが偶然 b"#0" になる
                # 可能性があるため、不定長ブロックはRAWとして扱う。
                return 0, file_size

            size_text = f.read(ndigits)

            if (
                len(size_text) != ndigits
                or not size_text.isdigit()
            ):
                return 0, file_size

            payload_size = int(size_text)
            offset = 2 + ndigits

            if offset + payload_size > file_size:
                raise ValueError(
                    f"TMCペイロードが途中で切れています: "
                    f"declared={payload_size}, "
                    f"available={file_size - offset}"
                )

            trailing_size = (
                file_size
                - offset
                - payload_size
            )

            # RAWデータ先頭が偶然 #N... に一致した場合を除外する。
            # 正常な definite-length block の後ろは空、CR、LF、CRLF。
            if trailing_size > 2:
                return 0, file_size

            if trailing_size:
                f.seek(offset + payload_size)
                trailing = f.read(trailing_size)

                if any(ch not in (10, 13) for ch in trailing):
                    return 0, file_size

            return offset, payload_size

    return 0, file_size


def _memmap_payload(path: Path, dtype):
    dtype = np.dtype(dtype)
    offset, payload_size = _binary_payload_region(path)

    if payload_size % dtype.itemsize != 0:
        raise ValueError(
            f"{path.name}のサイズ{payload_size} byteは"
            f"{dtype.itemsize} byte/点で割り切れません"
        )

    count = payload_size // dtype.itemsize

    if count == 0:
        return np.empty(0, dtype=dtype)

    return np.memmap(
        path,
        dtype=dtype,
        mode="r",
        offset=offset,
        shape=(count,)
    )


def load_analog(bin_path: Path, pre_path: Path):
    pre = parse_preamble(
        pre_path.read_text(
            encoding="utf-8",
            errors="ignore"
        )
    )

    if pre["fmt"] == 0:
        # BYTE
        yraw = _memmap_payload(
            bin_path,
            np.uint8
        )

    elif pre["fmt"] == 1:
        # WORD
        yraw = _memmap_payload(
            bin_path,
            "<u2"
        )

    elif pre["fmt"] == 2:
        # R12 single-BIN format:
        # RG03 analog payload is already little-endian float32 volts.
        # yinc=1, yorig=0, yref=0 means ScaledAnalogSamples remains compatible
        # with the existing min/max cache, cursor and CSV code.
        yraw = _memmap_payload(
            bin_path,
            "<f4"
        )

    else:
        raise ValueError(
            f"未対応のformat={pre['fmt']}（BYTE/WORD/FLOAT32想定）"
        )

    n = len(yraw)

    pre["point_count_matches"] = (
        pre["points"] <= 0
        or n == pre["points"]
    )

    if not pre["point_count_matches"]:
        pre["point_count_warning"] = (
            "PREの点数とBINの点数が不一致: "
            f"PRE={pre['points']}, BIN={n}"
        )

    pre["loaded_points"] = n
    pre["payload_bytes"] = (
        n * np.dtype(yraw.dtype).itemsize
    )

    x = UniformTimeAxis(
        n,
        pre["xinc"],
        pre["xorig"],
        pre["xref"]
    )

    y = ScaledAnalogSamples(
        yraw,
        pre
    )

    return x, y, pre


def load_digital_group_bytes(bin_path: Path, pre_path: Path):
    pre = parse_preamble(
        pre_path.read_text(
            encoding="utf-8",
            errors="ignore"
        )
    )

    b = _memmap_payload(
        bin_path,
        np.uint8
    )

    n = len(b)

    pre["loaded_points"] = n
    pre["point_count_matches"] = (
        pre["points"] <= 0
        or n == pre["points"]
    )

    if not pre["point_count_matches"]:
        pre["point_count_warning"] = (
            "PREの点数とBINの点数が不一致: "
            f"PRE={pre['points']}, BIN={n}"
        )

    x = UniformTimeAxis(
        n,
        pre["xinc"],
        pre["xorig"],
        pre["xref"]
    )

    return x, b, pre


def _load_optional_json(path: Path):
    if not path.exists():
        return None

    try:
        return json.loads(
            path.read_text(
                encoding="utf-8",
                errors="strict"
            )
        )
    except Exception:
        # 補助メタデータの破損だけで波形表示を止めない。
        return None


def resolve_dataset_root(folder: Path) -> Path:
    """新旧レイアウトのどちらでもデータセットルートを返す。"""
    folder = Path(folder)
    if (folder / "acquisition.json").exists() or (folder / "capture_summary.json").exists():
        return folder
    if folder.name.lower() in {"analog", "digital"}:
        parent = folder.parent
        if (parent / "acquisition.json").exists() or (parent / "capture_summary.json").exists():
            return parent
    return folder


def load_dataset_metadata(folder: Path):
    """波形表示を止めずに、取得結果と有効範囲の補助情報を読む。"""

    folder = resolve_dataset_root(folder)
    acquisition = _load_optional_json(
        folder / "acquisition.json"
    )
    summary = _load_optional_json(
        folder / "capture_summary.json"
    )
    capture_error = _load_optional_json(
        folder / "capture_error.json"
    )
    analog_time_range = _load_optional_json(
        folder / "analog_time_range.json"
    )
    digital_coverage = _load_optional_json(
        folder / "digital_coverage.json"
    )
    digital_configuration = _load_optional_json(
        folder / "digital_configuration.json"
    )
    capture_tool_version = _load_optional_json(
        folder / "capture_tool_version.json"
    )
    digital_segment_manifest = _load_optional_json(
        folder / "digital_segment_manifest.json"
    )

    if (
        digital_coverage is None
        and isinstance(summary, dict)
    ):
        digital = summary.get("digital")
        if isinstance(digital, dict):
            candidate = digital.get("coverage")
            if isinstance(candidate, dict):
                digital_coverage = candidate

    return dict(
        acquisition=acquisition,
        dataset_root=folder,
        capture_summary=summary,
        capture_error=capture_error,
        analog_time_range=analog_time_range,
        digital_coverage=digital_coverage,
        digital_configuration=digital_configuration,
        capture_tool_version=capture_tool_version,
        digital_segment_manifest=digital_segment_manifest
    )


def missing_digital_data_message(folder: Path, metadata, digital):
    """設定済みなのにイベント波形がないデータセットを診断する。"""

    if digital:
        return None

    acquisition = metadata.get("acquisition")
    if isinstance(acquisition, dict):
        channels = acquisition.get("channels", {})
        digital_manifest = (
            channels.get("digital", {})
            if isinstance(channels, dict)
            else {}
        )
        if (
            isinstance(digital_manifest, dict)
            and digital_manifest.get("source") == "instrument_memory_waveform_bin"
        ):
            return (
                "このr8データセットはMHO984本体側の同期Memory BIN保存までは完了していますが、"
                "PC側D0-D15デコードが未完了です。\n\n"
                "MHO984から保存BINをPCへコピーし、次を実行してください。\n"
                "  python mho984_bin_decoder_r8.py <BIN> <このデータセットフォルダ>\n\n"
                "デコード成功後、このViewerでCH1-CH4とD0-D15を同一時間軸表示できます。"
            )

    configuration = metadata.get("digital_configuration")
    if not isinstance(configuration, dict):
        return None

    enabled = configuration.get("digital_channels")
    enabled_names = []
    if isinstance(enabled, dict):
        enabled_names = sorted(
            (
                name
                for name, state in enabled.items()
                if state
            ),
            key=lambda name: int(name[1:])
        )

    expected = "、".join(enabled_names) if enabled_names else "D0～D15"
    return (
        f"{expected} はオシロスコープ上で有効に設定されていますが、\n"
        "このデータセットには digital/digital_events.bin または "
        "digital/digital_events.csv（旧形式ではルート）がありません。\n\n"
        "デジタル波形は設定JSONだけから復元できません。取得処理が "
        "BUSイベント読出しまで完了したデータセットを指定してください。"
    )


def digital_coverage_xlim(meta):
    """メタデータから描画してよいデジタル時間範囲を返す。"""

    if not isinstance(meta, dict):
        return None

    coverage = meta.get("coverage")
    if not isinstance(coverage, dict):
        return None

    try:
        start = float(coverage["valid_time_start_s"])
        end = float(coverage["valid_time_end_s"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None

    if (
        not np.isfinite(start)
        or not np.isfinite(end)
        or end < start
    ):
        return None

    return start, end


def load_digital_events(
    path: Path,
    *,
    calibration_mode="Raw",
    calibration_ppm=DEFAULT_DIGITAL_TIME_CALIBRATION_PPM,
    calibration_reference_s=DEFAULT_DIGITAL_TIME_CALIBRATION_REFERENCE_S,
):
    """
    現行取得ツールの16-bit Parallel BUSイベント列を読む。

    BIN: little-endian <float64 time_s, uint16 bus_value>
    CSV: D0-D15列 / data_hex / data_decimal の順に安全に復元（BINがない場合のフォールバック）
    """

    if path.suffix.lower() == ".bin":
        records = _memmap_payload(
            path,
            DIGITAL_EVENT_DTYPE
        )

        times = records["time_s"]
        bus_values = records["bus_value"]

    elif path.suffix.lower() == ".csv":
        times_list = []
        values_list = []

        with path.open(
            "r",
            encoding="utf-8-sig",
            newline=""
        ) as f:
            reader = csv.DictReader(f)

            if not reader.fieldnames:
                raise ValueError("digital_events.csvにヘッダがありません")

            for row_number, row in enumerate(reader, start=2):
                try:
                    time_s = float(row["time_s"])

                    # Compatibility priority:
                    #   1) explicit D0..D15 columns (most trustworthy)
                    #   2) data_hex (accept both 0x0100 and 0100 as HEX)
                    #   3) data_decimal
                    # This avoids interpreting a HEX-looking value such as
                    # "0100" as decimal 100 (=0x0064), which would make
                    # unrelated digital channels appear to chatter.
                    explicit_bits = []
                    have_all_bits = True
                    for bit_index in range(16):
                        key = f"D{bit_index}"
                        text = row.get(key, "")
                        if text is None or str(text).strip() == "":
                            have_all_bits = False
                            break
                        bit_value = int(float(str(text).strip()))
                        if bit_value not in (0, 1):
                            raise ValueError(f"{key} is not 0/1")
                        explicit_bits.append(bit_value)

                    if have_all_bits:
                        bus_value = 0
                        for bit_index, bit_value in enumerate(explicit_bits):
                            bus_value |= (bit_value & 1) << bit_index
                    else:                        hex_text = str(row.get("data_hex", "") or "").strip()
                        dec_text = str(row.get("data_decimal", "") or "").strip()

                        if hex_text:
                            if hex_text.lower().startswith("0x"):
                                bus_value = int(hex_text, 16)
                            else:
                                bus_value = int(hex_text, 16)
                        elif dec_text:
                            bus_value = int(dec_text, 10)
                        else:
                            raise ValueError(
                                "D0-D15/data_hex/data_decimal のいずれもありません"
                            )

                    if not 0 <= bus_value <= 0xFFFF:
                        raise ValueError("16-bit範囲外")

                except Exception as e:
                    raise ValueError(
                        f"digital_events.csvの{row_number}行目が不正です: {e}"
                    ) from e

                times_list.append(time_s)
                values_list.append(bus_value)

        times = np.asarray(
            times_list,
            dtype=np.float64
        )
        bus_values = np.asarray(
            values_list,
            dtype=np.uint16
        )

    else:
        raise ValueError(
            f"未対応のデジタルイベント形式です: {path.name}"
        )

    if len(times) == 0:
        raise ValueError("デジタルイベントが0件です")

    if not np.all(np.isfinite(times)):
        raise ValueError("デジタルイベント時刻にNaN/Infがあります")

    if np.any(np.diff(times) < 0):
        raise ValueError("デジタルイベント時刻が昇順ではありません")

    folder = path.parent
    dataset_root = resolve_dataset_root(folder)
    parse_report = _load_optional_json(
        folder / "digital_parse_report.json"
    )
    configuration = _load_optional_json(
        dataset_root / "digital_configuration.json"
    )
    dataset_metadata = load_dataset_metadata(dataset_root)

    mode_text = str(calibration_mode).strip()
    corrected = mode_text.lower() not in ("raw", "生", "未補正")
    calibration_ppm = float(calibration_ppm)
    calibration_reference_s = float(calibration_reference_s)
    if not np.isfinite(calibration_ppm):
        raise ValueError("Digital calibration ppm must be finite")
    scale_factor = 1.0 + calibration_ppm * 1e-6
    if scale_factor <= 0 or not np.isfinite(scale_factor):
        raise ValueError("Digital calibration scale must be positive")

    raw_times = times
    if corrected:
        times = (
            calibration_reference_s
            + (np.asarray(raw_times, dtype=np.float64) - calibration_reference_s)
            * scale_factor
        )

    coverage_meta = dataset_metadata.get("digital_coverage")
    if isinstance(coverage_meta, dict):
        coverage_meta = dict(coverage_meta)
        if corrected:
            for key in ("valid_time_start_s", "valid_time_end_s"):
                if key in coverage_meta:
                    try:
                        raw_value = float(coverage_meta[key])
                        coverage_meta[key] = (
                            calibration_reference_s
                            + (raw_value - calibration_reference_s) * scale_factor
                        )
                    except Exception:
                        pass
            if (
                "valid_time_start_s" in coverage_meta
                and "valid_time_end_s" in coverage_meta
            ):
                coverage_meta["valid_duration_s"] = (
                    float(coverage_meta["valid_time_end_s"])
                    - float(coverage_meta["valid_time_start_s"])
                )

    meta = dict(
        source_format="event_table",
        source_file=path.name,
        record_format="<float64 time_s, uint16 bus_value>",
        event_count=len(times),
        parse_report=parse_report,
        underlying_source=(
            "RG03_memory_BIN_LA"
            if isinstance(parse_report, dict)
            and parse_report.get("source_format") == "RG03"
            else "BUS_event_table"
        ),
        complete_uniform_raw=(
            isinstance(parse_report, dict)
            and parse_report.get("source_format") == "RG03"
            and isinstance(parse_report.get("digital"), dict)
            and bool(parse_report["digital"].get("uniform_bus_file"))
        ),
        configuration=configuration,
        coverage=coverage_meta,
        digital_time_mode=("Calibrated" if corrected else "Raw"),
        digital_time_calibration_ppm=calibration_ppm,
        digital_time_scale_factor=scale_factor,
        digital_time_reference_s=calibration_reference_s,
        capture_summary=dataset_metadata.get("capture_summary"),
        acquisition=dataset_metadata.get("acquisition"),
        synchronization=(
            dataset_metadata.get("acquisition", {}).get("synchronization")
            if isinstance(dataset_metadata.get("acquisition"), dict)
            else None
        ),
        segment_manifest=dataset_metadata.get("digital_segment_manifest")
    )

    return times, bus_values, meta


def discover_channels(folder: Path):
    """
    Discover Analog + Digital channels.

    R13.0 compatibility fix:
      - decoder-side RG03 record names are CH1..CH4
      - Viewer display names are CHAN1..CHAN4
      - older/newer acquisition.json may therefore contain either key form

    Also, finding Digital data must NOT stop the Analog fallback scan.
    This fixes the case where D0-D15 appeared normally while the Analog
    channel list remained empty even though analog/chanN.bin existed.
    """
    root = resolve_dataset_root(folder)
    analog = []
    digital = []

    manifest = _load_optional_json(root / "acquisition.json")
    analog_dir = root / "analog"
    digital_dir = root / "digital"

    # ------------------------------------------------------------
    # Manifest discovery
    # ------------------------------------------------------------
    if isinstance(manifest, dict):
        channels = manifest.get("channels", {})
        if not isinstance(channels, dict):
            channels = {}

        analog_manifest = channels.get("analog", {})
        if isinstance(analog_manifest, dict):

            # Make manifest lookup robust to CH1 vs CHAN1 and case.
            normalized_analog_manifest = {
                str(key).upper(): value
                for key, value in analog_manifest.items()
            }

            for n in range(1, 5):
                display_name = f"CHAN{n}"

                entry = normalized_analog_manifest.get(
                    display_name
                )
                if not isinstance(entry, dict):
                    entry = normalized_analog_manifest.get(
                        f"CH{n}"
                    )

                if not isinstance(entry, dict):
                    continue

                b = root / entry.get(
                    "data",
                    f"analog/chan{n}.bin"
                )
                p = root / entry.get(
                    "preamble",
                    f"analog/chan{n}_pre.txt"
                )

                if b.exists() and p.exists():
                    analog.append(
                        (display_name, b, p)
                    )

        digital_manifest = channels.get("digital", {})
        if isinstance(digital_manifest, dict):
            rel = digital_manifest.get(
                "data",
                "digital/digital_events.bin"
            )
            events_path = root / rel

            if not events_path.exists():
                csv_rel = digital_manifest.get(
                    "csv",
                    "digital/digital_events.csv"
                )
                events_path = root / csv_rel

            if events_path.exists():
                for n in range(16):
                    digital.append(
                        (f"D{n}", events_path, None)
                    )

    # ------------------------------------------------------------
    # Filesystem fallback / supplement
    #
    # IMPORTANT:
    # Never return early merely because Digital was found.
    # Analog and Digital are supplemented independently.
    # ------------------------------------------------------------
    if not analog:
        search_analog_dir = (
            analog_dir
            if analog_dir.is_dir()
            else root
        )

        for n in range(1, 5):
            b = search_analog_dir / f"chan{n}.bin"
            p = search_analog_dir / f"chan{n}_pre.txt"

            if b.exists() and p.exists():
                analog.append(
                    (f"CHAN{n}", b, p)
                )

    if not digital:
        search_digital_dir = (
            digital_dir
            if digital_dir.is_dir()
            else root
        )

        # Standard filename plus lock-safe re-decode versioned filenames.
        # Prefer the newest BIN, then the newest CSV.
        bin_candidates = [
            p for p in search_digital_dir.glob("digital_events*.bin")
            if p.is_file() and "calibrated" not in p.stem.lower()
        ]
        csv_candidates = [
            p for p in search_digital_dir.glob("digital_events*.csv")
            if p.is_file()
        ]
        if bin_candidates:
            events_path = max(bin_candidates, key=lambda p: p.stat().st_mtime)
        elif csv_candidates:
            events_path = max(csv_candidates, key=lambda p: p.stat().st_mtime)
        else:
            events_path = search_digital_dir / "digital_events.bin"

        if events_path.exists():
            for n in range(16):
                digital.append(
                    (f"D{n}", events_path, None)
                )
        else:
            # Legacy: dN.bin + dN_pre.txt
            for n in range(16):
                b = root / f"d{n}.bin"
                p = root / f"d{n}_pre.txt"

                if b.exists() and p.exists():
                    digital.append(
                        (f"D{n}", b, p)
                    )

    return analog, digital


class DigitalBusSource:
    """
    D0～D15で共有する16-bitイベントBUS。

    各チャンネルの全長0/1配列は作らず、時刻とBUS値を1組だけ保持する。
    現在の表示範囲を一度だけ切り出し、その結果を全Dチャンネルで共有する。
    """

    def __init__(self, times, bus_values, meta):
        if len(times) != len(bus_values):
            raise ValueError(
                "デジタルBUSの時刻数とデータ数が一致しません"
            )

        self.times = times
        self.bus_values = bus_values
        self.meta = meta
        self._window_key = None
        self._window_value = None
        self._bit_render_cache = {}
        self.window_build_count = 0
        self._transition_count_cache = {}

    @staticmethod
    def _normalized_range(value):
        if value is None:
            return None
        left, right = value
        return (
            float(min(left, right)),
            float(max(left, right))
        )

    def window(self, xmin, xmax, reference_xlim=None):
        """表示範囲の時刻、BUS値、隣接BUS変化マスクを共有して返す。"""

        coverage_xlim = digital_coverage_xlim(self.meta)
        key = (
            float(min(xmin, xmax)),
            float(max(xmin, xmax)),
            self._normalized_range(reference_xlim),
            self._normalized_range(coverage_xlim)
        )

        if self._window_key == key and self._window_value is not None:
            return self._window_value

        x_window, bus_window = digital_event_window(
            self.times,
            self.bus_values,
            xmin,
            xmax,
            reference_xlim=reference_xlim,
            coverage_xlim=coverage_xlim
        )
        bus_window = np.asarray(bus_window, dtype=np.uint16)

        if len(bus_window) >= 2:
            change_mask = np.bitwise_xor(
                bus_window[:-1],
                bus_window[1:]
            )
        else:
            change_mask = np.empty(0, dtype=np.uint16)

        value = (x_window, bus_window, change_mask)
        self._window_key = key
        self._window_value = value
        self._bit_render_cache = {}
        self.window_build_count += 1
        return value

    def downsample_bit(self, bit, target_points):
        """現在の共有窓から1bitを展開し、同一条件の結果を再利用する。"""

        if self._window_value is None:
            raise RuntimeError("先にDigitalBusSource.window()が必要です")

        key = (int(bit), int(target_points))
        cached = self._bit_render_cache.get(key)
        if cached is not None:
            return cached

        result = downsample_digital_bus_edges(
            *self._window_value,
            int(bit),
            int(target_points)
        )
        self._bit_render_cache[key] = result
        return result

    def state_at(self, bit, cursor_x):
        """元BUSから指定時刻の1bit状態を直接返す。"""

        if cursor_x is None or len(self.times) == 0:
            return None

        valid_range = digital_coverage_xlim(self.meta)
        if valid_range is None:
            valid_range = (
                float(self.times[0]),
                float(self.times[-1])
            )

        left, right = self._normalized_range(valid_range)
        cursor_x = float(cursor_x)
        if cursor_x < left or cursor_x > right:
            return None

        index = int(
            np.searchsorted(
                self.times,
                cursor_x,
                side="right"
            )
        ) - 1
        index = max(0, min(index, len(self.bus_values) - 1))
        return int(
            (int(self.bus_values[index]) >> int(bit)) & 1
        )

    def bit_state_at_record_edge(self, bit, first=True):
        if len(self.bus_values) == 0:
            return None
        index = 0 if first else -1
        return int(
            (int(self.bus_values[index]) >> int(bit)) & 1
        )

    def transition_count(self, bit, chunk_size=1_000_000):
        """全長D配列を作らず、BUSを分割して指定bitの変化数を数える。"""

        bit = int(bit)
        if bit in self._transition_count_cache:
            return self._transition_count_cache[bit]

        mask = np.uint16(1 << bit)
        count = 0
        previous_value = None

        for start in range(0, len(self.bus_values), chunk_size):
            stop = min(len(self.bus_values), start + chunk_size)
            values = np.asarray(
                self.bus_values[start:stop],
                dtype=np.uint16
            )
            if len(values) == 0:
                continue

            if previous_value is not None:
                if (previous_value ^ int(values[0])) & int(mask):
                    count += 1

            if len(values) >= 2:
                changes = np.bitwise_xor(values[:-1], values[1:])
                count += int(
                    np.count_nonzero(
                        np.bitwise_and(changes, mask) != 0
                    )
                )
            previous_value = int(values[-1])

        self._transition_count_cache[bit] = count
        return count


# ============================================================
# 動的間引き
# ============================================================

def _searchsorted(x, value, side="left"):
    if isinstance(x, UniformTimeAxis):
        return x.searchsorted(
            value,
            side=side
        )

    return int(
        np.searchsorted(
            x,
            value,
            side=side
        )
    )


def _visible_slice_indices(x, xmin, xmax):
    """
    xが単調増加の前提で、
    可視範囲のスライスインデックスを返す。

    境界の1点外側も含める。
    """
    if xmin > xmax:
        xmin, xmax = xmax, xmin

    i0 = _searchsorted(
        x,
        xmin,
        side="left"
    ) - 1

    i1 = _searchsorted(
        x,
        xmax,
        side="right"
    ) + 1

    i0 = max(i0, 0)
    i1 = min(i1, len(x))

    return i0, i1


def downsample_analog_window(
    x,
    y,
    i0,
    i1,
    target_bins,
    pyramid=None
):
    """
    可視範囲だけをmin/max間引きする。

    ScaledAnalogSamplesの場合はADCコードのmemmap上でmin/maxを
    求め、出力点だけを電圧変換する。全25 M点の時間・電圧配列を
    RAMへ展開しない。
    """

    n = max(0, i1 - i0)

    if n == 0:
        return (
            np.empty(0, dtype=np.float64),
            np.empty(0, dtype=np.float64)
        )

    if (
        not isinstance(y, ScaledAnalogSamples)
        or target_bins <= 1
        or n <= target_bins * 2
    ):
        return downsample_minmax(
            x[i0:i1],
            y[i0:i1],
            target_bins
        )

    if (
        isinstance(pyramid, AnalogMinMaxPyramid)
        and n >= ANALOG_CACHE_MIN_POINTS
    ):
        cached_result = pyramid.downsample_window(
            x,
            i0,
            i1,
            target_bins
        )
        if cached_result is not None:
            return cached_result

    pts_per_bin = int(
        np.ceil(n / target_bins)
    )

    if pts_per_bin <= 1:
        return x[i0:i1], y[i0:i1]

    starts = np.arange(
        0,
        n,
        pts_per_bin,
        dtype=np.int64
    )
    raw_view = y.raw[i0:i1]

    raw_max = np.maximum.reduceat(
        raw_view,
        starts
    ).astype(
        np.float64
    )
    raw_min = np.minimum.reduceat(
        raw_view,
        starts
    ).astype(
        np.float64
    )

    offset = y.yorig + y.yref
    scaled_a = (raw_max - offset) * y.yinc
    scaled_b = (raw_min - offset) * y.yinc

    y_max = np.maximum(scaled_a, scaled_b)
    y_min = np.minimum(scaled_a, scaled_b)

    x_left = x[
        i0:i1:pts_per_bin
    ]
    x_out = np.repeat(
        x_left,
        2
    )
    y_out = np.empty(
        len(starts) * 2,
        dtype=np.float64
    )
    y_out[0::2] = y_max
    y_out[1::2] = y_min

    return x_out, y_out


def sample_minmax(y, i0, i1, chunk_points=1_000_000):
    """指定範囲の厳密な最小・最大を低メモリで返す。"""

    if i1 <= i0:
        return None

    ymin = None
    ymax = None

    for start in range(i0, i1, chunk_points):
        stop = min(i1, start + chunk_points)
        values = y[start:stop]

        if len(values) == 0:
            continue

        part_min = float(np.min(values))
        part_max = float(np.max(values))

        ymin = (
            part_min
            if ymin is None
            else min(ymin, part_min)
        )
        ymax = (
            part_max
            if ymax is None
            else max(ymax, part_max)
        )

    if ymin is None:
        return None

    return ymin, ymax


def downsample_minmax(x, y, target_bins):
    """
    min/maxエンベロープ方式。

    ビンごとに最大・最小を残すことで、
    スパイクなどを消さない。
    """
    n = len(x)

    if target_bins <= 1 or n <= target_bins * 2:
        return x, y

    pts_per_bin = int(
        np.ceil(n / target_bins)
    )

    if pts_per_bin <= 1:
        return x, y

    starts = np.arange(
        0,
        n,
        pts_per_bin,
        dtype=np.int64
    )

    y_max = np.maximum.reduceat(
        y,
        starts
    )
    y_min = np.minimum.reduceat(
        y,
        starts
    )

    x_left = x[starts]

    x_out = np.repeat(
        x_left,
        2
    )

    y_out = np.empty(
        len(starts) * 2,
        dtype=np.float64
    )

    y_out[0::2] = y_max
    y_out[1::2] = y_min

    return x_out, y_out


def downsample_digital_edges(x, dig01, target_points):
    """
    デジタルは変化点（エッジ）を優先して残す。
    """
    n = len(x)

    if n <= target_points:
        return x, dig01

    edges = np.nonzero(
        np.diff(dig01) != 0
    )[0]

    idx = np.unique(
        np.concatenate(
            (
                [0],
                edges,
                edges + 1,
                [n - 1]
            )
        )
    )

    x_e = x[idx]
    d_e = dig01[idx]

    if len(x_e) <= target_points:
        return x_e, d_e

    step = max(
        1,
        len(x_e) // target_points
    )

    return (
        x_e[::step],
        d_e[::step]
    )


def downsample_digital_bus_edges(
    x,
    bus_values,
    change_mask,
    bit,
    target_points
):
    """
    共通16-bit BUSから指定bitの変化点だけを表示用に取り出す。

    全長のD0～D15配列を生成せず、BUSの隣接XORも表示範囲につき1回だけ
    計算されたchange_maskを再利用する。
    """

    x = np.asarray(x)
    bus_values = np.asarray(bus_values, dtype=np.uint16)
    point_count = len(x)

    if point_count == 0:
        return (
            np.empty(0, dtype=np.float64),
            np.empty(0, dtype=np.uint8)
        )

    if point_count != len(bus_values):
        raise ValueError(
            "表示用デジタルBUSの時刻数とデータ数が一致しません"
        )

    mask = np.uint16(1 << int(bit))

    if point_count <= target_points:
        states = (
            (bus_values >> int(bit)) & 1
        ).astype(np.uint8)
        return x, states

    if len(change_mask) != point_count - 1:
        change_mask = np.bitwise_xor(
            bus_values[:-1],
            bus_values[1:]
        )

    edges = np.flatnonzero(
        np.bitwise_and(change_mask, mask) != 0
    )

    # 変化前・変化後を必ず対で残す。単純に点列を等間隔抽出すると
    # 片側だけが落ち、存在しないパルス幅に見える場合がある。
    max_edges = max(1, (int(target_points) - 2) // 2)
    if len(edges) > max_edges:
        selected_positions = np.linspace(
            0,
            len(edges) - 1,
            num=max_edges,
            dtype=np.int64
        )
        edges = edges[selected_positions]

    indices = np.unique(
        np.concatenate(
            (
                np.asarray([0], dtype=np.int64),
                edges,
                edges + 1,
                np.asarray([point_count - 1], dtype=np.int64)
            )
        )
    )

    states = (
        (bus_values[indices] >> int(bit)) & 1
    ).astype(np.uint8)
    return x[indices], states


def digital_event_window(
    event_x,
    event_state,
    xmin,
    xmax,
    reference_xlim=None,
    coverage_xlim=None
):
    """
    イベント列を可視時間範囲のステップ波形へ変換する。

    現行取得ツールのデジタルデータは変化時刻だけを持つ。
    最初・最後の状態を保持するのは、取得側が保証した時間範囲内
    だけとする。範囲情報がない旧データは、観測イベントの先頭から
    末尾までだけを有効とみなし、未取得区間へ0/1を延長しない。
    """

    event_x = np.asarray(event_x)
    event_state = np.asarray(event_state)

    if len(event_x) == 0:
        return (
            np.empty(0, dtype=np.float64),
            np.empty(0, dtype=np.uint8)
        )

    if len(event_x) != len(event_state):
        raise ValueError(
            "デジタルイベントの時刻数と状態数が一致しません"
        )

    left = float(min(xmin, xmax))
    right = float(max(xmin, xmax))

    if reference_xlim is not None:
        ref_left, ref_right = reference_xlim
        ref_min = float(min(ref_left, ref_right))
        ref_max = float(max(ref_left, ref_right))
        ref_left = ref_min
        ref_right = ref_max
        left = max(left, ref_left)
        right = min(right, ref_right)

    if coverage_xlim is None:
        coverage_left = float(event_x[0])
        coverage_right = float(event_x[-1])
    else:
        coverage_left = float(
            min(coverage_xlim[0], coverage_xlim[1])
        )
        coverage_right = float(
            max(coverage_xlim[0], coverage_xlim[1])
        )

    if (
        not np.isfinite(coverage_left)
        or not np.isfinite(coverage_right)
    ):
        raise ValueError(
            "デジタル有効時間範囲にNaN/Infがあります"
        )

    left = max(left, coverage_left)
    right = min(right, coverage_right)

    if right < left:
        return (
            np.empty(0, dtype=np.float64),
            np.empty(0, dtype=event_state.dtype)
        )

    # left時点の状態。有効範囲内に限り、最初のイベントより前は
    # 最初の記録状態、最後のイベントより後は最後の記録状態を保持。
    previous_index = int(
        np.searchsorted(
            event_x,
            left,
            side="right"
        )
    ) - 1
    previous_index = max(
        0,
        min(previous_index, len(event_state) - 1)
    )

    first_inside = int(
        np.searchsorted(
            event_x,
            left,
            side="right"
        )
    )
    after_inside = int(
        np.searchsorted(
            event_x,
            right,
            side="right"
        )
    )

    inside_x = np.asarray(
        event_x[first_inside:after_inside],
        dtype=np.float64
    )
    inside_state = np.asarray(
        event_state[first_inside:after_inside]
    )

    x_parts = [
        np.asarray([left], dtype=np.float64),
        inside_x
    ]
    state_parts = [
        np.asarray(
            [event_state[previous_index]],
            dtype=event_state.dtype
        ),
        inside_state
    ]

    last_x = (
        float(inside_x[-1])
        if len(inside_x)
        else left
    )

    if right > last_x:
        last_state = (
            inside_state[-1]
            if len(inside_state)
            else event_state[previous_index]
        )
        x_parts.append(
            np.asarray([right], dtype=np.float64)
        )
        state_parts.append(
            np.asarray(
                [last_state],
                dtype=event_state.dtype
            )
        )

    return (
        np.concatenate(x_parts),
        np.concatenate(state_parts)
    )


# ============================================================
# UI アプリ
# ============================================================


class ChannelChecklist(ttk.Frame):
    """スクロール可能なチャンネル選択チェックリスト。"""

    STATUS_COLORS = {
        "active": "#137333",
        "high": "#8A5A00",
        "low": "#4B5563",
        "available": "#365F91",
        "missing": "#B3261E",
        "neutral": "#5F6368",
    }

    def __init__(self, master, height=180):
        super().__init__(master)

        self._names = []
        self._variables = []
        self._rows = []
        self._status_labels = {}
        self._change_callback = None
        self._suspend_callback = False

        self.canvas = tk.Canvas(
            self,
            height=height,
            highlightthickness=0,
            borderwidth=0
        )
        self.scrollbar = ttk.Scrollbar(
            self,
            orient=tk.VERTICAL,
            command=self.canvas.yview
        )
        self.inner = ttk.Frame(self.canvas)
        self._window_id = self.canvas.create_window(
            (0, 0),
            window=self.inner,            anchor="nw"
        )
        self.canvas.configure(
            yscrollcommand=self.scrollbar.set
        )

        self.canvas.pack(
            side=tk.LEFT,
            fill=tk.BOTH,
            expand=True
        )
        self.scrollbar.pack(
            side=tk.RIGHT,
            fill=tk.Y
        )

        self.inner.bind(
            "<Configure>",
            self._on_inner_configure
        )
        self.canvas.bind(
            "<Configure>",
            self._on_canvas_configure
        )
        # R9: bind_all() は使わない。Windows の native filedialog が
        # 開いている最中にもグローバル MouseWheel callback が発火すると、
        # Tcl/Tk の winfo_parent()/nametowidget() と競合して UI が停止する
        # ケースがあるため、チェックリスト配下の widget のみに直接 bind する。
        self._bind_mousewheel(self.canvas)
        self._bind_mousewheel(self.inner)

    def _on_inner_configure(self, _event=None):
        self.canvas.configure(
            scrollregion=self.canvas.bbox("all")
        )

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(
            self._window_id,
            width=max(1, event.width)
        )

    def _bind_mousewheel(self, widget):
        """このチェックリスト配下だけにホイール処理を登録する。"""
        widget.bind(
            "<MouseWheel>",
            self._on_mousewheel,
            add="+"
        )

    def _on_mousewheel(self, event):
        # R9: pointer 位置から widget tree を探索しない。
        # この callback 自体が checklist 内でのみ発火するため、
        # winfo_parent()/nametowidget() は不要。
        if not event.delta:
            return "break"

        steps = int(-event.delta / 120)
        if steps == 0:
            steps = -1 if event.delta > 0 else 1

        try:
            self.canvas.yview_scroll(steps, "units")
        except tk.TclError:
            pass
        return "break"

    def set_change_callback(self, callback):
        self._change_callback = callback

    def _notify_change(self):
        if (
            not self._suspend_callback
            and self._change_callback is not None
        ):
            self._change_callback(None)

    def _normalize_index(self, index):
        if index in (tk.END, "end"):
            return max(0, len(self._names) - 1)
        return int(index)

    def delete(self, first, last=None):
        del first, last
        self._suspend_callback = True
        try:
            for row in self._rows:
                row.destroy()
            self._names.clear()
            self._variables.clear()
            self._rows.clear()
            self._status_labels.clear()
        finally:
            self._suspend_callback = False
        self._on_inner_configure()

    def insert(self, index, name):
        del index
        variable = tk.BooleanVar(value=False)
        row = ttk.Frame(self.inner, padding=(2, 1))
        row.columnconfigure(0, weight=1)

        check = ttk.Checkbutton(
            row,
            text=name,
            variable=variable,
            command=self._notify_change
        )
        check.grid(
            row=0,
            column=0,
            sticky="w"
        )

        status = tk.Label(
            row,
            text="",
            anchor="e",
            foreground=self.STATUS_COLORS["neutral"],
            borderwidth=0
        )
        status.grid(
            row=0,
            column=1,
            sticky="e",
            padx=(4, 2)
        )
        row.pack(fill=tk.X)

        # 動的に追加される行とその子 widget にもローカル bind を付ける。
        self._bind_mousewheel(row)
        self._bind_mousewheel(check)
        self._bind_mousewheel(status)

        self._names.append(str(name))
        self._variables.append(variable)
        self._rows.append(row)
        self._status_labels[str(name)] = status

    def curselection(self):
        return tuple(
            index
            for index, variable in enumerate(self._variables)
            if variable.get()
        )

    def selection_set(self, first, last=None):
        if not self._variables:
            return
        first_index = self._normalize_index(first)
        last_index = (
            first_index
            if last is None
            else self._normalize_index(last)
        )
        first_index, last_index = sorted((first_index, last_index))
        for index in range(
            max(0, first_index),
            min(len(self._variables) - 1, last_index) + 1
        ):
            self._variables[index].set(True)

    def selection_clear(self, first, last=None):
        if not self._variables:
            return
        first_index = self._normalize_index(first)
        last_index = (
            first_index
            if last is None
            else self._normalize_index(last)
        )
        first_index, last_index = sorted((first_index, last_index))
        for index in range(
            max(0, first_index),
            min(len(self._variables) - 1, last_index) + 1
        ):
            self._variables[index].set(False)

    def select_names(self, names, replace=True):
        wanted = set(names)
        self._suspend_callback = True
        try:
            for name, variable in zip(self._names, self._variables):
                if replace:
                    variable.set(name in wanted)
                elif name in wanted:
                    variable.set(True)
        finally:
            self._suspend_callback = False
        self._notify_change()

    def selected_names(self):
        return [
            self._names[index]
            for index in self.curselection()
        ]

    def set_status(self, name, text, kind="neutral"):
        label = self._status_labels.get(name)
        if label is None:
            return
        label.configure(
            text=text,
            foreground=self.STATUS_COLORS.get(
                kind,
                self.STATUS_COLORS["neutral"]
            )
        )


class SimpleToolTip:
    """テキスト付きボタンの補足説明を表示する軽量ツールチップ。"""

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.window = None
        self.after_id = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._hide()
        self.after_id = self.widget.after(500, self._show)

    def _show(self):
        if self.window is not None or not self.text:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self.window,
            text=self.text,
            justify=tk.LEFT,
            background="#FFF8DC",
            relief=tk.SOLID,
            borderwidth=1,
            padx=7,
            pady=4
        ).pack()

    def _hide(self, _event=None):
        if self.after_id is not None:
            try:
                self.widget.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None
        if self.window is not None:
            self.window.destroy()
            self.window = None

class App(tk.Tk):

    def __init__(self):
        super().__init__()

        self.title(
            f"Unofficial MHO984 Toolkit Viewer — {VIEWER_TOOL_VERSION}"
        )

        self.geometry("1580x960")
        self.minsize(1180, 720)

        # ----------------------------------------------------
        # データ
        # ----------------------------------------------------

        self.dataset_dir = None

        self.analog_list = []
        self.digital_list = []
        self.analog_source_cache = {}
        self.digital_event_cache = {}
        self.dataset_metadata = {}

        # Per-channel waveform colors. User overrides are persisted in the
        # home folder so they survive Viewer/package updates.
        self.channel_colors = {}
        self.channel_color_settings_path = (
            Path.home() / ".mho984_viewer_channel_colors.json"
        )
        self._load_channel_color_settings()

        self.loaded_items = []
        self.digital_activity = {}
        self._selection_after_id = None
        self._busy = False
        self._render_refresh_active = False
        self._last_render_signature = None
        self._high_res_rendering_active = False
        self._canvas_size_capped = False

        # R13.1: use a larger canvas automatically on 4K-class displays.
        # The user can toggle back to the original 1600x900 safety ceiling.
        try:
            screen_w = int(self.winfo_screenwidth())
            screen_h = int(self.winfo_screenheight())
        except tk.TclError:
            screen_w = 0
            screen_h = 0

        self._plot_4k_capable = (
            screen_w >= PLOT_4K_SCREEN_MIN_WIDTH
            and screen_h >= PLOT_4K_SCREEN_MIN_HEIGHT
        )
        self._plot_large_mode = bool(self._plot_4k_capable)

        self._plot_resize_after_id = None
        self._plot_pending_size = None
        self._plot_resize_request_count = 0
        self._plot_resize_apply_count = 0
        self._plot_applied_size = (
            PLOT_INITIAL_WIDTH,
            PLOT_INITIAL_HEIGHT
        )

        # ----------------------------------------------------
        # 描画
        # ----------------------------------------------------

        self.axis_map = {}
        self.axes_all = []

        self.line_map = {}

        self.xlim_cid = None

        self.full_xlim = None

        # ----------------------------------------------------
        # Protocol overlay (beta.6)
        # ----------------------------------------------------
        self.protocol_overlay_enabled = tk.BooleanVar(value=True)
        self.protocol_overlay_doc = None
        self.protocol_overlay_events = []
        self.protocol_overlay_starts = []
        self.protocol_overlay_artists = []
        self.protocol_overlay_mtime_ns = None
        self.protocol_overlay_poll_after_id = None
        self.protocol_overlay_status_var = tk.StringVar(value="プロトコル帯: なし")
        self.protocol_overlay_alpha_var = tk.DoubleVar(value=0.16)
        self.protocol_overlay_label_mode_var = tk.StringVar(value="自動")

        # ----------------------------------------------------
        # カーソル
        # ----------------------------------------------------

        # A/Bを基本カーソルとして持つ。
        #
        # 各要素:
        # {
        #     "name": "A",
        #     "x": None,
        #     "color": "tab:red"
        # }

        self.cursors = [
            {
                "name": "A",
                "x": None,
                "color": "tab:red"
            },
            {
                "name": "B",
                "x": None,
                "color": "tab:blue"
            }
        ]

        # キーボードで操作するカーソル
        self.active_cursor_index = 0

        # クリックで次に配置するカーソル
        self.cursor_next_index = 0

        # カーソル文字情報
        self.cursor_text = None

        # 縦線など
        self.cursor_artists = []

        # 電圧ラベル
        self.cursor_value_artists = []

        # ----------------------------------------------------
        # Span zoom
        # ----------------------------------------------------

        self.span = None
        self.span_active = False

        # ----------------------------------------------------
        # Pan
        # ----------------------------------------------------

        self.pan_dragging = False
        self.pan_start_x = None
        self.pan_start_xlim = None
        self.cursor_dragging_index = None
        self.cursor_hover_index = None
        self._canvas_cursor_name = ""

        # ----------------------------------------------------
        # Shift状態
        # ----------------------------------------------------

        self.shift_down = False

        # ----------------------------------------------------
        # アクティブCH / Yズーム
        # ----------------------------------------------------

        self.active_channel_index = 0

        self.yzoom_factor = 1.25

        # ====================================================
        # R4 UI: 上部操作 → 状態 → CH選択 → 波形 → 詳細
        # ====================================================

        style = ttk.Style(self)
        style.configure(
            "Primary.TButton",
            font=("Segoe UI", 10, "bold"),
            padding=(12, 7)
        )
        style.configure(
            "Action.TButton",
            padding=(9, 6)
        )
        style.configure(
            "Section.TLabelframe",
            padding=7
        )
        style.configure(
            "Section.TLabelframe.Label",
            font=("Segoe UI", 10, "bold")
        )
        style.configure(
            "Hint.TLabel",
            foreground="#5F6368"
        )

        self.view_mode = tk.StringVar(value="stacked")
        self.expand_group = tk.BooleanVar(value=False)
        self.dig_separate_axis = tk.BooleanVar(value=True)
        self.cursor_mode = tk.BooleanVar(value=True)
        self.cursor_choice = tk.StringVar(value="A")
        self.cursor_count_var = tk.StringVar(value="2本")
        self.analog_choice = tk.StringVar(value="(アナログなし)")
        self.folder_path_var = tk.StringVar(
            value="データセットはまだ開かれていません"
        )
        self.banner_title_var = tk.StringVar(
            value="データセットを開いてください"
        )
        self.banner_detail_var = tk.StringVar(
            value="mho984_XXXX フォルダを選択すると、自動で内容を確認します。"
        )
        self.status_var = tk.StringVar(
            value="準備完了 — まず［データセットを開く］を押してください"
        )
        self.pointer_var = tk.StringVar(value="")
        self.measurement_hint_var = tk.StringVar(
            value="A/B/C…を複数追加できます。線の近くでは↔カーソルになり、そのまま左ドラッグで移動できます。A～Zキー選択、←→で1サンプル、Shift+←→で10サンプル移動。右ドラッグは時間軸パンです。"
        )

        # ----------------------------------------------------
        # R12: Analog / Digital synchronization analysis
        # ----------------------------------------------------
        self.sync_analog_var = tk.StringVar(value="")
        self.sync_digital_var = tk.StringVar(value="D0")
        self.sync_threshold_var = tk.StringVar(value="Auto")
        self.sync_slope_var = tk.StringVar(value="Both")
        self.sync_tolerance_ns_var = tk.StringVar(value="20")
        self.sync_result_cache = None

        # ----------------------------------------------------
        # R13.4: automatic timing measurements / scope selection
        # ----------------------------------------------------
        self.auto_measure_cache = {}
        self.auto_measure_after_id = None
        self.auto_measure_scope_var = tk.StringVar(value="全範囲")
        self.auto_pair_results_cache = {}

        # ----------------------------------------------------
        # R13.5: Digital time-axis calibration
        # ----------------------------------------------------
        self.digital_time_mode_var = tk.StringVar(value="Raw")
        self.digital_time_calibration_ppm_var = tk.StringVar(
            value=f"{DEFAULT_DIGITAL_TIME_CALIBRATION_PPM:.9f}"
        )
        self.digital_time_calibration_reference_s = (
            DEFAULT_DIGITAL_TIME_CALIBRATION_REFERENCE_S
        )
        self.digital_time_calibration_source = "reference profile from one tested MHO984 (mho984_0034); not universal"

        # ----------------------------------------------------
        # 上部の主要操作
        # ----------------------------------------------------

        command_bar = ttk.Frame(self, padding=(8, 7))
        command_bar.pack(side=tk.TOP, fill=tk.X)

        open_button = ttk.Button(
            command_bar,
            text="データセットを開く...",
            command=self.open_folder,
            style="Primary.TButton"
        )
        open_button.pack(side=tk.LEFT, padx=(0, 8))
        SimpleToolTip(
            open_button,
            "取得ツールが作成した mho984_XXXX フォルダを選択します。"
        )

        ttk.Separator(
            command_bar,
            orient=tk.VERTICAL
        ).pack(side=tk.LEFT, fill=tk.Y, padx=4)

        reset_button = ttk.Button(
            command_bar,
            text="全体表示",
            command=self.reset_view,
            style="Action.TButton"
        )
        reset_button.pack(side=tk.LEFT, padx=3)
        SimpleToolTip(reset_button, "アナログ記録の全時間範囲へ戻します（R）。")

        self.span_button = ttk.Button(
            command_bar,
            text="範囲拡大: OFF",
            command=self.toggle_span_zoom,
            style="Action.TButton"
        )
        self.span_button.pack(side=tk.LEFT, padx=3)
        SimpleToolTip(
            self.span_button,
            "ONにして波形上を横ドラッグすると、その範囲を拡大します（Z）。"
        )

        digital_zoom_button = ttk.Button(
            command_bar,
            text="デジタル範囲",
            command=self.zoom_to_digital_range,
            style="Action.TButton"
        )
        digital_zoom_button.pack(side=tk.LEFT, padx=3)
        SimpleToolTip(
            digital_zoom_button,
            "検証済みのデジタルイベント時間範囲へ移動します。"
        )

        self.cursor_mode_button = ttk.Button(
            command_bar,
            text="カーソル測定: ON",
            command=self.toggle_cursor_mode,
            style="Action.TButton"
        )
        self.cursor_mode_button.pack(side=tk.LEFT, padx=3)
        SimpleToolTip(
            self.cursor_mode_button,
            "波形クリックで測定カーソルを置くモードを切り替えます。"
        )

        export_button = ttk.Button(
            command_bar,
            text="アナログCSV保存",
            command=self.export_csv,
            style="Action.TButton"
        )
        export_button.pack(side=tk.LEFT, padx=3)

        color_button = ttk.Button(
            command_bar,
            text="チャンネル色...",
            command=self.open_channel_color_dialog,
            style="Action.TButton"
        )
        color_button.pack(side=tk.LEFT, padx=3)
        SimpleToolTip(
            color_button,
            "CH1～CH4 / D0～D15の波形色を任意に設定します。設定は次回起動にも保持されます。"
        )

        self.plot_size_button = ttk.Button(
            command_bar,
            text=(
                "波形サイズ: 4K拡大"
                if self._plot_large_mode
                else "波形サイズ: 安全"
            ),
            command=self.toggle_plot_canvas_size,
            style="Action.TButton"
        )
        self.plot_size_button.pack(side=tk.LEFT, padx=3)
        SimpleToolTip(
            self.plot_size_button,
            "4K拡大: 最大2400×1350px。安全: 従来の最大1600×900px。"
            "動作が重い場合は安全モードへ戻せます。"
        )

        help_button = ttk.Button(
            command_bar,
            text="操作ガイド",
            command=self.show_operation_guide,
            style="Action.TButton"
        )
        help_button.pack(side=tk.RIGHT, padx=(8, 0))

        # ----------------------------------------------------
        # 取得結果の状態バー
        # ----------------------------------------------------

        self.banner = tk.Frame(
            self,
            background="#E8F0FE",
            padx=12,
            pady=7
        )
        self.banner.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(0, 6))

        self.banner_icon = tk.Label(
            self.banner,
            text="●",
            font=("Segoe UI", 15, "bold"),
            foreground="#1967D2",
            background="#E8F0FE"
        )
        self.banner_icon.pack(side=tk.LEFT, padx=(0, 9))

        banner_text = tk.Frame(self.banner, background="#E8F0FE")
        banner_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.banner_title = tk.Label(
            banner_text,
            textvariable=self.banner_title_var,
            font=("Segoe UI", 10, "bold"),
            anchor="w",
            background="#E8F0FE"
        )
        self.banner_title.pack(anchor="w")
        self.banner_detail = tk.Label(
            banner_text,
            textvariable=self.banner_detail_var,
            anchor="w",
            justify=tk.LEFT,
            wraplength=1400,
            background="#E8F0FE"
        )
        self.banner_detail.pack(anchor="w")

        # ----------------------------------------------------
        # 最下部ステータス
        # ----------------------------------------------------

        status_bar = ttk.Frame(self, padding=(8, 4))
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(
            status_bar,
            textvariable=self.status_var
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.progress = ttk.Progressbar(
            status_bar,
            mode="indeterminate",
            length=120
        )
        ttk.Label(
            status_bar,
            textvariable=self.pointer_var
        ).pack(side=tk.RIGHT, padx=(10, 0))

        # ----------------------------------------------------
        # メイン3ペイン
        # ----------------------------------------------------

        panes = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        panes.pack(
            side=tk.TOP,
            fill=tk.BOTH,
            expand=True,
            padx=8,
            pady=(0, 6)
        )

        left = ttk.Frame(panes, width=300, padding=(2, 0, 7, 0))
        center = ttk.Frame(panes, padding=(0, 0))
        detail = ttk.Frame(panes, width=350, padding=(7, 0, 0, 0))
        panes.add(left, weight=0)
        panes.add(center, weight=1)
        panes.add(detail, weight=0)

        # ----------------------------------------------------
        # 左: 作業順序とチェック式チャンネル選択
        # ----------------------------------------------------

        ttk.Label(
            left,
            text="1. データセット",
            font=("Segoe UI", 10, "bold")
        ).pack(anchor="w", pady=(1, 2))
        ttk.Label(
            left,
            textvariable=self.folder_path_var,
            style="Hint.TLabel",
            wraplength=275,
            justify=tk.LEFT
        ).pack(fill=tk.X, pady=(0, 7))

        analog_box = ttk.LabelFrame(
            left,
            text="2. アナログチャンネル",
            style="Section.TLabelframe"
        )
        analog_box.pack(fill=tk.X, pady=(0, 7))
        self.lb_a = ChannelChecklist(analog_box, height=112)
        self.lb_a.pack(fill=tk.X, expand=True)
        self.lb_a.set_change_callback(self.request_channel_refresh)

        analog_buttons = ttk.Frame(analog_box)
        analog_buttons.pack(fill=tk.X, pady=(5, 0))
        ttk.Button(
            analog_buttons,
            text="すべて選択",
            command=self.select_all_analog
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 2))
        ttk.Button(
            analog_buttons,
            text="解除",
            command=self.clear_analog_selection
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(2, 0))

        digital_box = ttk.LabelFrame(
            left,
            text="3. デジタルチャンネル D0–D15",
            style="Section.TLabelframe"
        )
        digital_box.pack(fill=tk.BOTH, expand=True, pady=(0, 7))
        ttk.Label(
            digital_box,
            text="初期状態ではON/OFFしたチャンネルだけを選択します。",
            style="Hint.TLabel",
            wraplength=270,
            justify=tk.LEFT
        ).pack(anchor="w", pady=(0, 4))
        self.lb_d = ChannelChecklist(digital_box, height=300)
        self.lb_d.pack(fill=tk.BOTH, expand=True)
        self.lb_d.set_change_callback(self.request_channel_refresh)

        digital_buttons_1 = ttk.Frame(digital_box)
        digital_buttons_1.pack(fill=tk.X, pady=(5, 2))
        ttk.Button(
            digital_buttons_1,
            text="変化あり",
            command=self.select_active_digital
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 2))
        ttk.Button(
            digital_buttons_1,
            text="すべて",
            command=self.select_all_digital
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        ttk.Button(
            digital_buttons_1,
            text="解除",
            command=self.clear_digital_selection
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(2, 0))

        digital_buttons_2 = ttk.Frame(digital_box)
        digital_buttons_2.pack(fill=tk.X)
        ttk.Button(
            digital_buttons_2,
            text="D0–D7",
            command=lambda: self.select_digital_range(0, 7)
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 2))
        ttk.Button(
            digital_buttons_2,
            text="D8–D15",
            command=lambda: self.select_digital_range(8, 15)
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(2, 0))

        # ----------------------------------------------------
        # 中央: 波形
        # ----------------------------------------------------

        self.plot_host = tk.Frame(
            center,
            background=PLOT_BACKGROUND,
            borderwidth=0
        )

        self.fig = plt.Figure(
            figsize=(
                PLOT_INITIAL_WIDTH / 100,
                PLOT_INITIAL_HEIGHT / 100
            ),
            dpi=100,
            facecolor=PLOT_BACKGROUND
        )
        self.canvas = FigureCanvasTkAgg(
            self.fig,
            master=self.plot_host
        )
        self.toolbar = NavigationToolbar2Tk(
            self.canvas,
            center,
            pack_toolbar=False
        )
        self.toolbar.update()
        self.toolbar.pack(side=tk.BOTTOM, fill=tk.X)
        self.plot_host.pack(
            side=tk.TOP,
            fill=tk.BOTH,
            expand=True
        )
        self.canvas_widget = self.canvas.get_tk_widget()
        try:
            self.canvas_widget.configure(
                background=PLOT_BACKGROUND,
                highlightthickness=0
            )
        except tk.TclError:
            pass
        self.canvas_widget.place(
            relx=0.5,
            rely=0.5,
            anchor="center",
            width=PLOT_INITIAL_WIDTH,
            height=PLOT_INITIAL_HEIGHT
        )
        self.plot_host.bind(
            "<Configure>",
            self.on_plot_host_configure,
            add="+"
        )

        # ----------------------------------------------------
        # 右: 測定 / 表示 / 情報 / 診断
        # ----------------------------------------------------

        self.detail_tabs = ttk.Notebook(detail)
        self.detail_tabs.pack(fill=tk.BOTH, expand=True)

        measure_tab = ttk.Frame(self.detail_tabs, padding=9)
        display_tab = ttk.Frame(self.detail_tabs, padding=9)
        data_tab = ttk.Frame(self.detail_tabs, padding=6)
        diagnostic_tab = ttk.Frame(self.detail_tabs, padding=6)
        # Frequently used operations first.  Less frequently used information
        # and diagnostics are kept to the right so the normal measurement flow
        # is easier to follow on 4K/high-DPI displays.
        sync_tab = ttk.Frame(self.detail_tabs, padding=9)
        auto_measure_tab = ttk.Frame(self.detail_tabs, padding=9)
        protocol_tab = ttk.Frame(self.detail_tabs, padding=9)

        self.detail_tabs.add(display_tab, text="波形・表示")
        self.detail_tabs.add(measure_tab, text="カーソル")
        self.detail_tabs.add(auto_measure_tab, text="自動測定")
        self.detail_tabs.add(protocol_tab, text="プロトコル")
        self.detail_tabs.add(sync_tab, text="同期確認")
        self.detail_tabs.add(data_tab, text="データ情報")
        self.detail_tabs.add(diagnostic_tab, text="診断")
        ttk.Label(protocol_tab,text="Viewerプロトコルオーバーレイ",font=("Segoe UI",11,"bold")).pack(anchor="w")
        ttk.Label(protocol_tab,textvariable=self.protocol_overlay_status_var,style="Hint.TLabel",wraplength=310,justify=tk.LEFT).pack(fill=tk.X,pady=(2,8))
        ttk.Checkbutton(protocol_tab,text="プロトコル帯を波形上へ表示",variable=self.protocol_overlay_enabled,command=self._protocol_overlay_toggle).pack(anchor="w",pady=2)
        alpha_row=ttk.Frame(protocol_tab); alpha_row.pack(fill=tk.X,pady=4)
        ttk.Label(alpha_row,text="帯の濃さ:").pack(side=tk.LEFT)
        ttk.Scale(alpha_row,from_=0.05,to=0.45,variable=self.protocol_overlay_alpha_var,command=lambda _v:self._protocol_overlay_refresh()).pack(side=tk.LEFT,fill=tk.X,expand=True,padx=6)
        label_row=ttk.Frame(protocol_tab); label_row.pack(fill=tk.X,pady=4)
        ttk.Label(label_row,text="ラベル:").pack(side=tk.LEFT)
        cb_overlay_labels=ttk.Combobox(label_row,textvariable=self.protocol_overlay_label_mode_var,values=["自動","多め","少なめ","なし"],state="readonly",width=9)
        cb_overlay_labels.pack(side=tk.LEFT,padx=6); cb_overlay_labels.bind("<<ComboboxSelected>>",lambda _e:self._protocol_overlay_refresh())
        btnrow=ttk.Frame(protocol_tab); btnrow.pack(fill=tk.X,pady=(8,3))
        ttk.Button(btnrow,text="解析画面を開く...",command=self.open_protocol_analyzer).pack(side=tk.LEFT,expand=True,fill=tk.X,padx=(0,2))
        ttk.Button(btnrow,text="帯を再読込",command=self.reload_protocol_overlay).pack(side=tk.LEFT,expand=True,fill=tk.X,padx=2)
        ttk.Button(btnrow,text="帯をクリア",command=self.clear_protocol_overlay).pack(side=tk.LEFT,expand=True,fill=tk.X,padx=(2,0))
        ttk.Label(protocol_tab,text=("Protocol Analyzerでデコードすると、結果が active_protocol_overlay.json としてデータセット内へ保存され、Viewerへ自動反映されます。\n色はイベント種別ごとに分け、エラーイベントは赤系で表示します。"),style="Hint.TLabel",wraplength=310,justify=tk.LEFT).pack(fill=tk.X,pady=(8,0))

        ttk.Label(
            measure_tab,
            text="カーソル測定",
            font=("Segoe UI", 11, "bold")
        ).pack(anchor="w")
        ttk.Label(
            measure_tab,
            textvariable=self.measurement_hint_var,
            style="Hint.TLabel",
            wraplength=310,
            justify=tk.LEFT
        ).pack(fill=tk.X, pady=(2, 8))

        cursor_select_row = ttk.Frame(measure_tab)
        cursor_select_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(
            cursor_select_row,
            text="操作するカーソル:"
        ).pack(side=tk.LEFT)
        self.cursor_combo = ttk.Combobox(
            cursor_select_row,
            textvariable=self.cursor_choice,
            values=["A", "B"],
            state="readonly",
            width=5
        )
        self.cursor_combo.pack(side=tk.LEFT, padx=6)
        self.cursor_combo.bind(
            "<<ComboboxSelected>>",
            self.select_cursor_from_combo
        )
        ttk.Label(
            cursor_select_row,
            textvariable=self.cursor_count_var,
            style="Hint.TLabel"
        ).pack(side=tk.LEFT, padx=(2, 0))

        move_row_1 = ttk.Frame(measure_tab)
        move_row_1.pack(fill=tk.X, pady=2)
        ttk.Button(
            move_row_1,
            text="← 1サンプル",
            command=lambda: self.move_cursor_from_ui(-1, 1)
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 2))
        ttk.Button(
            move_row_1,
            text="1サンプル →",
            command=lambda: self.move_cursor_from_ui(1, 1)
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(2, 0))

        move_row_2 = ttk.Frame(measure_tab)
        move_row_2.pack(fill=tk.X, pady=2)
        ttk.Button(
            move_row_2,
            text="← 10サンプル",
            command=lambda: self.move_cursor_from_ui(-1, 10)
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 2))
        ttk.Button(
            move_row_2,
            text="10サンプル →",
            command=lambda: self.move_cursor_from_ui(1, 10)
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(2, 0))

        cursor_command_row = ttk.Frame(measure_tab)
        cursor_command_row.pack(fill=tk.X, pady=(5, 6))
        ttk.Button(
            cursor_command_row,
            text="＋カーソル",
            command=self.add_cursor
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 2))
        ttk.Button(
            cursor_command_row,
            text="－カーソル",
            command=self.remove_active_cursor
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        ttk.Button(
            cursor_command_row,
            text="位置クリア",
            command=self.clear_cursors
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(2, 0))

        self.measurement_info = tk.Text(
            measure_tab,
            height=22,
            wrap=tk.WORD,
            font=("Segoe UI", 10),
            padx=7,
            pady=7
        )
        self.measurement_info.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            display_tab,
            text="波形の並べ方",
            font=("Segoe UI", 10, "bold")
        ).pack(anchor="w")
        ttk.Radiobutton(
            display_tab,
            text="チャンネルごとに分けて表示",
            variable=self.view_mode,
            value="stacked",
            command=self.redraw
        ).pack(anchor="w", pady=2)
        ttk.Radiobutton(
            display_tab,
            text="同じグラフに重ねて表示",
            variable=self.view_mode,
            value="overlay",
            command=self.redraw
        ).pack(anchor="w", pady=2)

        ttk.Separator(display_tab).pack(fill=tk.X, pady=10)
        ttk.Checkbutton(
            display_tab,
            text="重ね表示時、デジタルを下段へ分離",
            variable=self.dig_separate_axis,
            command=self.redraw
        ).pack(anchor="w", pady=2)
        ttk.Checkbutton(            display_tab,
            text="選択したDチャンネルを8ch単位で展開",
            variable=self.expand_group,
            command=self.on_select
        ).pack(anchor="w", pady=2)

        ttk.Separator(display_tab).pack(fill=tk.X, pady=10)
        ttk.Label(
            display_tab,
            text="アナログY軸",
            font=("Segoe UI", 10, "bold")
        ).pack(anchor="w")
        analog_target_row = ttk.Frame(display_tab)
        analog_target_row.pack(fill=tk.X, pady=(5, 2))
        ttk.Label(
            analog_target_row,
            text="操作対象:"
        ).pack(side=tk.LEFT)
        self.analog_combo = ttk.Combobox(
            analog_target_row,
            textvariable=self.analog_choice,
            values=[],
            state="readonly",
            width=14
        )
        self.analog_combo.pack(side=tk.LEFT, padx=6)
        self.analog_combo.bind(
            "<<ComboboxSelected>>",
            self.select_analog_from_combo
        )
        y_buttons = ttk.Frame(display_tab)
        y_buttons.pack(fill=tk.X, pady=5)
        ttk.Button(
            y_buttons,
            text="自動調整",
            command=self.autoscale_active_y
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 2))
        ttk.Button(
            y_buttons,
            text="拡大",
            command=lambda: self.zoom_active_y(True)
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        ttk.Button(
            y_buttons,
            text="縮小",
            command=lambda: self.zoom_active_y(False)
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(2, 0))

        ttk.Label(
            display_tab,
            text=(
                "右ドラッグ: 時間移動\n"
                "ホイール: X軸拡大・縮小\n"
                "Shift+ホイール: 時間移動"
            ),
            style="Hint.TLabel",
            justify=tk.LEFT
        ).pack(anchor="w", pady=(12, 0))

        ttk.Separator(display_tab).pack(fill=tk.X, pady=10)
        ttk.Label(
            display_tab,
            text="4K表示: 自動拡大",
            font=("Segoe UI", 10, "bold")
        ).pack(anchor="w")
        ttk.Label(
            display_tab,
            text=(
                "最大化中は描画面を変更せず、確定後に1回だけ更新します。\n"
                "描画面は最大1600×900 pxに制限してTkAggのハングを防ぎます。"
            ),
            style="Hint.TLabel",
            wraplength=310,
            justify=tk.LEFT
        ).pack(anchor="w", pady=(2, 0))

        self.info = tk.Text(
            data_tab,
            width=40,
            wrap=tk.WORD,
            font=("Segoe UI", 9),
            padx=7,
            pady=7
        )
        self.info.pack(fill=tk.BOTH, expand=True)

        self.diagnostic_info = tk.Text(
            diagnostic_tab,
            width=40,
            wrap=tk.WORD,
            font=("Consolas", 9),
            padx=7,
            pady=7
        )
        self.diagnostic_info.pack(fill=tk.BOTH, expand=True)

        # ----------------------------------------------------
        # R12: Analog / Digital edge synchronization tab
        # ----------------------------------------------------
        ttk.Label(
            sync_tab,
            text="Analog ↔ Digital エッジ時間差",
            font=("Segoe UI", 11, "bold")
        ).pack(anchor="w")
        ttk.Label(
            sync_tab,
            text=(
                "アナログのしきい値交差時刻とDigitalのエッジ時刻を比較します。\n"
                "Δt = Digital edge - Analog threshold crossing"
            ),
            style="Hint.TLabel",
            wraplength=315,
            justify=tk.LEFT
        ).pack(fill=tk.X, pady=(2, 8))

        sync_source = ttk.Frame(sync_tab)
        sync_source.pack(fill=tk.X, pady=2)
        ttk.Label(sync_source, text="Analog:").grid(row=0, column=0, sticky="w")
        self.sync_analog_combo = ttk.Combobox(
            sync_source,
            textvariable=self.sync_analog_var,
            state="readonly",
            width=12,
            values=[]
        )
        self.sync_analog_combo.grid(row=0, column=1, padx=(5, 12), sticky="ew")
        ttk.Label(sync_source, text="Digital:").grid(row=0, column=2, sticky="w")
        self.sync_digital_combo = ttk.Combobox(
            sync_source,
            textvariable=self.sync_digital_var,
            state="readonly",
            width=8,
            values=[f"D{i}" for i in range(16)]
        )
        self.sync_digital_combo.grid(row=0, column=3, padx=(5, 0), sticky="ew")
        self.sync_analog_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.schedule_auto_measurements()
        )
        self.sync_digital_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.schedule_auto_measurements()
        )
        sync_source.columnconfigure(1, weight=1)
        sync_source.columnconfigure(3, weight=1)

        sync_param = ttk.Frame(sync_tab)
        sync_param.pack(fill=tk.X, pady=4)
        ttk.Label(sync_param, text="Threshold [V]:").grid(row=0, column=0, sticky="w")
        ttk.Entry(
            sync_param,
            textvariable=self.sync_threshold_var,
            width=10
        ).grid(row=0, column=1, padx=(5, 10), sticky="w")
        ttk.Label(sync_param, text="Slope:").grid(row=0, column=2, sticky="w")
        self.sync_slope_combo = ttk.Combobox(
            sync_param,
            textvariable=self.sync_slope_var,
            state="readonly",
            width=9,
            values=["Both", "Rising", "Falling"]
        )
        self.sync_slope_combo.grid(row=0, column=3, padx=(5, 0), sticky="w")
        self.sync_slope_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.schedule_auto_measurements()
        )

        sync_tol = ttk.Frame(sync_tab)
        sync_tol.pack(fill=tk.X, pady=2)
        ttk.Label(sync_tol, text="Pair tolerance [ns]:").pack(side=tk.LEFT)
        ttk.Entry(
            sync_tol,
            textvariable=self.sync_tolerance_ns_var,
            width=10
        ).pack(side=tk.LEFT, padx=6)
        ttk.Button(
            sync_tol,
            text="解析",
            command=self.run_sync_analysis
        ).pack(side=tk.RIGHT)

        sync_actions = ttk.Frame(sync_tab)
        sync_actions.pack(fill=tk.X, pady=(4, 6))
        ttk.Button(
            sync_actions,
            text="先頭ペアをA/Bへ",
            command=self.sync_first_pair_to_cursors
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 2))
        ttk.Button(
            sync_actions,
            text="結果をCSV保存",
            command=self.export_sync_analysis_csv
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(2, 0))

        self.sync_info = tk.Text(
            sync_tab,
            height=20,
            wrap=tk.WORD,
            font=("Consolas", 9),
            padx=7,
            pady=7
        )
        self.sync_info.pack(fill=tk.BOTH, expand=True)

        # ----------------------------------------------------
        # R13.4: automatic frequency / period / duty / dual edge delay
        # ----------------------------------------------------
        ttk.Label(
            auto_measure_tab,
            text="Digital 自動測定",
            font=("Segoe UI", 11, "bold")
        ).pack(anchor="w")
        ttk.Label(
            auto_measure_tab,
            text=(
                "選択中のDチャンネルの周波数・周期・High/Low幅・Dutyを測定します。\n"
                "CH1↔D8 と CH2↔D0 は同じ測定範囲で同時にエッジ差と時間ドリフト(ppm)を解析します。\n"
                "時間軸補正は個体依存の相対アライメントです。公開版の初期値はRawです。"
            ),
            style="Hint.TLabel",
            wraplength=315,
            justify=tk.LEFT
        ).pack(fill=tk.X, pady=(2, 7))

        auto_scope_row = ttk.Frame(auto_measure_tab)
        auto_scope_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(auto_scope_row, text="測定範囲:").pack(side=tk.LEFT)
        self.auto_measure_scope_combo = ttk.Combobox(
            auto_scope_row,
            textvariable=self.auto_measure_scope_var,
            values=["全範囲", "表示範囲", "A-B間"],
            state="readonly",
            width=11
        )
        self.auto_measure_scope_combo.pack(side=tk.LEFT, padx=(6, 4))
        self.auto_measure_scope_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.schedule_auto_measurements(40)
        )
        ttk.Label(
            auto_scope_row,
            text="A-B間はA/B両方の配置が必要",
            style="Hint.TLabel"
        ).pack(side=tk.LEFT, padx=(4, 0))

        calibration_row = ttk.Frame(auto_measure_tab)
        calibration_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(calibration_row, text="Digital時間軸:").pack(side=tk.LEFT)
        self.digital_time_mode_combo = ttk.Combobox(
            calibration_row,
            textvariable=self.digital_time_mode_var,
            values=["Raw", "補正後"],
            state="readonly",
            width=8,
        )
        self.digital_time_mode_combo.pack(side=tk.LEFT, padx=(6, 6))
        self.digital_time_mode_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.apply_digital_time_calibration_settings(),
        )
        ttk.Label(calibration_row, text="補正[ppm]:").pack(side=tk.LEFT)
        ttk.Entry(
            calibration_row,
            textvariable=self.digital_time_calibration_ppm_var,
            width=11,
        ).pack(side=tk.LEFT, padx=(4, 4))
        ttk.Button(
            calibration_row,
            text="適用",
            command=self.apply_digital_time_calibration_settings,
        ).pack(side=tk.LEFT)

        auto_action_row = ttk.Frame(auto_measure_tab)
        auto_action_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(
            auto_action_row,
            text="再測定",
            command=self.run_auto_measurements
        ).pack(side=tk.RIGHT)

        self.auto_measure_info = tk.Text(
            auto_measure_tab,
            height=22,
            wrap=tk.WORD,
            font=("Consolas", 9),
            padx=7,
            pady=7
        )
        self.auto_measure_info.pack(fill=tk.BOTH, expand=True)

        self._make_text_read_only(self.measurement_info)
        self._make_text_read_only(self.info)
        self._make_text_read_only(self.diagnostic_info)
        self._make_text_read_only(self.sync_info)
        self._make_text_read_only(self.auto_measure_info)

        # ====================================================
        # Matplotlib Events
        # ====================================================

        self.canvas.mpl_connect(
            "button_press_event",
            self.on_click
        )

        self.canvas.mpl_connect(
            "button_release_event",
            self.on_release
        )

        self.canvas.mpl_connect(
            "motion_notify_event",
            self.on_motion
        )

        self.canvas.mpl_connect(
            "scroll_event",
            self.on_scroll
        )

        self.canvas.mpl_connect(
            "key_press_event",
            self.on_key_press
        )

        self.canvas.mpl_connect(
            "key_release_event",
            self.on_key_release
        )

        self.canvas.mpl_connect(
            "resize_event",
            self.on_canvas_resize
        )

        self.canvas.mpl_connect(
            "figure_leave_event",
            lambda _event: self._set_canvas_mouse_cursor("")
        )

        self.bind("<Control-o>", lambda _event: self.open_folder())
        self.bind("<F1>", lambda _event: self.show_operation_guide())

        self.show_welcome_state()
        self.update_info([])

    # ========================================================
    # R4 UI 共通操作
    # ========================================================

    def _make_text_read_only(self, widget):
        """選択・コピーを許可しつつ、手入力だけを止める。"""

        def block_edit(event):
            if (
                event.state & 0x4
                and event.keysym.lower() in ("a", "c")
            ):
                return None
            return "break"

        widget.bind("<Key>", block_edit)

    def _replace_text(self, widget, text):
        widget.delete("1.0", tk.END)
        widget.insert(tk.END, text)

    def _set_busy(self, busy, message=None):
        self._busy = bool(busy)
        if message is not None:
            self.status_var.set(message)
        if busy:
            self.configure(cursor="watch")
            if not self.progress.winfo_ismapped():
                self.progress.pack(side=tk.RIGHT, padx=(8, 0))
            self.progress.start(12)
        else:
            self.progress.stop()
            if self.progress.winfo_ismapped():
                self.progress.pack_forget()
            self.configure(cursor="")
        self.update_idletasks()

    def _current_plot_canvas_limits(self):
        """Return the active maximum TkAgg canvas size."""
        if self._plot_large_mode:
            return (
                PLOT_4K_MAX_WIDTH,
                PLOT_4K_MAX_HEIGHT,
            )

        return (
            PLOT_SAFE_MAX_WIDTH,
            PLOT_SAFE_MAX_HEIGHT,
        )

    def toggle_plot_canvas_size(self):
        """
        Toggle between the original ultra-safe canvas ceiling and the larger
        4K-oriented ceiling. The plot host is not recreated; only the debounced
        TkAgg canvas dimensions change.
        """
        self._plot_large_mode = not self._plot_large_mode

        if hasattr(self, "plot_size_button"):
            self.plot_size_button.configure(
                text=(
                    "波形サイズ: 4K拡大"
                    if self._plot_large_mode
                    else "波形サイズ: 安全"
                )
            )

        # Force the next size application even if the host itself did not
        # change dimensions.
        self._plot_applied_size = (-1, -1)

        try:
            host_width = max(
                1,
                int(self.plot_host.winfo_width())
            )
            host_height = max(
                1,
                int(self.plot_host.winfo_height())
            )
        except tk.TclError:
            return

        self._plot_pending_size = (
            host_width,
            host_height,
        )

        max_w, max_h = self._current_plot_canvas_limits()
        self.status_var.set(
            (
                f"波形表示を4K拡大モードへ変更しました "
                f"(最大 {max_w}×{max_h}px)"
                if self._plot_large_mode
                else
                f"波形表示を安全モードへ変更しました "
                f"(最大 {max_w}×{max_h}px)"
            )
        )

        if self._plot_resize_after_id is not None:
            try:
                self.after_cancel(
                    self._plot_resize_after_id
                )
            except tk.TclError:
                pass

        try:
            self._plot_resize_after_id = self.after(
                20,
                self._apply_plot_host_size,
            )
        except tk.TclError:
            self._plot_resize_after_id = None

    def on_plot_host_configure(self, event):
        """
        外側フレームの連続サイズ変更を記録するだけに留める。

        Matplotlibキャンバス自身はこの時点で変更しないため、Windowsの
        最大化アニメーション中も巨大PhotoImageの再作成は発生しない。
        """

        width = max(1, int(event.width))
        height = max(1, int(event.height))
        self._plot_pending_size = (width, height)
        self._plot_resize_request_count += 1

        if self._plot_resize_after_id is not None:
            try:
                self.after_cancel(self._plot_resize_after_id)
            except tk.TclError:
                pass

        try:
            self._plot_resize_after_id = self.after(
                PLOT_RESIZE_DEBOUNCE_MS,
                self._apply_plot_host_size
            )
        except tk.TclError:
            self._plot_resize_after_id = None

    def _apply_plot_host_size(self):
        """確定したホスト寸法を安全上限内で標準TkAggへ1回だけ適用する。"""

        pending = self._plot_pending_size
        self._plot_pending_size = None
        self._plot_resize_after_id = None
        if pending is None:
            return

        host_width, host_height = pending
        if host_width < 320 or host_height < 240:
            return

        max_width, max_height = (
            self._current_plot_canvas_limits()
        )

        canvas_width = min(
            host_width,
            max_width,
        )
        canvas_height = min(
            host_height,
            max_height,
        )
        capped = (
            canvas_width < host_width
            or canvas_height < host_height
        )
        self._canvas_size_capped = capped

        requested_size = (canvas_width, canvas_height)
        if requested_size == self._plot_applied_size:
            return

        self._plot_applied_size = requested_size
        self._plot_resize_apply_count += 1
        try:
            self.canvas_widget.place_configure(
                width=canvas_width,
                height=canvas_height
            )
        except tk.TclError:
            return

        if capped:
            mode_name = (
                "4K拡大"
                if self._plot_large_mode
                else "安全"
            )
            self.status_var.set(
                f"{mode_name}表示: 描画面 "
                f"{canvas_width}×{canvas_height}px "
                f"(上限 {max_width}×{max_height}px)"
            )

    def show_welcome_state(self):
        self.fig.clear()
        ax = self.fig.subplots(1, 1)
        ax.set_axis_off()
        ax.text(
            0.5,
            0.62,
            "RIGOL MHO984 Waveform Viewer",
            ha="center",
            va="center",
            fontsize=22,
            fontweight="bold",
            color="#334155",
            transform=ax.transAxes
        )
        ax.text(
            0.5,
            0.46,
            "1. Click [Open Dataset]\n"
            "2. Select an mho984_XXXX folder\n"
            "3. Choose channels in the left panel",
            ha="center",
            va="center",
            fontsize=13,
            linespacing=1.7,
            color=AXIS_FOREGROUND,
            transform=ax.transAxes
        )
        ax.text(
            0.5,
            0.27,
            "Press F1 for the operation guide.",
            ha="center",
            va="center",
            fontsize=10,
            color=AXIS_MUTED_FOREGROUND,
            transform=ax.transAxes
        )
        self.canvas.draw_idle()

    def show_operation_guide(self):
        guide = tk.Toplevel(self)
        guide.title("Unofficial MHO984 Toolkit Viewer 操作ガイド")
        guide.geometry("640x590")
        guide.minsize(520, 460)
        guide.transient(self)

        outer = ttk.Frame(guide, padding=14)
        outer.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            outer,
            text="はじめての操作",
            font=("Segoe UI", 15, "bold")
        ).pack(anchor="w", pady=(0, 8))

        content = tk.Text(
            outer,
            wrap=tk.WORD,
            font=("Segoe UI", 10),
            padx=10,
            pady=10
        )
        content.pack(fill=tk.BOTH, expand=True)
        content.insert(
            tk.END,
            "1. データを開く\n"
            "   ［データセットを開く］から mho984_XXXX フォルダを選びます。\n"
            "   上の状態バーが緑なら、取得データは検証済みです。\n\n"
            "2. チャンネルを選ぶ\n"
            "   左側のチェックをON/OFFします。デジタルは通常、変化のある\n"
            "   チャンネルが自動選択されます。\n\n"
            "3. 時間軸を操作する\n"
            "   ホイール: X軸を拡大・縮小\n"
            "   右ドラッグ: 時間方向へ移動\n"
            "   ［全体表示］: アナログ記録全体へ戻る\n"
            "   ［範囲拡大］: ON後、波形を横ドラッグ\n\n"
            "4. カーソルで測る\n"
            "   ［カーソル測定: ON］で波形をクリックするとA、次にBを配置。\n"
            "   右側の［測定］タブにΔt、周波数、各CHの電圧を表示します。\n"
            "   ←→キーまたはボタンでサンプル単位に移動できます。\n\n"
            "5. 主なショートカット\n"
            "   R: 全体表示 / Z: 範囲拡大ON/OFF / C: カーソルクリア\n"
            "   A～Z: 操作カーソル選択\n"
            "   Ctrl+↑↓: Y軸拡大縮小 / Shift+↑↓: Y軸自動調整\n\n"
            "6. 大容量波形の高速表示\n"
            "   アナログch右側の『高速』は多段min/maxキャッシュ使用中です。\n"
            "   D0～D15は1本の16-bit BUSを共有し、表示範囲だけ展開します。\n"
            "   4K最大化時は標準TkAggを最大2400×1350pxで使用します。上部［波形サイズ］から従来の1600×900px安全モードへ切替できます。\n"
            "   詳細は［診断］タブの『表示高速化』で確認できます。\n\n"
            "状態がPARTIALまたはFAILEDの場合は、右側の［診断］タブを\n"
            "確認してください。"
        )
        self._make_text_read_only(content)
        ttk.Button(
            outer,
            text="閉じる",
            command=guide.destroy
        ).pack(anchor="e", pady=(8, 0))
        guide.bind("<Escape>", lambda _event: guide.destroy())
        guide.focus_set()

    def reset_view(self):
        if self.full_xlim and self.axes_all:
            self.set_xlim_all(*self.full_xlim)
            self.status_var.set("アナログ記録の全時間範囲を表示しました")
        elif self.dataset_dir is None:
            self.status_var.set("先にデータセットを開いてください")

    def toggle_span_zoom(self):
        self.span_active = not self.span_active
        if self.span:
            self.span.set_active(self.span_active)
        self.span_button.configure(
            text=(
                "範囲拡大: ON"
                if self.span_active
                else "範囲拡大: OFF"
            )
        )
        self.status_var.set(
            "波形上を横ドラッグして拡大範囲を選択してください"
            if self.span_active
            else "範囲拡大モードを終了しました"
        )
        self.update_info([])

    def toggle_cursor_mode(self):
        enabled = not self.cursor_mode.get()
        self.cursor_mode.set(enabled)
        self.cursor_mode_button.configure(
            text=(
                "カーソル測定: ON"
                if enabled
                else "カーソル測定: OFF"
            )
        )
        self.measurement_hint_var.set(
            (
                "A/B/C…を複数追加できます。選択中カーソルをクリック位置へ配置し、"
                "その後は次のカーソルへ進みます。"
            )
            if enabled
            else "カーソル配置は停止中です。ボタンをONにすると再開します。"
        )
        self.status_var.set(
            "カーソル測定を開始しました"
            if enabled
            else "カーソル測定を停止しました"
        )

    def request_channel_refresh(self, _event=None):
        """チェック連打時の再描画をまとめる。"""

        if self._selection_after_id is not None:
            try:
                self.after_cancel(self._selection_after_id)
            except tk.TclError:
                pass
        self.status_var.set("選択チャンネルを更新しています...")
        self._selection_after_id = self.after(100, self.on_select)

    def select_all_analog(self):
        self.lb_a.select_names(
            [name for name, _, _ in self.analog_list]
        )

    def clear_analog_selection(self):
        self.lb_a.select_names([])

    def select_all_digital(self):
        self.lb_d.select_names(
            [name for name, _, _ in self.digital_list]
        )

    def clear_digital_selection(self):
        self.lb_d.select_names([])

    def select_active_digital(self):
        active = [
            name
            for name, activity in self.digital_activity.items()
            if int(activity.get("transition_count", 0)) > 0
        ]
        if not active:
            messagebox.showinfo(
                "変化チャンネルなし",
                "ON/OFF変化が確認されたデジタルチャンネルはありません。"
            )
            return
        self.lb_d.select_names(active)

    def select_digital_range(self, start, end):
        available = {name for name, _, _ in self.digital_list}
        names = [
            f"D{number}"
            for number in range(start, end + 1)
            if f"D{number}" in available
        ]
        self.lb_d.select_names(names)

    def _focus_plot_for_keyboard(self):
        """カーソル選択後にキーボード操作を波形Canvasへ戻す。"""
        try:
            self.canvas.get_tk_widget().focus_set()
        except Exception:
            pass

    def select_cursor_from_combo(self, _event=None):
        name = self.cursor_choice.get()
        for index, cursor in enumerate(self.cursors):
            if cursor["name"] == name:
                self.select_cursor(index)
                break
        self.after_idle(self._focus_plot_for_keyboard)

    def _refresh_cursor_combo(self):
        names = [cursor["name"] for cursor in self.cursors]
        self.cursor_combo.configure(values=names)

        if hasattr(self, "cursor_count_var"):
            self.cursor_count_var.set(f"{len(self.cursors)}本")

        if self.cursors:
            self.active_cursor_index = max(
                0,
                min(
                    self.active_cursor_index,
                    len(self.cursors) - 1
                )
            )
            active = self.cursors[self.active_cursor_index]["name"]
            self.cursor_choice.set(active)
        else:
            self.cursor_choice.set("")

    def move_cursor_from_ui(self, direction, step):
        self._move_active_cursor_sample(direction, step)
        self.after_idle(self._focus_plot_for_keyboard)
        self.update_info([])

    def select_analog_from_combo(self, _event=None):
        name = self.analog_choice.get()
        analog_items = [
            item for item in self.loaded_items if item["kind"] == "analog"
        ]
        for index, item in enumerate(analog_items):
            if item["name"] == name:
                self.active_channel_index = index
                self.status_var.set(f"Y軸操作対象を {name} に変更しました")
                self.update_info([])
                break

    def _refresh_analog_combo(self):
        analog_names = [
            item["name"]
            for item in self.loaded_items
            if item["kind"] == "analog"
        ]
        self.analog_combo.configure(values=analog_names)
        if analog_names:
            self.active_channel_index = max(
                0,
                min(self.active_channel_index, len(analog_names) - 1)
            )
            self.analog_choice.set(
                analog_names[self.active_channel_index]
            )
        else:
            self.analog_choice.set("(アナログなし)")

    def autoscale_active_y(self):
        self._autoscale_y(self._get_active_analog_axes())
        self.status_var.set("選択中アナログチャンネルのY軸を自動調整しました")

    def zoom_active_y(self, zoom_in):
        self._zoom_y(
            self._get_active_analog_axes(),
            zoom_in=zoom_in
        )
        self.status_var.set(
            "選択中アナログチャンネルのY軸を拡大しました"
            if zoom_in
            else "選択中アナログチャンネルのY軸を縮小しました"
        )

    # ========================================================
    # Dark plot / channel color settings
    # ========================================================

    def _default_channel_color(self, name):
        """Return a high-visibility default color for a waveform channel."""
        key = str(name).upper()

        if key in DEFAULT_ANALOG_COLORS:
            return DEFAULT_ANALOG_COLORS[key]

        if key.startswith("D"):
            try:
                bit = int(key[1:])
            except ValueError:
                bit = 0
            return DEFAULT_DIGITAL_COLORS[
                bit % len(DEFAULT_DIGITAL_COLORS)
            ]

        return "#FFFFFF"

    def _channel_color(self, name):
        return self.channel_colors.get(
            str(name),
            self._default_channel_color(name)
        )

    def _load_channel_color_settings(self):
        self.channel_colors = {}

        try:
            if not self.channel_color_settings_path.is_file():
                return

            obj = json.loads(
                self.channel_color_settings_path.read_text(
                    encoding="utf-8"
                )
            )

            colors = obj.get("channel_colors", {})
            if not isinstance(colors, dict):
                return

            for name, color in colors.items():
                if (
                    isinstance(name, str)
                    and isinstance(color, str)
                    and color.startswith("#")
                    and len(color) in (4, 7, 9)
                ):
                    self.channel_colors[name] = color

        except Exception:
            # A broken preference file must never prevent waveform viewing.
            self.channel_colors = {}

    def _save_channel_color_settings(self):
        try:
            payload = {
                "version": 1,
                "channel_colors": self.channel_colors,
            }
            self.channel_color_settings_path.write_text(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    indent=2
                ),
                encoding="utf-8"
            )
        except Exception as exc:
            self.status_var.set(
                f"チャンネル色は変更しましたが、設定保存に失敗しました: {exc}"
            )

    def _apply_channel_colors_to_current_plot(self):
        """Apply current user colors without reloading waveform data."""

        for item in self.loaded_items:
            name = item["name"]
            color = self._channel_color(name)

            line = self.line_map.get(name)
            if line is not None:
                line.set_color(color)

            if self.view_mode.get() == "stacked":
                ax = self.axis_map.get(name)
                if ax is not None:
                    ax.title.set_color(color)

        # Legend handles reference the same Line2D objects, but recreating
        # legends ensures text/frame styling remains consistent.
        for ax in self.axes_all:
            legend = ax.get_legend()
            if legend is not None:
                self._style_legend_dark(legend)

        self.canvas.draw_idle()

    def open_channel_color_dialog(self):
        """Open a small dialog that can set an arbitrary color per channel."""

        available = []

        for name, _, _ in self.analog_list:
            if name not in available:
                available.append(name)

        for name, _, _ in self.digital_list:
            if name not in available:
                available.append(name)

        # Allow pre-configuration even before a dataset is opened.
        if not available:
            available.extend(
                ["CHAN1", "CHAN2", "CHAN3", "CHAN4"]
                + [f"D{i}" for i in range(16)]
            )

        dialog = tk.Toplevel(self)
        dialog.title("チャンネル色設定")
        dialog.transient(self)
        dialog.resizable(False, False)
        dialog.grab_set()

        outer = ttk.Frame(
            dialog,
            padding=14
        )
        outer.pack(
            fill=tk.BOTH,
            expand=True
        )

        ttk.Label(
            outer,
            text="チャンネルごとの波形色",
            font=("Segoe UI", 11, "bold")
        ).grid(
            row=0,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(0, 10)
        )

        ttk.Label(
            outer,
            text="チャンネル"
        ).grid(
            row=1,
            column=0,
            sticky="w",
            padx=(0, 8)
        )

        channel_var = tk.StringVar(
            value=available[0]
        )

        combo = ttk.Combobox(
            outer,
            textvariable=channel_var,
            values=available,
            state="readonly",            width=14
        )
        combo.grid(
            row=1,
            column=1,
            sticky="ew"
        )

        preview = tk.Canvas(
            outer,
            width=58,
            height=24,
            background=self._channel_color(available[0]),
            highlightthickness=1,
            highlightbackground="#808080"
        )
        preview.grid(
            row=1,
            column=2,
            padx=(10, 0)
        )

        current_text = tk.StringVar()

        ttk.Label(
            outer,
            textvariable=current_text
        ).grid(
            row=2,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(8, 10)
        )

        def refresh_preview(_event=None):
            name = channel_var.get()
            color = self._channel_color(name)
            preview.configure(background=color)
            current_text.set(
                f"{name}: {color}"
            )

        def choose_color():
            name = channel_var.get()
            initial = self._channel_color(name)

            result = colorchooser.askcolor(
                color=initial,
                parent=dialog,
                title=f"{name} の波形色"
            )

            color = result[1]
            if not color:
                return

            self.channel_colors[name] = color
            self._save_channel_color_settings()
            refresh_preview()
            self._apply_channel_colors_to_current_plot()

            self.status_var.set(
                f"{name} の波形色を {color} に変更しました"
            )

        def reset_one():
            name = channel_var.get()
            self.channel_colors.pop(name, None)
            self._save_channel_color_settings()
            refresh_preview()
            self._apply_channel_colors_to_current_plot()

            self.status_var.set(
                f"{name} を既定色へ戻しました"
            )

        def reset_all():
            if not messagebox.askyesno(
                "既定色へ戻す",
                "すべてのチャンネル色を既定値へ戻しますか？",
                parent=dialog
            ):
                return

            self.channel_colors.clear()
            self._save_channel_color_settings()
            refresh_preview()
            self._apply_channel_colors_to_current_plot()

            self.status_var.set(
                "すべてのチャンネル色を既定値へ戻しました"
            )

        combo.bind(
            "<<ComboboxSelected>>",
            refresh_preview
        )

        buttons = ttk.Frame(outer)
        buttons.grid(
            row=3,
            column=0,
            columnspan=3,
            sticky="ew"
        )

        ttk.Button(
            buttons,
            text="色を選ぶ...",
            command=choose_color,
            style="Primary.TButton"
        ).pack(
            side=tk.LEFT,
            padx=(0, 5)
        )

        ttk.Button(
            buttons,
            text="このCHを既定色",
            command=reset_one
        ).pack(
            side=tk.LEFT,
            padx=5
        )

        ttk.Button(
            buttons,
            text="全CHを既定色",
            command=reset_all
        ).pack(
            side=tk.LEFT,
            padx=5
        )

        ttk.Button(
            buttons,
            text="閉じる",
            command=dialog.destroy
        ).pack(
            side=tk.RIGHT,
            padx=(12, 0)
        )

        refresh_preview()

        dialog.update_idletasks()
        try:
            x = self.winfo_rootx() + (
                self.winfo_width() - dialog.winfo_width()
            ) // 2
            y = self.winfo_rooty() + (
                self.winfo_height() - dialog.winfo_height()
            ) // 2
            dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
        except tk.TclError:
            pass

    def _style_legend_dark(self, legend):
        if legend is None:
            return

        frame = legend.get_frame()
        frame.set_facecolor(LEGEND_BACKGROUND)
        frame.set_edgecolor(SPINE_COLOR)
        frame.set_alpha(0.88)

        for text in legend.get_texts():
            text.set_color(AXIS_FOREGROUND)

    def _style_axis_dark(self, ax):
        ax.set_facecolor(PLOT_BACKGROUND)

        ax.tick_params(
            axis="both",
            colors=AXIS_FOREGROUND
        )

        ax.xaxis.label.set_color(AXIS_FOREGROUND)
        ax.yaxis.label.set_color(AXIS_FOREGROUND)
        ax.title.set_color(AXIS_FOREGROUND)

        for spine in ax.spines.values():
            spine.set_color(SPINE_COLOR)

        # Replace prior default grid styling with a dark-theme grid.
        ax.grid(
            True,
            color=GRID_COLOR,
            alpha=0.50,
            linewidth=0.65
        )

        self._style_legend_dark(
            ax.get_legend()
        )

    def _apply_dark_plot_style(self):
        self.fig.patch.set_facecolor(
            PLOT_BACKGROUND
        )

        for ax in self.axes_all:
            self._style_axis_dark(ax)


    # ========================================================
    # カーソル関連
    # ========================================================

    def _cursor_name(self, index):
        """
        0 -> A
        1 -> B
        ...
        25 -> Z
        26 -> AA
        """
        result = ""
        n = index

        while True:
            result = (
                chr(ord("A") + (n % 26))
                + result
            )

            n = n // 26 - 1

            if n < 0:
                break

        return result

    def add_cursor(self):
        """
        新しいカーソルを追加する。
        最大26本。
        """

        if len(self.cursors) >= 26:
            messagebox.showinfo(
                "カーソル",
                "カーソルは最大26本までです。"
            )
            return

        colors = [
            "tab:red",
            "tab:blue",
            "tab:green",
            "tab:orange",
            "tab:purple",
            "tab:brown",
            "tab:pink",
            "tab:gray",
            "tab:olive",
            "tab:cyan",
        ]

        idx = len(self.cursors)

        name = self._cursor_name(idx)

        color = colors[
            idx % len(colors)
        ]

        x = None

        if self.axes_all:
            xmin, xmax = self.axes_all[0].get_xlim()
            left, right = sorted((float(xmin), float(xmax)))
            width = max(
                right - left,
                np.finfo(float).eps
            )

            # 新しいカーソルを既存カーソルと完全に重ねない。
            # 選択中カーソルの少し右へ置き、右端を越えれば左側へ回す。
            base_x = None
            if self.cursors:
                base_x = self.cursors[
                    self.active_cursor_index
                ].get("x")

            if base_x is None:
                fraction = 0.25 + (idx % 6) * 0.10
                x = left + min(0.85, fraction) * width
            else:
                candidate = float(base_x) + 0.06 * width
                if candidate > right:
                    candidate = left + 0.12 * width
                x = float(candidate)

        self.cursors.append(
            {
                "name": name,
                "x": x,
                "color": color
            }
        )

        self.active_cursor_index = idx
        self.cursor_next_index = idx

        self._refresh_cursor_combo()
        self.render_cursors()
        self.update_info([])
        self.after_idle(self._focus_plot_for_keyboard)

    def remove_active_cursor(self):
        """
        現在選択中のカーソルを削除。

        A/Bの2本は最低限残す。
        """

        if len(self.cursors) <= 2:
            messagebox.showinfo(
                "カーソル",
                "A/Bの2本は基本カーソルとして残します。"
            )
            return

        idx = self.active_cursor_index

        self.cursors.pop(idx)

        colors = [
            "tab:red",
            "tab:blue",
            "tab:green",
            "tab:orange",
            "tab:purple",
            "tab:brown",
            "tab:pink",
            "tab:gray",
            "tab:olive",
            "tab:cyan",
        ]

        for i, cursor in enumerate(
            self.cursors
        ):
            cursor["name"] = (
                self._cursor_name(i)
            )

            cursor["color"] = (
                colors[
                    i % len(colors)
                ]
            )

        self.active_cursor_index = max(
            0,
            min(
                idx,
                len(self.cursors) - 1
            )
        )

        self.cursor_next_index = (
            self.active_cursor_index
        )

        self._refresh_cursor_combo()
        self.render_cursors()
        self.update_info([])
        self.after_idle(self._focus_plot_for_keyboard)

    def select_cursor(self, index):
        if not self.cursors:
            return

        index = max(
            0,
            min(
                index,
                len(self.cursors) - 1
            )
        )

        self.active_cursor_index = index

        # コンボボックス/A-Zキーで選択したカーソルを、
        # 次の左クリックで確実に配置できるようにする。
        self.cursor_next_index = index

        self.cursor_choice.set(
            self.cursors[index]["name"]
        )
        self.render_cursors()
        self.update_info([])
        self.after_idle(self._focus_plot_for_keyboard)

    def clear_cursor_artists(self):
        """
        カーソルの描画オブジェクトを削除。
        """

        for artist in self.cursor_artists:
            try:
                artist.remove()
            except Exception:
                pass

        self.cursor_artists = []

        for artist in self.cursor_value_artists:
            try:
                artist.remove()
            except Exception:
                pass

        self.cursor_value_artists = []

    def clear_cursors(self):
        """
        全カーソルの位置をクリア。
        カーソル自体は残す。
        """

        for cursor in self.cursors:
            cursor["x"] = None

        self.active_cursor_index = 0
        self.cursor_next_index = 0
        self._refresh_cursor_combo()

        self.clear_cursor_artists()

        if self.cursor_text:
            self.cursor_text.set_text("")

        self.canvas.draw_idle()

        self.update_info([])
        if (
            hasattr(self, "auto_measure_scope_var")
            and self.auto_measure_scope_var.get() == "A-B間"
        ):
            self.schedule_auto_measurements(80)

    def _get_voltage_at_cursor(
        self,
        it,
        cursor_x
    ):
        """
        元データからカーソルに最も近い
        サンプルの電圧を取得。

        戻り値:
            (x_sample, voltage)
        """

        if it["kind"] != "analog":
            return None

        x = it["x"]
        y = it["y"]

        if len(x) == 0:
            return None

        idx = int(
            _searchsorted(
                x,
                cursor_x
            )
        )

        if idx <= 0:
            idx = 0

        elif idx >= len(x):
            idx = len(x) - 1

        else:
            if (
                abs(x[idx - 1] - cursor_x)
                <= abs(x[idx] - cursor_x)
            ):
                idx -= 1

        return (
            float(x[idx]),
            float(y[idx])
        )

    def _cursor_annotation_offset(
        self,
        ax,
        x_value,
        y_value,
        cursor_index,
        *,
        x_base=14,
        y_base=14
    ):
        """
        A/B/C…ラベルを交互方向へ配置し、軸端では必ず内側へ逃がす。

        A: 右上
        B: 左下
        C: 右下
        D: 左上
        E以降: 同じ4方向を距離を増やして使用

        これにより特にBラベルが上端/隣接軸へ隠れる問題を防ぐ。
        """

        patterns = (
            (1, 1),    # A
            (-1, -1),  # B
            (1, -1),   # C
            (-1, 1),   # D
        )

        sx, sy = patterns[
            cursor_index % len(patterns)
        ]

        ring = cursor_index // len(patterns)

        dx = sx * (
            x_base
            + ring * 12
        )
        dy = sy * (
            y_base
            + ring * 11
        )

        # データ座標をAxes fractionへ変換し、端では内向きにする。
        try:
            display_xy = ax.transData.transform(
                (float(x_value), float(y_value))
            )
            axes_xy = ax.transAxes.inverted().transform(
                display_xy
            )
            fx = float(axes_xy[0])
            fy = float(axes_xy[1])

            if fx >= 0.78:
                dx = -abs(dx)
            elif fx <= 0.22:
                dx = abs(dx)

            if fy >= 0.76:
                dy = -abs(dy)
            elif fy <= 0.24:
                dy = abs(dy)

        except Exception:
            pass

        return dx, dy


    def _add_cursor_voltage_label(
        self,
        cursor,
        it
    ):
        """
        1つのカーソルと1つのアナログ波形について、
        波形との交点付近に電圧値を表示する。
        """

        cursor_x = cursor["x"]

        if cursor_x is None:
            return

        result = self._get_voltage_at_cursor(
            it,
            cursor_x
        )

        if result is None:
            return

        x_sample, voltage = result

        if self.view_mode.get() == "stacked":
            ax = self.axis_map.get(
                it["name"]
            )
        else:
            ax = self.axis_map.get(
                "ANALOG"
            )

        if ax is None:
            return

        cursor_index = self.cursors.index(
            cursor
        )

        x_offset, y_offset = self._cursor_annotation_offset(
            ax,
            x_sample,
            voltage,
            cursor_index,
            x_base=15,
            y_base=15
        )

        text = (
            f"{cursor['name']}: "
            f"{voltage:.6g} V"
        )

        artist = ax.annotate(
            text,
            xy=(
                x_sample,
                voltage
            ),
            xytext=(
                x_offset,
                y_offset
            ),
            textcoords="offset points",
            fontsize=8,
            color=AXIS_FOREGROUND,
            ha=(
                "left"
                if x_offset >= 0
                else "right"
            ),
            va=(
                "bottom"
                if y_offset >= 0
                else "top"
            ),
            bbox=dict(
                boxstyle="round,pad=0.2",
                fc=LEGEND_BACKGROUND,
                ec=cursor["color"],
                alpha=0.94
            ),
            arrowprops=dict(
                arrowstyle="-",
                color=cursor["color"],
                alpha=0.72,
                lw=0.8
            ),
            annotation_clip=False,
            clip_on=False,
            zorder=20 + cursor_index
        )

        self.cursor_value_artists.append(
            artist
        )

    def _get_digital_state_at_cursor(
        self,
        it,
        cursor_x
    ):
        """
        元デジタルデータからカーソル位置の0/1状態を取得。

        event_table:
            DigitalBusSource.state_at()を使い、共有BUSの元イベント列から
            直接取得する。

        sampled_group:
            元x/dig配列からカーソル時刻以下の直近サンプルを取得する。
        """

        if it.get("kind") != "digital":
            return None

        if cursor_x is None:
            return None

        meta = it.get("meta", {})
        bit = int(meta.get("bit", 0))

        source = it.get("digital_source")
        if source is not None:
            state = source.state_at(
                bit,
                cursor_x
            )
            if state is None:
                return None
            return (
                float(cursor_x),
                int(state)
            )

        x = it.get("x")
        dig = it.get("dig")

        if x is None or dig is None:
            return None

        if len(x) == 0 or len(dig) == 0:
            return None

        cursor_x = float(cursor_x)

        left = float(x[0])
        right = float(x[-1])

        if cursor_x < min(left, right) or cursor_x > max(left, right):
            return None

        idx = int(
            _searchsorted(
                x,
                cursor_x,
                side="right"
            )
        ) - 1

        idx = max(
            0,
            min(
                idx,
                len(dig) - 1
            )
        )

        return (
            float(x[idx]),
            int(dig[idx])
        )

    def _add_cursor_digital_label(
        self,
        cursor,
        it,
        digital_index=0
    ):
        """
        カーソル位置のDigital値(0/1)を、その交点付近へ表示。

        Stacked:
            各Dチャンネル軸に A: 0 / A: 1 のように表示。

        Overlay + Digital別軸:
            同じ0/1位置に多数のラベルが重なるため、
            D番号とカーソル番号に応じて縦方向を少しずつずらす。
        """

        cursor_x = cursor.get("x")

        if cursor_x is None:
            return

        result = self._get_digital_state_at_cursor(
            it,
            cursor_x
        )

        if result is None:
            return

        x_sample, state = result

        if self.view_mode.get() == "stacked":
            ax = self.axis_map.get(
                it["name"]
            )
        elif self.dig_separate_axis.get():
            ax = self.axis_map.get(
                "DIGITAL"
            )
        else:
            ax = self.axis_map.get(
                "ANALOG"
            )

        if ax is None:
            return

        try:
            cursor_index = self.cursors.index(
                cursor
            )
        except ValueError:
            cursor_index = 0

        # A/B/C…を交互方向へ置く。BはAと反対側になるため、
        # Aと同時表示しても隠れにくい。
        x_offset, y_offset = self._cursor_annotation_offset(
            ax,
            x_sample,
            state,
            cursor_index,
            x_base=12,
            y_base=10
        )

        if self.view_mode.get() == "stacked":
            text = (
                f"{cursor['name']}: {state}"
            )
        else:
            # 同一Digital軸上ではチャンネルごとの微小offsetも追加。
            y_offset += (
                (digital_index % 5) - 2
            ) * 4
            text = (
                f"{cursor['name']} "
                f"{it['name']}={state}"
            )

        artist = ax.annotate(
            text,
            xy=(
                x_sample,
                state
            ),
            xytext=(
                x_offset,
                y_offset
            ),
            textcoords="offset points",
            fontsize=DIGITAL_CURSOR_LABEL_FONTSIZE,
            color=AXIS_FOREGROUND,
            ha=(
                "left"
                if x_offset >= 0
                else "right"
            ),
            va=(
                "bottom"
                if y_offset >= 0
                else "top"
            ),
            bbox=dict(
                boxstyle="round,pad=0.16",
                fc=LEGEND_BACKGROUND,
                ec=cursor["color"],
                alpha=0.94
            ),
            arrowprops=dict(
                arrowstyle="-",
                color=cursor["color"],
                alpha=0.68,
                lw=0.7
            ),
            annotation_clip=False,
            clip_on=False,
            zorder=20 + cursor_index
        )

        self.cursor_value_artists.append(
            artist
        )


    def _get_cursor_measurement(self):
        """
        A/Bカーソル間の測定値。

        Δt = B - A
        周波数 = 1 / |Δt|
        """

        if len(self.cursors) < 2:
            return None

        a = self.cursors[0]["x"]
        b = self.cursors[1]["x"]

        if a is None or b is None:
            return None

        dt = b - a

        result = {
            "a": a,
            "b": b,
            "dt": dt,
            "frequency": None
        }

        if abs(dt) > 0:
            result["frequency"] = (
                1.0 / abs(dt)
            )

        return result

    def _format_si(
        self,
        value,
        unit=""
    ):
        """
        SI接頭辞付き表示。

        例:
            0.000000580 s
            -> 580 ns
        """

        if value is None:
            return "-"

        value = float(value)

        if value == 0:
            return f"0 {unit}".strip()

        prefixes = [
            (-12, "p"),
            (-9, "n"),
            (-6, "µ"),
            (-3, "m"),
            (0, ""),
            (3, "k"),
            (6, "M"),
            (9, "G"),
            (12, "T")
        ]

        exponent = int(
            np.floor(
                np.log10(
                    abs(value)
                ) / 3
            ) * 3
        )

        exponent = max(
            -12,
            min(
                12,
                exponent
            )
        )

        prefix = ""

        for exp, p in prefixes:
            if exp == exponent:
                prefix = p
                break

        scaled = (
            value
            / (10 ** exponent)
        )

        return (
            f"{scaled:.6g} "
            f"{prefix}{unit}"
        ).strip()

    def render_cursors(self):
        """
        カーソルを描画。

        ・縦線
        ・アクティブカーソル強調
        ・各アナログCHとの交点電圧
        ・A/B測定値
        ・C以降を含む複数カーソル同時表示
        """

        self.clear_cursor_artists()

        if not self.axes_all:
            return

        if not self.cursors:
            return

        active_name = (
            self.cursors[
                self.active_cursor_index
            ]["name"]
        )

        # ----------------------------------------------------
        # 縦カーソル
        # ----------------------------------------------------

        for cursor in self.cursors:

            x = cursor["x"]

            if x is None:
                continue

            for ax in self.axes_all:
                if cursor["name"] == active_name:
                    alpha = 1.0
                    lw = 1.5
                else:
                    alpha = 0.65
                    lw = 1.0

                artist = ax.axvline(
                    x,
                    color=cursor["color"],
                    lw=lw,
                    alpha=alpha
                )

                self.cursor_artists.append(
                    artist
                )

        # ----------------------------------------------------
        # 電圧ラベル
        # ----------------------------------------------------

        analog_items = [
            it
            for it in self.loaded_items
            if it["kind"] == "analog"
        ]

        digital_items = [
            it
            for it in self.loaded_items
            if it["kind"] == "digital"
        ]

        for cursor in self.cursors:

            if cursor["x"] is None:
                continue

            for it in analog_items:

                self._add_cursor_voltage_label(
                    cursor,
                    it
                )

            for digital_index, it in enumerate(
                digital_items
            ):
                self._add_cursor_digital_label(
                    cursor,
                    it,
                    digital_index=digital_index
                )

        # ----------------------------------------------------
        # 測定値
        # ----------------------------------------------------

        if self.cursor_text:

            lines = []

            # カーソル位置
            for i, cursor in enumerate(
                self.cursors
            ):

                if cursor["x"] is None:
                    continue

                marker = (
                    " *"
                    if i == self.active_cursor_index
                    else ""
                )

                lines.append(
                    f"{cursor['name']}: "
                    f"{self._format_si(cursor['x'], 's')}"
                    f"{marker}"
                )

            # A/B測定
            measurement = (
                self._get_cursor_measurement()
            )

            if measurement is not None:

                lines.append("")

                dt = measurement["dt"]

                lines.append(
                    "Δt(A→B) = "
                    + self._format_si(
                        dt,
                        "s"
                    )
                )

                # 各アナログCHのA/B電圧とΔV
                if analog_items:

                    lines.append("")

                    for it in analog_items:

                        va = (
                            self._get_voltage_at_cursor(
                                it,
                                measurement["a"]
                            )
                        )

                        vb = (
                            self._get_voltage_at_cursor(
                                it,
                                measurement["b"]
                            )
                        )

                        if (
                            va is None
                            or vb is None
                        ):
                            continue

                        voltage_a = va[1]
                        voltage_b = vb[1]

                        dv = (
                            voltage_b
                            - voltage_a
                        )

                        lines.append(
                            f"{it['name']}: "
                            f"A={voltage_a:.6g} V  "
                            f"B={voltage_b:.6g} V  "
                            f"ΔV={dv:.6g} V"
                        )

                # Digital A/B state
                if digital_items:

                    lines.append("")

                    for it in digital_items:

                        da = self._get_digital_state_at_cursor(
                            it,
                            measurement["a"]
                        )

                        db = self._get_digital_state_at_cursor(
                            it,
                            measurement["b"]
                        )

                        if da is None and db is None:
                            continue

                        state_a = (
                            "-"
                            if da is None
                            else str(da[1])
                        )
                        state_b = (
                            "-"
                            if db is None
                            else str(db[1])
                        )

                        changed = (
                            ""
                            if (
                                da is None
                                or db is None
                                or da[1] == db[1]
                            )
                            else "  change"
                        )

                        lines.append(
                            f"{it['name']}: "
                            f"A={state_a}  "
                            f"B={state_b}"
                            f"{changed}"
                        )

                # 周波数
                frequency = (
                    measurement["frequency"]
                )

                if frequency is not None:

                    lines.append(
                        "f = "
                        + self._format_si(
                            frequency,
                            "Hz"
                        )
                    )

            self.cursor_text.set_text(
                "\n".join(lines)
            )

        if hasattr(self, "measurement_info"):
            self._update_measurement_panel()
        self.canvas.draw_idle()

    def _move_active_cursor_sample(
        self,
        direction,
        step=1
    ):
        """
        アクティブカーソルをサンプル単位で移動。

        direction:
            -1 = 左
            +1 = 右
        """

        if not self.cursors:
            return

        cursor = self.cursors[
            self.active_cursor_index
        ]

        # まだ配置されていない場合
        if cursor["x"] is None:

            if self.axes_all:

                xmin, xmax = (
                    self.axes_all[0].get_xlim()
                )

                cursor["x"] = float(
                    (xmin + xmax) / 2
                )

            else:
                return

        analog_items = [
            it
            for it in self.loaded_items
            if it["kind"] == "analog"
        ]

        if not analog_items:
            return

        # 現在のアクティブCHを基準にする
        ch_index = max(
            0,
            min(
                self.active_channel_index,
                len(analog_items) - 1
            )
        )

        it = analog_items[ch_index]

        x = it["x"]

        if len(x) == 0:
            return

        cursor_x = cursor["x"]

        idx = int(
            _searchsorted(
                x,
                cursor_x
            )
        )

        if idx <= 0:
            idx = 0

        elif idx >= len(x):
            idx = len(x) - 1

        else:

            if (
                abs(x[idx - 1] - cursor_x)
                <= abs(x[idx] - cursor_x)
            ):
                idx -= 1

        idx += (
            direction * step
        )

        idx = max(
            0,
            min(
                idx,
                len(x) - 1
            )
        )

        cursor["x"] = float(
            x[idx]
        )

        self.render_cursors()
        if (
            hasattr(self, "auto_measure_scope_var")
            and self.auto_measure_scope_var.get() == "A-B間"
        ):
            self.schedule_auto_measurements(100)

    # ========================================================
    # export CSV
    # ========================================================

    def export_csv(self):
        """
        選択されているアナログチャンネルを
        CSV出力する。

        全点・非間引き。
        """

        analog_items = [
            it
            for it in self.loaded_items
            if it["kind"] == "analog"
        ]

        if not analog_items:
            messagebox.showinfo(
                "対象なし",
                "アナログチャンネルが選択されていません。"
            )
            return

        out_dir = filedialog.askdirectory(
            title="CSV保存先フォルダを選択"
        )

        if not out_dir:
            return

        out_dir = Path(out_dir)

        for it in analog_items:

            outp = (
                out_dir
                / f"{it['name'].lower()}_export.csv"
            )

            pre = it.get(
                "pre",
                {}
            )

            header = [
                f"# {it['name']}",
                f"# preamble={pre}",
                "# time[s],voltage[V]"
            ]

            with outp.open(
                "w",
                encoding="utf-8"
            ) as f:

                f.write(
                    "\n".join(header)
                    + "\n"
                )

                point_count = len(it["x"])
                chunk_points = 200_000

                for start in range(
                    0,
                    point_count,
                    chunk_points
                ):
                    stop = min(
                        point_count,
                        start + chunk_points
                    )
                    x_chunk = it["x"][start:stop]
                    y_chunk = it["y"][start:stop]
                    rows = np.column_stack(
                        (x_chunk, y_chunk)
                    )
                    np.savetxt(
                        f,
                        rows,
                        delimiter=",",
                        fmt="%.17g"
                    )

        messagebox.showinfo(
            "保存完了",
            f"保存しました:\n{out_dir}"
        )
        self.status_var.set(f"CSVを保存しました: {out_dir}")

    # ========================================================
    # アクティブチャンネル関連
    # ========================================================

    def _get_active_analog_axes(self):

        analog_items = [
            it
            for it in self.loaded_items
            if it["kind"] == "analog"
        ]

        if not analog_items:
            return None

        self.active_channel_index = max(
            0,
            min(
                self.active_channel_index,
                len(analog_items) - 1
            )
        )

        it = analog_items[
            self.active_channel_index
        ]

        if self.view_mode.get() == "stacked":

            return self.axis_map.get(
                it["name"]
            )

        else:

            return self.axis_map.get(
                "ANALOG",
                self.axes_all[0]
                if self.axes_all
                else None
            )

    def _zoom_y(
        self,
        ax,
        zoom_in=True
    ):

        if ax is None:
            return

        ymin, ymax = ax.get_ylim()

        yc = (
            0.5
            * (ymin + ymax)
        )

        half = (
            0.5
            * (ymax - ymin)
        )

        if half <= 0:
            return

        factor = (
            self.yzoom_factor
            if zoom_in
            else 1 / self.yzoom_factor
        )

        new_half = (
            half / factor
        )

        ax.set_ylim(
            yc - new_half,
            yc + new_half
        )

        self.canvas.draw_idle()

    def _autoscale_y(self, ax):
        """
        可視範囲内のデータでYをオートスケール。
        """

        if ax is None:
            return

        if not self.loaded_items:
            return

        xmin, xmax = ax.get_xlim()

        analog_items = [
            it
            for it in self.loaded_items
            if it["kind"] == "analog"
        ]

        if not analog_items:
            return

        ranges = []

        for it in analog_items:

            if (
                self.view_mode.get()
                == "stacked"
            ):

                if (
                    self.axis_map.get(
                        it["name"]
                    ) != ax
                ):
                    continue

            x = it["x"]
            y = it["y"]

            i0 = _searchsorted(
                x,
                xmin,
                side="left"
            )

            i1 = _searchsorted(
                x,
                xmax,
                side="right"
            )

            if i1 > i0:
                result = sample_minmax(
                    y,
                    i0,
                    i1
                )

                if result is not None:
                    ranges.append(result)

        if not ranges:
            return

        ymin = min(v[0] for v in ranges)
        ymax = max(v[1] for v in ranges)

        if ymin == ymax:
            ymax = (
                ymin
                + 1e-12
            )

        pad = (
            0.05
            * (ymax - ymin)
        )

        ax.set_ylim(
            ymin - pad,
            ymax + pad
        )

        self.canvas.draw_idle()

    def _autoscale_initial_analog_y(self):
        """
        初回描画で生成済みの間引きデータからY軸を設定する。

        Line2Dを空データで作成してからset_data()しているため、
        relimしない限りMatplotlibの初期Y範囲が残る。ここでは
        全25 M点を再走査せず、min/max保存済みの描画データを使う。
        """

        analog_items = [
            it
            for it in self.loaded_items
            if it["kind"] == "analog"
        ]

        if not analog_items:
            return

        axis_items = {}

        for it in analog_items:
            if self.view_mode.get() == "stacked":
                ax = self.axis_map.get(it["name"])
            else:
                ax = self.axis_map.get("ANALOG")

            if ax is not None:
                axis_items.setdefault(ax, []).append(it)

        for ax, items in axis_items.items():
            ymin = None
            ymax = None

            for it in items:
                line = self.line_map.get(it["name"])

                if line is None:
                    continue

                values = np.asarray(
                    line.get_ydata(),
                    dtype=np.float64
                )
                values = values[np.isfinite(values)]

                if len(values) == 0:
                    continue

                item_min = float(np.min(values))
                item_max = float(np.max(values))
                ymin = (
                    item_min
                    if ymin is None
                    else min(ymin, item_min)
                )
                ymax = (
                    item_max
                    if ymax is None
                    else max(ymax, item_max)
                )

            if ymin is None or ymax is None:
                continue

            span = ymax - ymin

            if span <= 0:
                pad = max(
                    abs(ymin) * 0.05,
                    1e-12
                )
            else:
                pad = 0.05 * span

            ax.set_ylim(
                ymin - pad,
                ymax + pad
            )

        self.canvas.draw_idle()

    def _parse_keymods(self, key: str):
        """
        matplotlib event.key は
        'shift+up' のように来る場合がある。
        """

        parts = key.lower().split("+")

        mods = (
            set(parts[:-1])
            if len(parts) > 1
            else set()
        )

        base = parts[-1]

        return mods, base

    # ========================================================
    # xlim / dynamic decimation
    # ========================================================

    def compute_full_xlim(self):

        if not self.loaded_items:

            self.full_xlim = None

            return

        nonempty_items = [
            it
            for it in self.loaded_items
            if len(it["x"]) > 0
        ]

        if not nonempty_items:
            self.full_xlim = None
            return

        # アナログと同時表示する場合は、アナログ波形の時間範囲を
        # 共通X軸の基準とする。デジタルだけの場合は従来どおり
        # デジタルデータ自身の範囲を使う。
        analog_items = [
            it
            for it in nonempty_items
            if it["kind"] == "analog"
        ]
        reference_items = (
            analog_items
            if analog_items
            else nonempty_items
        )

        xmin = min(
            float(it["x"][0])
            for it in reference_items
        )

        xmax = max(
            float(it["x"][-1])
            for it in reference_items
        )

        if xmin == xmax:
            xmax = (
                xmin
                + 1e-9
            )

        self.full_xlim = (
            xmin,
            xmax
        )

    def shade_unavailable_digital_ranges(self, digital_items):
        """デジタル未取得区間を薄い灰色で示す。"""

        if self.full_xlim is None:
            return

        full_start = float(min(self.full_xlim))
        full_end = float(max(self.full_xlim))
        axes_and_ranges = {}

        for it in digital_items:
            meta = it.get("meta", {})
            if meta.get("source_format") != "event_table":
                continue

            coverage = digital_coverage_xlim(meta)
            if coverage is None and len(it["x"]) > 0:
                coverage = (
                    float(it["x"][0]),
                    float(it["x"][-1])
                )
            if coverage is None:
                continue

            if self.view_mode.get() == "stacked":
                ax = self.axis_map.get(it["name"])
            elif self.dig_separate_axis.get():
                ax = self.axis_map.get("DIGITAL")
            else:
                ax = self.axis_map.get("ANALOG")

            if ax is not None and ax not in axes_and_ranges:
                axes_and_ranges[ax] = coverage

        for ax, coverage in axes_and_ranges.items():
            valid_start = max(
                full_start,
                float(min(coverage))
            )
            valid_end = min(
                full_end,
                float(max(coverage))
            )

            if valid_start > full_start:
                ax.axvspan(
                    full_start,
                    min(valid_start, full_end),
                    color="0.5",
                    alpha=0.14,
                    zorder=-20
                )
            if valid_end < full_end:
                ax.axvspan(
                    max(valid_end, full_start),
                    full_end,
                    color="0.5",
                    alpha=0.14,
                    zorder=-20
                )

    def clamp_xlim(
        self,
        xmin,
        xmax
    ):

        if self.full_xlim is None:
            return xmin, xmax

        full_min, full_max = (
            self.full_xlim
        )

        width = (
            xmax - xmin
        )

        if width <= 0:
            width = (
                full_max
                - full_min
            ) * 1e-6

        if xmin < full_min:

            xmin = full_min
            xmax = xmin + width

        if xmax > full_max:

            xmax = full_max
            xmin = xmax - width

        return xmin, xmax

    def set_xlim_all(
        self,
        xmin,
        xmax
    ):

        xmin, xmax = (
            self.clamp_xlim(
                xmin,
                xmax
            )
        )

        # Viewerが生成する複数軸はすべてsharex=True。先頭軸だけを更新
        # すれば全軸へ伝播する。全軸へ個別設定すると、軸数分だけ
        # xlim_changedが発生し、4Kでは再描画要求が急増する。
        if self.axes_all:
            self.axes_all[0].set_xlim(
                xmin,
                xmax
            )

        self.canvas.draw_idle()

    def selected_digital_valid_xlim(self):
        """選択中イベントデータの有効時間範囲を返す。"""

        for it in self.loaded_items:
            if (
                it["kind"] != "digital"
                or it.get("meta", {}).get("source_format")
                != "event_table"
                or len(it["x"]) == 0
            ):
                continue

            coverage = digital_coverage_xlim(
                it.get("meta", {})
            )
            if coverage is not None:
                return coverage

            return (
                float(it["x"][0]),
                float(it["x"][-1])
            )

        return None

    def zoom_to_digital_range(self):
        """共通X軸を、実際に取得されたデジタル範囲へ合わせる。"""

        digital_xlim = self.selected_digital_valid_xlim()
        if digital_xlim is None:
            messagebox.showinfo(
                "デジタル範囲なし",
                "表示可能なデジタルイベントデータがありません。"
            )
            return

        start = float(min(digital_xlim))
        end = float(max(digital_xlim))
        span = end - start

        if span <= 0:
            full_span = (
                abs(float(self.full_xlim[1]) - float(self.full_xlim[0]))
                if self.full_xlim is not None
                else 1e-9
            )
            span = max(full_span * 1e-6, 1e-15)

        margin = span * 0.02
        self.set_xlim_all(
            start - margin,
            end + margin
        )
        self.status_var.set(
            "検証済みデジタル時間範囲へ移動しました"
        )

    def narrow_digital_range_message(self):
        """共通全時間に対してデジタル範囲が極端に狭いか調べる。"""

        if self.full_xlim is None:
            return None

        digital_xlim = self.selected_digital_valid_xlim()
        if digital_xlim is None:
            return None

        full_span = abs(
            float(self.full_xlim[1])
            - float(self.full_xlim[0])
        )
        digital_span = abs(
            float(digital_xlim[1])
            - float(digital_xlim[0])
        )
        if full_span <= 0:
            return None

        ratio = digital_span / full_span
        if ratio >= 0.01:
            return None

        return (
            "デジタル有効範囲は共通アナログ時間軸の "
            f"{ratio:.6%} だけです。全体表示では線が非常に細く "
            "見えます。［デジタル有効範囲へズーム］を押して "
            "確認してください。"
        )

    def _target_bins(self):

        try:
            width, height = self.canvas.get_width_height(
                physical=True
            )
        except TypeError:
            width, height = self.canvas.get_width_height()

        width = max(1, int(width))
        height = max(1, int(height))
        high_resolution = (
            width * height >= HIGH_RES_PIXEL_AREA
            or self._canvas_size_capped
        )
        self._high_res_rendering_active = high_resolution

        if high_resolution:
            target = int(
                max(
                    HIGH_RES_MIN_TARGET,
                    min(
                        HIGH_RES_MAX_TARGET,
                        width * HIGH_RES_PIXEL_MULT
                    )
                )
            )
        else:
            target = int(
                max(
                    MIN_TARGET,
                    min(
                        MAX_TARGET,
                        width * PIXEL_MULT
                    )
                )
            )

        bins = max(
            50,
            target // 2
        )

        return target, bins

    def on_canvas_resize(self, event):
        """デバウンス後の確定サイズで、表示データを一度だけ更新する。"""

        del event
        was_high_resolution = self._high_res_rendering_active
        self._last_render_signature = None
        if self.loaded_items and self.axes_all:
            self.on_xlim_changed(self.axes_all[0])
        else:
            self._target_bins()

        if (
            self._high_res_rendering_active
            and not was_high_resolution
        ):
            self.status_var.set(
                "4K表示モードを使用中です — 描画負荷は動的間引きで制限しています"
            )

    def on_xlim_changed(self, ax):

        del ax

        if (
            not self.loaded_items
            or not self.axes_all
            or self._render_refresh_active
        ):
            return

        xmin, xmax = (
            self.axes_all[0].get_xlim()
        )

        target_points, bins = (
            self._target_bins()
        )

        line_signature = tuple(
            (
                item["name"],
                id(self.line_map.get(item["name"]))
            )
            for item in self.loaded_items
        )
        signature = (
            float(xmin),
            float(xmax),
            int(target_points),
            int(bins),
            line_signature
        )
        if signature == self._last_render_signature:
            return

        self._render_refresh_active = True

        try:
            self._update_visible_line_data(
                xmin,
                xmax,
                target_points,
                bins
            )
            self._render_protocol_overlay_visible()
            self._last_render_signature = signature
        finally:
            self._render_refresh_active = False

        if (
            hasattr(self, "auto_measure_scope_var")
            and self.auto_measure_scope_var.get() == "表示範囲"
        ):
            self.schedule_auto_measurements(220)

    def _update_visible_line_data(
        self,
        xmin,
        xmax,
        target_points,
        bins
    ):
        """現在範囲の波形データを各Lineへ設定する。"""

        # 同じ16-bit BUSを参照するD0～D15では、表示範囲の切り出しと
        # 隣接XORを1回だけ行う。
        shared_digital_windows = {}

        for it in self.loaded_items:

            name = it["name"]

            line = self.line_map.get(
                name
            )

            if line is None:
                continue

            line.set_antialiased(
                not self._high_res_rendering_active
            )

            x = it["x"]

            if it["kind"] == "analog":

                y = it["y"]

                i0, i1 = (
                    _visible_slice_indices(
                        x,
                        xmin,
                        xmax
                    )
                )

                xd, yd = (
                    downsample_analog_window(
                        x,
                        y,
                        i0,
                        i1,
                        bins,
                        pyramid=it.get("pyramid")
                    )
                )

                pyramid = it.get("pyramid")
                if isinstance(pyramid, AnalogMinMaxPyramid):
                    if pyramid.ready:
                        self.lb_a.set_status(
                            name,
                            f"{len(x):,}点 / 高速",
                            "active"
                        )
                    elif pyramid.build_error:
                        self.lb_a.set_status(
                            name,
                            f"{len(x):,}点 / 通常",
                            "high"
                        )

                line.set_data(
                    xd,
                    yd
                )

            else:
                if (
                    it.get("meta", {}).get("source_format")
                    == "event_table"
                ):
                    source = it.get("digital_source")
                    if isinstance(source, DigitalBusSource):
                        source_key = id(source)
                        window = shared_digital_windows.get(source_key)
                        if window is None:
                            window = source.window(
                                xmin,
                                xmax,
                                reference_xlim=self.full_xlim
                            )
                            shared_digital_windows[source_key] = window

                        xd, dd = source.downsample_bit(
                            it.get("meta", {}).get("bit", 0),
                            target_points
                        )
                    else:
                        # 手動生成された旧event itemとの互換経路。
                        dig = it["dig"]
                        xv, dv = digital_event_window(
                            x,
                            dig,
                            xmin,
                            xmax,
                            reference_xlim=self.full_xlim,
                            coverage_xlim=digital_coverage_xlim(
                                it.get("meta", {})
                            )
                        )
                        xd, dd = downsample_digital_edges(
                            xv,
                            dv,
                            target_points
                        )

                else:
                    dig = it["dig"]
                    i0, i1 = (
                        _visible_slice_indices(
                            x,
                            xmin,
                            xmax
                        )
                    )

                    xv = x[i0:i1]
                    dv = dig[i0:i1]
                    xd, dd = downsample_digital_edges(
                        xv,
                        dv,
                        target_points
                    )

                line.set_data(
                    xd,
                    dd
                )

        # カーソル位置は元データを使うので
        # 表示範囲が変わった場合にもラベルを再配置する
        self.render_cursors()

        self.canvas.draw_idle()

    # ========================================================
    # データロード & 選択
    # ========================================================

    def _compute_digital_activity(self, bus_values):
        """大きなイベント列を分割走査してD0-D15の状態を集計する。"""

        transitions = [0] * 16
        observed_high = [False] * 16
        observed_low = [False] * 16
        previous_value = None
        chunk_size = 1_000_000

        for start in range(0, len(bus_values), chunk_size):
            stop = min(len(bus_values), start + chunk_size)
            values = np.asarray(
                bus_values[start:stop],
                dtype=np.uint16
            )
            if len(values) == 0:
                continue

            for bit in range(16):
                states = (values >> bit) & 1
                observed_high[bit] = (
                    observed_high[bit]
                    or bool(np.any(states != 0))
                )
                observed_low[bit] = (
                    observed_low[bit]
                    or bool(np.any(states == 0))
                )
                transitions[bit] += int(
                    np.count_nonzero(np.diff(states) != 0)
                )

            if previous_value is not None:
                changed = previous_value ^ int(values[0])
                for bit in range(16):
                    if changed & (1 << bit):
                        transitions[bit] += 1
            previous_value = int(values[-1])

        return {
            f"D{bit}": {
                "transition_count": transitions[bit],
                "observed_high": observed_high[bit],
                "observed_low": observed_low[bit],
                "observed_both_states": (
                    observed_high[bit] and observed_low[bit]
                )
            }
            for bit in range(16)
        }

    def _resolve_digital_activity(self, event_meta, bus_values):
        candidates = []
        parse_report = event_meta.get("parse_report")
        if isinstance(parse_report, dict):
            candidates.append(parse_report.get("channel_activity"))

        summary = event_meta.get("capture_summary")
        if isinstance(summary, dict):
            digital = summary.get("digital")
            if isinstance(digital, dict):
                candidates.append(digital.get("channel_activity"))

        for candidate in candidates:
            if (
                isinstance(candidate, dict)
                and all(f"D{bit}" in candidate for bit in range(16))
            ):
                return candidate

        return self._compute_digital_activity(bus_values)

    def _update_channel_status_labels(self):
        for name, _, _ in self.analog_list:
            self.lb_a.set_status(name, "取得済み", "available")

        for name, _, _ in self.digital_list:
            activity = self.digital_activity.get(name, {})
            transitions = int(activity.get("transition_count", 0))
            high = bool(activity.get("observed_high", False))
            low = bool(activity.get("observed_low", False))

            if transitions > 0:
                text = f"変化 {transitions:,}回"
                kind = "active"
            elif high and not low:
                text = "HIGH固定"
                kind = "high"
            elif low and not high:
                text = "LOW固定"
                kind = "low"
            elif high and low:
                text = "両状態"
                kind = "active"
            else:
                text = "状態不明"
                kind = "neutral"

            self.lb_d.set_status(name, text, kind)

    # ========================================================
    # Protocol overlay (beta.6)
    # ========================================================

    def _protocol_overlay_color(self, event):
        if not bool(event.get("ok", True)):
            return "#FF5252"
        kind = str(event.get("kind", "")).upper()
        if "CAN" in kind: return "#00E5FF"
        if "LIN" in kind: return "#FFD740"
        if "I2C" in kind:
            if "START" in kind: return "#69F0AE"
            if "STOP" in kind: return "#FF8A80"
            if "ADDR" in kind: return "#40C4FF"
            return "#7C4DFF"
        if "SPI" in kind: return "#FFAB40"
        if "GPS" in kind or "NMEA" in kind or "UBX" in kind or "PPS" in kind: return "#76FF03"
        if "RS-485" in kind: return "#E040FB"
        if "UART" in kind or "RS-232" in kind: return "#448AFF"
        return "#B0BEC5"

    def _clear_protocol_overlay_artists(self):
        for artist in self.protocol_overlay_artists:
            try: artist.remove()
            except Exception: pass
        self.protocol_overlay_artists = []

    def _protocol_overlay_toggle(self):
        self._protocol_overlay_refresh()

    def _protocol_overlay_refresh(self):
        self._last_render_signature = None
        if self.axes_all:
            self._render_protocol_overlay_visible()
            self.canvas.draw_idle()

    def reload_protocol_overlay(self, silent=False):
        if self.dataset_dir is None:
            if not silent: messagebox.showwarning("プロトコル帯", "先にデータセットを開いてください", parent=self)
            return False
        try:
            doc = load_active_overlay(self.dataset_dir)
            self.protocol_overlay_doc = doc
            self.protocol_overlay_events = list(doc.get("events", [])) if doc else []
            self.protocol_overlay_starts = [float(e["start_s"]) for e in self.protocol_overlay_events]
            self.protocol_overlay_mtime_ns = active_overlay_mtime_ns(self.dataset_dir)
            if doc:
                proto = doc.get("protocol", "Protocol")
                self.protocol_overlay_status_var.set(f"プロトコル帯: {proto} / {len(self.protocol_overlay_events):,} events")
            else:
                self.protocol_overlay_status_var.set("プロトコル帯: なし")
            self._protocol_overlay_refresh()
            return True
        except Exception as exc:
            self.protocol_overlay_status_var.set(f"プロトコル帯の読込エラー: {exc}")
            if not silent: messagebox.showerror("プロトコル帯", str(exc), parent=self)
            return False

    def clear_protocol_overlay(self):
        if self.dataset_dir is None:
            return
        try:
            clear_active_overlay(self.dataset_dir)
            self.protocol_overlay_doc = None
            self.protocol_overlay_events = []
            self.protocol_overlay_starts = []
            self.protocol_overlay_mtime_ns = None
            self.protocol_overlay_status_var.set("プロトコル帯: なし")
            self._protocol_overlay_refresh()
        except Exception as exc:
            messagebox.showerror("プロトコル帯", str(exc), parent=self)

    def open_protocol_analyzer(self):
        if self.dataset_dir is None:
            messagebox.showwarning("プロトコル解析", "先にデータセットを開いてください", parent=self)
            return
        script = Path(__file__).resolve().with_name("protocol_analyzer.py")
        if not script.is_file():
            messagebox.showerror("プロトコル解析", f"protocol_analyzer.py が見つかりません。\n{script}", parent=self)
            return
        try:
            subprocess.Popen([sys.executable, str(script), str(self.dataset_dir)], cwd=str(script.parent))
            self.status_var.set("Protocol Analyzerを開きました — デコード後の帯はViewerへ自動反映されます")
        except Exception as exc:
            messagebox.showerror("プロトコル解析", str(exc), parent=self)

    def _protocol_overlay_visible_events(self, xmin, xmax):
        events = self.protocol_overlay_events
        if not events:
            return []
        left, right = sorted((float(xmin), float(xmax)))
        i0 = max(0, bisect.bisect_left(self.protocol_overlay_starts, left) - 2)
        i1 = min(len(events), bisect.bisect_right(self.protocol_overlay_starts, right) + 1)
        return [e for e in events[i0:i1] if float(e.get("end_s", e["start_s"])) >= left and float(e["start_s"]) <= right]

    def _render_protocol_overlay_visible(self):
        self._clear_protocol_overlay_artists()
        if not self.protocol_overlay_enabled.get() or not self.axes_all or not self.protocol_overlay_events:
            return
        xmin, xmax = self.axes_all[0].get_xlim()
        left, right = sorted((float(xmin), float(xmax)))
        width = max(right-left, 1e-30)
        visible = self._protocol_overlay_visible_events(left, right)
        if not visible:
            return
        # Keep interactive pan/zoom responsive even for dense UART/CAN captures.
        max_spans = max(40, min(500, 1400 // max(1, len(self.axes_all))))
        if len(visible) > max_spans:
            step = max(1, int((len(visible) + max_spans - 1) // max_spans))
            span_events = visible[::step]
        else:
            span_events = visible
        alpha = max(0.03, min(0.60, float(self.protocol_overlay_alpha_var.get())))
        for e in span_events:
            start=float(e["start_s"]); end=float(e.get("end_s", start))
            # Point events such as PPS still need a visible band.
            if end <= start:
                end = start + max(width/2500.0, 1e-12)
            color=self._protocol_overlay_color(e)
            for ax in self.axes_all:
                try:
                    artist=ax.axvspan(start,end,facecolor=color,edgecolor="none",alpha=alpha,zorder=0.35)
                    self.protocol_overlay_artists.append(artist)
                except Exception:
                    pass
        mode=self.protocol_overlay_label_mode_var.get()
        if mode == "なし":
            return
        label_cap={"多め":120,"少なめ":24}.get(mode,60)
        topax=self.axes_all[0]
        pixel_width=max(1.0,float(getattr(topax.bbox,"width",1000.0)))
        candidates=[]
        for e in visible:
            es=float(e["start_s"]); ee=max(es,float(e.get("end_s",es)))
            event_px=max(0.0,(ee-es)/width*pixel_width)
            if mode == "多め" or event_px >= 42.0:
                candidates.append(e)
        if len(candidates)>label_cap:
            step=max(1,int((len(candidates)+label_cap-1)//label_cap)); candidates=candidates[::step]
        for idx,e in enumerate(candidates[:label_cap]):
            es=float(e["start_s"]); ee=max(es,float(e.get("end_s",es))); mid=(es+ee)/2.0
            label=str(e.get("summary") or e.get("kind") or "EVENT").replace("\n"," ")
            if len(label)>34: label=label[:31]+"..."
            color=self._protocol_overlay_color(e)
            try:
                artist=topax.text(mid,0.985-(idx%2)*0.085,label,transform=topax.get_xaxis_transform(),ha="center",va="top",fontsize=7.0,color="#FFFFFF",clip_on=True,zorder=8,bbox=dict(boxstyle="round,pad=0.18",facecolor=color,edgecolor="none",alpha=0.80))
                self.protocol_overlay_artists.append(artist)
            except Exception:
                pass

    def _poll_protocol_overlay(self):
        self.protocol_overlay_poll_after_id = None
        try:
            if self.dataset_dir is not None:
                current = active_overlay_mtime_ns(self.dataset_dir)
                if current != self.protocol_overlay_mtime_ns:
                    self.reload_protocol_overlay(silent=True)
        finally:
            try:
                self.protocol_overlay_poll_after_id = self.after(1500, self._poll_protocol_overlay)
            except tk.TclError:
                self.protocol_overlay_poll_after_id = None

    def open_folder(self):

        # R9: native dialog の owner を明示する。
        # owner 不定の dialog と Tk callback の競合を避け、
        # 常にこの Viewer のモーダル子として扱わせる。
        try:
            self.update_idletasks()
        except tk.TclError:
            pass

        d = filedialog.askdirectory(
            parent=self,
            title="データセットフォルダを選択",
            mustexist=True
        )

        if not d:
            return

        folder = Path(d)

        self._set_busy(True, "データセットを確認しています...")
        try:
            self._load_dataset_folder(folder)
        except Exception as exc:
            self.status_var.set("データセットを開けませんでした")
            self._replace_text(
                self.diagnostic_info,
                "データセットの読み込み中にエラーが発生しました。\n\n"
                f"種類: {type(exc).__name__}\n"
                f"内容: {exc}\n\n"
                "フォルダ内のJSON、BIN、PREファイルを確認してください。"
            )
            self.detail_tabs.select(3)
            messagebox.showerror(
                "データセットを開けません",
                "選択したフォルダを読み込めませんでした。\n\n"
                f"{exc}\n\n"
                "詳しくは［診断］タブを確認してください。"
            )
        finally:
            self._set_busy(False)

    def _load_dataset_folder(self, folder):

        folder = resolve_dataset_root(Path(folder))
        dataset_metadata = load_dataset_metadata(folder)
        analog, digital = (
            discover_channels(folder)
        )
        missing_digital_message = missing_digital_data_message(
            folder,
            dataset_metadata,
            digital
        )

        if not analog and not digital:

            self.status_var.set(
                "選択したフォルダには表示可能な波形がありません"
            )

            messagebox.showerror(
                (
                    "デジタル波形データなし"
                    if missing_digital_message
                    else "見つかりません"
                ),
                (
                    missing_digital_message
                    if missing_digital_message
                    else (
                        "chan?.bin + chan?_pre.txt / "
                        "digital_events.bin(.csv) / "
                        "d?.bin + d?_pre.txt が見つかりません。"
                    )
                )
            )

            return

        self.dataset_dir = folder

        self.analog_list = analog
        self.digital_list = digital
        self.analog_source_cache = {}
        self.digital_event_cache = {}
        self.digital_activity = {}
        self.auto_measure_cache = {}
        self.dataset_metadata = dataset_metadata
        self.protocol_overlay_doc = None
        self.protocol_overlay_events = []
        self.protocol_overlay_starts = []
        self.protocol_overlay_mtime_ns = None
        self._load_calibration_from_dataset_metadata(dataset_metadata)
        self.dataset_metadata[
            "missing_digital_data_message"
        ] = missing_digital_message
        self.folder_path_var.set(str(folder))
        self.reload_protocol_overlay(silent=True)

        self.lb_a.delete(
            0,
            tk.END
        )

        for name, _, _ in self.analog_list:

            self.lb_a.insert(
                tk.END,
                name
            )

        self.lb_d.delete(
            0,
            tk.END
        )

        for name, _, _ in self.digital_list:

            self.lb_d.insert(
                tk.END,
                name
            )

        self._update_channel_status_labels()
        self._refresh_sync_source_choices()

        if self.analog_list:

            self.lb_a.selection_set(
                0,
                tk.END
            )

        # イベント形式では、実際に変化したDチャンネルだけを
        # 初期選択する。固定レベルのチャンネルも一覧から選べる。
        event_entries = [
            entry
            for entry in self.digital_list
            if entry[2] is None
        ]

        if event_entries:
            try:
                event_path = event_entries[0][1]
                x_event, bus_values, event_meta = (
                    self._load_digital_events_current(event_path)
                )
                event_source = DigitalBusSource(
                    x_event,
                    bus_values,
                    event_meta
                )
                self.digital_event_cache[event_path] = event_source

                self.digital_activity = self._resolve_digital_activity(
                    event_meta,
                    bus_values
                )
                self._update_channel_status_labels()

                selected_digital_count = 0
                for idx, (name, _, _) in enumerate(
                    self.digital_list
                ):
                    activity = self.digital_activity.get(name, {})
                    if int(activity.get("transition_count", 0)) > 0:
                        self.lb_d.selection_set(idx)
                        selected_digital_count += 1

                # イベントが存在しても全ビットが一定の場合、従来は
                # デジタル選択が0件になり、消えたように見えていた。
                # その場合は有効設定されたチャンネルを初期選択する。
                if selected_digital_count == 0:
                    configuration = event_meta.get("configuration")
                    configured_channels = (
                        configuration.get("digital_channels", {})
                        if isinstance(configuration, dict)
                        else {}
                    )
                    for idx, (name, _, _) in enumerate(
                        self.digital_list
                    ):
                        if (
                            not configured_channels
                            or configured_channels.get(name, False)
                        ):
                            self.lb_d.selection_set(idx)

            except Exception as exc:
                # 個別ロード時に詳細を出せるよう、全Dチャンネルを選ぶ。
                self.dataset_metadata[
                    "viewer_initial_digital_error"
                ] = str(exc)
                if self.digital_list:
                    self.lb_d.selection_set(0, tk.END)
                for name, _, _ in self.digital_list:
                    self.lb_d.set_status(name, "要確認", "missing")

        elif self.digital_list:
            # 旧 sampled-group 形式では取得済みDチャンネルを初期表示。
            self.lb_d.selection_set(0, tk.END)
            for name, _, _ in self.digital_list:
                self.lb_d.set_status(name, "旧形式", "available")

        self.active_channel_index = 0

        self.on_select()

        self.status_var.set(
            f"読み込み完了 — アナログ {len(self.analog_list)}ch、"
            f"デジタル {len(self.digital_list)}ch"
        )

        narrow_message = self.narrow_digital_range_message()
        self.dataset_metadata[
            "narrow_digital_range_message"
        ] = narrow_message
        if narrow_message:
            self.update_info([])

        if missing_digital_message:
            self.detail_tabs.select(3)
            messagebox.showwarning(
                "デジタル波形データなし",
                missing_digital_message
            )
        elif narrow_message:
            self.detail_tabs.select(3)
            messagebox.showwarning(
                "デジタル範囲が狭いデータです",
                narrow_message
            )
        else:
            summary = self.dataset_metadata.get("capture_summary")
            if isinstance(summary, dict) and not summary.get("success"):
                self.detail_tabs.select(3)
            else:
                self.detail_tabs.select(0)

    def on_select(
        self,
        event=None
    ):

        del event
        self._selection_after_id = None

        if self.dataset_dir is None:
            return

        self._set_busy(True, "選択チャンネルの波形を準備しています...")

        self.loaded_items = []

        errors = []

        # ----------------------------------------------------
        # アナログ
        # ----------------------------------------------------

        for idx in list(
            self.lb_a.curselection()
        ):

            name, bin_path, pre_path = (
                self.analog_list[idx]
            )

            try:
                analog_source = self.analog_source_cache.get(bin_path)
                if analog_source is None:
                    x, y, pre = load_analog(
                        bin_path,
                        pre_path
                    )
                    pyramid = (
                        AnalogMinMaxPyramid(y)
                        if len(x) >= ANALOG_CACHE_MIN_POINTS
                        else None
                    )
                    analog_source = dict(
                        x=x,
                        y=y,
                        pre=pre,
                        pyramid=pyramid
                    )
                    self.analog_source_cache[bin_path] = analog_source
                else:
                    x = analog_source["x"]
                    y = analog_source["y"]
                    pre = analog_source["pre"]
                    pyramid = analog_source["pyramid"]

                if (
                    isinstance(pyramid, AnalogMinMaxPyramid)
                    and not pyramid.ready
                    and pyramid.build_error is None
                ):
                    self.status_var.set(
                        f"{name} の高速表示キャッシュを構築しています..."
                    )
                    pyramid.ensure_built()

                self.loaded_items.append(
                    dict(
                        kind="analog",
                        name=name,
                        x=x,
                        y=y,
                        pre=pre,
                        pyramid=pyramid
                    )
                )
                self.lb_a.set_status(
                    name,
                    (
                        f"{len(x):,}点 / 高速"
                        if isinstance(pyramid, AnalogMinMaxPyramid)
                        and pyramid.ready
                        else f"{len(x):,}点"
                    ),
                    (
                        "active"
                        if isinstance(pyramid, AnalogMinMaxPyramid)
                        and pyramid.ready
                        else "available"
                    )
                )

                if (
                    isinstance(pyramid, AnalogMinMaxPyramid)
                    and pyramid.build_error
                ):
                    errors.append(
                        f"{name}: 高速表示キャッシュを構築できないため、"
                        f"通常間引きを使用します: {pyramid.build_error}"
                    )

                if pre.get("point_count_warning"):
                    errors.append(
                        f"{name}: 警告: "
                        f"{pre['point_count_warning']}"
                    )

            except Exception as e:

                errors.append(
                    f"{name}: {e}"
                )

        # ----------------------------------------------------
        # デジタル
        # ----------------------------------------------------

        chosen_d = [
            self.digital_list[i][0]
            for i in list(
                self.lb_d.curselection()
            )
        ]

        def add_one_d(bit_name):

            n = int(
                bit_name[1:]
            )

            found = None

            for nm, bp, pp in (
                self.digital_list
            ):

                if nm == bit_name:

                    found = (
                        nm,
                        bp,
                        pp
                    )

                    break

            if not found:
                return

            nm, bp, pp = found

            try:

                if pp is None:
                    source = self.digital_event_cache.get(bp)

                    if source is None:
                        source = DigitalBusSource(
                            *self._load_digital_events_current(bp)
                        )
                        self.digital_event_cache[bp] = source

                    x = source.times
                    source_meta = source.meta
                    bit = n
                    channel_activity = self.digital_activity.get(
                        bit_name,
                        {}
                    )
                    if "transition_count" in channel_activity:
                        transition_count = int(
                            channel_activity["transition_count"]
                        )
                    else:
                        transition_count = source.transition_count(bit)
                    pre = None
                    meta = dict(source_meta)
                    meta.update(
                        bit=bit,
                        transition_count=transition_count,
                        initial_state=source.bit_state_at_record_edge(
                            bit,
                            first=True
                        ),
                        final_state=source.bit_state_at_record_edge(
                            bit,
                            first=False
                        ),
                        shared_bus=True
                    )

                else:
                    x, bytes_u8, pre = (
                        load_digital_group_bytes(
                            bp,
                            pp
                        )
                    )

                    group_base = (
                        n // 8
                    ) * 8

                    bit = (
                        n
                        - group_base
                    )

                    dig = (
                        (bytes_u8 >> bit)
                        & 1
                    ).astype(
                        np.uint8
                    )
                    meta = dict(
                        source_format="sampled_group",
                        source_file=bp.name,
                        group_base=group_base,
                        bit=bit,
                        transition_count=int(
                            np.count_nonzero(
                                np.diff(dig) != 0
                            )
                        )
                    )

                    if pre.get("point_count_warning"):
                        errors.append(
                            f"{nm}: 警告: "
                            f"{pre['point_count_warning']}"
                        )

                item = dict(
                    kind="digital",
                    name=bit_name,
                    x=x,
                    pre=pre,
                    meta=meta
                )
                if pp is None:
                    item["digital_source"] = source
                else:
                    item["dig"] = dig
                self.loaded_items.append(item)

            except Exception as e:

                errors.append(
                    f"{nm}: {e}"
                )

        if self.expand_group.get():

            expanded = set()

            for dn in chosen_d:

                n = int(
                    dn[1:]
                )

                base = (
                    n // 8
                ) * 8

                for k in range(
                    base,
                    base + 8
                ):

                    expanded.add(
                        f"D{k}"
                    )

            for dn in sorted(
                expanded,
                key=lambda s: int(
                    s[1:]
                )
            ):

                add_one_d(dn)
        else:

            for dn in chosen_d:
                add_one_d(dn)

        self.compute_full_xlim()

        self.redraw(errors)
        analog_count = sum(
            1 for item in self.loaded_items if item["kind"] == "analog"
        )
        digital_count = sum(
            1 for item in self.loaded_items if item["kind"] == "digital"
        )
        self._set_busy(
            False,
            f"表示中 — アナログ {analog_count}ch / "
            f"デジタル {digital_count}ch"
        )
        self.schedule_auto_measurements()

    # ========================================================
    # 描画
    # ========================================================

    def redraw(
        self,
        errors=None
    ):

        if errors is None:
            errors = []

        analog_items = [
            it
            for it in self.loaded_items
            if it["kind"] == "analog"
        ]

        digital_items = [
            it
            for it in self.loaded_items
            if it["kind"] == "digital"
        ]

        mode = self.view_mode.get()

        self.clear_cursor_artists()

        self.fig.clear()
        self.fig.patch.set_facecolor(
            PLOT_BACKGROUND
        )
        self._last_render_signature = None

        self.axis_map = {}

        self.axes_all = []

        self.line_map = {}

        if mode == "stacked":

            nrows = (
                len(analog_items)
                + len(digital_items)
            )

            if nrows == 0:
                ax = self.fig.subplots(1, 1)
                ax.set_axis_off()
                ax.text(
                    0.5,
                    0.56,
                    "Select channels to display",
                    ha="center",
                    va="center",
                    fontsize=17,
                    fontweight="bold",
                    color="#475569",
                    transform=ax.transAxes
                )
                ax.text(
                    0.5,
                    0.43,
                    "Use the analog and digital checkboxes on the left.",
                    ha="center",
                    va="center",
                    fontsize=11,
                    color="#64748B",
                    transform=ax.transAxes
                )
                self.canvas.draw_idle()

                self.update_info(
                    errors
                )

                return

            # Analogをできるだけ大きく、Digitalをコンパクトに表示。
            # 例: CH1-CH4 + D0-D15 でもAnalog側へ大部分の高さを配分する。
            height_ratios = (
                [ANALOG_STACK_HEIGHT] * len(analog_items)
                + [DIGITAL_STACK_HEIGHT] * len(digital_items)
            )

            axs = self.fig.subplots(
                nrows=nrows,
                ncols=1,
                sharex=True,
                gridspec_kw={
                    "height_ratios": height_ratios
                }
            )

            if nrows == 1:
                axs = [axs]

            r = 0

            # ----------------------------------------------
            # Analog
            # ----------------------------------------------

            for it in analog_items:

                ax = axs[r]

                r += 1

                line, = ax.plot(
                    [],
                    [],
                    lw=1,
                    color=self._channel_color(it["name"])
                )

                ax.set_ylabel("V")

                ax.grid(
                    True,
                    alpha=0.25
                )

                ax.set_title(
                    it["name"],
                    fontsize=10,
                    color=self._channel_color(it["name"])
                )

                self.axis_map[
                    it["name"]
                ] = ax

                self.axes_all.append(
                    ax
                )

                self.line_map[
                    it["name"]
                ] = line

            # ----------------------------------------------
            # Digital
            # ----------------------------------------------

            for it in digital_items:

                ax = axs[r]

                r += 1

                line, = ax.step(
                    [],
                    [],
                    where="post",
                    lw=1,
                    color=self._channel_color(it["name"])
                )

                ax.set_ylim(
                    -0.2,
                    1.2
                )

                ax.set_yticks(
                    [0, 1]
                )

                ax.set_ylabel("D")

                ax.grid(
                    True,
                    alpha=0.25
                )

                ax.set_title(
                    it["name"],
                    fontsize=10,
                    color=self._channel_color(it["name"])
                )

                self.axis_map[
                    it["name"]
                ] = ax

                self.axes_all.append(
                    ax
                )

                self.line_map[
                    it["name"]
                ] = line

            axs[-1].set_xlabel(
                "Time [s]"
            )

        else:

            # ----------------------------------------------
            # Analog / Digital separate
            # ----------------------------------------------

            if (
                self.dig_separate_axis.get()
                and digital_items
            ):

                # Analog領域を優先。Digitalは状態確認に十分な高さへ圧縮。
                axs = self.fig.subplots(
                    nrows=2,
                    ncols=1,
                    sharex=True,
                    gridspec_kw={
                        "height_ratios": [
                            OVERLAY_ANALOG_HEIGHT,
                            OVERLAY_DIGITAL_HEIGHT,
                        ]
                    }
                )

                axa, axd = axs

                self.axes_all.extend(
                    [axa, axd]
                )

                self.axis_map[
                    "ANALOG"
                ] = axa

                self.axis_map[
                    "DIGITAL"
                ] = axd

                # Analog
                for it in analog_items:

                    line, = axa.plot(
                        [],
                        [],
                        lw=1,
                        label=it["name"],
                        color=self._channel_color(it["name"])
                    )

                    self.line_map[
                        it["name"]
                    ] = line

                axa.set_ylabel(
                    "V"
                )

                axa.grid(
                    True,
                    alpha=0.25
                )

                if analog_items:

                    axa.legend(
                        loc="upper right",
                        fontsize=9
                    )

                axa.set_title(
                    "Overlay (Analog)",
                    fontsize=11
                )

                # Digital
                for it in digital_items:

                    line, = axd.step(
                        [],
                        [],
                        where="post",
                        lw=1,
                        label=it["name"],
                        color=self._channel_color(it["name"])
                    )

                    self.line_map[
                        it["name"]
                    ] = line

                axd.set_ylim(
                    -0.2,
                    1.2
                )

                axd.set_yticks(
                    [0, 1]
                )

                axd.set_xlabel(
                    "Time [s]"
                )

                axd.grid(
                    True,
                    alpha=0.25
                )

                if digital_items:

                    axd.legend(
                        loc="upper right",
                        fontsize=9
                    )

                axd.set_title(
                    "Overlay (Digital 0/1)",
                    fontsize=11
                )

            # ----------------------------------------------
            # Analog / Digital same axis
            # ----------------------------------------------

            else:

                ax = self.fig.subplots(
                    nrows=1,
                    ncols=1
                )

                self.axes_all.append(
                    ax
                )

                self.axis_map[
                    "ANALOG"
                ] = ax

                for it in analog_items:

                    line, = ax.plot(
                        [],
                        [],
                        lw=1,
                        label=it["name"],
                        color=self._channel_color(it["name"])
                    )

                    self.line_map[
                        it["name"]
                    ] = line

                for it in digital_items:

                    line, = ax.step(
                        [],
                        [],
                        where="post",
                        lw=1,
                        label=it["name"],
                        color=self._channel_color(it["name"])
                    )

                    self.line_map[
                        it["name"]
                    ] = line

                ax.set_xlabel(
                    "Time [s]"
                )

                ax.set_ylabel(
                    "Value"
                )

                ax.grid(
                    True,
                    alpha=0.25
                )

                if (
                    analog_items
                    or digital_items
                ):

                    ax.legend(
                        loc="upper right",
                        fontsize=9
                    )

                ax.set_title(
                    "Overlay",
                    fontsize=11
                )

        # Apply black waveform background and light labels after all
        # axes/legends have been created.
        self._apply_dark_plot_style()

        # ----------------------------------------------------
        # X軸
        # ----------------------------------------------------

        if (
            self.full_xlim
            and self.axes_all
        ):

            self.axes_all[0].set_xlim(
                *self.full_xlim
            )

        self.shade_unavailable_digital_ranges(
            digital_items
        )

        # 多軸でのtight_layoutは文字・軸の測定を多数回行う。8軸以上は
        # 固定余白に切り替え、4K最大化時のレイアウト停止を避ける。
        if len(self.axes_all) >= 8:
            self.fig.subplots_adjust(
                left=0.075,
                right=0.985,
                top=0.975,
                bottom=0.055,
                hspace=0.42
            )
        else:
            self.fig.tight_layout()

        # ----------------------------------------------------
        # カーソル情報
        # ----------------------------------------------------

        self.cursor_text = (
            self.fig.text(
                0.01,
                0.99,
                "",
                ha="left",
                va="top",
                fontsize=10,
                color=AXIS_FOREGROUND,
                bbox=dict(
                    boxstyle="round,pad=0.3",
                    facecolor=LEGEND_BACKGROUND,
                    edgecolor=SPINE_COLOR,
                    alpha=0.90
                )
            )
        )

        # ----------------------------------------------------
        # xlim_changed
        # ----------------------------------------------------

        if self.axes_all:

            ax0 = self.axes_all[0]

            if self.xlim_cid is not None:

                try:

                    ax0.callbacks.disconnect(
                        self.xlim_cid
                    )

                except Exception:
                    pass

            self.xlim_cid = (
                ax0.callbacks.connect(
                    "xlim_changed",
                    self.on_xlim_changed
                )
            )

        # ----------------------------------------------------
        # SpanSelector
        # ----------------------------------------------------

        self.update_span_selector()

        self.update_info(
            errors
        )

        # ----------------------------------------------------
        # 初回動的間引き
        # ----------------------------------------------------

        if self.axes_all:

            self.on_xlim_changed(
                self.axes_all[0]
            )

            # 空Line作成時の既定Y軸を、実アナログデータ範囲へ更新。
            self._autoscale_initial_analog_y()

        # ----------------------------------------------------
        # Protocol overlay + カーソル復帰
        # ----------------------------------------------------

        self._render_protocol_overlay_visible()
        self.render_cursors()
        self.canvas.draw_idle()

    # ========================================================
    # SpanSelector
    # ========================================================

    def update_span_selector(self):

        self.span = None

        if not self.axes_all:
            return

        ax = self.axes_all[0]

        self.span = SpanSelector(
            ax,
            self.on_span_select,
            "horizontal",
            useblit=True,
            interactive=True
        )

        self.span.set_active(
            self.span_active
        )

    def on_span_select(
        self,
        xmin,
        xmax
    ):

        if (
            xmin is None
            or xmax is None
            or xmin == xmax
        ):
            return

        self.set_xlim_all(
            min(xmin, xmax),
            max(xmin, xmax)
        )

    # ========================================================
    # 入力
    # ========================================================

    def _set_canvas_mouse_cursor(self, cursor_name=""):
        """Change the native Tk mouse pointer over the Matplotlib canvas."""
        cursor_name = cursor_name or ""
        if cursor_name == self._canvas_cursor_name:
            return
        try:
            self.canvas_widget.configure(cursor=cursor_name)
            self._canvas_cursor_name = cursor_name
        except Exception:
            pass

    def _hit_test_cursor(self, event, threshold_px=8):
        """Return the nearest visible measurement-cursor index in pixel space."""
        if event is None or event.inaxes is None or event.x is None:
            return None
        best_index = None
        best_distance = float(threshold_px) + 1.0
        for index, cursor in enumerate(self.cursors):
            x_value = cursor.get("x")
            if x_value is None:
                continue
            try:
                display_x = float(event.inaxes.transData.transform((float(x_value), 0.0))[0])
                distance = abs(float(event.x) - display_x)
            except Exception:
                continue
            if distance <= threshold_px and distance < best_distance:
                best_index = index
                best_distance = distance
        return best_index

    def _refresh_mouse_cursor_for_event(self, event):
        if self.cursor_dragging_index is not None:
            self._set_canvas_mouse_cursor("sb_h_double_arrow")
            return
        if self.pan_dragging:
            self._set_canvas_mouse_cursor("fleur")
            return
        hit = self._hit_test_cursor(event)
        self.cursor_hover_index = hit
        if hit is not None and self.cursor_mode.get():
            self._set_canvas_mouse_cursor("sb_h_double_arrow")
        elif event is not None and event.inaxes is not None:
            self._set_canvas_mouse_cursor("crosshair" if self.cursor_mode.get() else "")
        else:
            self._set_canvas_mouse_cursor("")

    def _move_cursor_by_mouse(self, index, x_value):
        if index is None or not (0 <= index < len(self.cursors)):
            return
        self.cursors[index]["x"] = float(x_value)
        self.active_cursor_index = index
        self._refresh_cursor_combo()
        self.render_cursors()
        self.update_info([])
        if hasattr(self, "auto_measure_scope_var") and self.auto_measure_scope_var.get() == "A-B間":
            self.schedule_auto_measurements(80)

    def on_click(self, event):

        if getattr(
            self.toolbar,
            "mode",
            ""
        ):
            return

        if (
            event.inaxes is None
            or event.xdata is None
            or not self.axes_all
        ):
            return

        if self.view_mode.get() == "stacked":
            analog_items = [
                item
                for item in self.loaded_items
                if item["kind"] == "analog"
            ]
            for index, item in enumerate(analog_items):
                if self.axis_map.get(item["name"]) == event.inaxes:
                    self.active_channel_index = index
                    self.analog_choice.set(item["name"])
                    break

        # ----------------------------------------------------
        # 右クリック：時間軸パン。ドラッグ中はポインタも変更する。
        # ----------------------------------------------------

        if event.button == 3:

            self.pan_dragging = True
            self.cursor_dragging_index = None
            self.pan_start_x = float(event.xdata)
            self.pan_start_xlim = self.axes_all[0].get_xlim()
            self._set_canvas_mouse_cursor("fleur")
            return

        # ----------------------------------------------------
        # 左クリック：既存カーソル線の近くなら、その線を直接つかむ。
        # それ以外は従来どおり次のカーソルを配置する。
        # ----------------------------------------------------

        if event.button == 1:

            if not self.cursor_mode.get():
                return

            hit_index = self._hit_test_cursor(event)
            if hit_index is not None:
                self.cursor_dragging_index = hit_index
                self.active_cursor_index = hit_index
                self.cursor_next_index = (hit_index + 1) % max(1, len(self.cursors))
                self._refresh_cursor_combo()
                self._set_canvas_mouse_cursor("sb_h_double_arrow")
                return

            x = float(event.xdata)

            if (
                self.cursor_next_index
                >= len(self.cursors)
            ):
                self.cursor_next_index = 0

            cursor = self.cursors[
                self.cursor_next_index
            ]

            cursor["x"] = x

            self.active_cursor_index = (
                self.cursor_next_index
            )

            # 次のカーソルへ
            self.cursor_next_index += 1

            if (
                self.cursor_next_index
                >= len(self.cursors)
            ):
                self.cursor_next_index = 0

            self._refresh_cursor_combo()
            self.render_cursors()

            self.update_info([])
            if (
                hasattr(self, "auto_measure_scope_var")
                and self.auto_measure_scope_var.get() == "A-B間"
            ):
                self.schedule_auto_measurements(100)

    def on_motion(self, event):

        if event.inaxes is not None and event.xdata is not None:
            self.pointer_var.set(
                "カーソル位置: "
                + self._format_si(float(event.xdata), "s")
            )
        else:
            self.pointer_var.set("")

        # Measurement cursor drag takes priority over view pan.
        if self.cursor_dragging_index is not None:
            if event.inaxes is not None and event.xdata is not None:
                self._move_cursor_by_mouse(self.cursor_dragging_index, event.xdata)
            self._refresh_mouse_cursor_for_event(event)
            return

        if self.pan_dragging:
            if getattr(self.toolbar, "mode", ""):
                return
            if (
                event.inaxes is not None
                and event.xdata is not None
                and self.pan_start_xlim is not None
            ):
                dx = float(event.xdata) - float(self.pan_start_x)
                xmin0, xmax0 = self.pan_start_xlim
                self.set_xlim_all(xmin0 - dx, xmax0 - dx)
            self._refresh_mouse_cursor_for_event(event)
            return

        # Hover feedback: indicate when a measurement cursor can be grabbed.
        self._refresh_mouse_cursor_for_event(event)

    def on_release(self, event):

        was_cursor_drag = self.cursor_dragging_index is not None
        self.cursor_dragging_index = None
        self.pan_dragging = False
        self.pan_start_x = None
        self.pan_start_xlim = None
        self._refresh_mouse_cursor_for_event(event)

        if was_cursor_drag:
            self.update_info([])
            if hasattr(self, "auto_measure_scope_var") and self.auto_measure_scope_var.get() == "A-B間":
                self.schedule_auto_measurements(80)

    def on_scroll(self, event):

        if getattr(
            self.toolbar,
            "mode",
            ""
        ):
            return

        if (
            event.inaxes is None
            or event.xdata is None
            or not self.axes_all
            or self.full_xlim is None
        ):
            return

        cur_xlim = (
            self.axes_all[0].get_xlim()
        )

        width = (
            cur_xlim[1]
            - cur_xlim[0]
        )

        xcenter = float(
            event.xdata
        )

        # ----------------------------------------------------
        # Shift + wheel：パン
        # ----------------------------------------------------

        if self.shift_down:

            step = width * 0.10

            if event.button == "up":

                self.set_xlim_all(
                    cur_xlim[0] + step,
                    cur_xlim[1] + step
                )

            else:

                self.set_xlim_all(
                    cur_xlim[0] - step,
                    cur_xlim[1] - step
                )

            return

        # ----------------------------------------------------
        # Wheel：Xズーム
        # ----------------------------------------------------

        scale = (
            1 / 1.25
            if event.button == "up"
            else 1.25
        )

        new_width = (
            width * scale
        )

        min_width = (
            self.full_xlim[1]
            - self.full_xlim[0]
        ) / 1e6

        new_width = max(
            new_width,
            min_width
        )

        self.set_xlim_all(
            xcenter - new_width / 2,
            xcenter + new_width / 2
        )

    def on_key_press(self, event):

        if not event.key:
            return

        mods, base = (
            self._parse_keymods(
                event.key
            )
        )

        # ----------------------------------------------------
        # Shift状態
        # ----------------------------------------------------

        if base == "shift":

            self.shift_down = True

            return

        # ----------------------------------------------------
        # A～Z：カーソル選択
        # ----------------------------------------------------
        # R13.3: 追加カーソルC以降を最優先で選択する。
        # 旧ショートカット C/R/Z は、対応するカーソルがまだ存在しない
        # 場合だけ従来動作にフォールバックする。

        if (
            len(base) == 1
            and "a" <= base <= "z"
        ):

            index = (
                ord(base)
                - ord("a")
            )

            if index < len(self.cursors):
                self.select_cursor(index)
                return

            if base == "c":
                self.clear_cursors()
                return
            if base == "z":
                self.toggle_span_zoom()
                return
            if base == "r":
                self.reset_view()
                return

            return

        # ----------------------------------------------------
        # ←→：カーソル移動
        # ----------------------------------------------------

        if base in (
            "left",
            "right"
        ):

            direction = (
                -1
                if base == "left"
                else 1
            )

            # 通常：1サンプル
            # Shift：10サンプル
            if (
                "shift" in mods
                or self.shift_down
            ):

                step = 10

            else:

                step = 1

            self._move_active_cursor_sample(
                direction,
                step
            )

            return

        # ----------------------------------------------------
        # ↑↓
        # ----------------------------------------------------

        if base in (
            "up",
            "down"
        ):

            analog_items = [
                it
                for it in self.loaded_items
                if it["kind"] == "analog"
            ]

            if not analog_items:
                return

            ax = (
                self._get_active_analog_axes()
            )

            # ----------------------------------------------
            # Shift+↑↓：Yオート
            # ----------------------------------------------

            if (
                "shift" in mods
                or self.shift_down
            ):

                self._autoscale_y(ax)

                return

            # ----------------------------------------------
            # Ctrl+↑↓：Yズーム
            # ----------------------------------------------

            if (
                "ctrl" in mods
                or "control" in mods
                or "cmd" in mods
                or "command" in mods
            ):

                self._zoom_y(
                    ax,
                    zoom_in=(                        base == "up"
                    )
                )

                return

            # ----------------------------------------------
            # ↑↓：アクティブCH切替
            # ----------------------------------------------

            self.active_channel_index += (
                -1
                if base == "up"
                else 1
            )

            self.active_channel_index = max(
                0,
                min(
                    self.active_channel_index,
                    len(analog_items) - 1
                )
            )

            self.update_info([])

            return

    def on_key_release(self, event):

        if (
            event.key
            and event.key.lower() == "shift"
        ):

            self.shift_down = False

    # ========================================================
    # 情報表示
    # ========================================================

    def _set_banner_appearance(self, background, foreground):
        self.banner.configure(background=background)

        def apply(widget):
            for child in widget.winfo_children():
                if isinstance(child, (tk.Frame, tk.Label)):
                    try:
                        child.configure(background=background)
                    except tk.TclError:
                        pass
                if isinstance(child, tk.Label):
                    try:
                        child.configure(foreground=foreground)
                    except tk.TclError:
                        pass
                apply(child)

        apply(self.banner)
        self.banner_icon.configure(foreground=foreground)

    def _update_capture_banner(self, capture_warnings):
        if self.dataset_dir is None:
            self.banner_icon.configure(text="●")
            self.banner_title_var.set("データセットを開いてください")
            self.banner_detail_var.set(
                "mho984_XXXX フォルダを選択すると、自動で内容を確認します。"
            )
            self._set_banner_appearance("#E8F0FE", "#1967D2")
            return

        summary = self.dataset_metadata.get("capture_summary")
        if isinstance(summary, dict):
            if summary.get("success"):
                status = "success"
            elif summary.get("partial_success"):
                status = "partial"
            else:
                status = "failed"
        elif isinstance(self.dataset_metadata.get("capture_error"), dict):
            status = "failed"
        else:
            status = "legacy"

        appearances = {
            "success": (
                "✓",
                "取得データは正常です",
                "#E6F4EA",
                "#137333"
            ),
            "partial": (
                "!",
                "一部の取得データに注意が必要です",
                "#FEF7E0",
                "#9A6700"
            ),
            "failed": (
                "×",
                "取得処理でエラーが記録されています",
                "#FCE8E6",
                "#B3261E"
            ),
            "legacy": (
                "i",
                "データセットを読み込みました",
                "#E8F0FE",
                "#1967D2"
            ),
        }
        icon, title, background, foreground = appearances[status]

        analog_names = [name for name, _, _ in self.analog_list]
        details = [
            "アナログ: "
            + (", ".join(analog_names) if analog_names else "なし")
        ]

        digital_summary = (
            summary.get("digital")
            if isinstance(summary, dict)
            else None
        )
        if isinstance(digital_summary, dict):
            event_count = digital_summary.get("event_count")
            if event_count is not None:
                details.append(f"デジタル: {int(event_count):,}イベント")
            coverage = digital_summary.get("coverage")
            if isinstance(coverage, dict):
                ratio = coverage.get("analog_coverage_ratio")
                if ratio is not None:
                    details.append(f"有効範囲: {float(ratio):.1%}")
        elif self.digital_list:
            details.append(f"デジタル: {len(self.digital_list)}ch")

        active_channels = [
            name
            for name, activity in self.digital_activity.items()
            if int(activity.get("transition_count", 0)) > 0
        ]
        if active_channels:
            details.append("変化あり: " + ", ".join(active_channels))
        if capture_warnings:
            details.append(f"警告: {len(capture_warnings)}件")

        self.banner_icon.configure(text=icon)
        self.banner_title_var.set(title)
        self.banner_detail_var.set("  |  ".join(details))
        self._set_banner_appearance(background, foreground)

    def _digital_state_at_cursor(self, item, cursor_x):
        if cursor_x is None or len(item["x"]) == 0:
            return None

        meta = item.get("meta", {})
        if meta.get("source_format") == "event_table":
            source = item.get("digital_source")
            if isinstance(source, DigitalBusSource):
                return source.state_at(
                    meta.get("bit", 0),
                    cursor_x
                )

        coverage = digital_coverage_xlim(meta)
        if coverage is not None:
            left, right = sorted(coverage)
            if cursor_x < left or cursor_x > right:
                return None

        index = int(
            _searchsorted(item["x"], cursor_x, side="right")
        ) - 1
        index = max(0, min(index, len(item["dig"]) - 1))
        return int(item["dig"][index])

    def _update_measurement_panel(self):
        self._refresh_cursor_combo()
        lines = [
            f"操作中: カーソル {self.cursors[self.active_cursor_index]['name']}",
            ""
        ]

        for cursor in self.cursors:
            active_mark = (
                "  ←操作中"
                if cursor is self.cursors[self.active_cursor_index]
                else ""
            )
            lines.append(
                f"{cursor['name']}: "
                + self._format_si(cursor["x"], "s")
                + active_mark
            )

        measurement = self._get_cursor_measurement()
        analog_items = [
            item for item in self.loaded_items if item["kind"] == "analog"
        ]
        digital_items = [
            item for item in self.loaded_items if item["kind"] == "digital"
        ]

        if measurement is None:
            lines.extend(
                [
                    "",
                    "AとBを波形上へ配置すると、",
                    "Δt・周波数・電圧差を表示します。"
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    "時間測定",
                    "────────────",
                    "Δt (A→B): "
                    + self._format_si(measurement["dt"], "s")
                ]
            )
            if measurement["frequency"] is not None:
                lines.append(
                    "1 / |Δt|: "
                    + self._format_si(measurement["frequency"], "Hz")
                )

            if analog_items:
                lines.extend(["", "アナログ電圧", "────────────"])
                for item in analog_items:
                    value_a = self._get_voltage_at_cursor(
                        item,
                        measurement["a"]
                    )
                    value_b = self._get_voltage_at_cursor(
                        item,
                        measurement["b"]
                    )
                    if value_a is None or value_b is None:
                        continue
                    voltage_a = value_a[1]
                    voltage_b = value_b[1]
                    lines.append(
                        f"{item['name']}:\n"
                        f"  A={voltage_a:.6g} V\n"
                        f"  B={voltage_b:.6g} V\n"
                        f"  ΔV={voltage_b - voltage_a:.6g} V"
                    )

            if digital_items:
                lines.extend(["", "デジタル状態", "────────────"])
                for item in digital_items:
                    state_a = self._digital_state_at_cursor(
                        item,
                        measurement["a"]
                    )
                    state_b = self._digital_state_at_cursor(
                        item,
                        measurement["b"]
                    )
                    text_a = "範囲外" if state_a is None else str(state_a)
                    text_b = "範囲外" if state_b is None else str(state_b)
                    changed = (
                        " 変化"
                        if state_a is not None
                        and state_b is not None
                        and state_a != state_b
                        else ""
                    )
                    lines.append(
                        f"{item['name']}: A={text_a}  B={text_b}{changed}"
                    )

        placed_cursors = [
            cursor
            for cursor in self.cursors
            if cursor.get("x") is not None
        ]

        if len(placed_cursors) >= 3:
            lines.extend([
                "",
                "追加カーソル値（C以降）",
                "────────────",
            ])

            for cursor in placed_cursors[2:]:
                x_cursor = cursor["x"]
                lines.append(
                    f"{cursor['name']} @ "
                    f"{self._format_si(x_cursor, 's')}"
                )

                analog_values = []
                for item in analog_items:
                    value = self._get_voltage_at_cursor(
                        item,
                        x_cursor
                    )
                    if value is not None:
                        analog_values.append(
                            f"{item['name']}={value[1]:.6g} V"
                        )

                if analog_values:
                    lines.append(
                        "  " + "  ".join(analog_values)
                    )

                digital_values = []
                for item in digital_items:
                    state = self._digital_state_at_cursor(
                        item,
                        x_cursor
                    )
                    if state is not None:
                        digital_values.append(
                            f"{item['name']}={state}"
                        )

                if digital_values:
                    # 16chでも横1行が長くなりすぎないよう8chずつ。
                    for start_index in range(
                        0,
                        len(digital_values),
                        8
                    ):
                        lines.append(
                            "  "
                            + "  ".join(
                                digital_values[
                                    start_index:start_index + 8
                                ]
                            )
                        )

        self._replace_text(self.measurement_info, "\n".join(lines) + "\n")

    def _update_diagnostic_panel(self, errors, capture_warnings):
        if self.dataset_dir is None:
            text = (
                "診断情報は、データセットを開いた後に表示されます。\n\n"
                "取得結果がPARTIALまたはFAILEDの場合は、このタブで原因と"
                "確認対象ファイルを確認できます。"
            )
            self._replace_text(self.diagnostic_info, text)
            return

        lines = [
            "診断サマリー",
            "============",
            f"Viewer: {VIEWER_TOOL_VERSION}",
            f"Folder: {self.dataset_dir}",
        ]

        pyramid_caches = [
            source.get("pyramid")
            for source in self.analog_source_cache.values()
            if isinstance(source.get("pyramid"), AnalogMinMaxPyramid)
        ]
        ready_pyramids = [
            pyramid for pyramid in pyramid_caches if pyramid.ready
        ]
        cache_bytes = sum(
            pyramid.cache_bytes for pyramid in ready_pyramids
        )
        lines.extend(
            [
                "",
                "表示高速化",
                "------------",
                "Analog min/max pyramid: "
                f"{len(ready_pyramids)}/{len(pyramid_caches)} ch ready, "
                f"{cache_bytes / (1024 ** 2):.2f} MiB",
            ]
        )

        try:
            canvas_width, canvas_height = self.canvas.get_width_height(
                physical=True
            )
        except TypeError:
            canvas_width, canvas_height = self.canvas.get_width_height()
        lines.append(
            "Canvas: "
            f"{int(canvas_width)} x {int(canvas_height)} px, "
            "4K-safe="
            f"{bool(self._high_res_rendering_active)}"
        )
        lines.append(
            "Plot host resize: "
            f"requests={self._plot_resize_request_count:,}, "
            f"applied={self._plot_resize_apply_count:,}, "
            f"delay={PLOT_RESIZE_DEBOUNCE_MS} ms, "
            f"capped={bool(self._canvas_size_capped)}, "
            f"large_mode={bool(self._plot_large_mode)}, "
            f"canvas={self._plot_applied_size[0]}x{self._plot_applied_size[1]}"
        )

        bus_sources = {
            id(source): source
            for source in self.digital_event_cache.values()
            if isinstance(source, DigitalBusSource)
        }
        if bus_sources:
            total_events = sum(
                len(source.times) for source in bus_sources.values()
            )
            lines.append(
                "Digital shared BUS: "
                f"{len(bus_sources)} source, {total_events:,} events "
                "(D0-D15 shared)"
            )

        for pyramid in pyramid_caches:
            if pyramid.build_error:
                lines.append(
                    "Analog cache fallback: " + pyramid.build_error
                )

        summary = self.dataset_metadata.get("capture_summary")
        if isinstance(summary, dict):
            status = (
                "SUCCESS"
                if summary.get("success")
                else (
                    "PARTIAL"
                    if summary.get("partial_success")
                    else "FAILED"
                )
            )
            lines.append(f"Capture status: {status}")
            digital = summary.get("digital")
            if isinstance(digital, dict):
                lines.extend(
                    [
                        f"Digital status: {digital.get('status', 'unknown')}",
                        f"Method: {digital.get('method', 'unknown')}",
                        f"Merged events: {int(digital.get('event_count', 0)):,}",
                    ]
                )
                if digital.get("probe_saturated") is not None:
                    lines.append(
                        "Full-view probe: "
                        f"{int(digital.get('probe_event_count', 0)):,} rows, "
                        f"saturated={bool(digital.get('probe_saturated'))}"
                    )
                segmentation = digital.get("segmentation")
                if isinstance(segmentation, dict) and segmentation:
                    lines.append(
                        "Segment scan: "
                        f"complete={segmentation.get('complete', False)}, "
                        f"queries={int(segmentation.get('table_queries', 0)):,}, "
                        f"accepted={int(segmentation.get('accepted_tables', 0)):,}, "
                        f"split={int(segmentation.get('split_tables', 0)):,}"
                    )

        capture_error = self.dataset_metadata.get("capture_error")
        if isinstance(capture_error, dict):
            lines.extend(
                [
                    "",
                    "Capture error",
                    "-------------",
                    str(capture_error.get("message", "unknown"))
                ]
            )

        if capture_warnings:
            lines.extend(["", "Warnings", "--------"])
            lines.extend(f"- {warning}" for warning in capture_warnings)

        if errors:
            lines.extend(["", "Viewer errors", "-------------"])
            lines.extend(f"- {error}" for error in errors)

        if not capture_warnings and not errors and not capture_error:
            lines.extend(["", "Viewerが検出した追加エラーはありません。"])

        lines.extend(
            [
                "",
                "主な確認ファイル",
                "----------------",
                "capture_summary.json",
                "digital_coverage.json",
                "digital_parse_report.json",
                "digital_segment_manifest.json（分割取得時）",
            ]
        )
        self._replace_text(self.diagnostic_info, "\n".join(lines) + "\n")


    # ========================================================
    # R13.5 Digital time-axis calibration
    # ========================================================

    def _digital_calibration_values(self):
        mode = (
            self.digital_time_mode_var.get().strip()
            if hasattr(self, "digital_time_mode_var")
            else "Raw"
        )
        ppm = float(
            self.digital_time_calibration_ppm_var.get().strip()
            if hasattr(self, "digital_time_calibration_ppm_var")
            else DEFAULT_DIGITAL_TIME_CALIBRATION_PPM
        )
        if not np.isfinite(ppm):
            raise ValueError("Digital calibration ppm must be finite")
        ref = float(getattr(
            self,
            "digital_time_calibration_reference_s",
            DEFAULT_DIGITAL_TIME_CALIBRATION_REFERENCE_S,
        ))
        corrected = mode != "Raw"
        return mode, ppm, ref, corrected

    def _load_digital_events_current(self, path):
        mode, ppm, ref, _corrected = self._digital_calibration_values()
        return load_digital_events(
            path,
            calibration_mode=mode,
            calibration_ppm=ppm,
            calibration_reference_s=ref,
        )

    def _load_calibration_from_dataset_metadata(self, metadata):
        calibration = None
        acquisition = metadata.get("acquisition") if isinstance(metadata, dict) else None
        if isinstance(acquisition, dict):
            channels = acquisition.get("channels", {})
            digital = channels.get("digital", {}) if isinstance(channels, dict) else {}
            if isinstance(digital, dict):
                calibration = digital.get("time_calibration")
            if not isinstance(calibration, dict):
                sync = acquisition.get("synchronization", {})
                if isinstance(sync, dict):
                    calibration = sync.get("digital_time_calibration")
        if isinstance(calibration, dict):
            try:
                ppm = float(calibration.get(
                    "scale_correction_ppm",
                    DEFAULT_DIGITAL_TIME_CALIBRATION_PPM,
                ))
                ref = float(calibration.get(
                    "reference_s",
                    DEFAULT_DIGITAL_TIME_CALIBRATION_REFERENCE_S,
                ))
                if np.isfinite(ppm) and np.isfinite(ref):
                    self.digital_time_calibration_ppm_var.set(f"{ppm:.9f}")
                    self.digital_time_calibration_reference_s = ref
                    self.digital_time_calibration_source = "dataset metadata"
                    return
            except Exception:
                pass
        self.digital_time_calibration_ppm_var.set(
            f"{DEFAULT_DIGITAL_TIME_CALIBRATION_PPM:.9f}"
        )
        self.digital_time_calibration_reference_s = (
            DEFAULT_DIGITAL_TIME_CALIBRATION_REFERENCE_S
        )
        self.digital_time_calibration_source = "reference profile from one tested MHO984 (mho984_0034); not universal"

    def apply_digital_time_calibration_settings(self):
        try:
            mode, ppm, ref, corrected = self._digital_calibration_values()
        except Exception as exc:
            messagebox.showerror("Digital時間軸", str(exc))
            return
        self.digital_event_cache = {}
        if self.dataset_dir is not None:
            self.on_select()
        else:
            self.schedule_auto_measurements(40)
        self.status_var.set(
            "Digital time axis: "
            + (
                f"Calibrated {ppm:+.6f} ppm @ t={ref:g} s"
                if corrected
                else "Raw"
            )
        )

    def _convert_sync_result_timebase(self, result, target_mode):
        if not result or not result.get("pairs"):
            return dict(result or {})
        _mode, ppm, ref, corrected_now = self._digital_calibration_values()
        scale = 1.0 + ppm * 1e-6
        target_corrected = str(target_mode).strip().lower() not in ("raw", "生", "未補正")
        pairs = []
        for ta, td, _dt in result["pairs"]:
            ta = float(ta); td = float(td)
            if corrected_now and not target_corrected:
                td2 = ref + (td - ref) / scale
            elif (not corrected_now) and target_corrected:
                td2 = ref + (td - ref) * scale
            else:
                td2 = td
            pairs.append((ta, td2, td2 - ta))
        out = dict(result)
        out["pairs"] = pairs
        out["pair_count"] = len(pairs)
        dt = np.asarray([p[2] for p in pairs], dtype=np.float64)
        if dt.size:
            out.update(
                mean_dt_s=float(np.mean(dt)),
                median_dt_s=float(np.median(dt)),
                std_dt_s=float(np.std(dt)),
                min_dt_s=float(np.min(dt)),
                max_dt_s=float(np.max(dt)),
                rms_dt_s=float(np.sqrt(np.mean(dt * dt))),
            )
        for key in list(out):
            if key.startswith("drift_"):
                out.pop(key, None)
        out.update(self._sync_drift_statistics(pairs))
        out["digital_time_mode"] = "Calibrated" if target_corrected else "Raw"
        return out

    # ========================================================
    # R13.5 Automatic timing measurements / selectable scope
    # ========================================================

    def schedule_auto_measurements(self, delay_ms=180):
        """選択/表示/カーソル変更の連続発生をまとめ、最後に1回だけ測定する。"""
        if not hasattr(self, "auto_measure_info"):
            return
        if self.auto_measure_after_id is not None:
            try:
                self.after_cancel(self.auto_measure_after_id)
            except Exception:
                pass
        self.auto_measure_after_id = self.after(
            int(delay_ms),
            self.run_auto_measurements
        )

    def _auto_measure_scope(self):
        """
        自動測定に使う範囲を返す。

        Returns:
            (bounds, label)
            bounds=None -> 全範囲
            bounds=(xmin, xmax) -> 表示範囲/A-B間
        """
        mode = (
            self.auto_measure_scope_var.get().strip()
            if hasattr(self, "auto_measure_scope_var")
            else "全範囲"
        )

        if mode == "全範囲":
            return None, "全範囲"

        if mode == "表示範囲":
            if not self.axes_all:
                raise ValueError("表示範囲を取得できません。先にデータセットを開いてください。")
            xmin, xmax = self.axes_all[0].get_xlim()
            xmin = float(xmin)
            xmax = float(xmax)
            if xmax < xmin:
                xmin, xmax = xmax, xmin
            if not np.isfinite(xmin) or not np.isfinite(xmax) or xmax <= xmin:
                raise ValueError("表示範囲が不正です。")
            return (xmin, xmax), "表示範囲"

        if mode == "A-B間":
            if len(self.cursors) < 2:
                raise ValueError("A/Bカーソルがありません。")
            a = self.cursors[0].get("x")
            b = self.cursors[1].get("x")
            if a is None or b is None:
                raise ValueError("A-B間測定にはA/Bカーソルを両方配置してください。")
            xmin = float(min(a, b))
            xmax = float(max(a, b))
            if not np.isfinite(xmin) or not np.isfinite(xmax) or xmax <= xmin:
                raise ValueError("A/Bカーソル間隔が0です。")
            return (xmin, xmax), "A-B間"

        raise ValueError(f"未対応の測定範囲: {mode}")

    @staticmethod
    def _filter_times_to_bounds(times, bounds):
        times = np.asarray(times, dtype=np.float64)
        if bounds is None or times.size == 0:
            return times
        lo, hi = sorted((float(bounds[0]), float(bounds[1])))
        left = int(np.searchsorted(times, lo, side="left"))
        right = int(np.searchsorted(times, hi, side="right"))
        return times[left:right]

    def _digital_transition_series(self, name, bounds=None):
        """指定Dチャンネルの遷移時刻と遷移後状態を返す。"""
        digital = self._sync_get_digital_source(name)
        bit = int(digital["bit"])

        if digital["kind"] == "events":
            times = np.asarray(digital["times"], dtype=np.float64)
            bus = np.asarray(digital["bus"], dtype=np.uint16)
            if len(times) == 0:
                return times, np.empty(0, dtype=np.uint8), None
            states = ((bus >> bit) & 1).astype(np.uint8, copy=False)
        else:
            times = digital["times"]
            states = np.asarray(digital["states"], dtype=np.uint8)
            if len(times) == 0:
                return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.uint8), None

        if len(states) == 0:
            return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.uint8), None

        record_initial_state = int(states[0])

        if len(states) < 2:
            return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.uint8), record_initial_state

        changed = states[1:] != states[:-1]
        edge_indices = np.flatnonzero(changed) + 1
        if isinstance(times, UniformTimeAxis):
            edge_times = self._sync_time_at_indices(times, edge_indices)
        else:
            edge_times = np.asarray(times[edge_indices], dtype=np.float64)
        new_states = states[edge_indices]

        initial_state = record_initial_state
        if bounds is not None:
            lo, hi = sorted((float(bounds[0]), float(bounds[1])))
            before = int(np.searchsorted(edge_times, lo, side="right"))
            if before > 0:
                initial_state = int(new_states[before - 1])
            mask = (edge_times >= lo) & (edge_times <= hi)
            edge_times = edge_times[mask]
            new_states = new_states[mask]

        return (
            np.asarray(edge_times, dtype=np.float64),
            np.asarray(new_states, dtype=np.uint8),
            initial_state
        )

    @staticmethod
    def _positive_finite(values):
        values = np.asarray(values, dtype=np.float64)
        return values[np.isfinite(values) & (values > 0)]

    def _measure_digital_timing(self, name, bounds=None):
        """
        Rising-to-rising周期を基準に周波数/周期/Dutyを計算する。
        指定範囲の端にかかる不完全周期は除外する。
        """
        edge_times, new_states, initial_state = self._digital_transition_series(
            name,
            bounds=bounds
        )
        rising = edge_times[new_states == 1]
        falling = edge_times[new_states == 0]

        result = {
            "channel": name,
            "initial_state": initial_state,
            "transition_count": int(len(edge_times)),
            "rising_count": int(len(rising)),
            "falling_count": int(len(falling)),
            "period_s": None,
            "period_mean_s": None,
            "period_std_s": None,
            "frequency_hz": None,
            "high_s": None,
            "low_s": None,
            "duty_percent": None,
            "cycle_count": 0,
            "bounds": bounds,
        }

        if len(rising) < 2:
            return result

        period_all = self._positive_finite(np.diff(rising))
        if len(period_all) == 0:
            return result

        # LA timing is quantized to the effective Digital interval (1 ns in the
        # verified 2 GSa/s / stride-2 case). For frequencies such as 12 MHz,
        # individual periods alternate between 83 ns and 84 ns. Therefore the
        # arithmetic mean is the unbiased representative; the median would
        # incorrectly report 83 ns (=12.048 MHz).
        result["period_s"] = float(np.mean(period_all))
        result["period_mean_s"] = result["period_s"]
        result["period_std_s"] = float(np.std(period_all))
        if result["period_s"] > 0:
            result["frequency_hz"] = float(1.0 / result["period_s"])

        # Each complete cycle: rising_i -> falling -> rising_(i+1)
        starts = rising[:-1]
        ends = rising[1:]
        fall_pos = np.searchsorted(falling, starts, side="right")
        valid = fall_pos < len(falling)
        if not np.any(valid):
            return result

        starts_v = starts[valid]
        ends_v = ends[valid]
        falls_v = falling[fall_pos[valid]]
        inside = falls_v < ends_v
        if not np.any(inside):
            return result

        starts_v = starts_v[inside]
        ends_v = ends_v[inside]
        falls_v = falls_v[inside]

        raw_periods = ends_v - starts_v
        raw_highs = falls_v - starts_v
        valid_cycle = (
            np.isfinite(raw_periods)
            & np.isfinite(raw_highs)
            & (raw_periods > 0)
            & (raw_highs >= 0)
            & (raw_highs <= raw_periods)
        )
        if not np.any(valid_cycle):
            return result

        cycle_periods = raw_periods[valid_cycle]
        cycle_highs = raw_highs[valid_cycle]
        cycle_lows = cycle_periods - cycle_highs
        duty = 100.0 * cycle_highs / cycle_periods

        result["cycle_count"] = int(len(cycle_periods))
        result["high_s"] = float(np.mean(cycle_highs))
        result["low_s"] = float(np.mean(cycle_lows))
        # Aggregate ratio is less biased by 1 ns edge quantization than the
        # median of individual cycle duty values.
        total_period = float(np.sum(cycle_periods))
        total_high = float(np.sum(cycle_highs))
        result["duty_percent"] = (
            float(100.0 * total_high / total_period)
            if total_period > 0
            else None
        )
        return result

    @staticmethod
    def _sync_drift_statistics(pairs):
        """
        Δt = Digital - Analog の時間変化を一次回帰し、傾きをppmで返す。

        drift_ppm = d(Δt) / d(time) * 1e6
        正値: 記録の後半ほどDigital edgeがAnalogより遅れる方向。
        """
        if len(pairs) < 2:
            return {}

        ta = np.asarray([p[0] for p in pairs], dtype=np.float64)
        dt = np.asarray([p[2] for p in pairs], dtype=np.float64)
        finite = np.isfinite(ta) & np.isfinite(dt)
        ta = ta[finite]
        dt = dt[finite]
        if ta.size < 2:
            return {}

        t_ref = float(np.mean(ta))
        x = ta - t_ref
        y_mean = float(np.mean(dt))
        y = dt - y_mean
        denom = float(np.dot(x, x))
        if not np.isfinite(denom) or denom <= 0:
            return {}

        slope = float(np.dot(x, y) / denom)
        intercept_at_ref = y_mean
        predicted = intercept_at_ref + slope * x
        residual = dt - predicted
        ss_res = float(np.dot(residual, residual))
        centered = dt - y_mean
        ss_tot = float(np.dot(centered, centered))
        r_squared = None
        if ss_tot > 0:
            r_squared = float(1.0 - ss_res / ss_tot)

        span_s = float(np.max(ta) - np.min(ta))
        drift_ppm = float(slope * 1e6)
        reliable = bool(
            ta.size >= 1000
            and span_s >= 5e-4
            and r_squared is not None
            and r_squared >= 0.5
        )
        return {
            "drift_ppm": drift_ppm,
            "drift_s_per_s": slope,
            "drift_delay_span_s": float(slope * span_s),
            "drift_fit_span_s": span_s,
            "drift_reference_time_s": t_ref,
            "drift_intercept_s": intercept_at_ref,
            "drift_r_squared": r_squared,
            "drift_reliable": reliable,
            "drift_pair_count": int(ta.size),
        }

    def _calculate_sync_result(
        self,
        analog_name=None,
        digital_name=None,
        *,
        bounds=None,
        threshold_text=None,
        slope=None,
        tolerance_ns=None,
    ):
        analog_name = (
            self.sync_analog_var.get().strip()
            if analog_name is None
            else str(analog_name).strip()
        )
        digital_name = (
            self.sync_digital_var.get().strip()
            if digital_name is None
            else str(digital_name).strip()
        )
        slope = (
            self.sync_slope_var.get().strip() or "Both"
            if slope is None
            else str(slope).strip() or "Both"
        )

        x, y, _pre = self._sync_get_analog_source(analog_name)
        digital = self._sync_get_digital_source(digital_name)

        if threshold_text is None:
            threshold_text = self.sync_threshold_var.get().strip()
        threshold_text = str(threshold_text).strip()
        if threshold_text.lower() in ("auto", "自動", ""):
            threshold = self._sync_auto_threshold(y, x=x, bounds=bounds)
            threshold_mode = "Auto"
        else:
            threshold = float(threshold_text)
            threshold_mode = "Manual"

        if tolerance_ns is None:
            tolerance_ns = float(self.sync_tolerance_ns_var.get())
        else:
            tolerance_ns = float(tolerance_ns)
        if not np.isfinite(tolerance_ns) or tolerance_ns <= 0:
            raise ValueError("Pair tolerance must be a positive number")
        tolerance_s = tolerance_ns * 1e-9

        a_edges = self._sync_analog_crossings(
            x,
            y,
            threshold,
            slope,
            bounds=bounds
        )
        d_edges = self._sync_digital_edges(
            digital,
            slope,
            bounds=bounds
        )
        pairs = self._sync_pair_edges(a_edges, d_edges, tolerance_s)
        dt = np.asarray([p[2] for p in pairs], dtype=np.float64)

        result = {
            "analog": analog_name,
            "digital": digital_name,
            "threshold_v": threshold,
            "threshold_mode": threshold_mode,
            "slope": slope,
            "tolerance_ns": tolerance_ns,
            "analog_edge_count": int(len(a_edges)),
            "digital_edge_count": int(len(d_edges)),
            "pair_count": int(len(pairs)),
            "pairs": pairs,
            "bounds": bounds,
            "digital_time_mode": (
                "Calibrated" if self._digital_calibration_values()[3] else "Raw"
            ),
            "digital_time_calibration_ppm": self._digital_calibration_values()[1],
            "digital_time_reference_s": self._digital_calibration_values()[2],
        }
        if len(dt):
            result.update(
                mean_dt_s=float(np.mean(dt)),
                median_dt_s=float(np.median(dt)),
                std_dt_s=float(np.std(dt)),
                min_dt_s=float(np.min(dt)),
                max_dt_s=float(np.max(dt)),
                rms_dt_s=float(np.sqrt(np.mean(dt * dt))),
            )
        result.update(self._sync_drift_statistics(pairs))
        return result

    def _append_auto_sync_result(self, lines, result):
        lines += [
            f"Timebase     : {result.get('digital_time_mode', 'Raw')}",
            f"Threshold    : {result['threshold_v']:.9g} V ({result['threshold_mode']})",
            f"Slope        : {result['slope']}",
            f"Tolerance    : {result['tolerance_ns']:.6g} ns",
            f"Paired edges : {result['pair_count']:,}",        ]
        if result.get("pair_count", 0):
            lines += [
                f"Mean Δt      : {result['mean_dt_s']*1e9:+.6f} ns",
                f"Median Δt    : {result['median_dt_s']*1e9:+.6f} ns",
                f"Std.dev      : {result['std_dt_s']*1e9:.6f} ns",
                f"Min / Max    : {result['min_dt_s']*1e9:+.6f} / {result['max_dt_s']*1e9:+.6f} ns",
            ]
            if result.get("drift_ppm") is not None:
                lines += [
                    f"Drift        : {result['drift_ppm']:+.6f} ppm",
                    "Δdelay/span  : " + self._format_si(
                        result.get("drift_delay_span_s"),
                        "s"
                    ),
                    "Fit span     : " + self._format_si(
                        result.get("drift_fit_span_s"),
                        "s"
                    ),
                ]
                if result.get("drift_r_squared") is not None:
                    lines.append(f"Drift R²     : {result['drift_r_squared']:.6f}")
                lines.append(
                    "Drift quality : "
                    + (
                        "GOOD"
                        if result.get("drift_reliable")
                        else "REFERENCE (short/noisy range)"
                    )
                )
        else:
            lines.append("No edge pairs inside tolerance.")

    def run_auto_measurements(self):
        self.auto_measure_after_id = None
        if not hasattr(self, "auto_measure_info"):
            return

        lines = [
            "Automatic timing measurements",
            "=============================",
        ]

        try:
            bounds, scope_label = self._auto_measure_scope()
            lines.append(f"Scope        : {scope_label}")
            if bounds is not None:
                lines.append(
                    "Range        : "
                    + self._format_si(bounds[0], "s")
                    + " ～ "
                    + self._format_si(bounds[1], "s")
                )
                lines.append(
                    "Width        : "
                    + self._format_si(bounds[1] - bounds[0], "s")
                )
        except Exception as exc:
            self._replace_text(
                self.auto_measure_info,
                "Automatic timing measurements\n=============================\n\n"
                f"測定範囲エラー: {exc}\n"
            )
            return

        try:
            _mode, _ppm, _ref, _corrected = self._digital_calibration_values()
            lines += [
                f"Digital time : {'Calibrated' if _corrected else 'Raw'}",
                f"Correction  : {_ppm:+.9f} ppm",
                f"Reference   : {_ref:+.9g} s (trigger)",
                f"Cal source  : {self.digital_time_calibration_source}",
            ]
        except Exception as exc:
            lines += [f"Digital calibration error: {exc}"]

        digital_names = [
            item["name"]
            for item in self.loaded_items
            if item.get("kind") == "digital"
        ]

        lines += ["", "Digital frequency / period / duty", "================================="]
        self.auto_measure_cache = {}
        if not digital_names:
            lines += ["", "左側でDチャンネルを選択してください。"]
        else:
            for name in digital_names:
                try:
                    result = self._measure_digital_timing(name, bounds=bounds)
                    self.auto_measure_cache[name] = result

                    lines += ["", name, "------------------------------"]
                    if result["frequency_hz"] is None:
                        state_text = (
                            "unknown"
                            if result["initial_state"] is None
                            else str(result["initial_state"])
                        )
                        lines += [
                            "Frequency   : -",
                            "Period      : -",
                            "Duty        : -",
                            f"State/start : {state_text}",
                            f"Transitions : {result['transition_count']:,}",
                        ]
                    else:
                        lines += [
                            "Frequency   : " + self._format_si(result["frequency_hz"], "Hz"),
                            "Period      : " + self._format_si(result["period_s"], "s"),
                            "High width  : " + self._format_si(result["high_s"], "s"),
                            "Low width   : " + self._format_si(result["low_s"], "s"),
                            f"Duty        : {result['duty_percent']:.6f} %",
                            "Period mean : " + self._format_si(result["period_mean_s"], "s"),
                            "Period σ    : " + self._format_si(result["period_std_s"], "s"),
                            f"Cycles used : {result['cycle_count']:,}",
                            f"Edges R/F   : {result['rising_count']:,} / {result['falling_count']:,}",
                        ]
                except Exception as exc:
                    lines += ["", name, "------------------------------", f"ERROR: {type(exc).__name__}: {exc}"]

        # Two verified signal pairs are always shown together.
        lines += [
            "",
            "Verified Analog ↔ Digital pairs",
            "================================",
            "Fixed analysis: Threshold=Auto, Slope=Rising",
            "Drift = d(Digital-Analog delay)/dt × 1e6 ppm",
        ]
        try:
            tolerance_ns = float(self.sync_tolerance_ns_var.get())
        except Exception:
            tolerance_ns = 20.0

        fixed_pairs = (("CHAN1", "D8"), ("CHAN2", "D0"))
        self.auto_pair_results_cache = {}
        for analog_name, digital_name in fixed_pairs:
            lines += ["", f"{analog_name} ↔ {digital_name}", "------------------------------"]
            try:
                result = self._calculate_sync_result(
                    analog_name,
                    digital_name,
                    bounds=bounds,
                    threshold_text="Auto",
                    slope="Rising",
                    tolerance_ns=tolerance_ns,
                )
                self.auto_pair_results_cache[(analog_name, digital_name)] = result
                self._append_auto_sync_result(lines, result)
                raw_result = self._convert_sync_result_timebase(result, "Raw")
                calibrated_result = self._convert_sync_result_timebase(result, "Calibrated")
                lines += [
                    "",
                    "Timebase calibration",
                    f"Raw drift     : {raw_result.get('drift_ppm', float('nan')):+.6f} ppm",
                    f"Applied corr. : {_ppm:+.6f} ppm",
                    f"Residual drift: {calibrated_result.get('drift_ppm', float('nan')):+.6f} ppm",
                ]
                if calibrated_result.get("drift_r_squared") is not None:
                    lines.append(
                        f"Residual R²   : {calibrated_result['drift_r_squared']:.6f}"
                    )
            except Exception as exc:
                lines.append(f"Unavailable: {type(exc).__name__}: {exc}")

        # Keep r13.3 manual-pair behavior as an additional summary.
        lines += ["", "同期確認タブの選択ペア", "========================"]
        try:
            sync_result = self._calculate_sync_result(bounds=bounds)
            self.sync_result_cache = sync_result
            lines.append(f"Pair         : {sync_result['analog']} ↔ {sync_result['digital']}")
            self._append_auto_sync_result(lines, sync_result)
        except Exception as exc:
            lines.append(f"Edge delay unavailable: {type(exc).__name__}: {exc}")

        self._replace_text(self.auto_measure_info, "\n".join(lines) + "\n")

    # ========================================================
    # R12 Analog / Digital synchronization analysis
    # ========================================================

    def _refresh_sync_source_choices(self):
        analog_names = [entry[0] for entry in getattr(self, "analog_list", [])]
        digital_names = [entry[0] for entry in getattr(self, "digital_list", [])]
        try:
            self.sync_analog_combo.configure(values=analog_names)
            self.sync_digital_combo.configure(values=digital_names)
        except Exception:
            return
        if analog_names and self.sync_analog_var.get() not in analog_names:
            self.sync_analog_var.set(analog_names[0])
        if digital_names and self.sync_digital_var.get() not in digital_names:
            self.sync_digital_var.set(digital_names[0])

    def _sync_get_analog_source(self, name):
        for nm, bin_path, pre_path in getattr(self, "analog_list", []):
            if nm != name:
                continue
            cached = self.analog_source_cache.get(bin_path)
            if cached is None:
                x, y, pre = load_analog(bin_path, pre_path)
                cached = dict(x=x, y=y, pre=pre, pyramid=None)
                self.analog_source_cache[bin_path] = cached
            return cached["x"], cached["y"], cached["pre"]
        raise ValueError(f"Analog channel not found: {name}")

    def _sync_get_digital_source(self, name):
        for nm, bin_path, pre_path in getattr(self, "digital_list", []):
            if nm != name:
                continue
            if pre_path is not None:
                x, bytes_u8, pre = load_digital_group_bytes(bin_path, pre_path)
                bit_number = int(name[1:])
                group_base = (bit_number // 8) * 8
                bit = bit_number - group_base
                dig = ((bytes_u8 >> bit) & 1).astype(np.uint8)
                return {
                    "kind": "sampled",
                    "times": x,
                    "states": dig,
                    "bit": bit_number,
                }

            source = self.digital_event_cache.get(bin_path)
            if source is None:
                source = DigitalBusSource(*self._load_digital_events_current(bin_path))
                self.digital_event_cache[bin_path] = source
            return {
                "kind": "events",
                "times": np.asarray(source.times, dtype=np.float64),
                "bus": np.asarray(source.bus_values, dtype=np.uint16),
                "bit": int(name[1:]),
                "source": source,
            }
        raise ValueError(f"Digital channel not found: {name}")

    def _sync_auto_threshold(self, y, x=None, bounds=None, max_samples=200_000):
        """範囲内を最大max_samples点まで間引いてAuto thresholdを決める。"""
        n = len(y)
        if n == 0:
            raise ValueError("Analog data is empty")

        i0 = 0
        i1 = n
        if bounds is not None and x is not None:
            lo, hi = sorted((float(bounds[0]), float(bounds[1])))
            i0 = max(0, min(n, _searchsorted(x, lo, side="left")))
            i1 = max(i0, min(n, _searchsorted(x, hi, side="right")))
        if i1 <= i0:
            raise ValueError("Selected range contains no Analog samples")

        count = i1 - i0
        step = max(1, int(np.ceil(count / max(1, int(max_samples)))))
        values = np.asarray(y[i0:i1:step], dtype=np.float64)
        finite = values[np.isfinite(values)]
        if finite.size == 0:
            raise ValueError("Analog data has no finite samples")
        lo_v, hi_v = np.percentile(finite, [1.0, 99.0])
        if not np.isfinite(lo_v) or not np.isfinite(hi_v):
            raise ValueError("Cannot determine analog threshold")
        return float((lo_v + hi_v) * 0.5)

    @staticmethod
    def _sync_time_at_indices(x, indices):
        indices = np.asarray(indices, dtype=np.int64)
        if hasattr(x, "xinc") and hasattr(x, "xorig") and hasattr(x, "xref"):
            return (
                (indices.astype(np.float64) - float(x.xref)) * float(x.xinc)
                + float(x.xorig)
            )
        arr = np.asarray(x)
        return np.asarray(arr[indices], dtype=np.float64)

    def _sync_analog_crossings(
        self,
        x,
        y,
        threshold,
        slope,
        bounds=None,
        chunk_pairs=1_000_000,
    ):
        """
        Analog threshold crossingをチャンク単位で抽出する。
        25 Mpts級でも全波形をfloat64へ展開しない。
        """
        n = len(y)
        if n < 2:
            return np.empty(0, dtype=np.float64)

        start = 0
        stop = n
        lo = hi = None
        if bounds is not None:
            lo, hi = sorted((float(bounds[0]), float(bounds[1])))
            start = max(0, _searchsorted(x, lo, side="left") - 1)
            stop = min(n, _searchsorted(x, hi, side="right") + 1)
        if stop - start < 2:
            return np.empty(0, dtype=np.float64)

        parts = []
        pair_start = int(start)
        pair_limit = int(stop - 1)
        chunk_pairs = max(10_000, int(chunk_pairs))

        while pair_start < pair_limit:
            pair_end = min(pair_limit, pair_start + chunk_pairs)
            values = np.asarray(
                y[pair_start:pair_end + 1],
                dtype=np.float64
            )
            if values.size < 2:
                break

            y0 = values[:-1]
            y1 = values[1:]
            finite = np.isfinite(y0) & np.isfinite(y1)
            rising = finite & (y0 < threshold) & (y1 >= threshold)
            falling = finite & (y0 > threshold) & (y1 <= threshold)

            if slope == "Rising":
                mask = rising
            elif slope == "Falling":
                mask = falling
            else:
                mask = rising | falling

            local_idx = np.flatnonzero(mask)
            if local_idx.size:
                idx = local_idx.astype(np.int64) + pair_start
                xa = self._sync_time_at_indices(x, idx)
                xb = self._sync_time_at_indices(x, idx + 1)
                ya = y0[local_idx]
                yb = y1[local_idx]
                denom = yb - ya
                frac = np.zeros_like(ya, dtype=np.float64)
                valid = denom != 0
                frac[valid] = (threshold - ya[valid]) / denom[valid]
                frac = np.clip(frac, 0.0, 1.0)
                crossings = xa + frac * (xb - xa)
                if bounds is not None:
                    crossings = crossings[(crossings >= lo) & (crossings <= hi)]
                if crossings.size:
                    parts.append(np.asarray(crossings, dtype=np.float64))

            pair_start = pair_end

        if not parts:
            return np.empty(0, dtype=np.float64)
        return np.concatenate(parts)

    def _sync_digital_edges(self, digital, slope, bounds=None):
        bit = int(digital["bit"])

        if digital["kind"] == "events":
            times = np.asarray(digital["times"], dtype=np.float64)
            bus = np.asarray(digital["bus"], dtype=np.uint16)
            if len(times) < 2:
                return np.empty(0, dtype=np.float64)
            prev = ((bus[:-1] >> bit) & 1).astype(np.uint8)
            cur = ((bus[1:] >> bit) & 1).astype(np.uint8)
            changed = prev != cur
            if slope == "Rising":
                mask = changed & (prev == 0) & (cur == 1)
            elif slope == "Falling":
                mask = changed & (prev == 1) & (cur == 0)
            else:
                mask = changed
            edges = np.asarray(times[1:][mask], dtype=np.float64)
        else:
            x = digital["times"]
            states = np.asarray(digital["states"], dtype=np.uint8)
            if len(states) < 2:
                return np.empty(0, dtype=np.float64)
            prev = states[:-1]
            cur = states[1:]
            changed = prev != cur
            if slope == "Rising":
                mask = changed & (prev == 0) & (cur == 1)
            elif slope == "Falling":
                mask = changed & (prev == 1) & (cur == 0)
            else:
                mask = changed
            idx = np.flatnonzero(mask) + 1
            edges = self._sync_time_at_indices(x, idx)

        if bounds is not None and edges.size:
            lo, hi = sorted((float(bounds[0]), float(bounds[1])))
            left = int(np.searchsorted(edges, lo, side="left"))
            right = int(np.searchsorted(edges, hi, side="right"))
            edges = edges[left:right]
        return np.asarray(edges, dtype=np.float64)

    @staticmethod
    def _sync_pair_edges(analog_edges, digital_edges, tolerance_s):
        analog_edges = np.asarray(analog_edges, dtype=np.float64)
        digital_edges = np.asarray(digital_edges, dtype=np.float64)
        if analog_edges.size == 0 or digital_edges.size == 0:
            return []

        used = np.zeros(analog_edges.size, dtype=bool)
        pairs = []
        for td in digital_edges:
            pos = int(np.searchsorted(analog_edges, td))
            candidates = []
            for k in (pos - 1, pos, pos + 1):
                if 0 <= k < analog_edges.size and not used[k]:
                    candidates.append(k)
            if not candidates:
                continue
            k = min(candidates, key=lambda i: abs(analog_edges[i] - td))
            dt = float(td - analog_edges[k])
            if abs(dt) <= tolerance_s:
                used[k] = True
                pairs.append((float(analog_edges[k]), float(td), dt))
        return pairs

    def run_sync_analysis(self):
        try:
            result = self._calculate_sync_result()
            analog_name = result["analog"]
            digital_name = result["digital"]
            threshold = result["threshold_v"]
            threshold_mode = result["threshold_mode"]
            slope = result["slope"]
            tolerance_ns = result["tolerance_ns"]
            pairs = result["pairs"]
            analog_edge_count = result["analog_edge_count"]
            digital_edge_count = result["digital_edge_count"]
            dt = np.asarray([p[2] for p in pairs], dtype=np.float64)

            lines = [
                f"Analog       : {analog_name}",
                f"Digital      : {digital_name}",
                f"Threshold    : {threshold:.9g} V ({threshold_mode})",
                f"Slope        : {slope}",
                f"Tolerance    : {tolerance_ns:.6g} ns",
                "",
                f"Analog edges : {analog_edge_count:,}",
                f"Digital edges: {digital_edge_count:,}",
                f"Paired edges : {len(pairs):,}",
                f"Timebase     : {result.get('digital_time_mode', 'Raw')}",
            ]

            if len(dt):
                result["mean_dt_s"] = float(np.mean(dt))
                result["median_dt_s"] = float(np.median(dt))
                result["std_dt_s"] = float(np.std(dt))
                result["min_dt_s"] = float(np.min(dt))
                result["max_dt_s"] = float(np.max(dt))
                result["rms_dt_s"] = float(np.sqrt(np.mean(dt * dt)))

                lines += [
                    "",
                    "Δt = Digital - Analog",
                    f"Mean         : {np.mean(dt)*1e9:+.6f} ns",
                    f"Median       : {np.median(dt)*1e9:+.6f} ns",
                    f"Std.dev      : {np.std(dt)*1e9:.6f} ns",
                    f"RMS          : {np.sqrt(np.mean(dt*dt))*1e9:.6f} ns",
                    f"Min / Max    : {np.min(dt)*1e9:+.6f} / {np.max(dt)*1e9:+.6f} ns",
                ]
                if result.get("drift_ppm") is not None:
                    lines += [
                        f"Drift        : {result['drift_ppm']:+.6f} ppm",
                        f"Δdelay/span  : {result['drift_delay_span_s']*1e9:+.6f} ns",
                        f"Fit span     : {result['drift_fit_span_s']*1e3:.6f} ms",
                        "Drift quality : " + (
                            "GOOD" if result.get("drift_reliable")
                            else "REFERENCE (short/noisy range)"
                        ),
                    ]
                raw_result = self._convert_sync_result_timebase(result, "Raw")
                calibrated_result = self._convert_sync_result_timebase(result, "Calibrated")
                _mode, _ppm, _ref, _corr = self._digital_calibration_values()
                lines += [
                    "",
                    "Timebase calibration",
                    f"Raw drift     : {raw_result.get('drift_ppm', float('nan')):+.6f} ppm",
                    f"Applied corr. : {_ppm:+.6f} ppm",
                    f"Residual drift: {calibrated_result.get('drift_ppm', float('nan')):+.6f} ppm",
                    "",
                    "First pairs:",
                ]
                for i, (ta, td, dti) in enumerate(pairs[:12], 1):
                    lines.append(
                        f"{i:2d}: A={ta*1e9:+.6f} ns  "
                        f"D={td*1e9:+.6f} ns  Δ={dti*1e9:+.6f} ns"
                    )
            else:
                lines += [
                    "",
                    "No edge pairs were found inside the tolerance.",
                    "Threshold, slope, or toleranceを変更してください。"
                ]

            self.sync_result_cache = result
            self._replace_text(self.sync_info, "\n".join(lines) + "\n")
            self.status_var.set(
                f"同期解析: {analog_name} ↔ {digital_name}, "
                f"{len(pairs)} pairs"
            )
            self.schedule_auto_measurements()
        except Exception as exc:
            self.sync_result_cache = None
            self._replace_text(
                self.sync_info,
                f"同期解析エラー\n\n{type(exc).__name__}: {exc}\n"
            )
            messagebox.showerror("同期解析エラー", str(exc))

    def sync_first_pair_to_cursors(self):
        result = self.sync_result_cache
        if not result or not result.get("pairs"):
            messagebox.showinfo(
                "同期解析",
                "先に［解析］を実行し、少なくとも1組のエッジを検出してください。"
            )
            return
        ta, td, _dt = result["pairs"][0]
        if len(self.cursors) < 2:
            return
        self.cursors[0]["x"] = float(ta)
        self.cursors[1]["x"] = float(td)
        self.active_cursor_index = 0
        self.cursor_next_index = 0
        self._refresh_cursor_combo()
        self.render_cursors()
        self.update_info([])
        if (
            hasattr(self, "auto_measure_scope_var")
            and self.auto_measure_scope_var.get() == "A-B間"
        ):
            self.schedule_auto_measurements(80)

    def export_sync_analysis_csv(self):
        result = self.sync_result_cache
        if not result or not result.get("pairs"):
            messagebox.showinfo(
                "同期解析",
                "保存する同期解析結果がありません。"
            )
            return
        initial = (
            f"sync_{result['analog']}_{result['digital']}.csv"
        )
        path = filedialog.asksaveasfilename(
            title="同期解析結果をCSV保存",
            initialdir=str(self.dataset_dir) if self.dataset_dir else None,
            initialfile=initial,
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")]
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow([
                "analog_channel", result["analog"],
                "digital_channel", result["digital"]
            ])
            w.writerow([
                "threshold_V", result["threshold_v"],
                "threshold_mode", result["threshold_mode"],
                "slope", result["slope"],
                "tolerance_ns", result["tolerance_ns"]
            ])
            if result.get("drift_ppm") is not None:
                w.writerow([
                    "drift_ppm", result.get("drift_ppm"),
                    "drift_delay_span_s", result.get("drift_delay_span_s"),
                    "drift_fit_span_s", result.get("drift_fit_span_s"),
                    "drift_r_squared", result.get("drift_r_squared")
                ])
            w.writerow([])
            w.writerow([
                "pair", "analog_time_s", "digital_time_s", "delta_s", "delta_ns"
            ])
            for i, (ta, td, dt) in enumerate(result["pairs"], 1):
                w.writerow([i, ta, td, dt, dt * 1e9])
        self.status_var.set(f"同期解析CSVを保存しました: {path}")

    def update_info(
        self,
        errors
    ):

        self._refresh_analog_combo()

        self.info.delete(
            "1.0",
            tk.END
        )

        self.info.insert(
            tk.END,
            f"Folder:\n"
            f"{self.dataset_dir}\n\n"
        )

        self.info.insert(
            tk.END,
            f"Viewer version: {VIEWER_TOOL_VERSION}\n"
        )

        capture_tool_version = self.dataset_metadata.get(
            "capture_tool_version"
        )
        if isinstance(capture_tool_version, dict):
            self.info.insert(
                tk.END,
                "Capture tool version: "
                f"{capture_tool_version.get('version', 'unknown')}\n"
            )
        else:
            self.info.insert(
                tk.END,
                "Capture tool version: unknown (older dataset)\n"
            )

        capture_warnings = []
        missing_digital_message = self.dataset_metadata.get(
            "missing_digital_data_message"
        )
        if missing_digital_message:
            capture_warnings.append(
                str(missing_digital_message).replace("\n", " ")
            )
        narrow_digital_message = self.dataset_metadata.get(
            "narrow_digital_range_message"
        )
        if narrow_digital_message:
            capture_warnings.append(
                str(narrow_digital_message).replace("\n", " ")
            )
        viewer_initial_digital_error = self.dataset_metadata.get(
            "viewer_initial_digital_error"
        )
        if viewer_initial_digital_error:
            capture_warnings.append(
                "Viewer initial digital analysis: "
                + str(viewer_initial_digital_error).replace("\n", " ")
            )
        summary = self.dataset_metadata.get(
            "capture_summary"
        )
        if isinstance(summary, dict):
            if summary.get("success"):
                capture_status = "SUCCESS"
            elif summary.get("partial_success"):
                capture_status = "PARTIAL"
            else:
                capture_status = "FAILED"

            self.info.insert(
                tk.END,
                f"Capture: {capture_status}\n"
            )

            selected_analog = summary.get(
                "selected_analog_channels"
            )
            if isinstance(selected_analog, list):
                self.info.insert(
                    tk.END,
                    "Captured analog selection: "
                    + (
                        ", ".join(selected_analog)
                        if selected_analog
                        else "(none)"
                    )
                    + "\n"
                )

            digital_summary = summary.get("digital")
            if isinstance(digital_summary, dict):
                self.info.insert(
                    tk.END,
                    "Digital result: "
                    f"{digital_summary.get('status', 'unknown')}\n"
                )
                self.info.insert(
                    tk.END,
                    "Digital method: "
                    f"{digital_summary.get('method', 'unknown')}\n"
                    "Digital events: "
                    f"{int(digital_summary.get('event_count', 0)):,}\n"
                )
                if digital_summary.get("probe_saturated") is not None:
                    self.info.insert(
                        tk.END,
                        "Full-view probe: "
                        f"{int(digital_summary.get('probe_event_count', 0)):,} "
                        "events, saturated="
                        f"{bool(digital_summary.get('probe_saturated'))}\n"
                    )

                segmentation = digital_summary.get("segmentation")
                if isinstance(segmentation, dict) and segmentation:
                    self.info.insert(
                        tk.END,
                        "Segment scan: "
                        f"complete={segmentation.get('complete', False)}, "
                        f"queries={int(segmentation.get('table_queries', 0)):,}, "
                        "accepted="
                        f"{int(segmentation.get('accepted_tables', 0)):,}\n"
                    )

            summary_warnings = summary.get("warnings")
            if isinstance(summary_warnings, list):
                capture_warnings.extend(
                    str(warning)
                    for warning in summary_warnings
                )
            if capture_warnings:
                self.info.insert(
                    tk.END,
                    f"Capture warnings: {len(capture_warnings)}\n"
                )

        elif isinstance(
            self.dataset_metadata.get("capture_error"),
            dict
        ):
            capture_error = self.dataset_metadata["capture_error"]
            self.info.insert(
                tk.END,
                "Capture: FAILED\n"
                f"Capture error: {capture_error.get('message', 'unknown')}\n"
            )

        self.info.insert(
            tk.END,
            "Mode: "
            + (
                "縦並び"
                if self.view_mode.get()
                == "stacked"
                else "重ね表示"
            )
            + "\n"
        )

        self.info.insert(
            tk.END,
            "Span zoom (Z): "
            + (
                "ON"
                if self.span_active
                else "OFF"
            )
            + "\n"
        )

        self.info.insert(
            tk.END,
            f"Shift down: "
            f"{self.shift_down}\n"
        )

        # ----------------------------------------------------
        # アクティブCH
        # ----------------------------------------------------

        analog_items = [
            it
            for it in self.loaded_items
            if it["kind"] == "analog"
        ]

        if analog_items:

            idx = max(
                0,
                min(
                    self.active_channel_index,
                    len(analog_items) - 1
                )
            )

            self.info.insert(
                tk.END,
                "Active analog: "
                f"{analog_items[idx]['name']}\n"
            )

        # ----------------------------------------------------
        # カーソル
        # ----------------------------------------------------

        self.info.insert(
            tk.END,
            "\nCursors:\n"
        )

        for i, cursor in enumerate(
            self.cursors
        ):

            active = (
                " *"
                if i
                == self.active_cursor_index
                else ""
            )

            if cursor["x"] is None:

                text = "-"

            else:

                text = (
                    self._format_si(
                        cursor["x"],
                        "s"
                    )
                )

            self.info.insert(
                tk.END,
                f"  {cursor['name']}: "
                f"{text}{active}\n"
            )

        # ----------------------------------------------------
        # A/B測定
        # ----------------------------------------------------

        measurement = (
            self._get_cursor_measurement()
        )

        if measurement is not None:

            self.info.insert(
                tk.END,
                "\nMeasurements:\n"
            )

            self.info.insert(
                tk.END,
                "  Δt(A→B): "
                + self._format_si(
                    measurement["dt"],
                    "s"
                )
                + "\n"
            )

            if (
                measurement["frequency"]
                is not None
            ):

                self.info.insert(
                    tk.END,
                    "  f: "
                    + self._format_si(
                        measurement[
                            "frequency"
                        ],
                        "Hz"
                    )
                    + "\n"
                )

            for it in analog_items:

                va = (
                    self._get_voltage_at_cursor(
                        it,
                        measurement["a"]
                    )
                )

                vb = (
                    self._get_voltage_at_cursor(
                        it,
                        measurement["b"]
                    )
                )

                if (
                    va is None
                    or vb is None
                ):
                    continue

                voltage_a = va[1]
                voltage_b = vb[1]

                dv = (
                    voltage_b
                    - voltage_a
                )

                self.info.insert(
                    tk.END,
                    f"  {it['name']}: "
                    f"A={voltage_a:.6g} V  "
                    f"B={voltage_b:.6g} V  "
                    f"ΔV={dv:.6g} V\n"
                )

        # ----------------------------------------------------
        # 選択チャンネル
        # ----------------------------------------------------

        self.info.insert(
            tk.END,
            "\nSelected:\n"
        )

        for it in self.loaded_items:

            if it["kind"] == "analog":

                pyramid = it.get("pyramid")
                if isinstance(pyramid, AnalogMinMaxPyramid):
                    if pyramid.ready:
                        cache_text = (
                            f" cache={len(pyramid.levels)} levels/"
                            f"{pyramid.cache_bytes / (1024 ** 2):.2f} MiB"
                        )
                    elif pyramid.build_error:
                        cache_text = " cache=fallback"
                    else:
                        cache_text = " cache=pending"
                else:
                    cache_text = " cache=not-needed"

                self.info.insert(
                    tk.END,
                    f"  {it['name']} "
                    f"pts={len(it['x'])}{cache_text}\n"
                )

            else:
                meta = it.get("meta", {})

                if (
                    meta.get("source_format")
                    == "event_table"
                ):
                    self.info.insert(
                        tk.END,
                        f"  {it['name']} (RG03 LA 0/1) "
                        if meta.get("underlying_source") == "RG03_memory_BIN_LA"
                        else f"  {it['name']} (event 0/1) "
                        f"events={len(it['x'])} "
                        f"edges={meta.get('transition_count', 0)} "
                        f"state={meta.get('initial_state')}→"
                        f"{meta.get('final_state')} "
                        f"shared_bus={bool(meta.get('shared_bus'))}\n"
                    )

                else:
                    gb = meta.get("group_base", 0)

                    self.info.insert(
                        tk.END,
                        f"  {it['name']} "
                        f"(sampled 0/1) "
                        f"group={gb}-{gb+7} "
                        f"pts={len(it['x'])} "
                        f"edges={meta.get('transition_count', 0)}\n"
                    )

        event_items = [
            it
            for it in self.loaded_items
            if (
                it["kind"] == "digital"
                and it.get("meta", {}).get("source_format")
                == "event_table"
            )
        ]

        if event_items:
            event_x = event_items[0]["x"]
            event_meta = event_items[0].get("meta", {})
            coverage = event_meta.get("coverage")
            segment_manifest = event_meta.get("segment_manifest")
            coverage_xlim = digital_coverage_xlim(event_meta)
            if event_meta.get("underlying_source") == "RG03_memory_BIN_LA":
                self.info.insert(
                    tk.END,
                    "  Digital source: full uniform LA RAW from RG03 memory BIN\n"
                    "  Display data: exact transition-compressed 16-bit BUS\n"
                    f"  Event span: {event_x[0]:.9g} "
                    f"to {event_x[-1]:.9g} s\n"
                )
            else:
                self.info.insert(
                    tk.END,
                    "  Digital note: transition/event table; "
                    "not uniform RAW\n"
                    f"  Event span: {event_x[0]:.9g} "
                    f"to {event_x[-1]:.9g} s\n"
                )
            if isinstance(coverage, dict):
                ratio = coverage.get("analog_coverage_ratio")
                ratio_text = (
                    f"{float(ratio):.3%}"
                    if ratio is not None
                    else "unknown"
                )
                self.info.insert(
                    tk.END,
                    "  Digital coverage: "
                    f"{coverage.get('status', 'unknown')} "
                    f"({ratio_text})\n"
                    "  Coverage basis: "
                    f"{coverage.get('validity_basis', 'unknown')}\n"
                )

            if coverage_xlim is not None:
                self.info.insert(
                    tk.END,
                    "  Valid digital time: "
                    f"{coverage_xlim[0]:.9g} to "
                    f"{coverage_xlim[1]:.9g} s\n"
                    "  Display: state held only inside valid range; "
                    "gray area is unavailable\n"
                )

            else:
                self.info.insert(
                    tk.END,
                    "  Valid digital time: metadata unavailable; "
                    "event span only\n"
                )

            if isinstance(segment_manifest, dict):
                segment_result = segment_manifest.get("result")
                if isinstance(segment_result, dict):
                    self.info.insert(
                        tk.END,
                        "  Segment tables: "
                        f"queries={int(segment_result.get('table_queries', 0)):,}, "
                        "accepted="
                        f"{int(segment_result.get('accepted_tables', 0)):,}, "
                        "split="
                        f"{int(segment_result.get('split_tables', 0)):,}\n"
                    )

        # ----------------------------------------------------
        # Errors
        # ----------------------------------------------------

        if errors or capture_warnings:

            self.info.insert(
                tk.END,
                "\n---- Warnings / Errors ----\n"
            )

            if capture_warnings:
                self.info.insert(
                    tk.END,
                    "\n".join(
                        f"Capture: {warning}"
                        for warning in capture_warnings[:8]
                    )
                    + "\n"
                )
                if len(capture_warnings) > 8:
                    self.info.insert(
                        tk.END,
                        "Capture: ... "
                        f"and {len(capture_warnings) - 8} more\n"
                    )

            if errors:
                self.info.insert(
                    tk.END,
                    "\n".join(errors)
                    + "\n"
                )

        # ----------------------------------------------------
        # 操作
        # ----------------------------------------------------

        self.info.insert(
            tk.END,
            "\n詳しい操作方法は［操作ガイド］またはF1で確認できます。\n"
        )

        self._update_capture_banner(capture_warnings)
        self._update_measurement_panel()
        self._update_diagnostic_panel(errors, capture_warnings)


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":

    app = App()

    # r12.2: One-Click から渡されたデコード済みデータセットを自動読込。
    # argv[1] を優先し、なければ MHO984_DATASET を使用する。
    auto_dataset = None
    if len(sys.argv) >= 2 and sys.argv[1].strip():
        auto_dataset = Path(sys.argv[1]).expanduser()
    elif os.environ.get("MHO984_DATASET"):
        auto_dataset = Path(os.environ["MHO984_DATASET"]).expanduser()

    if auto_dataset is not None:
        try:
            auto_dataset = auto_dataset.resolve()
        except Exception:
            pass

        def _auto_open_dataset():
            if not auto_dataset.exists():
                app.status_var.set(
                    f"自動指定データセットが見つかりません: {auto_dataset}"
                )
                return

            app._set_busy(True, "データセットを自動で開いています...")
            try:
                app._load_dataset_folder(auto_dataset)
                app.status_var.set(f"自動読込完了: {app.dataset_dir}")
            except Exception as exc:
                app._replace_text(
                    app.diagnostic_info,
                    "自動データセット読込中にエラーが発生しました。\\n\\n"
                    f"Folder: {auto_dataset}\\n"
                    f"Type: {type(exc).__name__}\\n"
                    f"Error: {exc}\\n"
                )
                try:
                    app.detail_tabs.select(3)
                except Exception:
                    pass
                messagebox.showerror(
                    "自動データセット読込エラー",
                    f"{auto_dataset}\\n\\n{exc}",
                    parent=app,
                )
            finally:
                app._set_busy(False)

        app.after(100, _auto_open_dataset)

    app.protocol_overlay_poll_after_id = app.after(1500, app._poll_protocol_overlay)
    app.mainloop()