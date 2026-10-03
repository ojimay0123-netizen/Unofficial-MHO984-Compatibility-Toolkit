#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""Persistent protocol-overlay exchange between Protocol Analyzer and Viewer.

The analyzer writes only decoded protocol annotations to the dataset's
``protocol_overlays`` directory. Waveform files are never modified.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os

FORMAT = "mho984-protocol-overlay-v1"
ACTIVE_NAME = "active_protocol_overlay.json"


def overlay_dir(dataset: Path) -> Path:
    return Path(dataset) / "protocol_overlays"


def active_overlay_path(dataset: Path) -> Path:
    return overlay_dir(dataset) / ACTIVE_NAME


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Path):
        return value.name
    try:
        import numpy as np
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, np.ndarray):
            return value.tolist()
    except Exception:
        pass
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def save_active_overlay(dataset: Path, *, protocol: str, events, config=None) -> Path:
    out_dir = overlay_dir(dataset)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / ACTIVE_NAME
    tmp = out_dir / (ACTIVE_NAME + ".tmp")
    rows = []
    for event in events:
        if hasattr(event, "to_dict"):
            row = event.to_dict()
        else:
            row = dict(event)
        rows.append(_jsonable(row))
    rows.sort(key=lambda e: (float(e.get("start_s", 0.0)), float(e.get("end_s", 0.0))))
    doc = {
        "format": FORMAT,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "protocol": str(protocol),
        "event_count": len(rows),
        "config": _jsonable(config or {}),
        "events": rows,
    }
    payload = (json.dumps(doc, ensure_ascii=False, indent=2) if len(rows) <= 5000 else json.dumps(doc, ensure_ascii=False, separators=(",", ":")))
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_active_overlay(dataset: Path) -> dict | None:
    path = active_overlay_path(dataset)
    if not path.is_file():
        return None
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise ValueError(f"Unsupported protocol overlay format: {doc.get('format') if isinstance(doc, dict) else type(doc).__name__}")
    events = doc.get("events")
    if not isinstance(events, list):
        raise ValueError("Protocol overlay has no events list")
    clean = []
    for e in events:
        if not isinstance(e, dict):
            continue
        try:
            start = float(e.get("start_s"))
            end = float(e.get("end_s", start))
        except Exception:
            continue
        if end < start:
            start, end = end, start
        clean.append({
            "start_s": start,
            "end_s": end,
            "kind": str(e.get("kind", "EVENT")),
            "summary": str(e.get("summary", "")),
            "details": e.get("details", {}) if isinstance(e.get("details", {}), dict) else {},
            "ok": bool(e.get("ok", True)),
        })
    clean.sort(key=lambda e: (e["start_s"], e["end_s"]))
    doc["events"] = clean
    doc["event_count"] = len(clean)
    doc["path"] = str(path)
    return doc


def clear_active_overlay(dataset: Path) -> bool:
    path = active_overlay_path(dataset)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def active_overlay_mtime_ns(dataset: Path):
    path = active_overlay_path(dataset)
    try:
        return path.stat().st_mtime_ns
    except FileNotFoundError:
        return None
