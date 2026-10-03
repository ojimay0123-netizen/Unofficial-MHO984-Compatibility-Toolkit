#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Independent, unofficial compatibility tool; not affiliated with RIGOL.
"""
MHO984 Toolkit Public Beta v0.1.0-beta.7

Default startup:
  Settings GUI
  -> SINGLE -> STOP
  -> save one RG03 BIN to scope C:/
  -> anonymous FTP retrieval
  -> decode Analog + D0-D15
  -> Viewer

UltraSigma/VISA is not used.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


VERSION = "0.1.0-beta.7 (engine r12.11e / viewer r13.6 + protocol overlay)"

OSC_IP = ""
OSC_PORT = 5555
BASE_DIR = Path(r"C:\Temp")
AUTO_LAUNCH_VIEWER = True

SETTINGS_PATH = Path.home() / ".mho984_toolkit_public_settings.json"
DEFAULT_LAN_INBOX_NAME = "MHO984_LAN_INBOX"

SCRIPT_DIR = Path(__file__).resolve().parent
CAPTURE_CORE = SCRIPT_DIR / "capture_mho984_sync_r12_7_bin_only_core.py"
RETRIEVER = SCRIPT_DIR / "mho984_lan_bin_retriever_r12_7_stable_ftp.py"
DECODER = SCRIPT_DIR / "mho984_bin_decoder_r12_7.py"
VIEWER = SCRIPT_DIR / "viewer_mho984_r13_5_timebase_calibrated.py"


class R12Error(RuntimeError):
    pass


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise R12Error(f"Cannot load module: {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def dataset_dirs(base_dir: Path):
    if not base_dir.exists():
        return set()
    return {
        p.resolve()
        for p in base_dir.iterdir()
        if p.is_dir() and p.name.lower().startswith("mho984_")
    }


def newest_new_dataset(before, base_dir: Path) -> Path:
    after = dataset_dirs(base_dir)
    candidates = list(after - before)
    if not candidates:
        candidates = list(after)
    if not candidates:
        raise R12Error("Capture finished but no mho984_* dataset was found")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def expected_bin_from_dataset(dataset: Path) -> str:
    path = dataset / "memory_waveform_export.json"
    if not path.exists():
        raise R12Error(f"Missing: {path}")
    obj = json.loads(path.read_text(encoding="utf-8"))
    instrument_path = obj.get("instrument_path")
    if not instrument_path:
        raise R12Error("instrument_path missing in memory_waveform_export.json")
    return str(instrument_path).replace("\\", "/").rsplit("/", 1)[-1]


def launch_viewer(dataset: Path):
    env = os.environ.copy()
    env["MHO984_DATASET"] = str(dataset)
    subprocess.Popen(
        [sys.executable, str(VIEWER), str(dataset)],
        cwd=str(SCRIPT_DIR),
        env=env,
    )


def validate_ipv4_text(text: str) -> bool:
    parts = str(text).strip().split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        if not part or not part.isdigit():
            return False
        value = int(part)
        if value < 0 or value > 255:
            return False
    return True


def load_user_settings():
    settings = {
        "scope_ip": OSC_IP,
        "base_dir": str(BASE_DIR),
    }

    try:
        if not SETTINGS_PATH.is_file():
            return settings

        obj = json.loads(
            SETTINGS_PATH.read_text(encoding="utf-8")
        )
        if not isinstance(obj, dict):
            return settings

        scope_ip = str(obj.get("scope_ip", "")).strip()
        base_dir = str(obj.get("base_dir", "")).strip()

        if validate_ipv4_text(scope_ip):
            settings["scope_ip"] = scope_ip
        if base_dir:
            settings["base_dir"] = base_dir

    except Exception:
        # Broken settings must never block acquisition.
        pass

    return settings


def save_user_settings(scope_ip: str, base_dir: Path):
    payload = {
        "version": 1,
        "scope_ip": str(scope_ip).strip(),
        "base_dir": str(base_dir),
    }
    SETTINGS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def console_status(text=""):
    print(text, flush=True)


def show_settings_gui(initial_scope_ip: str, initial_base_dir: Path):
    """
    Returns a dict with scope_ip/base_dir/lan_inbox, or None on cancel.
    """
    console_status("[GUI] 設定ウィンドウを準備しています...")

    try:
        root = tk.Tk()
    except Exception as exc:
        console_status(
            "[GUI] ERROR: Tk設定ウィンドウを作成できませんでした: "
            f"{type(exc).__name__}: {exc}"
        )
        raise

    root.withdraw()
    root.title("Unofficial MHO984 Toolkit - 取得設定")
    root.resizable(False, False)

    try:
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except tk.TclError:
        pass

    frame = ttk.Frame(root, padding=18)
    frame.grid(row=0, column=0, sticky="nsew")

    ttk.Label(
        frame,
        text="Unofficial MHO984 Toolkit - 波形取得設定",
        font=("Segoe UI", 13, "bold"),
    ).grid(
        row=0, column=0, columnspan=3,
        sticky="w", pady=(0, 14)
    )

    ttk.Label(
        frame, text="オシロスコープ IP"
    ).grid(
        row=1, column=0, sticky="w",
        padx=(0, 12), pady=6
    )

    ip_var = tk.StringVar(value=str(initial_scope_ip))
    ip_entry = ttk.Entry(
        frame, textvariable=ip_var, width=28
    )
    ip_entry.grid(
        row=1, column=1, columnspan=2,
        sticky="ew", pady=6
    )

    ttk.Label(
        frame,
        text=f"SCPIポート: {OSC_PORT}（固定）",
    ).grid(
        row=2, column=1, columnspan=2,
        sticky="w", pady=(0, 10)
    )

    ttk.Label(
        frame, text="PCデータ保存先"
    ).grid(
        row=3, column=0, sticky="w",
        padx=(0, 12), pady=6
    )

    base_var = tk.StringVar(value=str(initial_base_dir))
    base_entry = ttk.Entry(
        frame, textvariable=base_var, width=54
    )
    base_entry.grid(
        row=3, column=1, sticky="ew", pady=6
    )

    def browse_folder():
        current = base_var.get().strip()
        initial = (
            current if current and Path(current).exists()
            else str(Path.home())
        )
        selected = filedialog.askdirectory(
            parent=root,
            title="PC側のデータ保存先を選択",
            initialdir=initial,
            mustexist=False,
        )
        if selected:
            base_var.set(selected)

    ttk.Button(
        frame,
        text="参照...",
        command=browse_folder,
    ).grid(
        row=3, column=2,
        padx=(8, 0), pady=6
    )

    inbox_var = tk.StringVar()

    def update_inbox(*_):
        text = base_var.get().strip()
        if text:
            inbox_var.set(
                "取得BIN保存先: "
                + str(Path(text) / DEFAULT_LAN_INBOX_NAME)
            )
        else:
            inbox_var.set("取得BIN保存先: -")

    base_var.trace_add("write", update_inbox)
    update_inbox()

    ttk.Label(
        frame,
        textvariable=inbox_var,
        foreground="#555555",
    ).grid(
        row=4, column=1, columnspan=2,
        sticky="w", pady=(0, 12)
    )

    ttk.Separator(
        frame, orient=tk.HORIZONTAL
    ).grid(
        row=5, column=0, columnspan=3,
        sticky="ew", pady=(2, 12)
    )

    ttk.Label(
        frame,
        text=(
            "設定は次回起動時にも自動で読み込まれます。\n"
            "［保存して取得開始］で設定保存後、そのまま波形取得を開始します。"
        ),
    ).grid(
        row=6, column=0, columnspan=3,
        sticky="w", pady=(0, 14)
    )

    result = {"value": None}

    def validated_values():
        scope_ip = ip_var.get().strip()
        base_text = base_var.get().strip()

        if not validate_ipv4_text(scope_ip):
            messagebox.showerror(
                "入力エラー",
                "IPアドレスを 192.168.1.100 のような形式で入力してください。",
                parent=root,
            )
            ip_entry.focus_set()
            return None

        if not base_text:
            messagebox.showerror(
                "入力エラー",
                "PC側のデータ保存先を指定してください。",
                parent=root,
            )
            base_entry.focus_set()
            return None

        base_dir = Path(base_text).expanduser()

        try:
            base_dir.mkdir(parents=True, exist_ok=True)
            probe = base_dir / ".mho984_write_test.tmp"
            probe.write_text("MHO984", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except Exception as exc:
            messagebox.showerror(
                "保存先エラー",
                "指定した保存先へ書き込めません。\n\n"
                f"{base_dir}\n\n{exc}",
                parent=root,
            )
            return None

        return {
            "scope_ip": scope_ip,
            "base_dir": base_dir,
            "lan_inbox": base_dir / DEFAULT_LAN_INBOX_NAME,
        }

    def save_only():
        values = validated_values()
        if values is None:
            return
        try:
            save_user_settings(
                values["scope_ip"],
                values["base_dir"],
            )
        except Exception as exc:
            messagebox.showerror(
                "設定保存エラー",
                f"設定を保存できませんでした。\n\n{exc}",
                parent=root,
            )
            return
        messagebox.showinfo(
            "設定保存",
            f"設定を保存しました。\n\n{SETTINGS_PATH}",
            parent=root,
        )

    def save_and_start():
        values = validated_values()
        if values is None:
            return
        consent = messagebox.askyesno(
            "公開Beta版 - 取得前の確認",
            "このツールは独立した非公式Beta版で、RIGOLの公式製品ではありません。\n\n"
            "・実機検証済み取得対象はMHO984のみです。\n"
            "・取得はSINGLEを実行し、終了後はオシロをSTOP状態に残します。\n"
            "・匿名FTPは暗号化されません。信頼できる実験室LANだけで使用してください。\n"
            "・RG03デコードとDigital時間軸補正は実機検証依存で、計量トレーサブルな校正ではありません。\n"
            "・安全・医療・生命・保護回路など、誤測定が危険につながる用途には使用しないでください。\n"
            "・取得BINはオシロのC:/に残るため、空き容量を定期的に確認してください。\n\n"
            "README / LEGAL_NOTICE / SECURITY を確認した上で取得を開始しますか？",
            parent=root,
        )
        if not consent:
            return
        try:
            save_user_settings(
                values["scope_ip"],
                values["base_dir"],
            )
        except Exception as exc:
            messagebox.showerror(
                "設定保存エラー",
                f"設定を保存できませんでした。\n\n{exc}",
                parent=root,
            )
            return
        result["value"] = values
        root.destroy()

    def cancel():
        result["value"] = None
        root.destroy()

    buttons = ttk.Frame(frame)
    buttons.grid(
        row=7, column=0, columnspan=3,
        sticky="ew"
    )

    ttk.Button(
        buttons,
        text="設定のみ保存",
        command=save_only,
    ).pack(side=tk.LEFT)

    ttk.Button(
        buttons,
        text="キャンセル",
        command=cancel,
    ).pack(side=tk.RIGHT, padx=(8, 0))

    ttk.Button(
        buttons,
        text="保存して取得開始",
        command=save_and_start,
    ).pack(side=tk.RIGHT)

    frame.columnconfigure(1, weight=1)
    root.protocol("WM_DELETE_WINDOW", cancel)

    root.update_idletasks()

    try:
        w = max(520, root.winfo_reqwidth())
        h = max(250, root.winfo_reqheight())
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()

        # Always place on the primary visible desktop, not at a previously
        # remembered/off-screen coordinate.
        x = max(20, (sw - w) // 2)
        y = max(20, (sh - h) // 2)

        root.geometry(f"{w}x{h}+{x}+{y}")

    except tk.TclError as exc:
        console_status(
            "[GUI] Warning: ウィンドウ中央配置に失敗しました: "
            f"{exc}"
        )

    def force_gui_to_front():
        try:
            root.deiconify()
            root.lift()

            # Temporarily make it topmost so Windows Terminal cannot obscure
            # the first settings window. Release topmost shortly afterwards.
            root.attributes("-topmost", True)
            root.focus_force()
            ip_entry.focus_set()

            root.after(
                800,
                lambda: root.attributes("-topmost", False)
            )
        except tk.TclError:
            pass

    # Do it immediately and once more after Windows has actually mapped it.
    force_gui_to_front()
    root.after(120, force_gui_to_front)

    console_status(
        "[GUI] 設定ウィンドウを表示しました。"
        "タスクバーにも「MHO984 取得設定」が表示されます。"
    )
    console_status(
        "[GUI] IPアドレスと保存先を確認し、"
        "［保存して取得開始］を押してください。"
    )

    root.mainloop()

    if result["value"] is None:
        console_status("[GUI] 設定画面がキャンセルされました。")
    else:
        console_status("[GUI] 設定を受け取りました。取得処理へ進みます。")

    return result["value"]


def main():
    ap = argparse.ArgumentParser(
        description=(
            "MHO984 SINGLE -> STOP -> one RG03 BIN -> FTP -> "
            "Analog/Digital decode -> Viewer"
        )
    )
    ap.add_argument("--scope-ip", default=None)
    ap.add_argument("--scope-port", type=int, default=OSC_PORT)
    ap.add_argument("--base-dir", type=Path, default=None)
    ap.add_argument("--lan-inbox", type=Path, default=None)
    ap.add_argument("--no-viewer", action="store_true")
    ap.add_argument(
        "--force-trigger-on-timeout",
        action="store_true",
        help="Opt in to :TFORce when SINGLE does not trigger within the timeout. Public default is OFF.",
    )
    ap.add_argument(
        "--interactive-trigger-timeout",
        action="store_true",
        help=(
            "When SINGLE times out, show a local dialog offering: force once, "
            "wait another timeout interval, or abort. Intended for the integrated GUI."
        ),
    )
    ap.add_argument(
        "--trigger-timeout-seconds",
        type=float,
        default=30.0,
        help="Seconds to wait per SINGLE trigger interval (default: 30).",
    )
    ap.add_argument(
        "--allow-fallback-discovery",
        action="store_true",
        help="Opt in to broader read-only LAN fallback probing if anonymous FTP fails. Use only on an authorized trusted lab LAN.",
    )
    ap.add_argument(
        "--accept-risk",
        action="store_true",
        help="Required for --no-gui acquisition. Confirms the public-beta safety notice was read.",
    )
    ap.add_argument(
        "--no-gui",
        action="store_true",
        help="Use saved/default/CLI settings without showing the GUI.",
    )
    ap.add_argument(
        "--gui-test",
        action="store_true",
        help=(
            "Open the settings GUI only. Save/cancel exits without acquisition."
        ),
    )
    ap.add_argument(
        "--legacy-defaults",
        "--no-settings",
        dest="legacy_defaults",
        action="store_true",
        help=(
            "Compatibility/headless mode: do not show GUI and ignore saved settings. "
            "The public release intentionally has no default scope IP; provide "
            "--scope-ip and --accept-risk explicitly."
        ),
    )
    args = ap.parse_args()

    console_status("=" * 76)
    console_status("MHO984 Toolkit Public Beta v0.1.0-beta.7 startup")
    console_status("=" * 76)
    console_status(f"Python      : {sys.executable}")
    console_status(f"Program dir : {SCRIPT_DIR}")
    console_status("設定GUIを使用する場合は、この後GUIウィンドウが開きます。")
    console_status("")

    missing = [
        p for p in (CAPTURE_CORE, RETRIEVER, DECODER, VIEWER)
        if not p.is_file()
    ]
    if missing:
        raise FileNotFoundError(
            "R12 package component(s) missing:\n"
            + "\n".join(f"  - {p}" for p in missing)
        )

    # Settings resolution priority:
    #
    # 1) --legacy-defaults / --no-settings
    #      GUI OFF, saved JSON ignored. Public release requires explicit scope IP
    #      and --accept-risk before acquisition.
    #
    # 2) --no-gui
    #      GUI OFF, saved JSON is used, explicit CLI values override it.
    #
    # 3) normal
    #      GUI ON, saved JSON supplies initial values, explicit CLI values
    #      override the initial GUI values.
    if args.legacy_defaults:
        if args.scope_ip is None:
            args.scope_ip = OSC_IP
        if args.base_dir is None:
            args.base_dir = BASE_DIR
        if args.lan_inbox is None:
            args.lan_inbox = (
                Path(args.base_dir) / DEFAULT_LAN_INBOX_NAME
            )

    else:
        saved = load_user_settings()

        if not args.no_gui:
            initial_ip = (
                args.scope_ip
                if args.scope_ip is not None
                else saved["scope_ip"]
            )
            initial_base = (
                args.base_dir
                if args.base_dir is not None
                else Path(saved["base_dir"])
            )

            console_status(
                "[GUI] 起動します。Windows Terminalの背面ではなく"
                "前面へ表示するよう要求します。"
            )

            gui = show_settings_gui(initial_ip, initial_base)

            if gui is None:
                console_status("Acquisition cancelled.")
                return 0

            args.scope_ip = gui["scope_ip"]
            args.base_dir = gui["base_dir"]
            args.lan_inbox = gui["lan_inbox"]

            if args.gui_test:
                console_status("")
                console_status("GUI TEST: PASS")
                console_status(f"Scope IP : {args.scope_ip}")
                console_status(f"Base dir : {args.base_dir}")
                console_status("取得処理は実行していません。")
                return 0

        else:
            if args.scope_ip is None:
                args.scope_ip = saved["scope_ip"]
            if args.base_dir is None:
                args.base_dir = Path(saved["base_dir"])
            if args.lan_inbox is None:
                args.lan_inbox = (
                    Path(args.base_dir) / DEFAULT_LAN_INBOX_NAME
                )

    if args.lan_inbox is None:
        args.lan_inbox = (
            Path(args.base_dir) / DEFAULT_LAN_INBOX_NAME
        )

    if (args.no_gui or args.legacy_defaults) and not args.accept_risk:
        raise R12Error(
            "Public Beta headless acquisition requires --accept-risk after reading "
            "README.md, LEGAL_NOTICE.md, and SECURITY.md."
        )

    if not validate_ipv4_text(str(args.scope_ip or "")):
        raise R12Error(
            "A valid oscilloscope IPv4 address is required. Fresh public-release "
            "settings intentionally do not contain a default network target."
        )

    if not (1.0 <= float(args.trigger_timeout_seconds) <= 3600.0):
        raise R12Error("--trigger-timeout-seconds must be between 1 and 3600")

    base_dir = Path(args.base_dir).expanduser().resolve()
    lan_inbox = Path(args.lan_inbox).expanduser().resolve()

    base_dir.mkdir(parents=True, exist_ok=True)
    lan_inbox.mkdir(parents=True, exist_ok=True)

    print("=" * 76)
    print("MHO984 Toolkit Public Beta v0.1.0-beta.7 - Scope/Dual Edge Drift")
    print("=" * 76)
    print(f"Scope       : {args.scope_ip}:{args.scope_port}")
    print(f"Dataset dir : {base_dir}")
    print(f"LAN inbox   : {lan_inbox}")
    if args.legacy_defaults:
        print("Settings    : LEGACY DEFAULTS (saved settings ignored)")
    elif args.no_gui:
        print(f"Settings    : {SETTINGS_PATH} (GUI skipped)")
    else:
        print(f"Settings    : {SETTINGS_PATH}")
    print("UltraSigma  : not used")
    print(f"Force trig  : {'ENABLED (explicit opt-in)' if args.force_trigger_on_timeout else 'disabled'}")
    print(f"Trig timeout: {float(args.trigger_timeout_seconds):g} s per interval")
    print(f"Timeout UI  : {'ask (Force / Wait / Abort)' if args.interactive_trigger_timeout else 'disabled'}")
    print(f"LAN fallback: {'ENABLED (explicit opt-in)' if args.allow_fallback_discovery else 'disabled; FTP/21 only'}")
    print("")
    print("Optimized flow:")
    print("  SINGLE -> STOP -> C:/ RG03 BIN -> FTP once")
    print("  -> CH1-CH4 + optional D0-D15 decoded from the same BIN")
    print("  -> no per-channel analog waveform transfer")
    print("")

    total_started = time.perf_counter()
    timings = {}
    before = dataset_dirs(base_dir)

    capture = load_module(
        CAPTURE_CORE,
        "mho984_capture_r12_runtime",
    )
    capture.OSC_IP = args.scope_ip
    capture.OSC_PORT = args.scope_port
    capture.BASE_DIR = base_dir
    capture.FORCE_TRIGGER_ON_TIMEOUT = bool(args.force_trigger_on_timeout)
    capture.SINGLE_TRIGGER_TIMEOUT_S = float(args.trigger_timeout_seconds)

    if args.interactive_trigger_timeout:
        def _interactive_trigger_timeout_decision(context):
            # This acquisition process is launched by the integrated GUI. Keep
            # the public CLI/headless behavior fail-safe; only this explicit
            # mode opens a local timeout decision dialog.
            import tkinter as _tk
            from tkinter import messagebox as _messagebox

            root = _tk.Tk()
            root.withdraw()
            try:
                try:
                    root.attributes("-topmost", True)
                except _tk.TclError:
                    pass
                root.update_idletasks()
                status = context.get("status", "?")
                elapsed = context.get("single_elapsed_s", 0.0)
                timeout_s = context.get("timeout_s", capture.SINGLE_TRIGGER_TIMEOUT_S)
                already_forced = context.get("forced_trigger_already_used", False)

                if already_forced:
                    _messagebox.showerror(
                        "SINGLE取得タイムアウト",
                        "Force Trigger実行後もSTOPになりませんでした。\n"
                        "安全のため取得を中止します。",
                        parent=root,
                    )
                    return "abort"

                answer = _messagebox.askyesnocancel(
                    "SINGLE取得待ち",
                    "SINGLE取得がまだSTOPになっていません。\n\n"
                    f"現在状態: {status}\n"
                    f"待機時間: {elapsed:.1f} s\n\n"
                    "［はい］  今回だけ Force Trigger を実行\n"
                    f"［いいえ］ さらに {float(timeout_s):g} 秒待つ\n"
                    "［キャンセル］取得を中止\n\n"
                    "Force Triggerでは現在の入力をその時点で取得します。",
                    parent=root,
                    icon="warning",
                )
                if answer is True:
                    return "force"
                if answer is False:
                    return "wait"
                return "abort"
            finally:
                try:
                    root.destroy()
                except Exception:
                    pass

        capture.TRIGGER_TIMEOUT_DECISION_CALLBACK = (
            _interactive_trigger_timeout_decision
        )
    capture.R10_INSTRUMENT_MEMORY_DIRECTORY = "C:/"
    capture.R10_MEMORY_FILENAME_PREFIX = "mho_sync_r12_"

    print("[1/4] SINGLE -> STOP -> save one RG03 Memory BIN")
    t = time.perf_counter()
    capture.main()
    timings["capture_and_scope_bin_save_s"] = (
        time.perf_counter() - t
    )

    dataset = newest_new_dataset(before, base_dir)
    expected_name = expected_bin_from_dataset(dataset)

    print("")
    print(
        "[2/4] Wait for stable FTP size, then anonymous FTP: "
        f"C:/{expected_name}"
    )
    retriever = load_module(
        RETRIEVER,
        "mho984_retriever_r12_runtime",
    )
    retrieval_report_path = dataset / "lan_retrieval_r12.json"
    retriever.ALLOW_FALLBACK_DISCOVERY = bool(args.allow_fallback_discovery)

    t = time.perf_counter()
    retrieval = retriever.retrieve_bin(
        args.scope_ip,
        expected_name,
        lan_inbox,
        report_path=retrieval_report_path,
    )
    timings["lan_retrieval_s"] = time.perf_counter() - t

    if not retrieval.get("success"):
        raise R12Error(
            "Automatic LAN retrieval failed. "
            f"Report: {retrieval_report_path}"
        )

    bin_path = Path(retrieval["local_path"]).resolve()
    print(f"Method       : {retrieval['successful_method']}")
    print(f"Retrieved BIN: {bin_path}")

    fast_ftp = retrieval.get("fast_ftp", {})
    readiness = fast_ftp.get("readiness", {})
    validation = fast_ftp.get("download_validation", {})

    if readiness:
        print(
            "Remote FTP  : "
            f"{readiness.get('remote_size')} bytes stable"
        )
        header = readiness.get("header") or {}
        if header.get("valid"):
            print(
                "RG03 hdr    : "
                f"{header.get('total_file_bytes')} bytes declared"
            )

    if validation:
        print(
            "Local BIN   : "
            f"{validation.get('actual_size')} bytes"
        )
        if validation.get("header_declared_total") is not None:
            print(
                "Header diff : "
                f"{validation.get('header_declared_minus_actual')} bytes"
            )

    print("")
    print("[3/4] Decode Analog + optional Logic from the ONE RG03 BIN")
    t = time.perf_counter()
    cp = subprocess.run(
        [
            sys.executable,
            str(DECODER),
            str(bin_path),
            str(dataset),
        ]
    )
    timings["decode_s"] = time.perf_counter() - t
    if cp.returncode != 0:
        raise R12Error(f"Decoder failed: {cp.returncode}")

    timings["total_to_decoded_dataset_s"] = (
        time.perf_counter() - total_started
    )

    performance = {
        "tool_version": VERSION,
        "architecture": "single_RG03_BIN",
        "scope_ip": args.scope_ip,
        "dataset_base_dir": str(base_dir),
        "lan_inbox": str(lan_inbox),
        "settings_path": str(SETTINGS_PATH),
        "ultrasigma_used": False,
        "analog_scpi_waveform_transfers": 0,
        "memory_bin_transfers": 1,
        "lan_method": retrieval.get("successful_method"),
        "scope_file": f"C:/{expected_name}",
        "pc_file": str(bin_path),
        "timings_s": timings,
        "recorded_at": datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
    }
    (dataset / "performance_r12.json").write_text(
        json.dumps(performance, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("")
    print("[4/4] Viewer")
    if AUTO_LAUNCH_VIEWER and not args.no_viewer:
        launch_viewer(dataset)
    else:
        print("Viewer launch disabled.")

    print("")
    print("=" * 76)
    print("PUBLIC BETA ACQUISITION COMPLETE")
    print("=" * 76)
    print(f"Dataset : {dataset}")
    print(f"BIN     : {bin_path}")
    print("Analog SCPI waveform transfers: 0")
    print("FTP Memory BIN transfers       : 1")
    print(
        "Time to decoded dataset        : "
        f"{timings['total_to_decoded_dataset_s']:.3f} s"
    )
    print(
        "Performance log                : "
        f"{dataset / 'performance_r12.json'}"
    )
    print("=" * 76)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())