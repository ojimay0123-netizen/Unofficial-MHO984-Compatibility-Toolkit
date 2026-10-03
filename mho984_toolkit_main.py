#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Independent, unofficial compatibility tool; not affiliated with RIGOL.

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

APP_VERSION = "0.1.0-beta.7"
ENGINE_VERSION = "r12.11e public beta.2-compatible"
VIEWER_VERSION = "r13.6 + protocol overlay beta.1"
SCRIPT_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = Path.home() / ".mho984_toolkit_public_settings.json"
DEFAULT_BASE_DIR = Path(r"C:\Temp")
DEFAULT_SCOPE_PORT = 5555

ACQUISITION = SCRIPT_DIR / "mho984_sync_r12_11e_gui_settings.py"
DECODER = SCRIPT_DIR / "mho984_bin_decoder_r12_7.py"
VIEWER = SCRIPT_DIR / "viewer_mho984_r13_5_timebase_calibrated.py"
PROTOCOL_ANALYZER = SCRIPT_DIR / "protocol_analyzer.py"
README = SCRIPT_DIR / "README.md"
LEGAL = SCRIPT_DIR / "LEGAL_NOTICE.md"
SECURITY = SCRIPT_DIR / "SECURITY.md"
SETUP_BAT = SCRIPT_DIR / "setup_venv.bat"
EXPORT_DIAG_BAT = SCRIPT_DIR / "export_diagnostics_for_issue.bat"


def validate_ipv4_text(text: str) -> bool:
    parts = str(text).strip().split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        if not part.isdigit():
            return False
        value = int(part)
        if value < 0 or value > 255:
            return False
    return True


def load_settings() -> dict:
    result = {
        "scope_ip": "",
        "base_dir": str(DEFAULT_BASE_DIR),
        "trigger_timeout_s": 30,
    }
    try:
        if SETTINGS_PATH.is_file():
            obj = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                ip = str(obj.get("scope_ip", "")).strip()
                base_dir = str(obj.get("base_dir", "")).strip()
                if validate_ipv4_text(ip):
                    result["scope_ip"] = ip
                if base_dir:
                    result["base_dir"] = base_dir
                try:
                    trigger_timeout_s = int(obj.get("trigger_timeout_s", 30))
                    if 1 <= trigger_timeout_s <= 3600:
                        result["trigger_timeout_s"] = trigger_timeout_s
                except Exception:
                    pass
    except Exception:
        pass
    return result


def save_settings(scope_ip: str, base_dir: str, trigger_timeout_s: int = 30) -> None:
    payload = {
        "version": 1,
        "scope_ip": scope_ip.strip(),
        "base_dir": str(base_dir).strip(),
        "trigger_timeout_s": int(trigger_timeout_s),
    }
    SETTINGS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def find_python_for_child_processes() -> str:
    """Prefer the local venv if present, otherwise use the current interpreter."""
    candidate = SCRIPT_DIR / ".venv" / "Scripts" / "python.exe"
    if candidate.is_file():
        return str(candidate)
    return sys.executable


def open_local_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(path)
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])


