from __future__ import annotations
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

retr = load("beta9_retriever_test", ROOT / "mho984_lan_bin_retriever_r12_7_stable_ftp.py")
cap = load("beta9_capture_test", ROOT / "capture_mho984_sync_r12_7_bin_only_core.py")

name = "mho984_20261004_081016.bin"
assert len(name) == 26
assert cap.R10_MEMORY_FILENAME_PREFIX == "mho984_"
assert len(f"{cap.R10_MEMORY_FILENAME_PREFIX}20261004_081016.bin") <= 26
assert retr._select_match_from_names(["x.bin", name], name)[1] == name
variant = "mho984_20261004_0810160.bin"
assert retr._select_match_from_names([variant], name)[1] == variant

class FakeFTP:
    def __init__(self):
        self.root = "/"
        self.polls = 0
    def cwd(self, path):
        if path == "/":
            self.root = "/"
            return
        raise RuntimeError("550 Error")
    def nlst(self):
        assert self.root == "/"
        self.polls += 1
        return [] if self.polls < 3 else [name]
    def quit(self): pass
    def close(self): pass

fake = FakeFTP()
retr._ftp_open_anonymous = lambda host: (fake, "fake-welcome")
result = retr._wait_ftp_matching_file("192.0.2.1", name, timeout_s=0.2, poll_s=0.001)
assert result["found"] is True
assert result["name"] == name
assert result["poll_count"] == 3

class FakeClock:
    def __init__(self): self.t = 0.0
    def perf_counter(self): return self.t
    def sleep(self, seconds): self.t += float(seconds)
class SizeFTP:
    def cwd(self, path): pass
    def quit(self): pass
    def close(self): pass

original_perf_counter = retr.time.perf_counter
original_sleep = retr.time.sleep
original_open = retr._ftp_open_anonymous
original_header = retr._ftp_read_first16
original_size = retr._ftp_query_size
try:
    clock = FakeClock()
    declared = 1_500_000_484
    final_size = declared
    rate = 16 * 1024 * 1024
    retr.time.perf_counter = clock.perf_counter
    retr.time.sleep = clock.sleep
    retr._ftp_open_anonymous = lambda host: (SizeFTP(), "fake-welcome")
    retr._ftp_read_first16 = lambda *args: {"valid": True,"magic": "RG03","total_file_bytes": declared,"reserved": 0,"waveform_count": 3}
    retr._ftp_query_size = lambda ftp, remote: min(final_size, max(1, int(rate * clock.t) + 172))
    dynamic = retr._wait_remote_rg03_complete("192.0.2.1", "/", name)
    assert dynamic["complete"] is True
    assert dynamic["elapsed_s"] > 60.0
    assert dynamic["initial_wait_budget_s"] > 60.0
    assert dynamic["remote_size"] == final_size
    clock.t = 0.0
    declared = 300_000_484
    final_size = 250_000_484
    rate = 20 * 1024 * 1024
    retr._ftp_read_first16 = lambda *args: {"valid": True,"magic": "RG03","total_file_bytes": declared,"reserved": 0,"waveform_count": 3}
    mismatch = retr._wait_remote_rg03_complete("192.0.2.1", "/", name)
    assert mismatch["complete"] is True
    assert mismatch["remote_size"] == final_size
    assert mismatch["header_size_matches_remote"] is False
finally:
    retr.time.perf_counter = original_perf_counter
    retr.time.sleep = original_sleep
    retr._ftp_open_anonymous = original_open
    retr._ftp_read_first16 = original_header
    retr._ftp_query_size = original_size

fake2 = FakeFTP()
retr._ftp_open_anonymous = lambda host: (fake2, "fake-welcome")
retr._wait_remote_rg03_complete = lambda *args, **kwargs: {"complete": True, "remote_size": 1234, "history": []}
retr._download_one_ftp = lambda *args, **kwargs: {"success": True, "destination": "/tmp/fake.bin", "size": 1234,"elapsed_s": 0.01, "throughput_MiB_s": 0.1,"validation": {"valid": True, "actual_size": 1234}}
full = retr.ftp_retrieve("192.0.2.1", name, ROOT, {"21": {"open": True}})
assert full["success"] is True
assert full["appearance_wait"]["poll_count"] == 3

cap.scpi_query_line = lambda sock, command, **kwargs: "STOP" if command == ":TRIGger:STATus?" else "0"
cap.ensure_no_scpi_errors = lambda *args, **kwargs: None
cap.drain_scpi_errors = lambda *args, **kwargs: []
cap.scpi_send = lambda *args, **kwargs: None
cap.r10_wait_memory_save = lambda sock: {"completed": False, "elapsed_s": 180.0, "history": []}
save = cap.r10_save_stopped_memory_waveform(object(), "C:/" + name)
assert save["success"] is False
assert save["deferred_candidate"] is True
assert save["status"] == "pending_ftp_verification"
assert cap.R10_ALLOW_DEFERRED_MEMORY_SAVE_CONFIRMATION is False
wrapper_text = (ROOT / "mho984_sync_r12_11e_gui_settings.py").read_text(encoding="utf-8")
assert "capture.R10_ALLOW_DEFERRED_MEMORY_SAVE_CONFIRMATION = True" in wrapper_text
print("firmware_save_compat_selftest: PASS")
