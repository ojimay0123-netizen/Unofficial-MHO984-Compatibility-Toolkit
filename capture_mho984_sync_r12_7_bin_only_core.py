# SPDX-License-Identifier: MIT
# Independent, unofficial compatibility tool; not affiliated with RIGOL.
"""
RIGOL MHO984 LAN waveform acquisition tool.

This program acquires the analog channels and the D0-D15 logic channels from
the same stopped acquisition record.

Analog data:
    analog/chan1.bin ... analog/chan4.bin   all readable internal-memory points, WORD format
    analog/chan1_pre.txt ... analog/chan4_pre.txt

Digital data:
    D0-D15 are decoded on the PC from the LA record stored inside the same
    RG03 Memory BIN as the analog channels.  On the validated MHO984 capture
    format, the lower 16 bits of each LA uint32 sample hold D0-D15.

Important:
    This public compatibility tool intentionally does not issue per-channel
    D0-D15 waveform queries, BUS event-table scans, horizontal timebase
    changes, or LA/POD reconfiguration during acquisition.  It performs one
    fresh SINGLE acquisition, waits for STOP, saves one instrument Memory BIN,
    then decodes both Analog and Digital from that same stopped record on the PC.
    The RG03 layout is an empirically validated compatibility interpretation,
    not a vendor-published file-format specification.

Python 3.9 or later is recommended.
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import shutil
import socket
import struct
import time
import traceback
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple


# ============================================================================
# User settings
# ============================================================================

CAPTURE_TOOL_VERSION = "2026.10.04-r12.7-public-beta5-fw-save-compat"

OSC_IP = "127.0.0.1"
OSC_PORT = 5555

BASE_DIR = Path(r"C:\temp")
DATASET_PREFIX = "mho984_"
DIGITS = 4

ANALOG_CHANNELS = ["CHAN1", "CHAN2", "CHAN3", "CHAN4"]
DIGITAL_CHANNELS = [f"D{i}" for i in range(16)]
DIGITAL_BUS = 1

# MHO984 firmware/UI safety mode.  The instrument-side RIGOL.SCOPE app can
# become unstable when the stopped record is repeatedly re-scaled while the
# Parallel BUS event table is toggled and queried.  Keep the acquired SINGLE
# record untouched after STOP.  This preserves analog/digital time relation;
# however, BUS:DATA? may cover only the decoder table already available in the
# instrument and is therefore validated as partial when appropriate.
MHO984_SAFE_SCOPE_MODE = True

# True: acquire only analog channels currently displayed on the oscilloscope.
# False: acquire CHAN1-CHAN4 regardless of their display state.
CAPTURE_ONLY_DISPLAYED_ANALOG = True

# WORD preserves the MHO984's vertical resolution (two bytes per point).
# BYTE is faster and smaller, but reduces each point to eight bits.
ANALOG_WAVEFORM_FORMAT = "WORD"

# Maximum number of analog points requested in one SCPI block. The complete
# internal-memory record is still read; this only controls transfer chunk size.
# If the firmware or network is unstable, reduce this value.
ANALOG_CHUNK_POINTS = 1_000_000

# Refuse to start a channel transfer when the final file cannot fit with this
# amount of free space left. This avoids silently producing a truncated file.
MIN_FREE_DISK_MARGIN_BYTES = 256 * 1024 * 1024

CONNECT_TIMEOUT_S = 5.0
ASCII_QUERY_TIMEOUT_S = 5.0
ANALOG_BLOCK_TIMEOUT_S = 120.0
DIGITAL_BLOCK_TIMEOUT_S = 120.0
TERMINATOR_TIMEOUT_S = 2.0

# :BUSn:DATA? returns the decoder event table rather than sampled D0-D15 RAW
# memory. Expand the view before :SINGle, then refresh the event table while the
# acquired record is stopped after its exact analog time range is known.
DIGITAL_EXPAND_VIEW_TO_ANALOG_RECORD = False
DIGITAL_REFRESH_EVENT_TABLE_AFTER_STOP = False
DIGITAL_VIEW_MARGIN_RATIO = 0.01
DIGITAL_EVENT_TABLE_SETTLE_S = 1.0
DIGITAL_TIMEBASE_MAX_ADJUSTMENTS = 5

# The test instrument returned exactly 12,000 rows while covering only 1.2%
# of the analog record. Treat that exact row count as an empirical event-table
# saturation threshold and scan the stopped record in smaller time slices.
# This is deliberately configurable because RIGOL does not publish the row cap
# in the MHO900 programming guide.
DIGITAL_SEGMENTED_CAPTURE_ENABLED = False
DIGITAL_EVENT_TABLE_SATURATION_ROWS = 12_000
DIGITAL_SEGMENT_TARGET_ROWS = 8_000
DIGITAL_SEGMENT_VIEW_MARGIN_RATIO = 0.04
DIGITAL_SEGMENT_SETTLE_S = 0.50
DIGITAL_SEGMENT_RETRIES = 2
DIGITAL_SEGMENT_MAX_COUNT = 4096
DIGITAL_SEGMENT_MAX_SPLIT_DEPTH = 14
DIGITAL_SEGMENT_MIN_DURATION_S = 1e-12

# This installation is expected to have digital transitions throughout the
# record. If the returned event table spans less than this fraction of the
# analog record, mark the digital result incomplete and fail the overall run.
# Set DIGITAL_ACTIVITY_EXPECTED_ACROSS_RECORD to False for captures that can
# legitimately be idle for most of the record (for example, a one-shot burst).
DIGITAL_ACTIVITY_EXPECTED_ACROSS_RECORD = True
DIGITAL_MIN_EVENT_SPAN_RATIO = 0.90

# Wait for a single trigger. Public-release default is fail-safe: do NOT force
# a trigger automatically. The public controller can opt in explicitly with
# --force-trigger-on-timeout.
PRE_ACQUISITION_STOP_TIMEOUT_S = 10.0
STOP_SETTLE_DELAY_S = 0.10
# Ignore an immediate residual STOP indication for this long unless an active
# trigger state (WAIT/RUN/AUTO/TD) has already been observed after :SINGle.
SINGLE_INITIAL_STOP_GUARD_S = 0.50
SINGLE_TRIGGER_TIMEOUT_S = 30.0
FORCE_TRIGGER_ON_TIMEOUT = False
FORCED_TRIGGER_STOP_TIMEOUT_S = 10.0
TRIGGER_POLL_INTERVAL_S = 0.10

# Optional interactive decision hook used only by the integrated public GUI.
# The callback receives a small diagnostic dict and returns one of:
#   "force" -> send :TFORce once and wait for STOP
#   "wait"  -> extend the normal timeout and keep waiting
#   "abort" -> fail with TriggerTimeoutError
# Headless/CLI operation leaves this as None and therefore preserves the
# fail-safe public default.
TRIGGER_TIMEOUT_DECISION_CALLBACK: Optional[Callable[[Dict[str, Any]], str]] = None

# None keeps the threshold already configured on the oscilloscope.
# Set a voltage (for example 1.65) when the script must control the threshold.
POD_THRESHOLDS_V: Dict[int, Optional[float]] = {
    1: None,  # D0-D7
    2: None,  # D8-D15
}

# Restore the settings that this program changes after all files are saved.
RESTORE_INSTRUMENT_STATE = False

# Prevent accidental operation against another instrument.
VERIFY_MODEL = True
EXPECTED_MODEL_TOKEN = "MHO984"

# Defensive protocol limits.
MAX_ASCII_RESPONSE_BYTES = 1_048_576
MAX_SCPI_ERROR_COUNT = 100
MAX_REPORTED_PARSE_ERRORS = 50
SCPI_STREAM_CHUNK_BYTES = 1 * 1024 * 1024


# ============================================================================
# Exceptions
# ============================================================================


class AcquisitionError(RuntimeError):
    """Base exception for acquisition failures."""


class SCPIProtocolError(AcquisitionError):
    """The received SCPI response did not follow the expected framing."""


class SCPICommandError(AcquisitionError):
    """The instrument placed one or more errors in its SCPI error queue."""


class DataValidationError(AcquisitionError):
    """Received data failed a size, format, or ordering check."""


class TriggerTimeoutError(AcquisitionError):
    """A single acquisition did not reach STOP before the timeout."""


# ============================================================================
# Data classes
# ============================================================================


@dataclass
class WaveformPreamble:
    fmt: int
    mode: int
    points: int
    count: int
    xinc: float
    xorig: float
    xref: float
    yinc: float
    yorig: float
    yref: float

    def to_text(self) -> str:
        """Serialize in the ten-field format expected by the viewer."""

        return ",".join(
            [
                str(self.fmt),
                str(self.mode),
                str(self.points),
                str(self.count),
                f"{self.xinc:.17g}",
                f"{self.xorig:.17g}",
                f"{self.xref:.17g}",
                f"{self.yinc:.17g}",
                f"{self.yorig:.17g}",
                f"{self.yref:.17g}",
            ]
        )

    @classmethod
    def parse(cls, text: str) -> "WaveformPreamble":
        fields = [part.strip() for part in text.strip().split(",")]
        if len(fields) != 10:
            raise DataValidationError(
                f"Waveform preamble must contain 10 fields, got {len(fields)}: "
                f"{text!r}"
            )

        try:
            return cls(
                fmt=int(float(fields[0])),
                mode=int(float(fields[1])),
                points=int(float(fields[2])),
                count=int(float(fields[3])),
                xinc=float(fields[4]),
                xorig=float(fields[5]),
                xref=float(fields[6]),
                yinc=float(fields[7]),
                yorig=float(fields[8]),
                yref=float(fields[9]),
            )
        except ValueError as exc:
            raise DataValidationError(
                f"Invalid numeric value in waveform preamble: {text!r}"
            ) from exc


@dataclass
class InstrumentState:
    trigger_status: str
    trigger_sweep: str
    la_enabled: bool
    pod_display: Dict[int, bool]
    pod_threshold_v: Dict[int, float]
    digital_enabled: Dict[str, bool]
    bus_display: bool
    bus_mode: str
    bus_format: str
    bus_event: bool
    parallel: Dict[str, Any]
    waveform: Dict[str, str]
    timebase: Dict[str, Any]


# ============================================================================
# General file helpers
# ============================================================================


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def next_dataset_dir(base: Path, prefix: str, digits: int) -> Path:
    """Create a new monotonically numbered dataset directory."""

    base.mkdir(parents=True, exist_ok=True)
    max_n = 0

    for path in base.iterdir():
        if not path.is_dir() or not path.name.lower().startswith(prefix.lower()):
            continue

        suffix = path.name[len(prefix) :]
        if suffix.isdigit():
            max_n = max(max_n, int(suffix))

    # Retry protects against two processes selecting the same number.
    number = max_n + 1
    while True:
        dataset_dir = base / f"{prefix}{number:0{digits}d}"
        try:
            dataset_dir.mkdir(parents=True, exist_ok=False)
            return dataset_dir
        except FileExistsError:
            number += 1


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_lines(path: Path, lines: Iterable[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ============================================================================
# SCPI communication
# ============================================================================


def scpi_send(sock: socket.socket, command: str) -> None:
    sock.sendall((command.rstrip("\r\n") + "\n").encode("ascii"))


def recv_exact(sock: socket.socket, byte_count: int) -> bytes:
    if byte_count < 0:
        raise ValueError("byte_count must be non-negative")

    output = bytearray()
    while len(output) < byte_count:
        chunk = sock.recv(min(65_536, byte_count - len(output)))
        if not chunk:
            raise ConnectionError(
                f"Socket closed after {len(output):,}/{byte_count:,} bytes"
            )
        output.extend(chunk)
    return bytes(output)


def scpi_query_line(
    sock: socket.socket,
    command: str,
    timeout: float = ASCII_QUERY_TIMEOUT_S,
) -> str:
    """Send an ASCII query and receive exactly one LF-terminated response."""

    sock.settimeout(timeout)
    scpi_send(sock, command)

    data = bytearray()
    while b"\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError(
                f"Socket closed while reading the response to {command!r}"
            )
        data.extend(chunk)

        if len(data) > MAX_ASCII_RESPONSE_BYTES:
            raise SCPIProtocolError(
                f"ASCII response to {command!r} exceeded "
                f"{MAX_ASCII_RESPONSE_BYTES:,} bytes"
            )

    line, remainder = bytes(data).split(b"\n", 1)
    if remainder:
        raise SCPIProtocolError(
            f"Unexpected bytes after ASCII response to {command!r}: "
            f"{remainder[:64]!r}"
        )

    return line.rstrip(b"\r").decode("ascii", errors="replace").strip()


def _read_non_block_response(
    sock: socket.socket,
    command: str,
    first_byte: bytes,
) -> bytes:
    """Read a short ASCII response used for a useful protocol error message."""

    data = bytearray(first_byte)
    while not data.endswith(b"\n") and len(data) < 4096:
        try:
            chunk = sock.recv(4096 - len(data))
        except socket.timeout:
            break
        if not chunk:
            break
        data.extend(chunk)

    text = bytes(data).decode("ascii", errors="replace").strip()
    raise SCPIProtocolError(
        f"{command!r} did not return an IEEE/TMC block; response={text!r}"
    )


def _consume_block_terminator(sock: socket.socket, command: str) -> None:
    """Consume and validate LF or CRLF following an IEEE/TMC block."""

    previous_timeout = sock.gettimeout()
    sock.settimeout(TERMINATOR_TIMEOUT_S)
    try:
        try:
            first = recv_exact(sock, 1)
        except socket.timeout as exc:
            raise SCPIProtocolError(
                f"Missing terminator after the IEEE/TMC response to {command!r}"
            ) from exc

        if first == b"\n":
            return

        if first == b"\r":
            try:
                second = recv_exact(sock, 1)
            except socket.timeout as exc:
                raise SCPIProtocolError(
                    f"Incomplete CRLF terminator after {command!r}"
                ) from exc
            if second == b"\n":
                return
            raise SCPIProtocolError(
                f"Invalid CRLF terminator after {command!r}: "
                f"{first + second!r}"
            )

        raise SCPIProtocolError(
            f"Invalid block terminator after {command!r}: {first!r}"
        )
    finally:
        sock.settimeout(previous_timeout)


def _begin_ieee_block_response(
    sock: socket.socket,
    command: str,
    timeout: float,
    max_payload_bytes: Optional[int],
) -> int:
    """Send a query and return its definite-length IEEE block payload size."""

    if max_payload_bytes is not None and max_payload_bytes < 0:
        raise ValueError("max_payload_bytes must be non-negative")

    sock.settimeout(timeout)
    scpi_send(sock, command)

    first = recv_exact(sock, 1)
    if first != b"#":
        return _read_non_block_response(sock, command, first)

    digit_byte = recv_exact(sock, 1)
    if digit_byte < b"1" or digit_byte > b"9":
        raise SCPIProtocolError(
            f"Unsupported IEEE/TMC length digit after {command!r}: "
            f"{digit_byte!r}"
        )

    length_digit_count = int(digit_byte.decode("ascii"))
    length_text = recv_exact(sock, length_digit_count)
    if not length_text.isdigit():
        raise SCPIProtocolError(
            f"Invalid IEEE/TMC payload length after {command!r}: "
            f"{length_text!r}"
        )

    payload_length = int(length_text.decode("ascii"))
    if (
        max_payload_bytes is not None
        and payload_length > max_payload_bytes
    ):
        raise SCPIProtocolError(
            f"IEEE/TMC payload from {command!r} declares "
            f"{payload_length:,} bytes; limit is {max_payload_bytes:,}"
        )

    return payload_length


def scpi_query_ieee_block(
    sock: socket.socket,
    command: str,
    timeout: float,
    max_payload_bytes: Optional[int],
) -> bytes:
    """
    Read one definite-length IEEE 488.2/TMC block into memory:

        #<number-of-length-digits><payload-length><payload><LF-or-CRLF>
    """

    payload_length = _begin_ieee_block_response(
        sock,
        command,
        timeout,
        max_payload_bytes,
    )

    payload = recv_exact(sock, payload_length)
    _consume_block_terminator(sock, command)
    return payload


def scpi_query_ieee_block_to_file(
    sock: socket.socket,
    command: str,
    destination: Path,
    timeout: float,
    max_payload_bytes: Optional[int] = None,
) -> int:
    """
    Stream one complete IEEE 488.2/TMC payload directly to a file.

    The header supports up to nine payload-length digits, so omitting the
    optional software limit still remains bounded by the protocol framing.
    """

    payload_length = _begin_ieee_block_response(
        sock,
        command,
        timeout,
        max_payload_bytes,
    )

    received = 0
    with destination.open("wb") as output:
        while received < payload_length:
            chunk = sock.recv(
                min(
                    SCPI_STREAM_CHUNK_BYTES,
                    payload_length - received,
                )
            )
            if not chunk:
                raise ConnectionError(
                    f"Socket closed after {received:,}/"
                    f"{payload_length:,} bytes while reading {command!r}"
                )
            output.write(chunk)
            received += len(chunk)
        output.flush()

    _consume_block_terminator(sock, command)

    actual_size = destination.stat().st_size
    if actual_size != payload_length:
        raise DataValidationError(
            f"Streamed payload size mismatch for {command!r}: "
            f"declared {payload_length:,}, saved {actual_size:,} bytes"
        )
    return payload_length


# ============================================================================
# SCPI error handling and validation
# ============================================================================


SCPI_ERROR_RE = re.compile(r"^\s*([+-]?\d+)\s*(?:,\s*)?(.*)$")


def drain_scpi_errors(
    sock: socket.socket,
    max_count: int = MAX_SCPI_ERROR_COUNT,
) -> List[str]:
    """Read the complete SCPI error queue until error code 0."""

    errors: List[str] = []

    for _ in range(max_count):
        response = scpi_query_line(sock, ":SYSTem:ERRor?")
        match = SCPI_ERROR_RE.match(response)
        if not match:
            raise SCPIProtocolError(
                f"Cannot parse :SYSTem:ERRor? response: {response!r}"
            )

        code = int(match.group(1))
        if code == 0:
            return errors
        errors.append(response)

    raise SCPICommandError(
        f"SCPI error queue did not become empty after {max_count} entries"
    )


def ensure_no_scpi_errors(sock: socket.socket, context: str) -> None:
    errors = drain_scpi_errors(sock)
    if errors:
        joined = " | ".join(errors)
        raise SCPICommandError(f"{context}: {joined}")


def send_commands_checked(
    sock: socket.socket,
    commands: Sequence[str],
    context: str,
) -> None:
    """
    Send a command group and verify that the instrument, rather than merely the
    local TCP stack, accepted it.
    """

    for command in commands:
        scpi_send(sock, command)
    ensure_no_scpi_errors(sock, context)


def parse_scpi_bool(text: str) -> bool:
    normalized = text.strip().upper()
    if normalized in {"1", "ON", "TRUE"}:
        return True
    if normalized in {"0", "OFF", "FALSE"}:
        return False
    raise SCPIProtocolError(f"Invalid SCPI Boolean response: {text!r}")


def query_scpi_bool(sock: socket.socket, command: str) -> bool:
    return parse_scpi_bool(scpi_query_line(sock, command))


def bool_parameter(value: bool) -> str:
    return "ON" if value else "OFF"


def normalize_response(text: str) -> str:
    return text.strip().upper()


def require_response(
    actual: str,
    expected: str,
    description: str,
    allow_prefix: bool = False,
) -> None:
    actual_normalized = normalize_response(actual)
    expected_normalized = normalize_response(expected)

    matches = (
        actual_normalized.startswith(expected_normalized)
        if allow_prefix
        else actual_normalized == expected_normalized
    )
    if not matches:
        raise DataValidationError(
            f"{description}: expected {expected!r}, got {actual!r}"
        )


def parse_positive_scpi_integer(text: str, description: str) -> int:
    """Parse integer or scientific-notation SCPI values without truncation."""

    try:
        numeric = float(text.strip())
        value = int(numeric)
    except (ValueError, OverflowError) as exc:
        raise SCPIProtocolError(
            f"{description} is not a finite integer: {text!r}"
        ) from exc

    if value <= 0 or numeric != value:
        raise SCPIProtocolError(
            f"{description} must be a positive integer, got {text!r}"
        )
    return value


def ensure_free_disk_space(
    directory: Path,
    required_bytes: int,
    description: str,
) -> int:
    """Require enough room for a complete output plus a safety margin."""

    free_bytes = shutil.disk_usage(directory).free
    minimum_bytes = required_bytes + MIN_FREE_DISK_MARGIN_BYTES
    if free_bytes < minimum_bytes:
        raise DataValidationError(
            f"Insufficient free disk space for {description}: need at least "
            f"{minimum_bytes:,} bytes including margin, have "
            f"{free_bytes:,} bytes"
        )
    return free_bytes


# ============================================================================
# Instrument state capture and restoration
# ============================================================================


def capture_instrument_state(sock: socket.socket) -> InstrumentState:
    """Capture settings changed by this program so they can be restored."""

    trigger_status = normalize_response(
        scpi_query_line(sock, ":TRIGger:STATus?")
    )
    trigger_sweep = normalize_response(
        scpi_query_line(sock, ":TRIGger:SWEep?")
    )

    la_enabled = query_scpi_bool(sock, ":LA:ENABle?")
    pod_display = {
        pod: query_scpi_bool(sock, f":LA:POD{pod}:DISPlay?")
        for pod in (1, 2)
    }
    pod_threshold_v = {
        pod: float(scpi_query_line(sock, f":LA:POD{pod}:THReshold?"))
        for pod in (1, 2)
    }
    digital_enabled = {
        channel: query_scpi_bool(
            sock, f":LA:DIGital:ENABle? {channel}"
        )
        for channel in DIGITAL_CHANNELS
    }

    bus = f":BUS{DIGITAL_BUS}"
    bus_display = query_scpi_bool(sock, f"{bus}:DISPlay?")
    bus_mode = normalize_response(scpi_query_line(sock, f"{bus}:MODE?"))
    bus_format = normalize_response(scpi_query_line(sock, f"{bus}:FORMat?"))
    bus_event = query_scpi_bool(sock, f"{bus}:EVENt?")

    parallel: Dict[str, Any] = {}
    if bus_mode.startswith("PAR"):
        parallel = {
            "bus": normalize_response(
                scpi_query_line(sock, f"{bus}:PARallel:BUS?")
            ),
            "clock": normalize_response(
                scpi_query_line(sock, f"{bus}:PARallel:CLK?")
            ),
            "slope": normalize_response(
                scpi_query_line(sock, f"{bus}:PARallel:SLOPe?")
            ),
            "endian": normalize_response(
                scpi_query_line(sock, f"{bus}:PARallel:ENDian?")
            ),
            "polarity": normalize_response(
                scpi_query_line(sock, f"{bus}:PARallel:POLarity?")
            ),
        }

        if parallel["bus"] == "USER":
            selected_bit = int(
                float(scpi_query_line(sock, f"{bus}:PARallel:BITX?"))
            )
            width = int(
                float(scpi_query_line(sock, f"{bus}:PARallel:WIDTh?"))
            )
            bit_sources: Dict[str, str] = {}

            for bit in range(width):
                scpi_send(sock, f"{bus}:PARallel:BITX {bit}")
                bit_sources[str(bit)] = normalize_response(
                    scpi_query_line(sock, f"{bus}:PARallel:SOURce?")
                )

            scpi_send(sock, f"{bus}:PARallel:BITX {selected_bit}")
            parallel.update(
                {
                    "width": width,
                    "selected_bit": selected_bit,
                    "bit_sources": bit_sources,
                }
            )

    waveform = {
        "source": normalize_response(
            scpi_query_line(sock, ":WAVeform:SOURce?")
        ),
        "mode": normalize_response(scpi_query_line(sock, ":WAVeform:MODE?")),
        "format": normalize_response(
            scpi_query_line(sock, ":WAVeform:FORMat?")
        ),
        "points": scpi_query_line(sock, ":WAVeform:POINts?"),
        "start": scpi_query_line(sock, ":WAVeform:STARt?"),
        "stop": scpi_query_line(sock, ":WAVeform:STOP?"),
    }

    timebase = {
        "scale_s_per_div": float(
            scpi_query_line(sock, ":TIMebase:MAIN:SCALe?")
        ),
        "offset_s": float(
            scpi_query_line(sock, ":TIMebase:MAIN:OFFSet?")
        ),
        "vernier": query_scpi_bool(sock, ":TIMebase:VERNier?"),
    }
    if (
        not math.isfinite(timebase["scale_s_per_div"])
        or timebase["scale_s_per_div"] <= 0
        or not math.isfinite(timebase["offset_s"])
    ):
        raise SCPIProtocolError(
            f"Invalid initial timebase response: {timebase!r}"
        )

    ensure_no_scpi_errors(sock, "capturing initial instrument state")

    return InstrumentState(
        trigger_status=trigger_status,
        trigger_sweep=trigger_sweep,
        la_enabled=la_enabled,
        pod_display=pod_display,
        pod_threshold_v=pod_threshold_v,
        digital_enabled=digital_enabled,
        bus_display=bus_display,
        bus_mode=bus_mode,
        bus_format=bus_format,
        bus_event=bus_event,
        parallel=parallel,
        waveform=waveform,
        timebase=timebase,
    )


def restore_instrument_state(
    sock: socket.socket,
    state: InstrumentState,
) -> Dict[str, Any]:
    """
    Best-effort restoration. Failures are returned in the log and do not hide
    an earlier acquisition exception.
    """

    commands: List[str] = []
    transport_errors: List[str] = []

    def send(command: str) -> None:
        commands.append(command)
        try:
            scpi_send(sock, command)
        except Exception as exc:  # restoration must continue where possible
            transport_errors.append(f"{command}: {exc!r}")

    # Restore the horizontal view changed to decode the complete memory range.
    # Fine adjustment is enabled temporarily so an arbitrary original scale can
    # be restored exactly, then returned to its original state.
    timebase = state.timebase
    send(":TIMebase:VERNier ON")
    send(
        ":TIMebase:MAIN:SCALe "
        f"{float(timebase['scale_s_per_div']):.17g}"
    )
    send(
        ":TIMebase:MAIN:OFFSet "
        f"{float(timebase['offset_s']):.17g}"
    )
    send(
        ":TIMebase:VERNier "
        f"{bool_parameter(bool(timebase['vernier']))}"
    )

    # Restore waveform-read settings after the timebase because changing the
    # horizontal scale can also change the accepted waveform point range.
    waveform = state.waveform
    send(f":WAVeform:SOURce {waveform['source']}")
    send(f":WAVeform:MODE {waveform['mode']}")
    send(f":WAVeform:FORMat {waveform['format']}")
    send(f":WAVeform:POINts {waveform['points']}")
    send(f":WAVeform:STARt {waveform['start']}")
    send(f":WAVeform:STOP {waveform['stop']}")

    # Decoder settings. Temporarily display the bus because EVENT requires an
    # enabled decoder bus on this instrument.
    bus = f":BUS{DIGITAL_BUS}"
    send(f"{bus}:DISPlay ON")
    send(f"{bus}:MODE {state.bus_mode}")
    send(f"{bus}:FORMat {state.bus_format}")

    if state.bus_mode.startswith("PAR") and state.parallel:
        parallel = state.parallel
        send(f"{bus}:PARallel:BUS {parallel['bus']}")

        if parallel["bus"] == "USER":
            send(f"{bus}:PARallel:WIDTh {parallel['width']}")
            for bit_text, source in parallel["bit_sources"].items():
                send(f"{bus}:PARallel:BITX {bit_text}")
                send(f"{bus}:PARallel:SOURce {source}")
            send(f"{bus}:PARallel:BITX {parallel['selected_bit']}")

        send(f"{bus}:PARallel:CLK {parallel['clock']}")
        send(f"{bus}:PARallel:SLOPe {parallel['slope']}")
        send(f"{bus}:PARallel:ENDian {parallel['endian']}")
        send(f"{bus}:PARallel:POLarity {parallel['polarity']}")

    send(f"{bus}:EVENt {bool_parameter(state.bus_event)}")
    send(f"{bus}:DISPlay {bool_parameter(state.bus_display)}")

    # Logic analyzer settings.
    send(":LA:ENABle ON")
    for pod in (1, 2):
        send(f":LA:POD{pod}:THReshold {state.pod_threshold_v[pod]:.12g}")
        send(":LA:POD{}:DISPlay ON".format(pod))

    for channel in DIGITAL_CHANNELS:
        send(
            f":LA:DIGital:ENABle {channel},"
            f"{bool_parameter(state.digital_enabled[channel])}"
        )

    for pod in (1, 2):
        send(
            f":LA:POD{pod}:DISPlay "
            f"{bool_parameter(state.pod_display[pod])}"
        )
    send(f":LA:ENABle {bool_parameter(state.la_enabled)}")

    # :SINGLE changes the trigger sweep. Restore sweep and run/stop state last.
    send(f":TRIGger:SWEep {state.trigger_sweep}")
    if state.trigger_status == "STOP":
        send(":STOP")
    else:
        send(":RUN")

    scpi_errors: List[str] = []
    error_query_failure: Optional[str] = None
    try:
        scpi_errors = drain_scpi_errors(sock)
    except Exception as exc:
        error_query_failure = repr(exc)

    success = (
        not transport_errors
        and not scpi_errors
        and error_query_failure is None
    )
    return {
        "success": success,
        "commands": commands,
        "transport_errors": transport_errors,
        "scpi_errors": scpi_errors,
        "error_query_failure": error_query_failure,
    }


# ============================================================================
# Channel selection and digital configuration
# ============================================================================


def analog_display_query(channel: str) -> str:
    number = int(channel.replace("CHAN", ""))
    return f":CHANnel{number}:DISPlay?"


def select_analog_channels(sock: socket.socket) -> Tuple[List[str], List[str]]:
    selected: List[str] = []
    log: List[str] = []

    for channel in ANALOG_CHANNELS:
        displayed = query_scpi_bool(sock, analog_display_query(channel))
        log.append(f"{channel} displayed={int(displayed)}")
        if displayed or not CAPTURE_ONLY_DISPLAYED_ANALOG:
            selected.append(channel)

    ensure_no_scpi_errors(sock, "querying analog channel display states")
    return selected, log


def configure_digital_acquisition(sock: socket.socket) -> Dict[str, Any]:
    """
    Configure D0-D15 and a 16-bit Parallel decoder before starting the new
    acquisition. Decimal output makes the event parser unambiguous.
    """

    la_commands = [
        ":LA:ENABle ON",
        ":LA:POD1:DISPlay ON",
        ":LA:POD2:DISPlay ON",
    ]
    la_commands.extend(
        f":LA:DIGital:ENABle {channel},ON"
        for channel in DIGITAL_CHANNELS
    )

    for pod, threshold in POD_THRESHOLDS_V.items():
        if threshold is not None:
            la_commands.append(
                f":LA:POD{pod}:THReshold {float(threshold):.12g}"
            )

    send_commands_checked(
        sock,
        la_commands,
        "configuring logic-analyzer channels",
    )

    bus = f":BUS{DIGITAL_BUS}"
    bus_commands: List[str] = []
    def send_bus_command(command: str) -> None:
        # Check each command separately. If a firmware-specific value is
        # rejected, the acquisition log identifies the exact command instead
        # of only reporting that one command in a large group failed.
        send_commands_checked(
            sock,
            [command],
            f"configuring Parallel BUS with {command!r}",
        )
        bus_commands.append(command)

    for command in (
        f"{bus}:DISPlay ON",
        f"{bus}:MODE PARallel",
        f"{bus}:PARallel:BUS D0D15",
        f"{bus}:PARallel:CLK OFF",
        f"{bus}:PARallel:SLOPe BOTH",
    ):
        send_bus_command(command)

    # The MHO900 programming guide describes Parallel ENDian as POS/NEG, but
    # MHO984 firmware 00.01.00 has also been observed to report and accept
    # LSB/MSB. Select the target using the vocabulary actually returned by the
    # connected instrument. LSB and POS both represent the non-reversed D0-D15
    # interpretation required by the output files.
    endian_before = normalize_response(
        scpi_query_line(sock, f"{bus}:PARallel:ENDian?")
    )
    ensure_no_scpi_errors(sock, "detecting Parallel BUS endian vocabulary")
    if endian_before in {"LSB", "MSB"}:
        endian_vocabulary = "LSB_MSB"
        expected_endian = "LSB"
        endian_command = f"{bus}:PARallel:ENDian LSB"
    elif endian_before in {"POS", "NEG"}:
        endian_vocabulary = "POS_NEG"
        expected_endian = "POS"
        endian_command = f"{bus}:PARallel:ENDian POSitive"
    else:
        raise SCPIProtocolError(
            "Unsupported Parallel BUS endian response: "
            f"{endian_before!r}"
        )
    send_bus_command(endian_command)

    for command in (
        f"{bus}:PARallel:POLarity POSitive",
        f"{bus}:FORMat DEC",
        f"{bus}:EVENt ON",
    ):
        send_bus_command(command)

    # Read back and enforce every setting needed for a valid acquisition.
    la_enabled = query_scpi_bool(sock, ":LA:ENABle?")
    pod_display = {
        pod: query_scpi_bool(sock, f":LA:POD{pod}:DISPlay?")
        for pod in (1, 2)
    }
    pod_threshold_v = {
        pod: float(scpi_query_line(sock, f":LA:POD{pod}:THReshold?"))
        for pod in (1, 2)
    }
    channel_states = {
        channel: query_scpi_bool(
            sock, f":LA:DIGital:ENABle? {channel}"
        )
        for channel in DIGITAL_CHANNELS
    }

    bus_configuration = {
        "mode": normalize_response(scpi_query_line(sock, f"{bus}:MODE?")),
        "display": query_scpi_bool(sock, f"{bus}:DISPlay?"),
        "source": normalize_response(
            scpi_query_line(sock, f"{bus}:PARallel:BUS?")
        ),
        "clock": normalize_response(
            scpi_query_line(sock, f"{bus}:PARallel:CLK?")
        ),
        "slope": normalize_response(
            scpi_query_line(sock, f"{bus}:PARallel:SLOPe?")
        ),
        "endian": normalize_response(
            scpi_query_line(sock, f"{bus}:PARallel:ENDian?")
        ),
        "endian_before": endian_before,
        "endian_vocabulary": endian_vocabulary,
        "polarity": normalize_response(
            scpi_query_line(sock, f"{bus}:PARallel:POLarity?")
        ),
        "format": normalize_response(
            scpi_query_line(sock, f"{bus}:FORMat?")
        ),
        "event": query_scpi_bool(sock, f"{bus}:EVENt?"),
    }

    ensure_no_scpi_errors(sock, "verifying digital configuration")

    if not la_enabled:
        raise DataValidationError("Logic analyzer did not become enabled")
    if not all(pod_display.values()):
        raise DataValidationError(f"One or more PODs are disabled: {pod_display}")
    disabled = [
        channel
        for channel, enabled in channel_states.items()
        if not enabled
    ]
    if disabled:
        raise DataValidationError(
            "Digital channels did not become enabled: " + ", ".join(disabled)
        )

    require_response(
        bus_configuration["mode"],
        "PAR",
        "Parallel BUS mode",
        allow_prefix=True,
    )
    if not bus_configuration["display"]:
        raise DataValidationError("Parallel BUS display did not become enabled")
    require_response(
        bus_configuration["source"], "D0D15", "Parallel BUS source"
    )
    require_response(
        bus_configuration["clock"], "OFF", "Parallel BUS clock"
    )
    require_response(
        bus_configuration["format"], "DEC", "Parallel BUS format"
    )
    if not bus_configuration["event"]:
        raise DataValidationError("Parallel BUS event table is disabled")
    require_response(
        bus_configuration["endian"],
        expected_endian,
        "Parallel BUS endian",
        allow_prefix=True,
    )
    require_response(
        bus_configuration["polarity"],
        "POS",
        "Parallel BUS polarity",
        allow_prefix=True,
    )

    return {
        "la_enabled": la_enabled,
        "pod_display": pod_display,
        "pod_threshold_v": pod_threshold_v,
        "digital_channels": channel_states,
        "bus": bus_configuration,
        "commands": {
            "logic_analyzer": la_commands,
            "parallel_bus": bus_commands,
        },
    }


# ============================================================================
# Triggered acquisition
# ============================================================================


VALID_TRIGGER_STATES = {"TD", "WAIT", "RUN", "AUTO", "STOP"}
ACTIVE_TRIGGER_STATES = VALID_TRIGGER_STATES - {"STOP"}


def query_acquisition_snapshot(
    sock: socket.socket,
    context: str,
) -> Dict[str, Any]:
    """Read the parameters that determine record completion and file size."""

    status = normalize_response(
        scpi_query_line(sock, ":TRIGger:STATus?")
    )
    if status not in VALID_TRIGGER_STATES:
        raise SCPIProtocolError(f"Unexpected trigger status: {status!r}")

    sweep = normalize_response(
        scpi_query_line(sock, ":TRIGger:SWEep?")
    )
    memory_depth_text = scpi_query_line(sock, ":ACQuire:MDEPth?")
    memory_depth_points = parse_positive_scpi_integer(
        memory_depth_text,
        f"{context} acquisition memory depth",
    )
    sample_rate_text = scpi_query_line(sock, ":ACQuire:SRATe?")
    try:
        sample_rate_sps = float(sample_rate_text)
    except ValueError as exc:
        raise SCPIProtocolError(
            f"{context} sample rate is invalid: {sample_rate_text!r}"
        ) from exc
    if not math.isfinite(sample_rate_sps) or sample_rate_sps <= 0:
        raise SCPIProtocolError(
            f"{context} sample rate must be positive: "
            f"{sample_rate_text!r}"
        )

    ensure_no_scpi_errors(sock, f"reading {context} acquisition parameters")
    return {
        "trigger_status": status,
        "trigger_sweep": sweep,
        "memory_depth_query": memory_depth_text,
        "memory_depth_points": memory_depth_points,
        "sample_rate_query": sample_rate_text,
        "sample_rate_sps": sample_rate_sps,
    }


def stop_and_confirm_measurement(
    sock: socket.socket,
    context: str,
    phase: str,
    log_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Send STOP and do not return until the instrument confirms STOP."""

    started = time.monotonic()
    history: List[Dict[str, Any]] = []
    result: Dict[str, Any] = {
        "started_at": now_iso(),
        "context": context,
        "history": history,
        "success": False,
    }

    try:
        if PRE_ACQUISITION_STOP_TIMEOUT_S <= 0:
            raise ValueError(
                "PRE_ACQUISITION_STOP_TIMEOUT_S must be positive"
            )
        if STOP_SETTLE_DELAY_S < 0:
            raise ValueError("STOP_SETTLE_DELAY_S must not be negative")
        if TRIGGER_POLL_INTERVAL_S <= 0:
            raise ValueError("TRIGGER_POLL_INTERVAL_S must be positive")

        result["before_stop"] = query_acquisition_snapshot(
            sock,
            f"before {context} STOP",
        )
        send_commands_checked(
            sock,
            [":STOP"],
            f"stopping measurement before {context}",
        )
        opc_response = scpi_query_line(
            sock,
            "*OPC?",
            timeout=PRE_ACQUISITION_STOP_TIMEOUT_S,
        )
        require_response(
            opc_response,
            "1",
            f"STOP completion before {context}",
        )
        result["opc_response"] = opc_response

        deadline = time.monotonic() + PRE_ACQUISITION_STOP_TIMEOUT_S
        last_status: Optional[str] = None
        while True:
            status = normalize_response(
                scpi_query_line(sock, ":TRIGger:STATus?")
            )
            if status not in VALID_TRIGGER_STATES:
                raise SCPIProtocolError(
                    f"Unexpected trigger status after :STOP: {status!r}"
                )
            if status != last_status:
                history.append(
                    {
                        "phase": phase,
                        "elapsed_s": round(
                            time.monotonic() - started,
                            6,
                        ),
                        "status": status,
                    }
                )
                last_status = status
            if status == "STOP":
                break
            if time.monotonic() >= deadline:
                raise TriggerTimeoutError(
                    f"Oscilloscope did not reach STOP before {context}"
                )
            time.sleep(TRIGGER_POLL_INTERVAL_S)

        ensure_no_scpi_errors(sock, f"confirming STOP before {context}")
        if STOP_SETTLE_DELAY_S:
            time.sleep(STOP_SETTLE_DELAY_S)

        result["after_stop"] = query_acquisition_snapshot(
            sock,
            f"after {context} STOP",
        )
        if result["after_stop"]["trigger_status"] != "STOP":
            raise DataValidationError(
                f"Trigger status changed away from STOP before {context}"
            )

        result.update(
            {
                "success": True,
                "completed_at": now_iso(),
                "elapsed_s": round(time.monotonic() - started, 6),
            }
        )
        return result
    except Exception as exc:
        result.update(
            {
                "completed_at": now_iso(),
                "elapsed_s": round(time.monotonic() - started, 6),
                "error": repr(exc),
            }
        )
        raise
    finally:
        if log_path is not None:
            write_json(log_path, result)


