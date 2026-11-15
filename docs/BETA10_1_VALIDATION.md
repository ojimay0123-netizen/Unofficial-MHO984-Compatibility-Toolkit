# beta.10.1 validation

2026-11-15: maintainer/user reported successful operation of r12.7i with the actual failing BIN and Windows hardware. This is a user-reported result; the real BIN was not supplied for independent execution here.

Original report: CH2 payload offset 500,000,328; expected payload 500,000,000; available 496,000,000; missing 4,000,000 bytes (0.8%). File declared 1,000,000,328; actual 996,000,328. Expected recovered common prefix: 124,000,000 float32 points. Exact exported point count was not separately reported by the user.

Possible acquisition-in-progress save was suggested by the user, not established as the cause. Full firmware version, exact sample rate/timebase, Python version, and RAM/time measurements were not supplied. No general hardware pass is claimed for other models, LA captures, or larger tail losses.

Automated synthetic coverage: normal BIN; strict rejection; default recovery; shorter declared EOF; >1% loss; unaligned loss; LA rejection; common-prefix metadata and exact sample values; original BIN unchanged; CLI defaults/strict/legacy option; warning file. Run `python tests/analog_tail_selftest.py`.