def next_offline_dataset(base_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = base_dir / f"mho984_offline_{stamp}"
    suffix = 1
    while candidate.exists():
        candidate = base_dir / f"mho984_offline_{stamp}_{suffix:02d}"
        suffix += 1
    return candidate


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, settings: dict):
        super().__init__(parent)
        self.result = None
        self.title("接続・保存先設定")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        frame = ttk.Frame(self, padding=18)
        frame.grid(sticky="nsew")

        ttk.Label(frame, text="オシロスコープ IP").grid(row=0, column=0, sticky="w", pady=6)
        self.ip_var = tk.StringVar(value=settings.get("scope_ip", ""))
        ttk.Entry(frame, textvariable=self.ip_var, width=32).grid(row=0, column=1, columnspan=2, sticky="ew", pady=6)

        ttk.Label(frame, text="SCPIポート").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Label(frame, text=str(DEFAULT_SCOPE_PORT)).grid(row=1, column=1, sticky="w", pady=6)
        ttk.Label(frame, text="固定").grid(row=1, column=2, sticky="w", pady=6)

        ttk.Label(frame, text="データ保存先").grid(row=2, column=0, sticky="w", pady=6)
        self.base_var = tk.StringVar(value=settings.get("base_dir", str(DEFAULT_BASE_DIR)))
        ttk.Entry(frame, textvariable=self.base_var, width=52).grid(row=2, column=1, sticky="ew", pady=6)
        ttk.Button(frame, text="参照...", command=self._browse).grid(row=2, column=2, padx=(8, 0), pady=6)

        ttk.Label(frame, text="SINGLE待機時間").grid(row=3, column=0, sticky="w", pady=6)
        self.trigger_timeout_var = tk.StringVar(value=str(settings.get("trigger_timeout_s", 30)))
        ttk.Spinbox(
            frame, textvariable=self.trigger_timeout_var, from_=5, to=3600, increment=5, width=10
        ).grid(row=3, column=1, sticky="w", pady=6)
        ttk.Label(frame, text="秒（タイムアウト時は選択ダイアログ）").grid(row=3, column=2, sticky="w", pady=6)

        note = (
            "保存先にはローカルフォルダまたはUNC共有フォルダを指定できます。\n"
            "IPアドレスは取得時だけ使用し、BINオフライン表示ではオシロへ接続しません。"
        )
        ttk.Label(frame, text=note, foreground="#555555", justify="left").grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(8, 14)
        )

        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=3, sticky="e")
        ttk.Button(buttons, text="キャンセル", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="保存", command=self._save).pack(side="right", padx=(0, 8))

        frame.columnconfigure(1, weight=1)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.after(50, self._center)

    def _center(self):
        self.update_idletasks()
        x = self.master.winfo_rootx() + max(20, (self.master.winfo_width() - self.winfo_width()) // 2)
        y = self.master.winfo_rooty() + max(20, (self.master.winfo_height() - self.winfo_height()) // 2)
        self.geometry(f"+{x}+{y}")

    def _browse(self):
        initial = self.base_var.get().strip() or str(DEFAULT_BASE_DIR)
        selected = filedialog.askdirectory(parent=self, initialdir=initial, title="データ保存先を選択")
        if selected:
            self.base_var.set(selected)

    def _save(self):
        ip = self.ip_var.get().strip()
        base_dir = self.base_var.get().strip()
        if ip and not validate_ipv4_text(ip):
            messagebox.showerror("入力エラー", "IPv4アドレスを確認してください。", parent=self)
            return
        if not base_dir:
            messagebox.showerror("入力エラー", "データ保存先を指定してください。", parent=self)
            return
        try:
            trigger_timeout_s = int(self.trigger_timeout_var.get().strip())
        except ValueError:
            trigger_timeout_s = 0
        if not (1 <= trigger_timeout_s <= 3600):
            messagebox.showerror("入力エラー", "SINGLE待機時間は1～3600秒で指定してください。", parent=self)
            return
        self.result = {
            "scope_ip": ip,
            "base_dir": base_dir,
            "trigger_timeout_s": trigger_timeout_s,
        }
        self.destroy()


class MainApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Unofficial MHO984 Compatibility Toolkit {APP_VERSION}")
        self.geometry("980x700")
        self.minsize(820, 580)
        self.settings = load_settings()
        self.worker_queue: queue.Queue = queue.Queue()
        self.busy = False
        self.current_process: subprocess.Popen | None = None
        self.last_dataset: Path | None = None
        self._build_style()
        self._build_menu()
        self._build_ui()
        self._refresh_settings_display()
        self.after(100, self._drain_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_style(self):
        try:
            style = ttk.Style(self)
            if "vista" in style.theme_names():
                style.theme_use("vista")
        except tk.TclError:
            pass

    def _build_menu(self):
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="オシロから波形取得", command=self.acquire_from_scope, accelerator="Ctrl+A")
        file_menu.add_command(label="保存済みBINを開く", command=self.open_memory_bin, accelerator="Ctrl+B")
        file_menu.add_command(label="既存データセットを開く", command=self.open_dataset, accelerator="Ctrl+O")
        file_menu.add_command(label="プロトコル解析", command=self.open_protocol_analyzer, accelerator="Ctrl+P")
        file_menu.add_separator()
        file_menu.add_command(label="終了", command=self._on_close)
        menubar.add_cascade(label="ファイル", menu=file_menu)

        settings_menu = tk.Menu(menubar, tearoff=False)
        settings_menu.add_command(label="接続・保存先...", command=self.edit_settings)
        menubar.add_cascade(label="設定", menu=settings_menu)

        tools_menu = tk.Menu(menubar, tearoff=False)
        tools_menu.add_command(label="Python環境セットアップ", command=self.run_setup)
        tools_menu.add_command(label="診断情報ZIPを作成", command=self.run_diagnostics_export)
        menubar.add_cascade(label="ツール", menu=tools_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="README", command=lambda: self._open_doc(README))
        help_menu.add_command(label="法的注意事項", command=lambda: self._open_doc(LEGAL))
        help_menu.add_command(label="セキュリティ", command=lambda: self._open_doc(SECURITY))
        help_menu.add_separator()
        help_menu.add_command(label="バージョン情報", command=self.show_about)
        menubar.add_cascade(label="ヘルプ", menu=help_menu)

        self.config(menu=menubar)
        self.bind_all("<Control-a>", lambda e: self.acquire_from_scope())
        self.bind_all("<Control-b>", lambda e: self.open_memory_bin())
        self.bind_all("<Control-o>", lambda e: self.open_dataset())
        self.bind_all("<Control-p>", lambda e: self.open_protocol_analyzer())

    def _build_ui(self):
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)

        ttk.Label(
            outer,
            text="Unofficial MHO984 Compatibility Toolkit",
            font=("Segoe UI", 18, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            outer,
            text="波形取得・保存BIN解析・Viewerを1つの画面から操作します",
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(2, 12))

        status_box = ttk.LabelFrame(outer, text="現在の設定", padding=12)
        status_box.pack(fill="x")
        status_box.columnconfigure(1, weight=1)
        ttk.Label(status_box, text="オシロ IP:").grid(row=0, column=0, sticky="w")
        self.ip_label = ttk.Label(status_box, text="")
        self.ip_label.grid(row=0, column=1, sticky="w", padx=(8, 8))
        ttk.Label(status_box, text="保存先:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.base_label = ttk.Label(status_box, text="")
        self.base_label.grid(row=1, column=1, sticky="w", padx=(8, 8), pady=(6, 0))
        ttk.Label(status_box, text="SINGLE待機:").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.timeout_label = ttk.Label(status_box, text="")
        self.timeout_label.grid(row=2, column=1, sticky="w", padx=(8, 8), pady=(6, 0))
        ttk.Button(status_box, text="設定変更...", command=self.edit_settings).grid(row=0, column=2, rowspan=3, padx=(12, 0))

        action_frame = ttk.Frame(outer)
        action_frame.pack(fill="x", pady=14)
        for i in range(4):
            action_frame.columnconfigure(i, weight=1)

        self.acquire_btn = ttk.Button(action_frame, text="オシロから\n波形取得", command=self.acquire_from_scope)
        self.acquire_btn.grid(row=0, column=0, sticky="nsew", padx=(0, 6), ipady=14)
        self.bin_btn = ttk.Button(action_frame, text="保存済みBINを\n開く", command=self.open_memory_bin)
        self.bin_btn.grid(row=0, column=1, sticky="nsew", padx=6, ipady=14)
        self.dataset_btn = ttk.Button(action_frame, text="既存データセットを\nViewerで開く", command=self.open_dataset)
        self.dataset_btn.grid(row=0, column=2, sticky="nsew", padx=6, ipady=14)
        self.protocol_btn = ttk.Button(action_frame, text="プロトコル\n解析", command=self.open_protocol_analyzer)
        self.protocol_btn.grid(row=0, column=3, sticky="nsew", padx=(6, 0), ipady=14)

        info = ttk.LabelFrame(outer, text="動作モード", padding=10)
        info.pack(fill="x")
        mode_text = (
            "● オシロから取得: SINGLE → STOP → Memory BIN保存 → FTP取得 → デコード → Viewer\n"
            "● BINを開く: オシロで保存したMemory BINをPC上だけでデコード → Viewer（LAN接続なし）\n"
            "● 既存データセット: すでにデコード済みのmho984_*フォルダをそのままViewerで表示\n"
            "● プロトコル解析: UART / RS-232 / RS-485 / I2C / SPI / LIN / CAN / GPSを解析"
        )
        ttk.Label(info, text=mode_text, justify="left").pack(anchor="w")

        log_frame = ttk.LabelFrame(outer, text="進捗 / ログ", padding=6)
        log_frame.pack(fill="both", expand=True, pady=(14, 0))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log = tk.Text(log_frame, wrap="word", height=16, font=("Consolas", 9))
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(state="disabled")

        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(8, 0))
        self.status_var = tk.StringVar(value="待機中")
        ttk.Label(bottom, textvariable=self.status_var).pack(side="left")
        self.cancel_btn = ttk.Button(bottom, text="処理を中止", command=self.cancel_current, state="disabled")
        self.cancel_btn.pack(side="right")

        self._append_log(f"MHO984 Toolkit {APP_VERSION} started.\n")
        self._append_log("通常操作はこのメイン画面だけで行えます。\n")

    def _refresh_settings_display(self):
        ip = self.settings.get("scope_ip", "") or "未設定"
        self.ip_label.configure(text=f"{ip}:{DEFAULT_SCOPE_PORT}")
        self.base_label.configure(text=self.settings.get("base_dir", str(DEFAULT_BASE_DIR)))
        timeout_s = int(self.settings.get("trigger_timeout_s", 30))
        self.timeout_label.configure(text=f"{timeout_s}秒 → Force / さらに待つ / 中止を選択")

    def _append_log(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_busy(self, busy: bool, status: str = ""):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for button in (self.acquire_btn, self.bin_btn, self.dataset_btn, self.protocol_btn):
            button.configure(state=state)
        self.cancel_btn.configure(state="normal" if busy else "disabled")
        self.status_var.set(status or ("処理中..." if busy else "待機中"))

    def edit_settings(self):
        if self.busy:
            messagebox.showinfo("処理中", "現在の処理が終了してから設定を変更してください。", parent=self)
            return
        dlg = SettingsDialog(self, self.settings)
        self.wait_window(dlg)
        if dlg.result:
            self.settings = dlg.result
            save_settings(
                self.settings["scope_ip"],
                self.settings["base_dir"],
                int(self.settings.get("trigger_timeout_s", 30)),
            )
            self._refresh_settings_display()
            self._append_log(f"設定を保存しました: {SETTINGS_PATH}\n")

    def _require_scope_settings(self) -> bool:
        ip = self.settings.get("scope_ip", "").strip()
        if not validate_ipv4_text(ip):
            messagebox.showwarning(
                "IPアドレス未設定",
                "オシロスコープのIPv4アドレスを設定してください。",
                parent=self,
            )
            self.edit_settings()
            return validate_ipv4_text(self.settings.get("scope_ip", "").strip())
        return True

    def _require_base_dir(self) -> Path | None:
        base = self.settings.get("base_dir", "").strip()
        if not base:
            self.edit_settings()
            base = self.settings.get("base_dir", "").strip()
        if not base:
            return None
        return Path(base).expanduser()

    def _ensure_runtime_dependencies(self) -> bool:
        python_exe = find_python_for_child_processes()
        try:
            cp = subprocess.run(
                [python_exe, "-c", "import numpy, matplotlib"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
            if cp.returncode == 0:
                return True
        except Exception:
            pass
        if messagebox.askyesno(
            "Python環境が未準備です",
            "NumPy / Matplotlibを利用できるPython環境を確認できませんでした。\n"
            "setup_venv.bat を起動しますか？",
            parent=self,
        ):
            self.run_setup()
        return False

    def acquire_from_scope(self):
        if self.busy:
            return
        if not self._ensure_runtime_dependencies():
            return
        if not self._require_scope_settings():
            return
        base_dir = self._require_base_dir()
        if base_dir is None:
            return

        msg = (
            "MHO984から波形を取得します。\n\n"
            "・非公式Betaツールです。RIGOLによる承認・保証はありません。\n"
            "・オシロをSINGLE/STOP状態へ変更し、C:/にMemory BINを保存します。\n"
            "・取得には平文のanonymous FTP/21を使用します。信頼できるLANでのみ使用してください。\n"
            "・安全・校正・認証用途の測定保証はありません。\n\n"
            f"接続先: {self.settings['scope_ip']}:{DEFAULT_SCOPE_PORT}\n"
            f"保存先: {base_dir}\n"
            f"SINGLE待機: {int(self.settings.get('trigger_timeout_s', 30))}秒\n"
            "タイムアウト時: Force Trigger / さらに待つ / 中止を選択\n\n"
            "続行しますか？"
        )
        if not messagebox.askokcancel("波形取得の確認", msg, parent=self):
            return

        python_exe = find_python_for_child_processes()
        cmd = [
            python_exe,
            "-u",
            str(ACQUISITION),
            "--no-gui",
            "--accept-risk",
            "--scope-ip",
            self.settings["scope_ip"],
            "--base-dir",
            str(base_dir),
            "--no-viewer",
            "--interactive-trigger-timeout",
            "--trigger-timeout-seconds",
            str(int(self.settings.get("trigger_timeout_s", 30))),
        ]
        self._append_log("\n=== オシロから波形取得 ===\n")
        self._append_log(f"Command runtime: {python_exe}\n")
        self._run_process(cmd, mode="acquire", success_callback=self._acquire_success)

    def _acquire_success(self, captured_text: str):
        dataset = None
        for line in captured_text.splitlines():
            m = re.match(r"\s*Dataset\s*:\s*(.+?)\s*$", line)
            if m:
                dataset = Path(m.group(1).strip())
        if dataset is None or not dataset.exists():
            base_dir = self._require_base_dir()
            if base_dir and base_dir.exists():
                candidates = [p for p in base_dir.iterdir() if p.is_dir() and p.name.lower().startswith("mho984_")]
                if candidates:
                    dataset = max(candidates, key=lambda p: p.stat().st_mtime)
        if dataset and dataset.exists():
            self._append_log(f"取得完了: {dataset}\n")
            self.launch_viewer(dataset)
        else:
            messagebox.showwarning("取得完了", "取得は完了しましたが、Viewer用データセットを自動検出できませんでした。", parent=self)

    def open_memory_bin(self):
        if self.busy:
            return
        if not self._ensure_runtime_dependencies():
            return
        selected = filedialog.askopenfilename(
            parent=self,
            title="MHO984で保存したMemory BINを選択",
            filetypes=[("BIN files", "*.bin"), ("All files", "*.*")],
        )
        if not selected:
            return
        bin_path = Path(selected)
        base_dir = self._require_base_dir()
        if base_dir is None:
            return
        try:
            base_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            messagebox.showerror("保存先エラー", f"保存先を作成できません。\n\n{exc}", parent=self)
            return

        dataset = next_offline_dataset(base_dir)
        dataset.mkdir(parents=True, exist_ok=False)
        offline_meta = {
            "source": "offline_user_selected_memory_bin",
            "source_file": bin_path.name,
            "source_path_recorded": False,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "tool_version": APP_VERSION,
            "network_used": False,
        }
        (dataset / "offline_import.json").write_text(
            json.dumps(offline_meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        python_exe = find_python_for_child_processes()
        cmd = [python_exe, "-u", str(DECODER), str(bin_path), str(dataset)]
        self._append_log("\n=== 保存済みBINを開く ===\n")
        self._append_log(f"Source BIN: {bin_path}\n")
        self._append_log(f"Dataset   : {dataset}\n")
        self._run_process(
            cmd,
            mode="offline",
            success_callback=lambda text, ds=dataset: self._offline_success(ds),
            failure_callback=lambda code, ds=dataset: self._offline_failure(code, ds),
        )

    def _offline_success(self, dataset: Path):
        self._append_log(f"BINデコード完了: {dataset}\n")
        self.launch_viewer(dataset)

    def _offline_failure(self, code: int, dataset: Path):
        self._append_log(f"BINデコード失敗 (exit={code})。作業フォルダ: {dataset}\n")
        messagebox.showerror(
            "BINデコードエラー",
            "選択したBINをデコードできませんでした。\n"
            "MHO984 Memory waveformのRG03 BINか確認してください。\n\n"
            f"作業フォルダ: {dataset}",
            parent=self,
        )

    def open_dataset(self):
        if self.busy:
            return
        if not self._ensure_runtime_dependencies():
            return
        base = self.settings.get("base_dir", str(DEFAULT_BASE_DIR))
        selected = filedialog.askdirectory(parent=self, title="デコード済みデータセットを選択", initialdir=base)
        if not selected:
            return
        dataset = Path(selected)
        if not (dataset / "acquisition.json").exists() and not (dataset / "analog").exists():
            if not messagebox.askyesno(
                "確認",
                "標準的なMHO984データセット構成を検出できません。\nそれでもViewerで開きますか？",
                parent=self,
            ):
                return
        self.launch_viewer(dataset)

    def launch_viewer(self, dataset: Path):
        self.last_dataset = Path(dataset)
        python_exe = find_python_for_child_processes()
        env = os.environ.copy()
        env["MHO984_DATASET"] = str(dataset)
        try:
            subprocess.Popen(
                [python_exe, str(VIEWER), str(dataset)],
                cwd=str(SCRIPT_DIR),
                env=env,
            )
            self._append_log(f"Viewerを起動しました: {dataset}\n")
        except Exception as exc:
            messagebox.showerror("Viewer起動エラー", str(exc), parent=self)


    def open_protocol_analyzer(self):
        if self.busy:
            return
        if not self._ensure_runtime_dependencies():
            return
        dataset = self.last_dataset
        if dataset is None or not dataset.exists():
            base = self.settings.get("base_dir", str(DEFAULT_BASE_DIR))
            selected = filedialog.askdirectory(
                parent=self,
                title="プロトコル解析するデータセットを選択",
                initialdir=base,
            )
            if not selected:
                return
            dataset = Path(selected)
        if not PROTOCOL_ANALYZER.exists():
            messagebox.showerror("エラー", "protocol_analyzer.py が見つかりません。", parent=self)
            return
        python_exe = find_python_for_child_processes()
        try:
            subprocess.Popen(
                [python_exe, str(PROTOCOL_ANALYZER), str(dataset)],
                cwd=str(SCRIPT_DIR),
            )
            self._append_log(f"プロトコル解析を起動しました: {dataset}\n")
        except Exception as exc:
            messagebox.showerror("プロトコル解析起動エラー", str(exc), parent=self)

    def _run_process(self, cmd, *, mode: str, success_callback=None, failure_callback=None):
        self._set_busy(True, "処理中...")

        def worker():
            captured = []
            try:
                creationflags = 0
                if os.name == "nt":
                    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                child_env = os.environ.copy()
                child_env["PYTHONIOENCODING"] = "utf-8"
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(SCRIPT_DIR),
                    env=child_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=creationflags,
                )
                self.current_process = proc
                assert proc.stdout is not None
                for line in proc.stdout:
                    captured.append(line)
                    self.worker_queue.put(("log", line))
                code = proc.wait()
                self.worker_queue.put(("done", code, "".join(captured), success_callback, failure_callback, mode))
            except Exception as exc:
                self.worker_queue.put(("exception", exc, mode))
            finally:
                self.current_process = None

        threading.Thread(target=worker, daemon=True).start()

    def _drain_queue(self):
        try:
            while True:
                item = self.worker_queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    self._append_log(item[1])
                elif kind == "done":
                    _, code, captured, success_cb, failure_cb, mode = item
                    self._set_busy(False, "完了" if code == 0 else f"エラー (exit={code})")
                    if code == 0:
                        if success_cb:
                            success_cb(captured)
                    else:
                        if failure_cb:
                            failure_cb(code)
                        elif mode == "acquire":
                            messagebox.showerror("波形取得エラー", "取得処理が失敗しました。詳細はログ欄を確認してください。", parent=self)
                elif kind == "exception":
                    _, exc, mode = item
                    self._set_busy(False, "エラー")
                    self._append_log(f"ERROR: {type(exc).__name__}: {exc}\n")
                    messagebox.showerror("実行エラー", f"{type(exc).__name__}: {exc}", parent=self)
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def cancel_current(self):
        proc = self.current_process
        if proc is None or proc.poll() is not None:
            return
        if not messagebox.askyesno("処理を中止", "現在の処理を中止しますか？\n途中のデータセットが残る場合があります。", parent=self):
            return
        try:
            proc.terminate()
            self._append_log("ユーザー操作により処理を中止しました。\n")
        except Exception as exc:
            self._append_log(f"中止要求エラー: {exc}\n")

    def run_setup(self):
        if self.busy:
            return
        if not SETUP_BAT.exists():
            messagebox.showerror("エラー", "setup_venv.bat が見つかりません。", parent=self)
            return
        if os.name != "nt":
            messagebox.showinfo("情報", "このセットアップはWindows用です。", parent=self)
            return
        subprocess.Popen(["cmd", "/c", "start", "", str(SETUP_BAT)], cwd=str(SCRIPT_DIR))

    def run_diagnostics_export(self):
        if not EXPORT_DIAG_BAT.exists():
            messagebox.showerror("エラー", "診断情報エクスポータが見つかりません。", parent=self)
            return
        if os.name == "nt":
            subprocess.Popen(["cmd", "/c", "start", "", str(EXPORT_DIAG_BAT)], cwd=str(SCRIPT_DIR))
        else:
            messagebox.showinfo("情報", "診断情報エクスポータはWindows用です。", parent=self)

    def _open_doc(self, path: Path):
        try:
            open_local_file(path)
        except Exception as exc:
            messagebox.showerror("ファイルを開けません", str(exc), parent=self)

    def show_about(self):
        messagebox.showinfo(
            "バージョン情報",
            f"Unofficial MHO984 Compatibility Toolkit {APP_VERSION}\n"
            f"Acquisition engine: {ENGINE_VERSION}\n"
            f"Viewer: {VIEWER_VERSION}\n\n"
            "MHO984で実機検証した非公式互換ツールです。\n"
            "RIGOL Technologiesとは提携・承認関係にありません。",
            parent=self,
        )

    def _on_close(self):
        if self.busy:
            if not messagebox.askyesno("終了", "処理が実行中です。終了すると処理も中断されます。\n終了しますか？", parent=self):
                return
            if self.current_process and self.current_process.poll() is None:
                try:
                    self.current_process.terminate()
                except Exception:
                    pass
        self.destroy()


if __name__ == "__main__":
    app = MainApp()
    app.mainloop()