def acquire_single_record(
    sock: socket.socket,
    log_path: Path,
) -> Dict[str, Any]:
    """Stop the old measurement, then acquire and verify one fresh record."""

    history: List[Dict[str, Any]] = []
    result: Dict[str, Any] = {
        "started_at": now_iso(),
        "forced_trigger": False,
        "timeout_extension_count": 0,
        "timeout_prompt_count": 0,
        "active_state_observed": False,
        "initial_stop_guard_s": SINGLE_INITIAL_STOP_GUARD_S,
        "history": history,
        "success": False,
    }

    start_time = time.monotonic()

    try:
        if SINGLE_INITIAL_STOP_GUARD_S < 0:
            raise ValueError(
                "SINGLE_INITIAL_STOP_GUARD_S must not be negative"
            )
        if SINGLE_TRIGGER_TIMEOUT_S <= 0:
            raise ValueError("SINGLE_TRIGGER_TIMEOUT_S must be positive")
        if FORCED_TRIGGER_STOP_TIMEOUT_S <= 0:
            raise ValueError(
                "FORCED_TRIGGER_STOP_TIMEOUT_S must be positive"
            )
        if TRIGGER_POLL_INTERVAL_S <= 0:
            raise ValueError("TRIGGER_POLL_INTERVAL_S must be positive")

        # Reconfirm STOP immediately before :SINGle even when the caller
        # already stopped the instrument for decoder configuration.
        stop_result = stop_and_confirm_measurement(
            sock,
            "single acquisition",
            "pre_acquisition_stop",
        )
        result["pre_acquisition_stop"] = stop_result
        result["before_stop"] = stop_result["before_stop"]
        result["after_stop"] = stop_result["after_stop"]
        history.extend(stop_result["history"])

        single_start_time = time.monotonic()
        deadline = single_start_time + SINGLE_TRIGGER_TIMEOUT_S
        send_commands_checked(sock, [":SINGle"], "starting single acquisition")
        result["single_command_scpi_accepted"] = True

        # IMPORTANT (public beta.2): do not use an immediate :TRIGger:SWEep?
        # readback as the pass/fail criterion for :SINGle.  The MHO900
        # Programming Guide documents :SINGle as equivalent to
        # :TRIGger:SWEep SINGle, but a validated MHO984 has been observed to
        # return its previous sweep value (for example AUTO) when queried
        # immediately after :SINGle, especially when the input triggers very
        # quickly.  Treat this readback as diagnostic information only.
        # Fresh-record completion is verified below from trigger-status
        # progression and the final confirmed STOP state.
        single_sweep = normalize_response(
            scpi_query_line(sock, ":TRIGger:SWEep?")
        )
        result["single_sweep_readback"] = single_sweep
        result["single_sweep_readback_is_sing"] = single_sweep.startswith(
            "SING"
        )
        if not result["single_sweep_readback_is_sing"]:
            history.append(
                {
                    "phase": "single_acquisition",
                    "single_elapsed_s": round(
                        time.monotonic() - single_start_time, 6
                    ),
                    "warning": (
                        "Immediate :TRIGger:SWEep? readback after :SINGle "
                        f"was {single_sweep!r}, not 'SING'. This is retained "
                        "for diagnostics and is not treated as acquisition "
                        "failure; completion is verified by trigger status "
                        "and final STOP."
                    ),
                }
            )
        ensure_no_scpi_errors(sock, "verifying single-trigger command")

        last_status: Optional[str] = None
        ignored_initial_stop_logged = False
        while True:
            elapsed = time.monotonic() - start_time
            single_elapsed = time.monotonic() - single_start_time
            status = normalize_response(
                scpi_query_line(sock, ":TRIGger:STATus?")
            )
            if status not in VALID_TRIGGER_STATES:
                raise SCPIProtocolError(
                    f"Unexpected trigger status: {status!r}"
                )

            if status != last_status:
                history.append(
                    {
                        "phase": "single_acquisition",
                        "elapsed_s": round(elapsed, 6),
                        "single_elapsed_s": round(single_elapsed, 6),
                        "status": status,
                    }
                )
                last_status = status

            if status in ACTIVE_TRIGGER_STATES:
                result["active_state_observed"] = True

            if status == "STOP":
                initial_stop_is_guarded = (
                    not result["active_state_observed"]
                    and single_elapsed < SINGLE_INITIAL_STOP_GUARD_S
                )
                if initial_stop_is_guarded:
                    if not ignored_initial_stop_logged:
                        history.append(
                            {
                                "phase": "single_acquisition",
                                "elapsed_s": round(elapsed, 6),
                                "single_elapsed_s": round(
                                    single_elapsed,
                                    6,
                                ),
                                "action": "ignore_initial_stop",
                            }
                        )
                        ignored_initial_stop_logged = True
                else:
                    ensure_no_scpi_errors(
                        sock,
                        "completing single acquisition",
                    )
                    after_acquisition = query_acquisition_snapshot(
                        sock,
                        "after single acquisition",
                    )
                    if after_acquisition["trigger_status"] != "STOP":
                        raise DataValidationError(
                            "Trigger status changed away from STOP before "
                            "waveform readout"
                        )

                    bytes_per_point = {
                        "BYTE": 1,
                        "WORD": 2,
                    }.get(ANALOG_WAVEFORM_FORMAT.strip().upper())
                    expected_bytes = (
                        after_acquisition["memory_depth_points"]
                        * bytes_per_point
                        if bytes_per_point is not None
                        else None
                    )
                    result["after_acquisition"] = after_acquisition
                    result["memory_depth_changed"] = (
                        result["after_stop"]["memory_depth_points"]
                        != after_acquisition["memory_depth_points"]
                    )
                    result[
                        "expected_analog_bytes_per_channel"
                    ] = expected_bytes
                    result["expected_analog_mib_per_channel"] = (
                        expected_bytes / (1024 * 1024)
                        if expected_bytes is not None
                        else None
                    )
                    result.update(
                        {
                            "success": True,
                            "completed_at": now_iso(),
                            "elapsed_s": round(elapsed, 6),
                            "single_elapsed_s": round(single_elapsed, 6),
                            "final_status": status,
                            "completion_basis": (
                                "active_trigger_state_then_stop"
                                if result["active_state_observed"]
                                else "confirmed_stop_after_initial_guard"
                            ),
                        }
                    )
                    return result

            if time.monotonic() >= deadline:
                timeout_action: Optional[str] = None

                if TRIGGER_TIMEOUT_DECISION_CALLBACK is not None:
                    result["timeout_prompt_count"] += 1
                    context = {
                        "status": status,
                        "single_elapsed_s": round(single_elapsed, 6),
                        "timeout_s": SINGLE_TRIGGER_TIMEOUT_S,
                        "active_state_observed": bool(result["active_state_observed"]),
                        "forced_trigger_already_used": bool(result["forced_trigger"]),
                        "timeout_extension_count": int(result["timeout_extension_count"]),
                    }
                    try:
                        timeout_action = str(
                            TRIGGER_TIMEOUT_DECISION_CALLBACK(context)
                        ).strip().lower()
                    except Exception as callback_exc:
                        history.append(
                            {
                                "phase": "single_acquisition",
                                "single_elapsed_s": round(single_elapsed, 6),
                                "warning": (
                                    "Trigger-timeout decision callback failed: "
                                    + repr(callback_exc)
                                ),
                            }
                        )
                        timeout_action = "abort"

                    if timeout_action not in {"force", "wait", "abort"}:
                        history.append(
                            {
                                "phase": "single_acquisition",
                                "single_elapsed_s": round(single_elapsed, 6),
                                "warning": (
                                    "Invalid trigger-timeout action "
                                    f"{timeout_action!r}; treating as abort"
                                ),
                            }
                        )
                        timeout_action = "abort"

                    history.append(
                        {
                            "phase": "single_acquisition",
                            "single_elapsed_s": round(single_elapsed, 6),
                            "action": "timeout_decision",
                            "decision": timeout_action,
                        }
                    )

                elif FORCE_TRIGGER_ON_TIMEOUT and not result["forced_trigger"]:
                    timeout_action = "force"
                else:
                    timeout_action = "abort"

                if timeout_action == "wait":
                    result["timeout_extension_count"] += 1
                    deadline = time.monotonic() + SINGLE_TRIGGER_TIMEOUT_S
                    history.append(
                        {
                            "phase": "single_acquisition",
                            "single_elapsed_s": round(
                                time.monotonic() - single_start_time, 6
                            ),
                            "action": "extend_wait",
                            "extension_s": SINGLE_TRIGGER_TIMEOUT_S,
                            "extension_count": result["timeout_extension_count"],
                        }
                    )

                elif timeout_action == "force":
                    if result["forced_trigger"]:
                        raise TriggerTimeoutError(
                            "Single acquisition still did not reach STOP after "
                            "the one permitted forced trigger"
                        )
                    send_commands_checked(
                        sock,
                        [":TFORce"],
                        "forcing a timed-out single trigger",
                    )
                    result["forced_trigger"] = True
                    history.append(
                        {
                            "elapsed_s": round(
                                time.monotonic() - start_time, 6
                            ),
                            "single_elapsed_s": round(
                                time.monotonic() - single_start_time,
                                6,
                            ),
                            "phase": "single_acquisition",
                            "action": "TFORCE",
                        }
                    )
                    deadline = (
                        time.monotonic() + FORCED_TRIGGER_STOP_TIMEOUT_S
                    )

                else:
                    raise TriggerTimeoutError(
                        "Single acquisition did not reach STOP within the "
                        "configured timeout"
                    )

            time.sleep(TRIGGER_POLL_INTERVAL_S)
    except Exception as exc:
        result.update(
            {
                "completed_at": now_iso(),
                "elapsed_s": round(time.monotonic() - start_time, 6),
                "error": repr(exc),
            }
        )
        raise
    finally:
        write_json(log_path, result)


