#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Independent, unofficial compatibility tool; not affiliated with RIGOL.
"""Re-decode Digital only in an existing MHO984 dataset (public beta; optional relative timebase reference profile)."""
from __future__ import annotations
import argparse
import importlib.util
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DECODER = SCRIPT_DIR / "mho984_bin_decoder_r12_7.py"


def load_decoder():
    spec = importlib.util.spec_from_file_location("mho984_decoder_r1211e", DECODER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load decoder: {DECODER}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def choose_dataset() -> Path | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk(); root.withdraw()
        d = filedialog.askdirectory(title="再デコードする mho984_* データセットを選択")
        root.destroy()
        return Path(d) if d else None
    except Exception:
        return None


def find_memory_bin(dataset: Path) -> Path:
    memory_dir = dataset / "memory"
    candidates = list(memory_dir.glob("*.bin")) if memory_dir.exists() else []
    if not candidates:
        candidates = [p for p in dataset.glob("*.bin") if p.is_file()]
    if not candidates:
        raise FileNotFoundError(f"Memory BIN not found under: {dataset}")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", nargs="?", type=Path)
    ap.add_argument(
        "--digital-calibration-ppm",
        type=float,
        default=None,
        help="Override Digital time-axis correction ppm; default uses the decoder empirical reference profile; Raw remains the Viewer default",
    )
    args = ap.parse_args()
    dataset = args.dataset or choose_dataset()
    if dataset is None:
        print("Cancelled.")
        return 1
    dataset = dataset.resolve()
    memory_bin = find_memory_bin(dataset)
    dec = load_decoder()
    print(f"Dataset   : {dataset}")
    print(f"Memory BIN: {memory_bin}")
    kwargs = {}
    if args.digital_calibration_ppm is not None:
        kwargs["digital_time_calibration_ppm"] = args.digital_calibration_ppm
    result = dec.decode_to_dataset(
        memory_bin,
        dataset,
        copy_memory_bin=False,
        rewrite_analog=False,
        **kwargs,
    )
    print("Re-decode complete. Analog files were NOT rewritten.")
    print(f"Digital event : {result.get('digital_event_file')}")
    print(f"Digital raw   : {result.get('digital_bus_file')}")
    print(f"Digital points: {result.get('digital_points')}")
    print(f"Raw X inc     : {result.get('x_increment_s')}")
    print(f"Cal X inc     : {result.get('calibrated_x_increment_s')}")
    cal = result.get("digital_time_calibration") or {}
    print(f"Reference ppm : {cal.get('scale_correction_ppm')} ppm (empirical, not universal)")
    print(f"Cal event     : {result.get('digital_event_calibrated_file')}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
