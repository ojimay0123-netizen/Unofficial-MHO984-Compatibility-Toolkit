#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""Create a small redacted diagnostics ZIP for bug reports; waveform/BIN files are excluded."""
from __future__ import annotations
import argparse, json, re, zipfile
from pathlib import Path
from datetime import datetime

TEXT_EXTS={".json", ".txt", ".log", ".csv"}
MAX_FILE=8*1024*1024
SKIP_PARTS={"analog", "memory"}
SKIP_SUFFIXES={".bin", ".npy", ".npz"}
SENSITIVE_KEYS={
    "ip","host","scope_ip","oscilloscope_ip","idn","serial","serial_number",
    "pc_file","local_path","destination","dataset_directory","dataset_root",
    "dataset_base_dir","lan_inbox","settings_path","source_bin","stored_memory_bin",
}
IP_RE=re.compile(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])")
USER_RE=re.compile(r"(?i)([A-Z]:\\Users\\)([^\\]+)")

def redact_text(s:str)->str:
    s=IP_RE.sub("<REDACTED_IP>",s)
    s=USER_RE.sub(r"\1<REDACTED_USER>",s)
    s=re.sub(r"(?im)^(RIGOL[^,]*,[^,]*,)[^,\r\n]+(,[^\r\n]+)$",r"\1<REDACTED_SERIAL>\2",s)
    return s

def scrub(obj, key=""):
    if isinstance(obj,dict):
        return {k:("<REDACTED>" if k.lower() in SENSITIVE_KEYS else scrub(v,k)) for k,v in obj.items()}
    if isinstance(obj,list): return [scrub(v,key) for v in obj]
    if isinstance(obj,str): return redact_text(obj)
    return obj

def main()->int:
    ap=argparse.ArgumentParser()
    ap.add_argument("dataset", nargs="?", type=Path)
    ap.add_argument("--output", type=Path)
    args=ap.parse_args()
    dataset=args.dataset
    if dataset is None:
        try:
            import tkinter as tk
            from tkinter import filedialog
            root=tk.Tk(); root.withdraw()
            d=filedialog.askdirectory(title="共有用診断情報を作るデータセットを選択")
            root.destroy(); dataset=Path(d) if d else None
        except Exception: dataset=None
    if dataset is None:
        print("Cancelled."); return 1
    dataset=dataset.resolve()
    if not dataset.is_dir(): raise FileNotFoundError(dataset)
    out=args.output or dataset.parent/(dataset.name+"_diagnostics_redacted.zip")
    manifest=[]
    with zipfile.ZipFile(out,"w",compression=zipfile.ZIP_DEFLATED) as z:
        notice=(
            "Redacted diagnostics export. Raw waveform/BIN files are intentionally excluded.\n"
            "Redaction is best-effort; review contents before public sharing.\n"
        )
        z.writestr("README_REDACTED_EXPORT.txt",notice)
        for p in sorted(dataset.rglob("*")):
            if not p.is_file(): continue
            rel=p.relative_to(dataset)
            if any(part.lower() in SKIP_PARTS for part in rel.parts): continue
            if p.suffix.lower() in SKIP_SUFFIXES: continue
            if p.suffix.lower() not in TEXT_EXTS: continue
            if p.stat().st_size>MAX_FILE: continue
            try:
                raw=p.read_text(encoding="utf-8",errors="replace")
                if p.suffix.lower()==".json":
                    try:
                        obj=json.loads(raw); raw=json.dumps(scrub(obj),ensure_ascii=False,indent=2)+"\n"
                    except Exception: raw=redact_text(raw)
                else: raw=redact_text(raw)
                z.writestr(str(rel).replace("\\","/"),raw)
                manifest.append(str(rel).replace("\\","/"))
            except Exception: pass
        z.writestr("EXPORT_MANIFEST.json",json.dumps({
            "created_at":datetime.now().astimezone().isoformat(timespec="seconds"),
            "source_dataset_name":dataset.name,
            "files":manifest,
            "warning":"Best-effort redaction only; review before sharing. Raw waveform/BIN excluded.",
        },ensure_ascii=False,indent=2))
    print(f"Created: {out}")
    return 0
if __name__=="__main__": raise SystemExit(main())