# ============================================================================
# Analog waveform acquisition
# ============================================================================


def capture_analog_channel(
    sock: socket.socket,
    channel: str,
    dataset_dir: Path,
) -> Dict[str, Any]:
    """
    Read every waveform point exposed from the stopped internal-memory record.

    :WAVeform:POINts is made equal to the acquisition memory depth before the
    preamble is queried. A redundant write is skipped because MHO984 firmware
    can reject the same large point count when changing waveform sources.
    Every returned block is checked in both points and bytes. A short response
    is a hard failure and is never treated as a successful capture.
    """

    channel_name = channel.lower()
    preamble_path = dataset_dir / f"{channel_name}_pre.txt"
    instrument_preamble_path = (
        dataset_dir / f"{channel_name}_pre_instrument.txt"
    )
    final_path = dataset_dir / f"{channel_name}.bin"
    partial_path = dataset_dir / f"{channel_name}.bin.partial"
    info_path = dataset_dir / f"{channel_name}_info.json"

    info: Dict[str, Any] = {
        "channel": channel,
        "status": "started",
        "started_at": now_iso(),
        "file": final_path.name,
        "partial_file": partial_path.name,
        "chunks": [],
    }

    try:
        waveform_format = ANALOG_WAVEFORM_FORMAT.strip().upper()
        format_definition = {
            "BYTE": {"preamble_code": 0, "bytes_per_point": 1, "dtype": "u1"},
            "WORD": {"preamble_code": 1, "bytes_per_point": 2, "dtype": "<u2"},
        }.get(waveform_format)
        if format_definition is None:
            raise ValueError(
                "ANALOG_WAVEFORM_FORMAT must be BYTE or WORD, got "
                f"{ANALOG_WAVEFORM_FORMAT!r}"
            )
        if ANALOG_CHUNK_POINTS <= 0:
            raise ValueError("ANALOG_CHUNK_POINTS must be positive")

        bytes_per_point = int(format_definition["bytes_per_point"])
        expected_preamble_format = int(
            format_definition["preamble_code"]
        )

        send_commands_checked(
            sock,
            [
                f":WAVeform:SOURce {channel}",
                ":WAVeform:MODE RAW",
                f":WAVeform:FORMat {waveform_format}",
            ],
            f"setting waveform mode for {channel}",
        )

        source = normalize_response(
            scpi_query_line(sock, ":WAVeform:SOURce?")
        )
        mode = normalize_response(scpi_query_line(sock, ":WAVeform:MODE?"))
        data_format = normalize_response(
            scpi_query_line(sock, ":WAVeform:FORMat?")
        )

        require_response(source, channel, f"{channel} waveform source")
        require_response(
            mode, "RAW", f"{channel} waveform mode", allow_prefix=True
        )
        require_response(
            data_format,
            waveform_format,
            f"{channel} waveform format",
            allow_prefix=True,
        )

        memory_depth_text = scpi_query_line(sock, ":ACQuire:MDEPth?")
        memory_depth = parse_positive_scpi_integer(
            memory_depth_text,
            f"{channel} acquisition memory depth",
        )
        sample_rate_text = scpi_query_line(sock, ":ACQuire:SRATe?")
        try:
            sample_rate_sps = float(sample_rate_text)
        except ValueError as exc:
            raise SCPIProtocolError(
                f"{channel} sample rate is invalid: {sample_rate_text!r}"
            ) from exc
        if not math.isfinite(sample_rate_sps) or sample_rate_sps <= 0:
            raise SCPIProtocolError(
                f"{channel} sample rate must be positive: "
                f"{sample_rate_text!r}"
            )

        points_before_text = scpi_query_line(sock, ":WAVeform:POINts?")
        points_before = parse_positive_scpi_integer(
            points_before_text,
            f"{channel} waveform point count before configuration",
        )
        ensure_no_scpi_errors(
            sock,
            f"reading initial {channel} waveform point count",
        )

        # The previous channel leaves START/STOP at its final transfer chunk.
        # Return the range to values valid for the current POINts setting before
        # attempting to change POINts for this channel.
        initial_range_stop = min(points_before, memory_depth)
        send_commands_checked(
            sock,
            [
                ":WAVeform:STARt 1",
                f":WAVeform:STOP {initial_range_stop}",
            ],
            f"resetting {channel} waveform range before point selection",
        )

        points_command_sent = points_before != memory_depth
        points_command_error: Optional[str] = None
        if points_command_sent:
            try:
                send_commands_checked(
                    sock,
                    [f":WAVeform:POINts {memory_depth}"],
                    f"requesting all readable {channel} memory points",
                )
            except SCPICommandError as exc:
                # Some firmware reports an execution error even when the
                # requested point count has taken effect. Readback is the
                # authoritative check; continue only when it equals the full
                # acquisition memory depth.
                points_command_error = repr(exc)

        points_readback_text = scpi_query_line(
            sock,
            ":WAVeform:POINts?",
        )
        readable_points = parse_positive_scpi_integer(
            points_readback_text,
            f"{channel} waveform point readback",
        )
        ensure_no_scpi_errors(
            sock,
            f"verifying all readable {channel} memory points",
        )

        if points_command_error is not None:
            if readable_points != memory_depth:
                raise SCPICommandError(
                    f"{channel}: :WAVeform:POINts {memory_depth} failed "
                    f"and readback is only {readable_points}: "
                    f"{points_command_error}"
                )
            print(
                f"{channel}: point-count command reported an error, but "
                f"readback confirms all {readable_points:,} points; "
                "continuing."
            )

        if readable_points > memory_depth:
            raise DataValidationError(
                f"{channel}: waveform point readback {readable_points:,} "
                f"exceeds acquisition memory depth {memory_depth:,}"
            )
        if readable_points < memory_depth:
            print(
                f"{channel}: instrument exposes {readable_points:,}/"
                f"{memory_depth:,} stored points through the waveform "
                "interface; all exposed points will be read."
            )

        send_commands_checked(
            sock,
            [
                ":WAVeform:STARt 1",
                f":WAVeform:STOP {readable_points}",
            ],
            f"selecting the complete readable {channel} range",
        )
        full_start_text = scpi_query_line(sock, ":WAVeform:STARt?")
        full_stop_text = scpi_query_line(sock, ":WAVeform:STOP?")
        full_start = parse_positive_scpi_integer(
            full_start_text,
            f"{channel} full-range start readback",
        )
        full_stop = parse_positive_scpi_integer(
            full_stop_text,
            f"{channel} full-range stop readback",
        )
        ensure_no_scpi_errors(
            sock,
            f"verifying complete readable {channel} range",
        )
        if full_start != 1 or full_stop != readable_points:
            raise DataValidationError(
                f"{channel}: requested full range 1-{readable_points:,}, "
                f"instrument accepted {full_start:,}-{full_stop:,}"
            )

        preamble_text = scpi_query_line(sock, ":WAVeform:PREamble?")
        instrument_preamble = WaveformPreamble.parse(preamble_text)
        instrument_preamble_path.write_text(
            preamble_text + "\n",
            encoding="utf-8",
        )

        if instrument_preamble.fmt != expected_preamble_format:
            raise DataValidationError(
                f"{channel}: preamble format is "
                f"{instrument_preamble.fmt}, expected "
                f"{waveform_format}={expected_preamble_format}"
            )
        if instrument_preamble.mode != 2:
            raise DataValidationError(
                f"{channel}: preamble mode is {instrument_preamble.mode}, "
                "expected RAW=2"
            )

        # If the preamble point-count field differs from the accepted
        # :WAVeform:POINts? range, the range readback remains authoritative for
        # transfer. Normalize only the viewer-facing point-count field and keep
        # the unmodified instrument response beside it for audit.
        preamble = WaveformPreamble(
            fmt=instrument_preamble.fmt,
            mode=instrument_preamble.mode,
            points=readable_points,
            count=instrument_preamble.count,
            xinc=instrument_preamble.xinc,
            xorig=instrument_preamble.xorig,
            xref=instrument_preamble.xref,
            yinc=instrument_preamble.yinc,
            yorig=instrument_preamble.yorig,
            yref=instrument_preamble.yref,
        )
        preamble_path.write_text(
            preamble.to_text() + "\n",
            encoding="utf-8",
        )
        if instrument_preamble.points != readable_points:
            print(
                f"{channel}: normalized preamble point count from "
                f"{instrument_preamble.points:,} to "
                f"{readable_points:,} to match the complete read range."
            )
        ensure_no_scpi_errors(sock, f"reading {channel} waveform metadata")

        total_points = readable_points
        expected_total_bytes = total_points * bytes_per_point
        total_chunks = (
            total_points + ANALOG_CHUNK_POINTS - 1
        ) // ANALOG_CHUNK_POINTS
        free_bytes_before = ensure_free_disk_space(
            dataset_dir,
            expected_total_bytes,
            f"complete {channel} waveform",
        )

        info.update(
            {
                "preamble": asdict(preamble),
                "instrument_preamble": asdict(instrument_preamble),
                "preamble_points_normalized": (
                    instrument_preamble.points != readable_points
                ),
                "instrument_preamble_file": (
                    instrument_preamble_path.name
                ),
                "waveform_format": waveform_format,
                "sample_dtype": format_definition["dtype"],
                "bytes_per_point": bytes_per_point,
                "memory_depth_query": memory_depth_text,
                "memory_depth_points": memory_depth,
                "sample_rate_query": sample_rate_text,
                "sample_rate_sps": sample_rate_sps,
                "waveform_points_before_query": points_before_text,
                "waveform_points_before": points_before,
                "waveform_points_command_sent": points_command_sent,
                "waveform_points_command_error": points_command_error,
                "waveform_points_query": points_readback_text,
                "readable_points": readable_points,
                "all_memory_points_exposed": (
                    readable_points == memory_depth
                ),
                "memory_points_not_exposed": (
                    memory_depth - readable_points
                ),
                "expected_points": total_points,
                "expected_bytes": expected_total_bytes,
                "chunk_points": ANALOG_CHUNK_POINTS,
                "chunk_count": total_chunks,
                "free_disk_bytes_before": free_bytes_before,
            }
        )

        received_points_total = 0
        received_bytes_total = 0
        progress_interval = max(1, total_chunks // 20)

        with partial_path.open("wb") as output:
            start = 1
            chunk_index = 0

            while start <= total_points:
                chunk_index += 1
                stop = min(
                    start + ANALOG_CHUNK_POINTS - 1,
                    total_points,
                )
                expected_points = stop - start + 1
                expected_bytes = expected_points * bytes_per_point

                send_commands_checked(
                    sock,
                    [
                        f":WAVeform:STARt {start}",
                        f":WAVeform:STOP {stop}",
                    ],
                    (
                        f"setting {channel} chunk "
                        f"{chunk_index}/{total_chunks}"
                    ),
                )

                payload = scpi_query_ieee_block(
                    sock,
                    ":WAVeform:DATA?",
                    timeout=ANALOG_BLOCK_TIMEOUT_S,
                    max_payload_bytes=expected_bytes,
                )

                actual_bytes = len(payload)
                if actual_bytes != expected_bytes:
                    raise DataValidationError(
                        f"{channel} chunk {chunk_index}/{total_chunks}: "
                        f"requested {expected_points:,} {waveform_format} "
                        f"points ({expected_bytes:,} bytes, "
                        f"{start:,}-{stop:,}), received "
                        f"{actual_bytes:,} bytes"
                    )
                actual_points = actual_bytes // bytes_per_point

                output.write(payload)
                received_points_total += actual_points
                received_bytes_total += actual_bytes
                info["chunks"].append(
                    {
                        "index": chunk_index,
                        "start": start,
                        "stop": stop,
                        "expected_points": expected_points,
                        "received_points": actual_points,
                        "expected_bytes": expected_bytes,
                        "received_bytes": actual_bytes,
                    }
                )

                ensure_no_scpi_errors(
                    sock,
                    (
                        f"reading {channel} chunk "
                        f"{chunk_index}/{total_chunks}"
                    ),
                )
                if (
                    chunk_index == 1
                    or chunk_index == total_chunks
                    or chunk_index % progress_interval == 0
                ):
                    print(
                        f"{channel}: chunk {chunk_index}/{total_chunks}, "
                        f"{received_points_total:,}/{total_points:,} points, "
                        f"{received_bytes_total:,}/"
                        f"{expected_total_bytes:,} bytes"
                    )

                start = stop + 1

            output.flush()

        if received_points_total != total_points:
            raise DataValidationError(
                f"{channel}: total point-count mismatch, expected "
                f"{total_points:,}, received {received_points_total:,}"
            )
        if received_bytes_total != expected_total_bytes:
            raise DataValidationError(
                f"{channel}: total byte-count mismatch, expected "
                f"{expected_total_bytes:,}, received "
                f"{received_bytes_total:,}"
            )
        actual_file_size = partial_path.stat().st_size
        if actual_file_size != expected_total_bytes:
            raise DataValidationError(
                f"{channel}: saved file size mismatch, expected "
                f"{expected_total_bytes:,}, got {actual_file_size:,} bytes"
            )

        # The viewer only sees the final .bin after complete validation.
        partial_path.replace(final_path)
        info.update(
            {
                "status": "success",
                "completed_at": now_iso(),
                "received_points": received_points_total,
                "received_bytes": received_bytes_total,
            }
        )
        return info

    except Exception as exc:
        partial_size = (
            partial_path.stat().st_size if partial_path.exists() else 0
        )
        info.update(
            {
                "status": "failed",
                "completed_at": now_iso(),
                "partial_bytes": partial_size,
                "error": repr(exc),
            }
        )
        raise
    finally:
        write_json(info_path, info)


# ============================================================================
# Analog/digital time-range alignment
# ============================================================================


def derive_analog_reference_time_range(
    analog_results: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Return the common time range of the successfully captured channels."""

    channel_ranges: List[Dict[str, Any]] = []
    for channel, result in analog_results.items():
        if result.get("status") != "success":
            continue

        preamble = result.get("preamble")
        if not isinstance(preamble, dict):
            raise DataValidationError(
                f"{channel}: successful result has no waveform preamble"
            )

        try:
            points = int(preamble["points"])
            xinc = float(preamble["xinc"])
            xorig = float(preamble["xorig"])
            xref = float(preamble["xref"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise DataValidationError(
                f"{channel}: invalid waveform time metadata: {preamble!r}"
            ) from exc

        if (
            points <= 0
            or not math.isfinite(xinc)
            or xinc <= 0
            or not math.isfinite(xorig)
            or not math.isfinite(xref)
        ):
            raise DataValidationError(
                f"{channel}: invalid waveform time metadata: {preamble!r}"
            )

        first_time = (0.0 - xref) * xinc + xorig
        last_time = (float(points - 1) - xref) * xinc + xorig
        end_exclusive = last_time + xinc
        channel_ranges.append(
            {
                "channel": channel,
                "points": points,
                "xinc_s": xinc,
                "first_sample_time_s": first_time,
                "last_sample_time_s": last_time,
                "end_time_exclusive_s": end_exclusive,
                "duration_s": points * xinc,
            }
        )

    if not channel_ranges:
        return None

    baseline = channel_ranges[0]
    for current in channel_ranges[1:]:
        tolerance = max(
            float(baseline["xinc_s"]),
            float(current["xinc_s"]),
        ) * 1.5
        if (
            abs(
                float(current["first_sample_time_s"])
                - float(baseline["first_sample_time_s"])
            )
            > tolerance
            or abs(
                float(current["end_time_exclusive_s"])
                - float(baseline["end_time_exclusive_s"])
            )
            > tolerance
        ):
            raise DataValidationError(
                "Successful analog channels do not share the same time "
                f"range: {channel_ranges!r}"
            )

    start_time = min(
        float(item["first_sample_time_s"]) for item in channel_ranges
    )
    end_time = max(
        float(item["end_time_exclusive_s"]) for item in channel_ranges
    )
    duration = end_time - start_time
    if not math.isfinite(duration) or duration <= 0:
        raise DataValidationError(
            f"Invalid common analog time range: {channel_ranges!r}"
        )

    return {
        "channels": [item["channel"] for item in channel_ranges],
        "start_time_s": start_time,
        "end_time_exclusive_s": end_time,
        "last_sample_time_s": max(
            float(item["last_sample_time_s"])
            for item in channel_ranges
        ),
        "duration_s": duration,
        "center_time_s": (start_time + end_time) / 2.0,
        "channel_ranges": channel_ranges,
    }


def read_timebase_view(
    sock: socket.socket,
    context: str,
) -> Dict[str, Any]:
    """Read and validate the main horizontal display range."""

    scale = float(scpi_query_line(sock, ":TIMebase:MAIN:SCALe?"))
    offset = float(scpi_query_line(sock, ":TIMebase:MAIN:OFFSet?"))
    vernier = query_scpi_bool(sock, ":TIMebase:VERNier?")
    trigger_status = normalize_response(
        scpi_query_line(sock, ":TRIGger:STATus?")
    )
    ensure_no_scpi_errors(sock, context)

    if (
        not math.isfinite(scale)
        or scale <= 0
        or not math.isfinite(offset)
    ):
        raise SCPIProtocolError(
            f"Invalid timebase response during {context}: "
            f"scale={scale!r}, offset={offset!r}"
        )

    return {
        "scale_s_per_div": scale,
        "offset_s": offset,
        "vernier": vernier,
        "display_start_time_s": offset - 5.0 * scale,
        "display_end_time_s": offset + 5.0 * scale,
        "trigger_status": trigger_status,
    }


def prepare_full_record_digital_view_before_acquisition(
    sock: socket.socket,
) -> Dict[str, Any]:
    """Set a full-memory view before :SINGle builds the BUS event table."""

    if DIGITAL_VIEW_MARGIN_RATIO < 0:
        raise ValueError("DIGITAL_VIEW_MARGIN_RATIO must not be negative")
    if DIGITAL_TIMEBASE_MAX_ADJUSTMENTS <= 0:
        raise ValueError(
            "DIGITAL_TIMEBASE_MAX_ADJUSTMENTS must be positive"
        )

    before = read_timebase_view(
        sock,
        "reading the timebase before digital pre-acquisition setup",
    )
    if before["trigger_status"] != "STOP":
        raise DataValidationError(
            "The digital full-record timebase must be configured while "
            f"stopped; trigger status is {before['trigger_status']!r}"
        )

    if not DIGITAL_EXPAND_VIEW_TO_ANALOG_RECORD:
        snapshot = query_acquisition_snapshot(
            sock,
            "reading the unmodified acquisition range",
        )
        return {
            "status": "disabled",
            "configured_before_single": False,
            "before": before,
            "accepted": before,
            "attempts": [],
            "final_acquisition_snapshot": snapshot,
        }

    attempts: List[Dict[str, Any]] = []
    for attempt_index in range(1, DIGITAL_TIMEBASE_MAX_ADJUSTMENTS + 1):
        snapshot_before = query_acquisition_snapshot(
            sock,
            f"estimating full record before timebase attempt {attempt_index}",
        )
        predicted_duration = (
            int(snapshot_before["memory_depth_points"])
            / float(snapshot_before["sample_rate_sps"])
        )
        if not math.isfinite(predicted_duration) or predicted_duration <= 0:
            raise DataValidationError(
                "Invalid predicted acquisition duration before :SINGle: "
                f"{snapshot_before!r}"
            )

        target_scale = (
            predicted_duration
            * (1.0 + DIGITAL_VIEW_MARGIN_RATIO)
            / 10.0
        )
        target_offset = 0.0
        send_commands_checked(
            sock,
            [
                ":TIMebase:VERNier ON",
                f":TIMebase:MAIN:SCALe {target_scale:.17g}",
                f":TIMebase:MAIN:OFFSet {target_offset:.17g}",
            ],
            (
                "setting the full-record decoder view before :SINGle "
                f"attempt {attempt_index}"
            ),
        )
        opc_response = scpi_query_line(sock, "*OPC?")
        if opc_response.strip() != "1":
            raise SCPIProtocolError(
                "Unexpected *OPC? response after pre-acquisition timebase "
                f"setup: {opc_response!r}"
            )

        accepted = read_timebase_view(
            sock,
            (
                "verifying the full-record decoder view before :SINGle "
                f"attempt {attempt_index}"
            ),
        )
        if accepted["trigger_status"] != "STOP":
            raise DataValidationError(
                "Pre-acquisition timebase setup disturbed STOP: "
                f"{accepted['trigger_status']!r}"
            )

        snapshot_after = query_acquisition_snapshot(
            sock,
            (
                "rechecking acquisition parameters after timebase attempt "
                f"{attempt_index}"
            ),
        )
        final_duration = (
            int(snapshot_after["memory_depth_points"])
            / float(snapshot_after["sample_rate_sps"])
        )
        predicted_start = -final_duration / 2.0
        predicted_end = final_duration / 2.0
        tolerance = max(final_duration * 1e-9, 1e-15)
        contains_predicted_record = (
            float(accepted["display_start_time_s"])
            <= predicted_start + tolerance
            and float(accepted["display_end_time_s"])
            >= predicted_end - tolerance
        )
        attempts.append(
            {
                "index": attempt_index,
                "snapshot_before": snapshot_before,
                "predicted_duration_before_s": predicted_duration,
                "target": {
                    "scale_s_per_div": target_scale,
                    "offset_s": target_offset,
                    "margin_ratio": DIGITAL_VIEW_MARGIN_RATIO,
                },
                "accepted": accepted,
                "snapshot_after": snapshot_after,
                "predicted_duration_after_s": final_duration,
                "predicted_record_start_time_s": predicted_start,
                "predicted_record_end_time_s": predicted_end,
                "predicted_record_contained": contains_predicted_record,
                "opc_response": opc_response,
            }
        )
        if contains_predicted_record:
            return {
                "status": "expanded",
                "configured_before_single": True,
                "before": before,
                "accepted": accepted,
                "attempts": attempts,
                "final_acquisition_snapshot": snapshot_after,
                "predicted_record_start_time_s": predicted_start,
                "predicted_record_end_time_s": predicted_end,
                "predicted_record_contained": True,
            }

    raise DataValidationError(
        "Could not configure a horizontal range that contains the predicted "
        f"acquisition record after {DIGITAL_TIMEBASE_MAX_ADJUSTMENTS} "
        f"attempts: {attempts!r}"
    )


def refresh_digital_event_table_for_analog_record(
    sock: socket.socket,
    analog_time_range: Dict[str, Any],
    preparation: Dict[str, Any],
    warnings: List[str],
) -> Dict[str, Any]:
    """Fit the stopped record to the display and rebuild the BUS event table.

    MHO984 can change its sample rate when :SINGle is armed. Consequently, the
    record duration predicted before acquisition is not authoritative. This
    function uses the time metadata read from the completed analog record and
    keeps the instrument stopped while it refreshes the decoder table.
    """

    if DIGITAL_EVENT_TABLE_SETTLE_S < 0:
        raise ValueError("DIGITAL_EVENT_TABLE_SETTLE_S must not be negative")

    before = read_timebase_view(
        sock,
        "reading the stopped decoder view after analog acquisition",
    )
    if before["trigger_status"] != "STOP":
        raise DataValidationError(
            "The acquisition record is no longer stopped before BUS DATA: "
            f"{before['trigger_status']!r}"
        )

    record_start = float(analog_time_range["start_time_s"])
    record_end = float(analog_time_range["end_time_exclusive_s"])
    duration = float(analog_time_range["duration_s"])
    tolerance = max(duration * 1e-9, 1e-15)
    target_scale = (
        duration * (1.0 + DIGITAL_VIEW_MARGIN_RATIO) / 10.0
    )
    target_offset = float(analog_time_range["center_time_s"])
    commands: List[str] = []
    opc_responses: List[str] = []

    if DIGITAL_EXPAND_VIEW_TO_ANALOG_RECORD:
        timebase_commands = [
            ":TIMebase:VERNier ON",
            f":TIMebase:MAIN:SCALe {target_scale:.17g}",
            f":TIMebase:MAIN:OFFSet {target_offset:.17g}",
        ]
        send_commands_checked(
            sock,
            timebase_commands,
            "fitting the stopped record to the decoder display",
        )
        commands.extend(timebase_commands)
        opc_response = scpi_query_line(sock, "*OPC?")
        require_response(
            opc_response,
            "1",
            "stopped-record timebase update completion",
        )
        opc_responses.append(opc_response)

    accepted_before_refresh = read_timebase_view(
        sock,
        "verifying the decoder view before event-table refresh",
    )
    if accepted_before_refresh["trigger_status"] != "STOP":
        raise DataValidationError(
            "The stopped record started running during decoder setup: "
            f"{accepted_before_refresh['trigger_status']!r}"
        )

    event_state_before = query_scpi_bool(
        sock,
        f":BUS{DIGITAL_BUS}:EVENt?",
    )
    event_refresh_attempted = False
    if DIGITAL_REFRESH_EVENT_TABLE_AFTER_STOP:
        refresh_commands = [
            f":BUS{DIGITAL_BUS}:EVENt OFF",
            f":BUS{DIGITAL_BUS}:EVENt ON",
        ]
        send_commands_checked(
            sock,
            refresh_commands,
            "refreshing the stopped-record Parallel BUS event table",
        )
        commands.extend(refresh_commands)
        event_refresh_attempted = True
        opc_response = scpi_query_line(sock, "*OPC?")
        require_response(
            opc_response,
            "1",
            "Parallel BUS event-table refresh completion",
        )
        opc_responses.append(opc_response)

    event_state_after = query_scpi_bool(
        sock,
        f":BUS{DIGITAL_BUS}:EVENt?",
    )
    if not event_state_after:
        raise DataValidationError(
            "Parallel BUS event table is OFF immediately before :BUS:DATA?"
        )

    time.sleep(DIGITAL_EVENT_TABLE_SETTLE_S)
    accepted = read_timebase_view(
        sock,
        "verifying the final decoder view before BUS DATA",
    )
    contains_record = (
        float(accepted["display_start_time_s"])
        <= record_start + tolerance
        and float(accepted["display_end_time_s"])
        >= record_end - tolerance
    )
    if not contains_record:
        warnings.append(
            "The oscilloscope did not accept a decoder display range that "
            "contains the complete analog record. BUS data will still be "
            "saved, but its verified time coverage will be marked partial."
        )

    return {
        "status": (
            "refreshed_full_record"
            if contains_record
            else "refreshed_partial_record"
        ),
        "configured_before_single": bool(
            preparation.get("configured_before_single")
        ),
        "preparation_status": preparation.get("status"),
        "before": before,
        "target": {
            "scale_s_per_div": target_scale,
            "offset_s": target_offset,
            "margin_ratio": DIGITAL_VIEW_MARGIN_RATIO,
        },
        "accepted_before_event_refresh": accepted_before_refresh,
        "accepted": accepted,
        "analog_record_contained": contains_record,
        "analog_record_start_time_s": record_start,
        "analog_record_end_time_s": record_end,
        "event_table_refresh_enabled": (
            DIGITAL_REFRESH_EVENT_TABLE_AFTER_STOP
        ),
        "event_table_refresh_attempted": event_refresh_attempted,
        "event_state_before_refresh": event_state_before,
        "event_state_after_refresh": event_state_after,
        "commands": commands,
        "opc_responses": opc_responses,
        "settle_time_s": DIGITAL_EVENT_TABLE_SETTLE_S,
    }


# ============================================================================
# Digital event-table parsing
# ============================================================================


TIME_UNITS = {
    "": 1.0,
    "S": 1.0,
    "MS": 1e-3,
    "US": 1e-6,
    "NS": 1e-9,
    "PS": 1e-12,
    "FS": 1e-15,
}

TIME_VALUE_RE = re.compile(
    r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)"
    r"\s*([A-Za-z]*)$"
)


def parse_time_to_seconds(text: str) -> float:
    normalized = text.strip().replace("µ", "u").replace("μ", "u")
    match = TIME_VALUE_RE.fullmatch(normalized)
    if not match:
        raise ValueError(f"Invalid time value: {text!r}")

    value = float(match.group(1))
    unit = match.group(2).upper()
    if unit not in TIME_UNITS:
        raise ValueError(f"Unknown time unit: {unit!r}")
    return value * TIME_UNITS[unit]


def decode_digital_payload(payload: bytes) -> str:
    return payload.decode("ascii", errors="replace")


def parse_digital_events(
    payload: bytes,
) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any]]:
    """
    Parse a Parallel event table after the bus has explicitly been set to DEC.

    There is deliberately no base guessing: guessing caused bare BIN values
    such as "1010" to be interpreted as decimal 1010 and bare HEX values such
    as "A5" to be silently discarded.
    """

    text = decode_digital_payload(payload)
    events: List[Dict[str, Any]] = []
    stats = _parse_digital_event_lines(
        text.splitlines(),
        events.append,
        payload_bytes=len(payload),
    )
    return text, events, stats


def _parse_digital_event_lines(
    lines: Iterable[str],
    event_consumer: Callable[[Dict[str, Any]], None],
    payload_bytes: int,
) -> Dict[str, Any]:
    """Parse event-table lines and deliver every valid row incrementally."""

    protocol_seen = False
    header_seen = False
    line_count = 0
    candidate_rows = 0
    parsed_rows = 0
    skipped_rows = 0
    ignored_lines = 0
    out_of_order_rows = 0
    reported_errors: List[Dict[str, Any]] = []
    previous_time: Optional[float] = None
    previous_value: Optional[int] = None
    first_time: Optional[float] = None
    last_time: Optional[float] = None
    first_value: Optional[int] = None
    last_value: Optional[int] = None
    transition_counts = [0] * len(DIGITAL_CHANNELS)
    observed_high = [False] * len(DIGITAL_CHANNELS)
    observed_low = [False] * len(DIGITAL_CHANNELS)

    def report_error(line_number: int, line: str, reason: str) -> None:
        nonlocal skipped_rows
        skipped_rows += 1
        if len(reported_errors) < MAX_REPORTED_PARSE_ERRORS:
            reported_errors.append(
                {
                    "line_number": line_number,
                    "line": line,
                    "reason": reason,
                }
            )

    for line_number, raw_line in enumerate(lines, start=1):
        line_count = line_number
        line = raw_line.strip()
        if not line:
            continue

        if line.upper().startswith("PARALLEL"):
            protocol_seen = True
            continue

        try:
            row = next(csv.reader([line]))
        except csv.Error as exc:
            report_error(line_number, line, f"CSV error: {exc}")
            continue

        row = [value.strip() for value in row]
        if len(row) >= 2 and row[0].lower() == "time":
            if row[1].lower() != "data":
                report_error(line_number, line, "unexpected table header")
            else:
                header_seen = True
            continue

        if len(row) < 2:
            ignored_lines += 1
            continue

        candidate_rows += 1
        time_text = row[0]
        data_text = row[1]

        try:
            event_time = parse_time_to_seconds(time_text)
        except ValueError as exc:
            report_error(line_number, line, str(exc))
            continue

        try:
            value = int(data_text, 10)
        except ValueError:
            report_error(
                line_number,
                line,
                f"invalid DEC data value: {data_text!r}",
            )
            continue

        if not 0 <= value <= 0xFFFF:
            report_error(
                line_number,
                line,
                f"16-bit data value out of range: {value}",
            )
            continue

        if previous_time is not None and event_time < previous_time:
            out_of_order_rows += 1
        previous_time = event_time

        for bit in range(len(DIGITAL_CHANNELS)):
            bit_is_high = bool(value & (1 << bit))
            observed_high[bit] = observed_high[bit] or bit_is_high
            observed_low[bit] = observed_low[bit] or not bit_is_high
        if previous_value is not None:
            changed = previous_value ^ value
            for bit in range(len(DIGITAL_CHANNELS)):
                if changed & (1 << bit):
                    transition_counts[bit] += 1
        previous_value = value

        event = {"time_s": event_time, "data": value}
        event_consumer(event)
        parsed_rows += 1
        if first_time is None:
            first_time = event_time
            first_value = value
        last_time = event_time
        last_value = value

    channel_activity = {
        channel: {
            "transition_count": transition_counts[bit],
            "observed_high": observed_high[bit],
            "observed_low": observed_low[bit],
            "observed_both_states": (
                observed_high[bit] and observed_low[bit]
            ),
        }
        for bit, channel in enumerate(DIGITAL_CHANNELS)
    }

    stats = {
        "payload_bytes": payload_bytes,
        "line_count": line_count,
        "protocol_seen": protocol_seen,
        "header_seen": header_seen,
        "candidate_rows": candidate_rows,
        "parsed_rows": parsed_rows,
        "skipped_rows": skipped_rows,
        "ignored_lines": ignored_lines,
        "out_of_order_rows": out_of_order_rows,
        "reported_errors": reported_errors,
        "data_format": "DEC",
        "first_time_s": first_time,
        "last_time_s": last_time,
        "first_value": first_value,
        "last_value": last_value,
        "total_bit_transitions": sum(transition_counts),
        "channels_observed_both_states": [
            channel
            for channel, activity in channel_activity.items()
            if activity["observed_both_states"]
        ],
        "channel_activity": channel_activity,
    }

    if not protocol_seen:
        raise DataValidationError(
            "Digital payload does not contain a PARALLEL protocol marker"
        )
    if not header_seen:
        raise DataValidationError(
            "Digital payload does not contain the Time,Data header"
        )

    return stats


DIGITAL_EVENT_FIELDNAMES = [
    "time_s",
    "data_hex",
    "data_decimal",
    *DIGITAL_CHANNELS,
]


def digital_event_csv_row(event: Dict[str, Any]) -> Dict[str, Any]:
    value = int(event["data"])
    row: Dict[str, Any] = {
        "time_s": f"{float(event['time_s']):.15e}",
        "data_hex": f"0x{value:04X}",
        "data_decimal": str(value),
    }
    for bit, channel in enumerate(DIGITAL_CHANNELS):
        row[channel] = 1 if value & (1 << bit) else 0
    return row


def save_digital_events_csv(
    path: Path,
    events: Sequence[Dict[str, Any]],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=DIGITAL_EVENT_FIELDNAMES,
        )
        writer.writeheader()

        for event in events:
            writer.writerow(digital_event_csv_row(event))


def save_digital_events_bin(
    path: Path,
    events: Sequence[Dict[str, Any]],
) -> None:
    with path.open("wb") as output:
        for event in events:
            output.write(
                struct.pack(
                    "<dH",
                    float(event["time_s"]),
                    int(event["data"]),
                )
            )


def stream_digital_event_files(
    raw_path: Path,
    csv_path: Path,
    binary_path: Path,
) -> Dict[str, Any]:
    """
    Parse a potentially large event table without retaining all rows in RAM.

    Output files become visible under their final names only after the complete
    payload has been parsed and every candidate row has passed validation.
    """

    csv_partial = csv_path.with_name(csv_path.name + ".partial")
    binary_partial = binary_path.with_name(binary_path.name + ".partial")

    with (
        raw_path.open(
            "r",
            encoding="ascii",
            errors="replace",
            newline="",
        ) as source,
        csv_partial.open("w", newline="", encoding="utf-8") as csv_output,
        binary_partial.open("wb") as binary_output,
    ):
        writer = csv.DictWriter(
            csv_output,
            fieldnames=DIGITAL_EVENT_FIELDNAMES,
        )
        writer.writeheader()

        def consume(event: Dict[str, Any]) -> None:
            writer.writerow(digital_event_csv_row(event))
            binary_output.write(
                struct.pack(
                    "<dH",
                    float(event["time_s"]),
                    int(event["data"]),
                )
            )

        stats = _parse_digital_event_lines(
            source,
            consume,
            payload_bytes=raw_path.stat().st_size,
        )
        csv_output.flush()
        binary_output.flush()

    outputs_valid = (
        stats["skipped_rows"] == 0
        and stats["out_of_order_rows"] == 0
    )
    stats["output_files_finalized"] = outputs_valid
    if outputs_valid:
        csv_partial.replace(csv_path)
        binary_partial.replace(binary_path)

    return stats


def create_digital_raw_text_alias(
    raw_binary_path: Path,
    raw_text_path: Path,
) -> str:
    """Preserve the old .txt filename without normally duplicating large data."""

    try:
        os.link(raw_binary_path, raw_text_path)
        return "hard_link"
    except OSError:
        shutil.copyfile(raw_binary_path, raw_text_path)
        return "stream_copy"


class DigitalEventMergeWriter:
    """Incrementally write nondecreasing event rows from time slices."""

    def __init__(self, csv_path: Path, binary_path: Path) -> None:
        self.csv_path = csv_path
        self.binary_path = binary_path
        self.csv_partial = csv_path.with_name(csv_path.name + ".partial")
        self.binary_partial = binary_path.with_name(
            binary_path.name + ".partial"
        )
        self.csv_output = self.csv_partial.open(
            "w", newline="", encoding="utf-8"
        )
        self.binary_output = self.binary_partial.open("wb")
        self.csv_writer = csv.DictWriter(
            self.csv_output,
            fieldnames=DIGITAL_EVENT_FIELDNAMES,
        )
        self.csv_writer.writeheader()

        self.closed = False
        self.input_rows = 0
        self.parsed_rows = 0
        self.filtered_before_core = 0
        self.filtered_after_core = 0
        self.duplicates_skipped = 0
        self.out_of_order_rows = 0
        self.first_time_s: Optional[float] = None
        self.last_time_s: Optional[float] = None
        self.first_value: Optional[int] = None
        self.last_value: Optional[int] = None
        self.transition_counts = [0] * len(DIGITAL_CHANNELS)
        self.observed_high = [False] * len(DIGITAL_CHANNELS)
        self.observed_low = [False] * len(DIGITAL_CHANNELS)

    def write_core_events(
        self,
        events: Sequence[Dict[str, Any]],
        core_start_s: float,
        core_end_s: float,
        include_end: bool,
    ) -> Dict[str, int]:
        """Write rows in [start, end), except the final slice includes end."""

        accepted = 0
        before = 0
        after = 0
        duplicates = 0
        nonmonotonic = 0

        for event in events:
            self.input_rows += 1
            event_time = float(event["time_s"])
            value = int(event["data"])

            if event_time < core_start_s:
                self.filtered_before_core += 1
                before += 1
                continue
            if (
                event_time > core_end_s
                or (not include_end and event_time >= core_end_s)
            ):
                self.filtered_after_core += 1
                after += 1
                continue

            if self.last_time_s is not None:
                if event_time < self.last_time_s:
                    self.out_of_order_rows += 1
                    nonmonotonic += 1
                    continue
                if (
                    event_time == self.last_time_s
                    and value == self.last_value
                ):
                    self.duplicates_skipped += 1
                    duplicates += 1
                    continue

            for bit in range(len(DIGITAL_CHANNELS)):
                bit_is_high = bool(value & (1 << bit))
                self.observed_high[bit] = (
                    self.observed_high[bit] or bit_is_high
                )
                self.observed_low[bit] = (
                    self.observed_low[bit] or not bit_is_high
                )
            if self.last_value is not None:
                changed = self.last_value ^ value
                for bit in range(len(DIGITAL_CHANNELS)):
                    if changed & (1 << bit):
                        self.transition_counts[bit] += 1

            output_event = {"time_s": event_time, "data": value}
            self.csv_writer.writerow(digital_event_csv_row(output_event))
            self.binary_output.write(struct.pack("<dH", event_time, value))

            if self.first_time_s is None:
                self.first_time_s = event_time
                self.first_value = value
            self.last_time_s = event_time
            self.last_value = value
            self.parsed_rows += 1
            accepted += 1

        return {
            "input_rows": len(events),
            "accepted_rows": accepted,
            "filtered_before_core": before,
            "filtered_after_core": after,
            "duplicates_skipped": duplicates,
            "out_of_order_rows": nonmonotonic,
        }

    def finish(self) -> Dict[str, Any]:
        if self.closed:
            raise RuntimeError("DigitalEventMergeWriter is already closed")

        self.csv_output.flush()
        self.binary_output.flush()
        self.csv_output.close()
        self.binary_output.close()
        self.closed = True

        # Nonmonotonic rows are rejected before writing, so the files that
        # remain are still monotonic and useful as partial diagnostics.
        output_files_finalized = True
        self.csv_partial.replace(self.csv_path)
        self.binary_partial.replace(self.binary_path)

        channel_activity = {
            channel: {
                "transition_count": self.transition_counts[bit],
                "observed_high": self.observed_high[bit],
                "observed_low": self.observed_low[bit],
                "observed_both_states": (
                    self.observed_high[bit] and self.observed_low[bit]
                ),
            }            for bit, channel in enumerate(DIGITAL_CHANNELS)
        }
        return {
            "parsed_rows": self.parsed_rows,
            "input_rows": self.input_rows,
            "filtered_before_core": self.filtered_before_core,
            "filtered_after_core": self.filtered_after_core,
            "duplicates_skipped": self.duplicates_skipped,
            "out_of_order_rows": self.out_of_order_rows,
            "first_time_s": self.first_time_s,
            "last_time_s": self.last_time_s,
            "first_value": self.first_value,
            "last_value": self.last_value,
            "total_bit_transitions": sum(self.transition_counts),
            "channels_observed_both_states": [
                channel
                for channel, activity in channel_activity.items()
                if activity["observed_both_states"]
            ],
            "channel_activity": channel_activity,
            "output_files_finalized": output_files_finalized,
        }

    def abort(self) -> None:
        if self.closed:
            return
        self.csv_output.close()
        self.binary_output.close()
        self.closed = True


def validate_segmented_digital_settings() -> None:
    if DIGITAL_EVENT_TABLE_SATURATION_ROWS <= 0:
        raise ValueError(
            "DIGITAL_EVENT_TABLE_SATURATION_ROWS must be positive"
        )
    if not 0 < DIGITAL_SEGMENT_TARGET_ROWS < (
        DIGITAL_EVENT_TABLE_SATURATION_ROWS
    ):
        raise ValueError(
            "DIGITAL_SEGMENT_TARGET_ROWS must be positive and smaller than "
            "DIGITAL_EVENT_TABLE_SATURATION_ROWS"
        )
    if DIGITAL_SEGMENT_VIEW_MARGIN_RATIO < 0:
        raise ValueError(
            "DIGITAL_SEGMENT_VIEW_MARGIN_RATIO must not be negative"
        )
    if DIGITAL_SEGMENT_SETTLE_S < 0:
        raise ValueError("DIGITAL_SEGMENT_SETTLE_S must not be negative")
    if DIGITAL_SEGMENT_RETRIES <= 0:
        raise ValueError("DIGITAL_SEGMENT_RETRIES must be positive")
    if DIGITAL_SEGMENT_MAX_COUNT <= 0:
        raise ValueError("DIGITAL_SEGMENT_MAX_COUNT must be positive")
    if DIGITAL_SEGMENT_MAX_SPLIT_DEPTH < 0:
        raise ValueError(
            "DIGITAL_SEGMENT_MAX_SPLIT_DEPTH must not be negative"
        )
    if DIGITAL_SEGMENT_MIN_DURATION_S <= 0:
        raise ValueError(
            "DIGITAL_SEGMENT_MIN_DURATION_S must be positive"
        )


def configure_digital_segment_view(
    sock: socket.socket,
    core_start_s: float,
    core_end_s: float,
    context: str,
) -> Dict[str, Any]:
    """Move the stopped decoder view to one time slice and rebuild its table."""

    duration = core_end_s - core_start_s
    if (
        not math.isfinite(core_start_s)
        or not math.isfinite(core_end_s)
        or duration <= 0
    ):
        raise ValueError(
            f"Invalid digital segment range: {core_start_s!r} to "
            f"{core_end_s!r}"
        )

    before = read_timebase_view(sock, f"reading timebase before {context}")
    if before["trigger_status"] != "STOP":
        raise DataValidationError(
            f"The record is not stopped before {context}: "
            f"{before['trigger_status']!r}"
        )

    target_display_duration = duration * (
        1.0 + DIGITAL_SEGMENT_VIEW_MARGIN_RATIO
    )
    target_scale = target_display_duration / 10.0
    target_offset = (core_start_s + core_end_s) / 2.0
    timebase_commands = [
        ":TIMebase:VERNier ON",
        f":TIMebase:MAIN:SCALe {target_scale:.17g}",
        f":TIMebase:MAIN:OFFSet {target_offset:.17g}",
    ]
    send_commands_checked(sock, timebase_commands, f"setting {context}")
    timebase_opc = scpi_query_line(sock, "*OPC?")
    require_response(timebase_opc, "1", f"{context} timebase completion")

    accepted_before_refresh = read_timebase_view(
        sock, f"verifying timebase before refreshing {context}"
    )
    if accepted_before_refresh["trigger_status"] != "STOP":
        raise DataValidationError(
            f"The record started running while setting {context}"
        )

    event_state_before = query_scpi_bool(
        sock, f":BUS{DIGITAL_BUS}:EVENt?"
    )
    refresh_commands = [
        f":BUS{DIGITAL_BUS}:EVENt OFF",
        f":BUS{DIGITAL_BUS}:EVENt ON",
    ]
    send_commands_checked(
        sock,
        refresh_commands,
        f"refreshing the Parallel event table for {context}",
    )
    refresh_opc = scpi_query_line(sock, "*OPC?")
    require_response(refresh_opc, "1", f"{context} event refresh completion")

    event_state_after = query_scpi_bool(
        sock, f":BUS{DIGITAL_BUS}:EVENt?"
    )
    if not event_state_after:
        raise DataValidationError(
            f"Parallel event table is OFF after refreshing {context}"
        )

    time.sleep(DIGITAL_SEGMENT_SETTLE_S)
    accepted = read_timebase_view(sock, f"verifying final view for {context}")
    if accepted["trigger_status"] != "STOP":
        raise DataValidationError(
            f"The record is no longer stopped before reading {context}"
        )

    tolerance = max(duration * 1e-9, 1e-15)
    contains_core = (
        float(accepted["display_start_time_s"])
        <= core_start_s + tolerance
        and float(accepted["display_end_time_s"])
        >= core_end_s - tolerance
    )
    if not contains_core:
        raise DataValidationError(
            f"Accepted decoder view does not contain {context}: "
            f"core={core_start_s:.17g}..{core_end_s:.17g}, "
            f"display={accepted['display_start_time_s']:.17g}.."
            f"{accepted['display_end_time_s']:.17g}"
        )

    return {
        "core_start_s": core_start_s,
        "core_end_s": core_end_s,
        "core_duration_s": duration,
        "before": before,
        "target": {
            "scale_s_per_div": target_scale,
            "offset_s": target_offset,
            "display_duration_s": target_display_duration,
            "margin_ratio": DIGITAL_SEGMENT_VIEW_MARGIN_RATIO,
        },
        "accepted_before_event_refresh": accepted_before_refresh,
        "accepted": accepted,
        "contains_core": contains_core,
        "event_state_before_refresh": event_state_before,
        "event_state_after_refresh": event_state_after,
        "timebase_commands": timebase_commands,
        "refresh_commands": refresh_commands,
        "opc_responses": [timebase_opc, refresh_opc],
        "settle_time_s": DIGITAL_SEGMENT_SETTLE_S,
    }


def estimate_initial_digital_segment_count(
    analog_time_range: Dict[str, Any],
    probe_stats: Dict[str, Any],
) -> Dict[str, Any]:
    """Estimate a safe slice width from the capped full-view event density."""

    duration = float(analog_time_range["duration_s"])
    probe_rows = int(probe_stats.get("parsed_rows") or 0)
    first_time = probe_stats.get("first_time_s")
    last_time = probe_stats.get("last_time_s")
    probe_span: Optional[float] = None
    if first_time is not None and last_time is not None and probe_rows >= 2:
        candidate_span = float(last_time) - float(first_time)
        if math.isfinite(candidate_span) and candidate_span > 0:
            probe_span = candidate_span

    if probe_span is not None and probe_rows > 0:
        estimated_core_duration = (
            probe_span
            * DIGITAL_SEGMENT_TARGET_ROWS
            / probe_rows
            / (1.0 + DIGITAL_SEGMENT_VIEW_MARGIN_RATIO)
        )
        estimation_basis = "full_view_probe_event_density"
    else:
        estimated_core_duration = duration / 10.0
        estimation_basis = "ten_equal_slices_fallback"

    if (
        not math.isfinite(estimated_core_duration)
        or estimated_core_duration <= 0
    ):
        estimated_core_duration = duration / 10.0
        estimation_basis = "ten_equal_slices_invalid_density_fallback"

    estimated_core_duration = min(duration, estimated_core_duration)
    desired_count = max(1, int(math.ceil(duration / estimated_core_duration)))

    # Leave approximately half of the SCPI-query budget for recursive splits.
    initial_limit = max(1, DIGITAL_SEGMENT_MAX_COUNT // 2)
    initial_count = min(desired_count, initial_limit)
    return {
        "basis": estimation_basis,
        "probe_rows": probe_rows,
        "probe_event_span_s": probe_span,
        "estimated_core_duration_s": estimated_core_duration,
        "desired_initial_segment_count": desired_count,
        "initial_segment_count": initial_count,
        "initial_count_capped": initial_count != desired_count,
    }


def capture_segmented_digital_events(
    sock: socket.socket,
    dataset_dir: Path,
    analog_time_range: Dict[str, Any],
    probe_stats: Dict[str, Any],
    csv_path: Path,
    binary_path: Path,
    warnings: List[str],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Sweep the stopped decoder view and merge non-saturated event tables."""

    validate_segmented_digital_settings()
    bus = f":BUS{DIGITAL_BUS}"
    segment_dir = dataset_dir / "digital_segments"
    segment_dir.mkdir(parents=False, exist_ok=False)
    manifest_path = dataset_dir / "digital_segment_manifest.json"

    record_start = float(analog_time_range["start_time_s"])
    record_end = float(analog_time_range["end_time_exclusive_s"])
    duration = float(analog_time_range["duration_s"])
    estimate = estimate_initial_digital_segment_count(
        analog_time_range, probe_stats
    )
    initial_count = int(estimate["initial_segment_count"])
    core_width = duration / initial_count

    manifest: Dict[str, Any] = {
        "tool_version": CAPTURE_TOOL_VERSION,
        "method": "stopped_record_horizontal_segment_scan",
        "status": "running",
        "analog_record": {
            "start_time_s": record_start,
            "end_time_exclusive_s": record_end,
            "duration_s": duration,
        },
        "settings": {
            "empirical_saturation_rows": (
                DIGITAL_EVENT_TABLE_SATURATION_ROWS
            ),
            "target_rows_per_segment": DIGITAL_SEGMENT_TARGET_ROWS,
            "view_margin_ratio": DIGITAL_SEGMENT_VIEW_MARGIN_RATIO,
            "settle_time_s": DIGITAL_SEGMENT_SETTLE_S,
            "attempts_per_range": DIGITAL_SEGMENT_RETRIES,
            "maximum_table_queries": DIGITAL_SEGMENT_MAX_COUNT,
            "maximum_split_depth": DIGITAL_SEGMENT_MAX_SPLIT_DEPTH,
            "minimum_segment_duration_s": DIGITAL_SEGMENT_MIN_DURATION_S,
        },
        "initial_estimate": estimate,
        "full_view_probe": {
            "parsed_rows": probe_stats.get("parsed_rows"),
            "payload_bytes": probe_stats.get("payload_bytes"),
            "first_time_s": probe_stats.get("first_time_s"),
            "last_time_s": probe_stats.get("last_time_s"),
        },
        "attempts": [],
        "incomplete_ranges": [],
    }
    write_json(manifest_path, manifest)

    merge_writer = DigitalEventMergeWriter(csv_path, binary_path)
    query_count = 0
    accepted_table_count = 0
    split_table_count = 0
    saturated_leaf_count = 0
    parse_candidate_rows = 0
    parse_ignored_lines = 0
    parse_line_count = 0
    parse_payload_bytes = 0
    queried_payload_bytes = 0
    reported_errors: List[Dict[str, Any]] = []

    def save_manifest() -> None:
        manifest["progress"] = {
            "table_queries": query_count,
            "accepted_tables": accepted_table_count,
            "split_tables": split_table_count,
            "saturated_leaf_tables": saturated_leaf_count,
            "incomplete_range_count": len(manifest["incomplete_ranges"]),
            "merged_events_so_far": merge_writer.parsed_rows,
        }
        write_json(manifest_path, manifest)

    def process_range(
        core_start_s: float,
        core_end_s: float,
        depth: int,
        include_end: bool,
    ) -> None:
        nonlocal query_count
        nonlocal accepted_table_count
        nonlocal split_table_count
        nonlocal saturated_leaf_count
        nonlocal parse_candidate_rows
        nonlocal parse_ignored_lines
        nonlocal parse_line_count
        nonlocal parse_payload_bytes
        nonlocal queried_payload_bytes

        if query_count >= DIGITAL_SEGMENT_MAX_COUNT:
            manifest["incomplete_ranges"].append(
                {
                    "core_start_s": core_start_s,
                    "core_end_s": core_end_s,
                    "depth": depth,
                    "reason": "maximum_table_query_count_reached",
                }
            )
            save_manifest()
            return

        successful_entry: Optional[Dict[str, Any]] = None
        successful_events: Optional[List[Dict[str, Any]]] = None
        successful_stats: Optional[Dict[str, Any]] = None

        for retry_index in range(1, DIGITAL_SEGMENT_RETRIES + 1):
            if query_count >= DIGITAL_SEGMENT_MAX_COUNT:
                break
            query_count += 1
            raw_name = (
                f"segment_{query_count:05d}_depth_{depth:02d}.bin"
            )
            raw_path = segment_dir / raw_name
            raw_partial = raw_path.with_name(raw_path.name + ".partial")
            context = (
                f"digital segment query {query_count} "
                f"({core_start_s:.9e} to {core_end_s:.9e} s)"
            )
            entry: Dict[str, Any] = {
                "query_index": query_count,
                "retry_index": retry_index,
                "depth": depth,
                "core_start_s": core_start_s,
                "core_end_s": core_end_s,
                "core_duration_s": core_end_s - core_start_s,
                "include_end": include_end,
                "raw_file": f"digital_segments/{raw_name}",
                "status": "running",
            }
            manifest["attempts"].append(entry)
            save_manifest()

            try:
                view = configure_digital_segment_view(
                    sock, core_start_s, core_end_s, context
                )
                payload_length = scpi_query_ieee_block_to_file(
                    sock,
                    f"{bus}:DATA?",
                    raw_partial,
                    timeout=DIGITAL_BLOCK_TIMEOUT_S,
                    max_payload_bytes=None,
                )
                ensure_no_scpi_errors(sock, f"reading {context}")
                raw_partial.replace(raw_path)
                queried_payload_bytes += payload_length

                _, events, table_stats = parse_digital_events(
                    raw_path.read_bytes()
                )
                if table_stats["skipped_rows"]:
                    raise DataValidationError(
                        f"{context} skipped "
                        f"{table_stats['skipped_rows']} event rows"
                    )
                if table_stats["out_of_order_rows"]:
                    raise DataValidationError(
                        f"{context} contains out-of-order event times"
                    )

                saturated = int(table_stats["parsed_rows"]) >= (
                    DIGITAL_EVENT_TABLE_SATURATION_ROWS
                )
                entry.update(
                    {
                        "status": "queried",
                        "view": view,
                        "payload_bytes": payload_length,
                        "parse": table_stats,
                        "saturated": saturated,
                    }
                )
                successful_entry = entry
                successful_events = events
                successful_stats = table_stats
                save_manifest()
                break
            except Exception as exc:
                entry.update(
                    {
                        "status": "failed",
                        "error": {
                            "type": type(exc).__name__,
                            "message": str(exc),
                            "repr": repr(exc),
                        },
                    }
                )
                try:
                    entry["recovery_scpi_errors"] = drain_scpi_errors(sock)
                    entry["recovery_trigger_status"] = normalize_response(
                        scpi_query_line(sock, ":TRIGger:STATus?")
                    )
                except Exception as recovery_exc:
                    entry["recovery_error"] = repr(recovery_exc)
                save_manifest()

        if (
            successful_entry is None
            or successful_events is None
            or successful_stats is None
        ):
            manifest["incomplete_ranges"].append(
                {
                    "core_start_s": core_start_s,
                    "core_end_s": core_end_s,
                    "depth": depth,
                    "reason": "all_segment_query_attempts_failed",
                }
            )
            save_manifest()
            return

        saturated = bool(successful_entry["saturated"])
        core_duration = core_end_s - core_start_s
        can_split = (
            saturated
            and depth < DIGITAL_SEGMENT_MAX_SPLIT_DEPTH
            and core_duration / 2.0 >= DIGITAL_SEGMENT_MIN_DURATION_S
            and query_count + 2 <= DIGITAL_SEGMENT_MAX_COUNT
        )
        if can_split:
            split_table_count += 1
            successful_entry["disposition"] = "split_saturated_table"
            middle = (core_start_s + core_end_s) / 2.0
            successful_entry["split_at_s"] = middle
            save_manifest()
            process_range(core_start_s, middle, depth + 1, False)
            process_range(middle, core_end_s, depth + 1, include_end)
            return

        merge_result = merge_writer.write_core_events(
            successful_events,
            core_start_s,
            core_end_s,
            include_end,
        )
        successful_entry["merge"] = merge_result
        accepted_table_count += 1
        parse_candidate_rows += int(successful_stats["candidate_rows"])
        parse_ignored_lines += int(successful_stats["ignored_lines"])
        parse_line_count += int(successful_stats["line_count"])
        parse_payload_bytes += int(successful_stats["payload_bytes"])
        for error in successful_stats["reported_errors"]:
            if len(reported_errors) < MAX_REPORTED_PARSE_ERRORS:
                reported_errors.append(error)

        if saturated:
            saturated_leaf_count += 1
            successful_entry["disposition"] = (
                "accepted_but_still_saturated"
            )
            manifest["incomplete_ranges"].append(
                {
                    "core_start_s": core_start_s,
                    "core_end_s": core_end_s,
                    "depth": depth,
                    "reason": "event_table_still_saturated_at_split_limit",
                    "query_index": successful_entry["query_index"],
                }
            )
        else:
            successful_entry["disposition"] = "accepted_complete_table"

        print(
            "Digital slice "
            f"{accepted_table_count}: "
            f"{core_start_s:.6e}..{core_end_s:.6e} s, "
            f"table={len(successful_events):,}, "
            f"merged={merge_result['accepted_rows']:,}"
        )
        save_manifest()

    try:
        for index in range(initial_count):
            start = record_start + index * core_width
            end = (
                record_end
                if index == initial_count - 1
                else record_start + (index + 1) * core_width
            )
            process_range(
                start,
                end,
                depth=0,
                include_end=(index == initial_count - 1),
            )
        merged = merge_writer.finish()
    except Exception:
        merge_writer.abort()
        manifest["status"] = "aborted"
        manifest["fatal_error"] = traceback.format_exc()
        save_manifest()
        raise

    complete = (
        not manifest["incomplete_ranges"]
        and merged["out_of_order_rows"] == 0
    )
    manifest["status"] = "complete" if complete else "incomplete"
    manifest["result"] = {
        "complete": complete,
        "table_queries": query_count,
        "accepted_tables": accepted_table_count,
        "split_tables": split_table_count,
        "saturated_leaf_tables": saturated_leaf_count,
        "queried_payload_bytes": queried_payload_bytes,
        "accepted_payload_bytes": parse_payload_bytes,
        "merged_event_count": merged["parsed_rows"],
        "first_time_s": merged["first_time_s"],
        "last_time_s": merged["last_time_s"],
    }
    save_manifest()

    merged.update(
        {
            "payload_bytes": parse_payload_bytes,
            "queried_payload_bytes": queried_payload_bytes,
            "line_count": parse_line_count,
            "protocol_seen": accepted_table_count > 0,
            "header_seen": accepted_table_count > 0,
            "candidate_rows": parse_candidate_rows,
            "skipped_rows": 0,
            "ignored_lines": parse_ignored_lines,
            "reported_errors": reported_errors,
            "data_format": "DEC",
            "parse_completed": True,
            "streamed_from_instrument": True,
            "segmented": True,
            "segment_scan_complete": complete,
            "table_query_count": query_count,
            "accepted_table_count": accepted_table_count,
            "split_table_count": split_table_count,
            "saturated_leaf_count": saturated_leaf_count,
            "incomplete_range_count": len(manifest["incomplete_ranges"]),
            "manifest_file": manifest_path.name,
        }
    )
    if not complete:
        warnings.append(
            "One or more digital time slices could not be read completely; "
            f"see {manifest_path.name}."
        )
    return merged, manifest


def build_digital_coverage(
    *,
    event_count: int,
    first_time_s: Optional[float],
    last_time_s: Optional[float],
    digital_status: str,
    analog_time_range: Optional[Dict[str, Any]],
    digital_timebase_view: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Describe only the time interval for which digital data is trustworthy."""

    analog_start: Optional[float] = None
    analog_end: Optional[float] = None
    analog_duration: Optional[float] = None
    if analog_time_range is not None:
        analog_start = float(analog_time_range["start_time_s"])
        analog_end = float(analog_time_range["end_time_exclusive_s"])
        analog_duration = float(analog_time_range["duration_s"])

    accepted_view: Dict[str, Any] = {}
    decoder_contains_analog = False
    if digital_timebase_view is not None:
        candidate = digital_timebase_view.get("accepted")
        if isinstance(candidate, dict):
            accepted_view = candidate
        decoder_contains_analog = bool(
            digital_timebase_view.get("analog_record_contained")
        )

    display_start = accepted_view.get("display_start_time_s")
    display_end = accepted_view.get("display_end_time_s")
    if display_start is not None:
        display_start = float(display_start)
    if display_end is not None:
        display_end = float(display_end)

    event_start = (
        float(first_time_s)
        if event_count > 0 and first_time_s is not None
        else None
    )
    event_end = (
        float(last_time_s)
        if event_count > 0 and last_time_s is not None
        else None
    )

    validated = bool(
        digital_status == "success"
        and event_count > 0
        and analog_start is not None
        and analog_end is not None
        and decoder_contains_analog
    )
    if event_start is not None and event_end is not None:
        # :BUSn:DATA? is an event representation. Even when the decoder view
        # contains the complete analog record, do not invent a state before
        # the first returned event or after the last returned event.
        valid_start = event_start
        valid_end = event_end
        if analog_start is not None and analog_end is not None:
            valid_start = max(valid_start, analog_start)
            valid_end = min(valid_end, analog_end)

    else:
        valid_start = None
        valid_end = None

    if (
        validated
        and valid_start is not None
        and valid_end is not None
        and valid_end >= valid_start
    ):
        validity_basis = (
            "observed_event_span_with_full_decoder_view_and_"
            "activity_validation"
        )
        coverage_status = "validated"
    elif valid_start is not None and valid_end is not None:
        if valid_end < valid_start:
            valid_start = None
            valid_end = None
            validity_basis = "event_times_do_not_overlap_analog_record"
            coverage_status = "unavailable"
        else:
            validity_basis = "observed_event_span_only"
            coverage_status = "partial"
    else:
        valid_start = None
        valid_end = None
        validity_basis = "no_digital_event_state_available"
        coverage_status = "unavailable"

    valid_span = (
        max(0.0, float(valid_end) - float(valid_start))
        if valid_start is not None and valid_end is not None
        else None
    )
    coverage_ratio = (
        min(1.0, valid_span / analog_duration)
        if valid_span is not None
        and analog_duration is not None
        and analog_duration > 0
        else None
    )

    return {
        "status": coverage_status,
        "validity_basis": validity_basis,
        "valid_time_start_s": valid_start,
        "valid_time_end_s": valid_end,
        "valid_time_span_s": valid_span,
        "analog_coverage_ratio": coverage_ratio,
        "analog_record_start_s": analog_start,
        "analog_record_end_s": analog_end,
        "analog_record_duration_s": analog_duration,
        "decoder_display_start_s": display_start,
        "decoder_display_end_s": display_end,
        "decoder_view_contains_analog_record": decoder_contains_analog,
        "event_start_s": event_start,
        "event_end_s": event_end,
        "event_count": event_count,
    }


def _capture_full_view_digital_probe(
    sock: socket.socket,
    dataset_dir: Path,
    digital_configuration: Dict[str, Any],
    analog_time_range: Optional[Dict[str, Any]],
    digital_timebase_view: Optional[Dict[str, Any]],
    warnings: List[str],
) -> Dict[str, Any]:
    bus = f":BUS{DIGITAL_BUS}"

    raw_bin_path = dataset_dir / "digital_bus_raw.bin"
    raw_partial_path = dataset_dir / "digital_bus_raw.bin.partial"
    raw_text_path = dataset_dir / "digital_bus_raw.txt"
    csv_path = dataset_dir / "digital_events.csv"
    binary_path = dataset_dir / "digital_events.bin"
    report_path = dataset_dir / "digital_parse_report.json"
    coverage_path = dataset_dir / "digital_coverage.json"
    info_path = dataset_dir / "digital_info.txt"

    stats: Dict[str, Any] = {
        "payload_bytes": None,
        "parse_completed": False,
        "streamed_from_instrument": True,
    }
    payload_length = 0
    raw_text_method: Optional[str] = None

    try:
        # :BUSn:DATA? returns the complete current event table as one block.
        # Stream it so the software imposes no 512 MiB cap and does not retain
        # the whole table in RAM.
        payload_length = scpi_query_ieee_block_to_file(
            sock,
            f"{bus}:DATA?",
            raw_partial_path,
            timeout=DIGITAL_BLOCK_TIMEOUT_S,
            max_payload_bytes=None,
        )
        stats["payload_bytes"] = payload_length
        ensure_no_scpi_errors(sock, "reading Parallel BUS event table")

        actual_payload_size = raw_partial_path.stat().st_size
        if actual_payload_size != payload_length:
            raise DataValidationError(
                "Digital event-table file size mismatch: declared "
                f"{payload_length:,}, saved {actual_payload_size:,} bytes"
            )
        raw_partial_path.replace(raw_bin_path)

        try:
            raw_text_method = create_digital_raw_text_alias(
                raw_bin_path,
                raw_text_path,
            )
        except OSError as exc:
            warnings.append(
                "digital_bus_raw.txt could not be created; the complete "
                "ASCII event payload remains available in "
                f"{raw_bin_path.name}. Error: {exc}"
            )

        stats = stream_digital_event_files(
            raw_bin_path,
            csv_path,
            binary_path,
        )
        stats["parse_completed"] = True
        stats["streamed_from_instrument"] = True
        stats["raw_text_representation"] = raw_text_method

        if stats["skipped_rows"]:
            raise DataValidationError(
                "Digital event parsing skipped "
                f"{stats['skipped_rows']:,} candidate rows; see "
                f"{report_path.name}"
            )
        if stats["out_of_order_rows"]:
            raise DataValidationError(
                "Digital event times are not monotonic; see "
                f"{report_path.name}"
            )
    except Exception as exc:
        stats["error"] = repr(exc)
        raise
    finally:
        write_json(report_path, stats)

    event_count = int(stats["parsed_rows"])
    if event_count == 0:
        warnings.append(
            "The Parallel event table contained no events. This can be valid "
            "for a constant bus, but the probe, threshold, and displayed time "
            "range should be checked."
        )

    event_span_s: Optional[float] = None
    event_span_ratio: Optional[float] = None
    if event_count >= 2:
        event_span_s = float(stats["last_time_s"]) - float(
            stats["first_time_s"]
        )

    validation_reasons: List[str] = []
    validation_required = bool(
        DIGITAL_ACTIVITY_EXPECTED_ACROSS_RECORD
        and analog_time_range is not None
    )
    if analog_time_range is not None and event_span_s is not None:
        analog_duration_s = float(analog_time_range["duration_s"])
        event_span_ratio = event_span_s / analog_duration_s
    else:
        analog_duration_s = (
            float(analog_time_range["duration_s"])
            if analog_time_range is not None
            else None
        )

    if validation_required:
        if event_span_ratio is None:
            validation_reasons.append(
                "fewer than two digital events were returned"
            )
        elif event_span_ratio < DIGITAL_MIN_EVENT_SPAN_RATIO:
            validation_reasons.append(
                "digital event span is only "
                f"{event_span_ratio:.6%} of the analog record; expected at "
                f"least {DIGITAL_MIN_EVENT_SPAN_RATIO:.1%} because digital "
                "activity is configured as continuous"
            )

    if event_count == 0:
        validation_reasons.append(
            "the event table contains no digital state to display"
        )

    decoder_contains_analog = bool(
        digital_timebase_view is not None
        and digital_timebase_view.get("analog_record_contained")
    )
    if analog_time_range is not None and not decoder_contains_analog:
        validation_reasons.append(
            "the verified decoder display does not contain the complete "
            "analog record"
        )

    activity_validation = {
        "required": validation_required,
        "expectation": (
            "transitions_across_record"
            if DIGITAL_ACTIVITY_EXPECTED_ACROSS_RECORD
            else "not_required"
        ),
        "minimum_event_span_ratio": DIGITAL_MIN_EVENT_SPAN_RATIO,
        "analog_duration_s": analog_duration_s,
        "event_span_s": event_span_s,
        "event_span_ratio": event_span_ratio,
        "passed": not validation_reasons,
        "reasons": validation_reasons,
    }
    digital_status = "incomplete" if validation_reasons else "success"
    coverage = build_digital_coverage(
        event_count=event_count,
        first_time_s=stats["first_time_s"],
        last_time_s=stats["last_time_s"],
        digital_status=digital_status,
        analog_time_range=analog_time_range,
        digital_timebase_view=digital_timebase_view,
    )
    write_json(coverage_path, coverage)
    if validation_reasons:
        warnings.append(
            "Digital acquisition is incomplete: "
            + "; ".join(validation_reasons)
            + ". The returned files are retained for diagnosis, but must not "
            "be interpreted as the complete D0-D15 waveform."
        )

    info_lines = [
        "MHO984 digital acquisition",
        "",
        "Method: 16-bit Parallel BUS event table",
        "Bus source: D0D15 (bit 0 = D0, bit 15 = D15)",
        "Bus format: DEC",
        "Clock: OFF (event generated when the bus value changes)",
        "",
        f"Payload bytes: {payload_length}",
        f"Parsed events: {event_count}",
        f"Skipped rows: {stats['skipped_rows']}",
        f"Out-of-order rows: {stats['out_of_order_rows']}",
        (
            "Transfer: complete IEEE/TMC block for the current event table "
            "streamed directly to disk"
        ),
        "Parser: incremental; event rows are not accumulated in RAM",
        "",
        "POD thresholds:",
        (
            f"POD1 D0-D7: "
            f"{digital_configuration['pod_threshold_v'][1]:.12g} V"
        ),
        (
            f"POD2 D8-D15: "
            f"{digital_configuration['pod_threshold_v'][2]:.12g} V"
        ),
        "",
        "Binary record format:",
        "little-endian <float64 time_s, uint16 bus_value>, 10 bytes/record",
        "",
        "Limitation:",
        (
            "This is a transition/event representation from the current "
            "decoder event table, not a uniformly sampled D0-D15 RAW waveform."
        ),
        (
            "The contents of the event table can depend on the oscilloscope "
            "horizontal time range."
        ),
    ]

    if analog_time_range is not None:
        info_lines.extend(
            [
                "",
                "Analog reference range:",
                (
                    f"{float(analog_time_range['start_time_s']):.15e} to "
                    f"{float(analog_time_range['end_time_exclusive_s']):.15e} s"
                ),
                (
                    "Duration: "
                    f"{float(analog_time_range['duration_s']):.15e} s"
                ),
            ]
        )
    if digital_timebase_view is not None:
        accepted_view = digital_timebase_view.get("accepted", {})
        if (
            "display_start_time_s" in accepted_view
            and "display_end_time_s" in accepted_view
        ):
            info_lines.extend(
                [
                    "",
                    "Decoder display range used for BUS DATA:",
                    (
                        f"{float(accepted_view['display_start_time_s']):.15e} "
                        "to "
                        f"{float(accepted_view['display_end_time_s']):.15e} s"
                    ),
                    (
                        "Contains complete analog record: "
                        f"{digital_timebase_view['analog_record_contained']}"
                    ),
                ]
            )

    if event_count:
        info_lines.extend(
            [
                "",
                f"First event: {stats['first_time_s']:.15e} s",
                f"Last event: {stats['last_time_s']:.15e} s",
            ]
        )
    if event_span_ratio is not None:
        info_lines.append(
            "Event span / analog duration: "
            f"{event_span_ratio:.9%}"
        )
    info_lines.extend(
        [
            "",
            f"Verified digital coverage: {coverage['status'].upper()}",
            f"Coverage basis: {coverage['validity_basis']}",
            (
                "Valid time range: "
                f"{coverage['valid_time_start_s']!r} to "
                f"{coverage['valid_time_end_s']!r} s"
            ),
            (
                "Analog coverage ratio: "
                f"{coverage['analog_coverage_ratio']!r}"
            ),
        ]
    )
    info_lines.extend(
        [
            "",            f"Activity validation: {digital_status.upper()}",
            *[f"Reason: {reason}" for reason in validation_reasons],
            "",
            "Per-channel observed activity:",
        ]
    )
    for channel in DIGITAL_CHANNELS:
        activity = stats["channel_activity"][channel]
        info_lines.append(
            f"{channel}: transitions={activity['transition_count']}, "
            f"low={activity['observed_low']}, "
            f"high={activity['observed_high']}"
        )
    write_lines(info_path, info_lines)

    return {
        "status": digital_status,
        "method": "parallel_event_table_full_acquisition_view",
        "raw_digital_waveform": False,
        "event_count": event_count,
        "payload_bytes": payload_length,
        "first_time_s": stats["first_time_s"],
        "last_time_s": stats["last_time_s"],
        "event_span_s": event_span_s,
        "event_span_ratio_of_analog_record": event_span_ratio,
        "activity_validation": activity_validation,
        "coverage": coverage,
        "channel_activity": stats["channel_activity"],
        "analog_time_range": analog_time_range,
        "digital_timebase_view": digital_timebase_view,
        "files": {
            "raw_binary": raw_bin_path.name,
            "raw_text": (
                raw_text_path.name if raw_text_path.exists() else None
            ),
            "csv": csv_path.name,
            "binary": binary_path.name,
            "parse_report": report_path.name,
            "coverage": coverage_path.name,
            "information": info_path.name,
        },
        "binary_record_size_bytes": 10,
        "parse": stats,
    }


def capture_digital_events(
    sock: socket.socket,
    dataset_dir: Path,
    digital_configuration: Dict[str, Any],
    analog_time_range: Optional[Dict[str, Any]],
    digital_timebase_view: Optional[Dict[str, Any]],
    warnings: List[str],
) -> Dict[str, Any]:
    """Read the full-view probe, then segment the STOP record if it caps."""

    probe_warnings: List[str] = []
    probe_result = _capture_full_view_digital_probe(
        sock,
        dataset_dir,
        digital_configuration,
        analog_time_range,
        digital_timebase_view,
        probe_warnings,
    )
    probe_stats = probe_result["parse"]
    probe_rows = int(probe_result["event_count"])
    probe_saturated = probe_rows >= DIGITAL_EVENT_TABLE_SATURATION_ROWS
    probe_incomplete = probe_result["status"] != "success"
    should_segment = bool(
        DIGITAL_SEGMENTED_CAPTURE_ENABLED
        and analog_time_range is not None
        and (probe_saturated or probe_incomplete)
    )

    if not should_segment:
        warnings.extend(probe_warnings)
        return probe_result

    print(
        "Full-view digital table requires segmentation: "
        f"rows={probe_rows:,}, saturated={probe_saturated}, "
        f"status={probe_result['status']}"
    )
    for warning in probe_warnings:
        if not warning.startswith("Digital acquisition is incomplete:"):
            warnings.append(warning)

    csv_path = dataset_dir / "digital_events.csv"
    binary_path = dataset_dir / "digital_events.bin"
    report_path = dataset_dir / "digital_parse_report.json"
    coverage_path = dataset_dir / "digital_coverage.json"
    info_path = dataset_dir / "digital_info.txt"

    stats, segment_manifest = capture_segmented_digital_events(
        sock,
        dataset_dir,
        analog_time_range,
        probe_stats,
        csv_path,
        binary_path,
        warnings,
    )
    stats["probe_full_view"] = probe_stats
    stats["probe_saturated"] = probe_saturated
    stats["probe_event_count"] = probe_rows
    stats["probe_payload_bytes"] = probe_result["payload_bytes"]
    write_json(report_path, stats)

    event_count = int(stats["parsed_rows"])
    event_span_s: Optional[float] = None
    event_span_ratio: Optional[float] = None
    if event_count >= 2:
        event_span_s = float(stats["last_time_s"]) - float(
            stats["first_time_s"]
        )

    analog_duration_s = float(analog_time_range["duration_s"])
    if event_span_s is not None:
        event_span_ratio = event_span_s / analog_duration_s

    validation_required = bool(
        DIGITAL_ACTIVITY_EXPECTED_ACROSS_RECORD
        and analog_time_range is not None
    )
    validation_reasons: List[str] = []
    if not stats.get("segment_scan_complete"):
        validation_reasons.append(
            "one or more stopped-record time slices failed or remained "
            "saturated; see digital_segment_manifest.json"
        )
    if stats.get("out_of_order_rows"):
        validation_reasons.append(
            "merged digital event times are not monotonic"
        )
    if event_count == 0:
        validation_reasons.append(
            "the segmented event tables contain no digital state to display"
        )
    if validation_required:
        if event_span_ratio is None:
            validation_reasons.append(
                "fewer than two merged digital events were returned"
            )
        elif event_span_ratio < DIGITAL_MIN_EVENT_SPAN_RATIO:
            validation_reasons.append(
                "merged digital event span is only "
                f"{event_span_ratio:.6%} of the analog record; expected at "
                f"least {DIGITAL_MIN_EVENT_SPAN_RATIO:.1%} because digital "
                "activity is configured as continuous"
            )

    decoder_contains_analog = bool(
        digital_timebase_view is not None
        and digital_timebase_view.get("analog_record_contained")
    )
    if not decoder_contains_analog:
        validation_reasons.append(
            "the verified full-record decoder display does not contain the "
            "complete analog record"
        )

    activity_validation = {
        "required": validation_required,
        "expectation": (
            "transitions_across_record"
            if DIGITAL_ACTIVITY_EXPECTED_ACROSS_RECORD
            else "not_required"
        ),
        "minimum_event_span_ratio": DIGITAL_MIN_EVENT_SPAN_RATIO,
        "analog_duration_s": analog_duration_s,
        "event_span_s": event_span_s,
        "event_span_ratio": event_span_ratio,
        "segment_scan_complete": bool(
            stats.get("segment_scan_complete")
        ),
        "passed": not validation_reasons,
        "reasons": validation_reasons,
    }
    digital_status = "incomplete" if validation_reasons else "success"
    coverage = build_digital_coverage(
        event_count=event_count,
        first_time_s=stats["first_time_s"],
        last_time_s=stats["last_time_s"],
        digital_status=digital_status,
        analog_time_range=analog_time_range,
        digital_timebase_view=digital_timebase_view,
    )
    coverage["segment_scan_complete"] = bool(
        stats.get("segment_scan_complete")
    )
    coverage["segment_manifest"] = "digital_segment_manifest.json"
    write_json(coverage_path, coverage)

    if validation_reasons:
        warnings.append(
            "Digital segmented acquisition is incomplete: "
            + "; ".join(validation_reasons)
            + ". Retained event files must not be interpreted as the "
            "complete D0-D15 waveform."
        )

    info_lines = [
        "MHO984 digital acquisition",
        "",
        "Method: stopped-record segmented 16-bit Parallel BUS event tables",
        "Bus source: D0D15 (bit 0 = D0, bit 15 = D15)",
        "Bus format: DEC",
        "Clock: OFF (event generated when the bus value changes)",
        "",
        (
            "Full-view probe: "
            f"{probe_rows:,} rows, {probe_result['payload_bytes']:,} bytes, "
            f"empirical saturation={probe_saturated}"
        ),
        (
            "Segment tables queried: "
            f"{stats['table_query_count']:,}"
        ),
        (
            "Accepted non-parent tables: "
            f"{stats['accepted_table_count']:,}"
        ),
        f"Merged events: {event_count:,}",
        f"Accepted segment payload bytes: {stats['payload_bytes']:,}",
        f"All queried segment payload bytes: {stats['queried_payload_bytes']:,}",
        f"Skipped parse rows: {stats['skipped_rows']}",
        f"Out-of-order merged rows: {stats['out_of_order_rows']}",
        f"Duplicate boundary rows removed: {stats['duplicates_skipped']}",
        (
            "Segment scan complete: "
            f"{stats['segment_scan_complete']}"
        ),
        "Segment diagnostics: digital_segment_manifest.json",
        "Raw segment tables: digital_segments/",
        "",
        "POD thresholds:",
        (
            f"POD1 D0-D7: "
            f"{digital_configuration['pod_threshold_v'][1]:.12g} V"
        ),
        (
            f"POD2 D8-D15: "
            f"{digital_configuration['pod_threshold_v'][2]:.12g} V"
        ),
        "",
        "Binary record format:",
        "little-endian <float64 time_s, uint16 bus_value>, 10 bytes/record",
        "",
        "Representation:",
        (
            "Transition/event rows merged from multiple decoder time views; "
            "this is not a uniformly sampled D0-D15 RAW waveform."
        ),
        (
            "digital_bus_raw.bin/.txt is the capped full-view probe. The "
            "complete merged result is digital_events.bin/.csv."
        ),
        "",
        "Analog reference range:",
        (
            f"{float(analog_time_range['start_time_s']):.15e} to "
            f"{float(analog_time_range['end_time_exclusive_s']):.15e} s"
        ),
        f"Duration: {analog_duration_s:.15e} s",
    ]
    if event_count:
        info_lines.extend(
            [
                "",
                f"First merged event: {stats['first_time_s']:.15e} s",
                f"Last merged event: {stats['last_time_s']:.15e} s",
            ]
        )
    if event_span_ratio is not None:
        info_lines.append(
            "Merged event span / analog duration: "
            f"{event_span_ratio:.9%}"
        )
    info_lines.extend(
        [
            "",
            f"Verified digital coverage: {coverage['status'].upper()}",
            f"Coverage basis: {coverage['validity_basis']}",
            (
                "Valid time range: "
                f"{coverage['valid_time_start_s']!r} to "
                f"{coverage['valid_time_end_s']!r} s"
            ),
            (
                "Analog coverage ratio: "
                f"{coverage['analog_coverage_ratio']!r}"
            ),
            "",
            f"Activity validation: {digital_status.upper()}",
            *[f"Reason: {reason}" for reason in validation_reasons],
            "",
            "Per-channel observed activity:",
        ]
    )
    for channel in DIGITAL_CHANNELS:
        activity = stats["channel_activity"][channel]
        info_lines.append(
            f"{channel}: transitions={activity['transition_count']}, "
            f"low={activity['observed_low']}, "
            f"high={activity['observed_high']}"
        )
    write_lines(info_path, info_lines)

    return {
        "status": digital_status,
        "method": "parallel_event_table_segmented_stopped_record",
        "raw_digital_waveform": False,
        "event_count": event_count,
        "payload_bytes": int(stats["payload_bytes"]),
        "queried_payload_bytes": int(stats["queried_payload_bytes"]),
        "probe_event_count": probe_rows,
        "probe_payload_bytes": probe_result["payload_bytes"],
        "probe_saturated": probe_saturated,
        "first_time_s": stats["first_time_s"],
        "last_time_s": stats["last_time_s"],
        "event_span_s": event_span_s,
        "event_span_ratio_of_analog_record": event_span_ratio,
        "activity_validation": activity_validation,
        "coverage": coverage,
        "channel_activity": stats["channel_activity"],
        "analog_time_range": analog_time_range,
        "digital_timebase_view": digital_timebase_view,
        "segmentation": segment_manifest.get("result", {}),
        "files": {
            "raw_binary_probe": "digital_bus_raw.bin",
            "raw_text_probe": (
                "digital_bus_raw.txt"
                if (dataset_dir / "digital_bus_raw.txt").exists()
                else None
            ),
            "segment_raw_directory": "digital_segments",
            "segment_manifest": "digital_segment_manifest.json",
            "csv": csv_path.name,
            "binary": binary_path.name,
            "parse_report": report_path.name,
            "coverage": coverage_path.name,
            "information": info_path.name,
        },
        "binary_record_size_bytes": 10,
        "parse": stats,
    }


# ============================================================================
# Main
# ============================================================================



# ============================================================================
# r9 synchronized memory-export acquisition
# ============================================================================

# r9.1 deliberately does NOT configure D0-D15, Parallel BUS, decoder event tables,
# horizontal timebase, or POD thresholds. Configure the desired LA channels on
# the front panel before running this program.
#
# The r6 probe on the user's MHO984 (firmware 00.01.00) confirmed that
# :SAVE:MEMory:WAVeform succeeds while stopped, whereas
# :LA:DIGital0:DISPlay? ... :LA:DIGital15:DISPlay? are invalid/timeout-prone.
R10_INSTRUMENT_MEMORY_DIRECTORY = "C:/"  # controller overrides for SMB
R10_MEMORY_FILENAME_PREFIX = "mho984_"
R10_MEMORY_SAVE_TIMEOUT_S = 180.0
R10_MEMORY_SAVE_POLL_INTERVAL_S = 1.0
# Firmware compatibility: some MHO984 firmware revisions can keep
# :SAVE:STATus? at 0 for an extended period, or expose the completed file to
# FTP after the SCPI save-status indication. The integrated controller may
# therefore defer final save confirmation to the FTP/RG03 validation stage.
# Standalone capture keeps the conservative behavior unless explicitly enabled.
R10_ALLOW_DEFERRED_MEMORY_SAVE_CONFIRMATION = False


def r10_query_basic_logic_state(sock: socket.socket) -> Dict[str, Any]:
    """Query only generic LA/POD state known to respond on the tested MHO984."""
    result: Dict[str, Any] = {}
    queries = {
        "la_enable": ":LA:ENABle?",
        "pod1_display": ":LA:POD1:DISPlay?",
        "pod2_display": ":LA:POD2:DISPlay?",
    }
    for key, command in queries.items():
        try:
            result[key] = {
                "ok": True,
                "value": normalize_response(scpi_query_line(sock, command)),
            }
        except Exception as exc:
            # Diagnostic queries must never abort the acquisition.
            result[key] = {
                "ok": False,
                "error": repr(exc),
            }
            try:
                result[key]["cleared_errors"] = drain_scpi_errors(sock)
            except Exception:
                pass
    return result


def r10_wait_memory_save(
    sock: socket.socket,
    timeout_s: float = R10_MEMORY_SAVE_TIMEOUT_S,
) -> Dict[str, Any]:
    started = time.monotonic()
    deadline = started + timeout_s
    history: List[Dict[str, Any]] = []

    while time.monotonic() < deadline:
        try:
            value_text = scpi_query_line(sock, ":SAVE:STATus?")
            value = int(float(value_text))
            history.append({
                "elapsed_s": round(time.monotonic() - started, 6),
                "ok": True,
                "value": value,
            })
            # On the tested MHO984 the r6 probe observed 1 immediately after
            # completion. Do not keep polling and stress the UI once completed.
            if value == 1:
                return {
                    "completed": True,
                    "elapsed_s": time.monotonic() - started,
                    "history": history,
                }
        except Exception as exc:
            history.append({
                "elapsed_s": round(time.monotonic() - started, 6),
                "ok": False,
                "error": repr(exc),
            })
        time.sleep(R10_MEMORY_SAVE_POLL_INTERVAL_S)

    return {
        "completed": False,
        "elapsed_s": time.monotonic() - started,
        "history": history,
    }


def r10_save_stopped_memory_waveform(
    sock: socket.socket,
    instrument_path: str,
) -> Dict[str, Any]:
    """
    Save the untouched STOP acquisition to an instrument-side BIN file.

    IMPORTANT:
      * Must be called immediately after the SINGLE acquisition reaches STOP.
      * Does not change timebase, LA, BUS, trigger, or WAVeform source.
      * File transfer from the instrument is intentionally not guessed here.
        The BIN remains on the instrument until a verified transfer route is
        implemented.
    """
    trigger_status = normalize_response(
        scpi_query_line(sock, ":TRIGger:STATus?")
    )
    ensure_no_scpi_errors(sock, "checking STOP before memory export")
    if trigger_status != "STOP":
        raise DataValidationError(
            f"Memory export requires STOP, got {trigger_status!r}"
        )

    errors_before = drain_scpi_errors(sock)
    started_at = now_iso()
    started = time.monotonic()

    scpi_send(sock, f":SAVE:MEMory:WAVeform {instrument_path}")
    completion = r10_wait_memory_save(sock)
    errors_after = drain_scpi_errors(sock)

    success = (
        completion.get("completed") is True
        and not errors_after
    )
    deferred_candidate = (
        completion.get("completed") is not True
        and not errors_after
    )

    return {
        "status": (
            "success"
            if success
            else ("pending_ftp_verification" if deferred_candidate else "failed")
        ),
        "success": success,
        "deferred_candidate": deferred_candidate,
        "started_at": started_at,
        "completed_at": now_iso(),
        "elapsed_s": time.monotonic() - started,
        "instrument_path": instrument_path,
        "format": "BIN",
        "same_stopped_record": True,
        "performed_before_pc_analog_readout": True,
        "completion": completion,
        "errors_before": errors_before,
        "errors_after": errors_after,
        "pc_copy": {
            "status": "direct_smb_expected",
            "decoder": "mho984_bin_decoder_r12.py",
            "note": (
                "r11.1 LAN controller retrieves the completed C:/ Memory BIN over LAN "
                "probe before acquisition, then points this memory save directly "
                "at the verified SMB destination."
            ),
        },
    }


def r10_minimal_stop_guard(sock: socket.socket) -> str:
    """Verify that analog readout has not restarted acquisition."""
    status = normalize_response(scpi_query_line(sock, ":TRIGger:STATus?"))
    ensure_no_scpi_errors(sock, "r8 STOP guard")
    if status != "STOP":
        raise DataValidationError(
            f"Acquisition record is no longer STOP: {status!r}"
        )
    return status


def main() -> None:
    dataset_dir = next_dataset_dir(BASE_DIR, DATASET_PREFIX, DIGITS)
    analog_dir = dataset_dir / "analog"
    digital_dir = dataset_dir / "digital"
    analog_dir.mkdir(parents=True, exist_ok=True)
    digital_dir.mkdir(parents=True, exist_ok=True)

    acquisition_id = str(uuid.uuid4())
    started_at = now_iso()
    timestamp_token = datetime.now().strftime("%Y%m%d_%H%M%S")
    instrument_bin_name = (
        f"{R10_MEMORY_FILENAME_PREFIX}{timestamp_token}.bin"
    )
    if len(instrument_bin_name) > 26:
        raise DataValidationError(
            "Instrument Memory BIN filename exceeds the current MHO900 "
            f"Programming Guide limit (26 chars): {instrument_bin_name!r}"
        )
    instrument_bin_path = (
        f"{R10_INSTRUMENT_MEMORY_DIRECTORY}{instrument_bin_name}"
    )

    print(f"Dataset directory: {dataset_dir}")
    print(f"Capture tool version: {CAPTURE_TOOL_VERSION}")
    print("")
    print("r12.7 BIN-only safety policy:")
    print("  - no D0-D15 individual display queries")
    print("  - no BUS event-table scan")
    print("  - no horizontal timebase changes")
    print("  - no LA/POD configuration changes")
    print("  - memory BIN save immediately after SINGLE -> STOP")
    print("  - NO CH1-CH4 SCPI waveform transfer; Analog comes from RG03 BIN")
    print("")

    write_json(
        dataset_dir / "capture_tool_version.json",
        {
            "tool": "RIGOL MHO984 synchronized memory acquisition",
            "version": CAPTURE_TOOL_VERSION,
            "source_file": Path(__file__).name,
            "started_at": started_at,
        },
    )

    (dataset_dir / "timestamp.txt").write_text(
        started_at + "\n", encoding="utf-8"
    )
    (dataset_dir / "notes.txt").write_text(
        "MHO984 synchronized acquisition r12 BIN-only\n"
        "\n"
        "Run condition:\n"
        "- Configure desired analog and D0-D15 channels on the front panel.\n"
        "- This program does not change LA/Digital/BUS/timebase settings.\n"
        "- One fresh SINGLE acquisition is made.\n"
        "- Immediately after STOP, the untouched instrument memory is saved "
        "as BIN on the scope.\n"
        "- beta.8 uses a 26-character-or-shorter instrument filename for current "
        "MHO900 firmware compatibility. If the scope appends numeric digits, the "
        "retriever accepts the actual variant automatically.\n"
        "- Analog RAW SCPI transfer is intentionally skipped.\n- CH1-CH4 are decoded directly from the same RG03 Memory BIN on the PC.\n"
        "\n"
        "The r11.1 LAN controller uses proven C:/ Memory BIN save. The synchronized "
        "Memory BIN is then written to the SMB share and automatically decoded.\n",
        encoding="utf-8",
    )

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(CONNECT_TIMEOUT_S)

    connected = False
    selected_analog: List[str] = []
    analog_results: Dict[str, Any] = {}
    analog_failures: Dict[str, Any] = {}
    analog_time_range: Optional[Dict[str, Any]] = None
    acquisition_result: Optional[Dict[str, Any]] = None
    memory_export_result: Optional[Dict[str, Any]] = None
    logic_state: Optional[Dict[str, Any]] = None
    warnings: List[str] = []
    idn: Optional[str] = None
    success = False
    partial_success = False
    error_details: Optional[Dict[str, Any]] = None
    caught_exception: Optional[BaseException] = None
    caught_traceback = None

    try:
        print(f"Connecting to {OSC_IP}:{OSC_PORT} ...")
        sock.connect((OSC_IP, OSC_PORT))
        connected = True
        print("Connected.")

        preexisting_errors = drain_scpi_errors(sock)
        write_json(
            dataset_dir / "preexisting_scpi_errors.json",
            preexisting_errors,
        )
        if preexisting_errors:
            warnings.append(
                "Pre-existing SCPI errors were present and cleared before r10."
            )

        idn = scpi_query_line(sock, "*IDN?")
        ensure_no_scpi_errors(sock, "querying instrument identification")
        (dataset_dir / "idn.txt").write_text(
            idn + "\n", encoding="utf-8"
        )
        print(f"Instrument: {idn}")

        if VERIFY_MODEL and EXPECTED_MODEL_TOKEN.upper() not in idn.upper():
            raise AcquisitionError(
                f"Connected instrument is not the expected "
                f"{EXPECTED_MODEL_TOKEN}: {idn!r}"
            )

        # Only the three generic LA/POD queries that succeeded in the r6 log.
        logic_state = r10_query_basic_logic_state(sock)
        write_json(dataset_dir / "logic_state.json", logic_state)

        if (
            logic_state.get("la_enable", {}).get("ok")
            and logic_state["la_enable"].get("value") not in ("1", "ON")
        ):
            warnings.append(
                "Logic analyzer appears disabled. Enable LA/D0-D15 on the "
                "front panel before acquisition if digital data are required."
            )

        selected_analog, channel_log = select_analog_channels(sock)
        write_lines(
            dataset_dir / "channel_selection.txt",
            [
                *channel_log,
                "",
                "selected=" + ",".join(selected_analog),
            ],
        )
        print(
            "Analog channels: "
            + (", ".join(selected_analog) if selected_analog else "(none)")
        )

        print("")
        print("========== SINGLE ACQUISITION ==========")
        acquisition_result = acquire_single_record(
            sock,
            dataset_dir / "acquisition_log.json",
        )
        print("Fresh acquisition reached STOP.")

        # This is deliberately FIRST after STOP.
        print("")
        print("========== SYNCHRONIZED MEMORY SAVE ==========")
        print(f"Saving untouched stopped memory to {instrument_bin_path}")
        memory_export_result = r10_save_stopped_memory_waveform(
            sock,
            instrument_bin_path,
        )
        write_json(
            dataset_dir / "memory_waveform_export.json",
            memory_export_result,
        )
        if not memory_export_result["success"]:
            if (
                R10_ALLOW_DEFERRED_MEMORY_SAVE_CONFIRMATION
                and memory_export_result.get("deferred_candidate")
            ):
                warnings.append(
                    "Memory save completion was not confirmed by :SAVE:STATus? "
                    "within the extended timeout, but no SCPI error was reported. "
                    "The integrated controller will continue and require the "
                    "expected RG03 file to appear on FTP, reach a stable size, "
                    "download exactly, and pass decoder validation before the "
                    "acquisition is accepted."
                )
                partial_success = True
                print(
                    "Memory save status remains pending with no SCPI error; "
                    "deferring final confirmation to FTP/RG03 validation."
                )
            else:
                raise AcquisitionError(
                    "Instrument memory BIN save failed; refusing to claim "
                    "synchronized digital acquisition."
                )
        else:
            print("Instrument memory BIN save completed without SCPI error.")

        # Confirm that saving did not restart acquisition.
        r10_minimal_stop_guard(sock)

        print("")
        print("========== ANALOG TRANSFER ==========")
        print("Skipped: CH1-CH4 SCPI waveform transfer is disabled in r12.")
        print("Analog channels will be decoded directly from the RG03 Memory BIN.")
        analog_results = {}
        analog_failures = {}
        write_json(
            dataset_dir / "analog_capture_errors.json",
            analog_failures,
        )

        analog_time_range = {
            "status": "pending_memory_bin_decode",
            "source": "RG03_memory_BIN",
        }
        write_json(
            dataset_dir / "analog_time_range.json",
            analog_time_range,
        )

        final_status = r10_minimal_stop_guard(sock)
        final_errors = drain_scpi_errors(sock)
        write_json(
            dataset_dir / "final_scpi_errors.json",
            final_errors,
        )
        if final_errors:
            raise SCPICommandError(
                "Unexpected SCPI errors remained at end of r10 acquisition: "
                + "; ".join(final_errors)
            )

        successful_analog = [
            ch for ch, result in analog_results.items()
            if result.get("status") == "success"
        ]

        if analog_failures:
            partial_success = True
        elif not selected_analog:
            # The memory BIN may still be useful for digital-only capture.
            partial_success = False

        # Capture-core success is deliberately conservative. A save that is
        # still "pending_ftp_verification" is not called successful here; the
        # integrated controller can nevertheless continue and establish final
        # success only after exact FTP transfer plus RG03 decoder validation.
        success = (
            memory_export_result.get("success") is True
            and not analog_failures
            and final_status == "STOP"
        )
        if (
            not success
            and R10_ALLOW_DEFERRED_MEMORY_SAVE_CONFIRMATION
            and memory_export_result.get("deferred_candidate")
            and final_status == "STOP"
        ):
            partial_success = True

    except BaseException as exc:
        caught_exception = exc
        caught_traceback = exc.__traceback__
        error_details = {
            "type": type(exc).__name__,
            "message": str(exc),
            "repr": repr(exc),
            "traceback": traceback.format_exc(),
        }
        write_json(dataset_dir / "capture_error.json", error_details)
        print("")
        print("ACQUISITION FAILED")
        print(f"{type(exc).__name__}: {exc}")

    finally:
        if connected:
            # r9.1 intentionally leaves the instrument STOPped and does not run
            # the r5 broad restoration sequence. This avoids timebase/BUS/LA
            # writes after the valuable captured record has been preserved.
            try:
                stop_status = normalize_response(
                    scpi_query_line(sock, ":TRIGger:STATus?")
                )
            except Exception:
                stop_status = "UNKNOWN"
            try:
                sock.close()
            except Exception:
                pass
        else:
            stop_status = "NOT_CONNECTED"

        successful_analog = [
            channel
            for channel, result in analog_results.items()
            if result.get("status") == "success"
        ]
        failed_analog = [
            channel
            for channel, result in analog_results.items()
            if result.get("status") != "success"
        ]

        if not success and (
            successful_analog
            or (
                isinstance(memory_export_result, dict)
                and memory_export_result.get("success")
            )
        ):
            partial_success = True

        completed_at = now_iso()

        # The digital section is deliberately explicit: the scope-side BIN is
        # synchronized, but its D0-D15 payload is not yet decoded/validated.
        digital_manifest = {
            "channels_expected": DIGITAL_CHANNELS,
            "source": "instrument_memory_waveform_bin",
            "instrument_path": (
                memory_export_result.get("instrument_path")
                if isinstance(memory_export_result, dict)
                else instrument_bin_path
            ),
            "memory_save_status": (
                memory_export_result.get("status")
                if isinstance(memory_export_result, dict)
                else "not_completed"
            ),
            "synchronization_status": (
                "same_stopped_single_record"
                if (
                    isinstance(memory_export_result, dict)
                    and memory_export_result.get("success")
                )
                else "not_verified"
            ),
            "pc_raw_decode_status": "pending_bin_retrieval_then_r12_decoder",
            "complete_D0_D15_claim": False,
            "validated_bin_layout": "RG03: CH1-CH4 float32 + LA uint32 lower16=D0-D15 (validated on supplied capture)",
            "reason": (
                "r9.1 intentionally does not use the partial BUS event table. "
                "D0-D15 are finalized from the full LA record by "
                "mho984_bin_decoder_r12.py after the scope BIN is retrieved by FTP."
            ),
        }

        summary = {
            "tool": {
                "name": "RIGOL MHO984 synchronized memory acquisition",
                "version": CAPTURE_TOOL_VERSION,
                "source_file": Path(__file__).name,
            },
            "success": success,
            "partial_success": partial_success,
            "started_at": started_at,
            "completed_at": completed_at,
            "oscilloscope": {
                "ip": OSC_IP,
                "port": OSC_PORT,
                "idn": idn,
                "final_trigger_status": stop_status,
            },
            "dataset_directory": str(dataset_dir),
            "acquisition_id": acquisition_id,
            "selected_analog_channels": selected_analog,
            "logic_state": logic_state,
            "acquisition": acquisition_result,
            "memory_waveform_export": memory_export_result,
            "analog": analog_results,
            "analog_failures": analog_failures,
            "analog_time_range": analog_time_range,
            "digital": digital_manifest,
            "warnings": warnings,
            "error": error_details,
        }

        acquisition_manifest = {
            "schema": "mho984-synchronized-acquisition",
            "schema_version": 3,
            "acquisition_id": acquisition_id,
            "same_acquisition": bool(
                isinstance(memory_export_result, dict)
                and memory_export_result.get("success")
            ),
            "synchronization": {
                "basis": (
                    "fresh_single_acquisition_then_immediate_instrument_"
                    "memory_bin_is_the_only_waveform_data_source"
                ),
                "trigger_reference_s": 0.0,
                "pc_transfer_order_is_not_time_reference": True,
                "analog_scpi_waveform_transfer_skipped": True,
                "memory_bin_saved_before_waveform_source_changes": True,
                "analog_time_axis": (
                    "(sample_index - xref) * xinc + xorig"
                ),
                "digital_time_axis": (
                    "pending decode of same stopped-record instrument BIN"
                ),
            },
            "started_at": started_at,
            "completed_at": completed_at,
            "success": success,
            "partial_success": partial_success,
            "oscilloscope": summary["oscilloscope"],
            "trigger_acquisition": acquisition_result,
            "analog_reference_time_range": analog_time_range,
            "channels": {
                "analog": {},
                "digital": digital_manifest,
            },
            "instrument_memory_waveform": {
                "instrument_file": instrument_bin_path,
                "export_log": "memory_waveform_export.json",
                "copy_to_pc_required": False,
                "transfer_mode": "local_C_then_anonymous_FTP",
                "decoder": "mho984_bin_decoder_r12.py",
            },
            "compatibility": {
                "viewer_r12_9_can_read_bin_float32_analog": True,
                "viewer_r12_9_uses_decoder_output": True,
                "decoder": "mho984_bin_decoder_r12.py",
                "next_required_step": (
                    "r12 controller retrieves the C:/ BIN by FTP, runs the BIN-only decoder, "
                    "then launches viewer_mho984_r12_9_dark_channel_colors.py"
                ),
            },
            "warnings": warnings,
            "error": error_details,
        }

        write_json(dataset_dir / "acquisition.json", acquisition_manifest)
        write_json(dataset_dir / "capture_summary.json", summary)

        print("")
        print("=" * 72)
        status_text = (
            "SUCCESS"
            if success
            else ("PARTIAL" if partial_success else "FAILED")
        )
        print(f"Status: {status_text}")
        print(f"Saved PC dataset: {dataset_dir}")
        print("Analog SCPI RAW: SKIPPED (decoded from Memory BIN)")
        if failed_analog:
            print("Failed analog: " + ", ".join(failed_analog))

        if (
            isinstance(memory_export_result, dict)
            and memory_export_result.get("success")
        ):
            print(
                "Synchronized scope BIN: "
                + memory_export_result["instrument_path"]
            )
            print("Synchronized Memory BIN save status was confirmed.")
            print("The r12.7 controller uses the packed-LA decoder and Viewer analog-discovery compatibility fix.")
        elif (
            isinstance(memory_export_result, dict)
            and memory_export_result.get("deferred_candidate")
            and R10_ALLOW_DEFERRED_MEMORY_SAVE_CONFIRMATION
        ):
            print(
                "Synchronized scope BIN: PENDING FTP/RG03 verification: "
                + memory_export_result["instrument_path"]
            )
        else:
            print("Synchronized scope BIN: FAILED / not completed")

        if warnings:
            print("Warnings:")
            for warning in warnings:
                print(f"  - {warning}")
        print("=" * 72)

    if caught_exception is not None:
        raise caught_exception.with_traceback(caught_traceback)


if __name__ == "__main__":
    main()