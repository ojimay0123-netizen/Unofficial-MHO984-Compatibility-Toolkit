#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Protocol decoding helpers for the Unofficial MHO984 Compatibility Toolkit.

This module intentionally contains no instrument-control code.  It operates only on
logic waveforms that have already been captured.  Decoders are best-effort analysis
helpers, not safety/traceable measurement functions.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Iterable, Sequence
import math
import numpy as np


@dataclass
class DecodeEvent:
    start_s: float
    end_s: float
    kind: str
    summary: str
    details: dict
    ok: bool = True

    def to_dict(self):
        d = asdict(self)
        d["start_s"] = float(self.start_s)
        d["end_s"] = float(self.end_s)
        return d


class LogicSignal:
    """Piecewise-constant binary signal.

    `edge_times[i]` is the time at which the signal changes to `edge_states[i]`.
    """

    def __init__(self, start_s: float, end_s: float, initial_state: int,
                 edge_times: Sequence[float] | np.ndarray,
                 edge_states: Sequence[int] | np.ndarray,
                 *, name: str = ""):
        self.start_s = float(start_s)
        self.end_s = float(end_s)
        self.initial_state = int(initial_state) & 1
        self.edge_times = np.asarray(edge_times, dtype=np.float64)
        self.edge_states = np.asarray(edge_states, dtype=np.uint8)
        self.name = str(name)
        if self.edge_times.size != self.edge_states.size:
            raise ValueError("edge_times/edge_states length mismatch")
        if self.edge_times.size and np.any(np.diff(self.edge_times) < 0):
            raise ValueError("edge times must be sorted")

    def state_at(self, t):
        arr = np.asarray(t, dtype=np.float64)
        idx = np.searchsorted(self.edge_times, arr, side="right") - 1
        out = np.full(arr.shape, self.initial_state, dtype=np.uint8)
        mask = idx >= 0
        if np.any(mask):
            out[mask] = self.edge_states[idx[mask]]
        if np.isscalar(t):
            return int(out.reshape(-1)[0])
        return out

    def edges(self, *, rising=None, bounds=None):
        mask = np.ones(self.edge_times.size, dtype=bool)
        if rising is True:
            mask &= self.edge_states == 1
        elif rising is False:
            mask &= self.edge_states == 0
        if bounds is not None:
            lo, hi = sorted(map(float, bounds))
            mask &= (self.edge_times >= lo) & (self.edge_times <= hi)
        return self.edge_times[mask]

    def inverted(self, name=None):
        return LogicSignal(
            self.start_s, self.end_s, 1 - self.initial_state,
            self.edge_times, 1 - self.edge_states,
            name=self.name if name is None else name,
        )

    def transitions_in(self, lo, hi):
        i0 = int(np.searchsorted(self.edge_times, lo, side="left"))
        i1 = int(np.searchsorted(self.edge_times, hi, side="right"))
        return self.edge_times[i0:i1], self.edge_states[i0:i1]

    def high_low_intervals(self, bounds=None):
        if bounds is None:
            lo, hi = self.start_s, self.end_s
        else:
            lo, hi = sorted(map(float, bounds))
            lo = max(lo, self.start_s)
            hi = min(hi, self.end_s)
        if hi <= lo:
            return []
        state = self.state_at(lo + np.finfo(float).eps)
        times, states = self.transitions_in(lo, hi)
        result = []
        last = lo
        for t, new_state in zip(times, states):
            t = float(t)
            if t > last:
                result.append((last, t, state))
            state = int(new_state)
            last = t
        if hi > last:
            result.append((last, hi, state))
        return result


def make_logic_from_digital_events(times, bus_values, bit: int, *, name="") -> LogicSignal:
    times = np.asarray(times, dtype=np.float64)
    bus_values = np.asarray(bus_values, dtype=np.uint16)
    if times.size == 0:
        raise ValueError("digital event source is empty")
    bit = int(bit)
    states = ((bus_values >> bit) & 1).astype(np.uint8)
    if states.size >= 2:
        change = states[1:] != states[:-1]
        edge_times = times[1:][change]
        edge_states = states[1:][change]
    else:
        edge_times = np.empty(0, dtype=np.float64)
        edge_states = np.empty(0, dtype=np.uint8)
    return LogicSignal(float(times[0]), float(times[-1]), int(states[0]), edge_times, edge_states,
                       name=name or f"D{bit}")


def _bit_list_to_int(bits, *, lsb_first=False):
    bits = [int(b) & 1 for b in bits]
    if lsb_first:
        value = 0
        for i, b in enumerate(bits):
            value |= b << i
        return value
    value = 0
    for b in bits:
        value = (value << 1) | b
    return value


def decode_uart(signal: LogicSignal, *, baud: float, data_bits=8, parity="None",
                stop_bits=1.0, invert=False, bounds=None, max_frames=200000):
    if invert:
        signal = signal.inverted()
    baud = float(baud)
    if baud <= 0:
        raise ValueError("baud must be > 0")
    data_bits = int(data_bits)
    if data_bits < 5 or data_bits > 9:
        raise ValueError("data_bits must be 5..9")
    parity = str(parity).strip().lower()
    if parity not in ("none", "even", "odd"):
        raise ValueError("parity must be None/Even/Odd")
    stop_bits = float(stop_bits)
    if stop_bits not in (1.0, 1.5, 2.0):
        raise ValueError("stop_bits must be 1/1.5/2")
    T = 1.0 / baud
    lo, hi = (signal.start_s, signal.end_s) if bounds is None else sorted(map(float, bounds))
    lo = max(lo, signal.start_s)
    hi = min(hi, signal.end_s)
    falling = signal.edges(rising=False, bounds=(lo, hi))
    events = []
    next_allowed = lo - T
    parity_count = 0 if parity == "none" else 1
    total_bits = 1 + data_bits + parity_count + stop_bits
    for start in falling:
        start = float(start)
        if start < next_allowed:
            continue
        # Valid start must still be low at the center of the start bit.
        if signal.state_at(start + 0.5*T) != 0:
            continue
        sample_t = start + (1.5 + np.arange(data_bits, dtype=np.float64)) * T
        if sample_t[-1] > hi:
            break
        bits = signal.state_at(sample_t).astype(np.uint8)
        value = _bit_list_to_int(bits, lsb_first=True)
        cursor = start + (1.5 + data_bits) * T
        parity_ok = True
        parity_bit = None
        if parity != "none":
            parity_bit = signal.state_at(cursor)
            ones = int(np.sum(bits)) + int(parity_bit)
            parity_ok = (ones % 2 == 0) if parity == "even" else (ones % 2 == 1)
            cursor += T
        # Sample every stop bit at its midpoint.  For 1.5 stops, the last check is near 1.25 bit.
        # cursor is the midpoint of the first stop bit.
        stop_samples = [cursor]
        if stop_bits >= 1.5:
            stop_samples.append(cursor + 0.75*T)
        if stop_bits >= 2.0:
            stop_samples[-1] = cursor + 1.0*T
        framing_ok = all(signal.state_at(t) == 1 for t in stop_samples if t <= hi)
        end = min(start + total_bits*T, hi)
        text = chr(value) if 32 <= value <= 126 else "."
        events.append(DecodeEvent(
            start, end, "UART",
            f"0x{value:0{max(2,(data_bits+3)//4)}X}  '{text}'",
            {
                "value": value, "ascii": text, "data_bits": data_bits,
                "parity": parity.title(), "parity_bit": parity_bit,
                "parity_ok": parity_ok, "framing_ok": framing_ok,
                "baud": baud,
            }, ok=bool(parity_ok and framing_ok)
        ))
        next_allowed = start + max(T, total_bits*T - 0.25*T)
        if len(events) >= max_frames:
            break
    return events


def decode_i2c(scl: LogicSignal, sda: LogicSignal, *, bounds=None, max_events=200000):
    lo = max(scl.start_s, sda.start_s)
    hi = min(scl.end_s, sda.end_s)
    if bounds is not None:
        b0, b1 = sorted(map(float, bounds)); lo=max(lo,b0); hi=min(hi,b1)
    if hi <= lo:
        return []
    eps = max((hi-lo)*1e-15, 1e-15)
    # START/STOP are SDA edges while SCL is high.
    markers = []
    t_sda, st_sda = sda.transitions_in(lo, hi)
    for t, st in zip(t_sda, st_sda):
        if scl.state_at(float(t) - eps) == 1 and scl.state_at(float(t) + eps) == 1:
            markers.append((float(t), "START" if int(st)==0 else "STOP"))
    markers.sort()
    rise_scl = scl.edges(rising=True, bounds=(lo, hi))
    events = []
    frame_no = 0
    i = 0
    while i < len(markers):
        if markers[i][1] != "START":
            i += 1; continue
        start_t = markers[i][0]
        # Repeated START ends one logical segment and starts the next.
        j = i + 1
        while j < len(markers) and markers[j][1] not in ("START", "STOP"):
            j += 1
        # markers only contain start/stop, so next marker is boundary.
        if j >= len(markers):
            end_t = hi; boundary = "END"
        else:
            end_t, boundary = markers[j]
        clocks = rise_scl[(rise_scl > start_t + eps) & (rise_scl < end_t - eps)]
        bits = sda.state_at(clocks + eps*4) if clocks.size else np.empty(0, dtype=np.uint8)
        frame_no += 1
        events.append(DecodeEvent(start_t, min(start_t+eps, hi), "I2C START", f"START #{frame_no}", {"frame": frame_no}))
        byte_count = len(bits)//9
        for bi in range(byte_count):
            b = bits[bi*9:bi*9+8]
            ack = int(bits[bi*9+8])
            val = _bit_list_to_int(b)
            bt0 = float(clocks[bi*9]) if clocks.size else start_t
            bt1 = float(clocks[bi*9+8]) if clocks.size else end_t
            if bi == 0:
                addr = val >> 1; rw = val & 1
                summary = f"ADDR 0x{addr:02X} {'R' if rw else 'W'}  {'NACK' if ack else 'ACK'}"
                details = {"frame":frame_no,"address":addr,"rw":"R" if rw else "W","byte":val,"ack":ack==0}
                kind = "I2C ADDR"
            else:
                summary = f"DATA 0x{val:02X}  {'NACK' if ack else 'ACK'}"
                details = {"frame":frame_no,"index":bi-1,"byte":val,"ack":ack==0}
                kind = "I2C DATA"
            events.append(DecodeEvent(bt0, bt1, kind, summary, details, ok=True))
            if len(events)>=max_events: return events
        if boundary == "STOP":
            events.append(DecodeEvent(end_t, min(end_t+eps, hi), "I2C STOP", f"STOP #{frame_no}", {"frame":frame_no}))
            i = j + 1
        elif boundary == "START":
            # repeated start: process marker j as next frame segment
            i = j
        else:
            break
    return events


def decode_spi(sclk: LogicSignal, mosi: LogicSignal | None, miso: LogicSignal | None,
               *, cs: LogicSignal | None=None, mode=0, bits_per_word=8,
               lsb_first=False, cs_active_low=True, bounds=None, max_words=200000):
    mode = int(mode)
    if mode not in (0,1,2,3): raise ValueError("SPI mode must be 0..3")
    bits_per_word = int(bits_per_word)
    if bits_per_word < 1 or bits_per_word > 32: raise ValueError("bits_per_word must be 1..32")
    lo, hi = sclk.start_s, sclk.end_s
    for sig in (mosi,miso,cs):
        if sig is not None:
            lo=max(lo,sig.start_s); hi=min(hi,sig.end_s)
    if bounds is not None:
        b0,b1=sorted(map(float,bounds)); lo=max(lo,b0); hi=min(hi,b1)
    cpol=(mode>>1)&1; cpha=mode&1
    sample_rising = (cpol == cpha)
    sample_edges = sclk.edges(rising=sample_rising, bounds=(lo,hi))
    if cs is None:
        windows=[(lo,hi,0)]
    else:
        active = 0 if cs_active_low else 1
        windows=[]; cur=None; idx=0
        for a,b,state in cs.high_low_intervals((lo,hi)):
            if state==active:
                windows.append((a,b,idx)); idx+=1
    events=[]
    for w0,w1,frame in windows:
        clocks=sample_edges[(sample_edges>=w0)&(sample_edges<=w1)]
        words=len(clocks)//bits_per_word
        for wi in range(words):
            ts=clocks[wi*bits_per_word:(wi+1)*bits_per_word]
            mbits=mosi.state_at(ts) if mosi is not None else None
            ibits=miso.state_at(ts) if miso is not None else None
            mv=_bit_list_to_int(mbits,lsb_first=lsb_first) if mbits is not None else None
            iv=_bit_list_to_int(ibits,lsb_first=lsb_first) if ibits is not None else None
            parts=[]
            width=max(1,(bits_per_word+3)//4)
            if mv is not None: parts.append(f"MOSI=0x{mv:0{width}X}")
            if iv is not None: parts.append(f"MISO=0x{iv:0{width}X}")
            events.append(DecodeEvent(float(ts[0]),float(ts[-1]),"SPI WORD","  ".join(parts),
                                     {"frame":frame,"word_index":wi,"mosi":mv,"miso":iv,"mode":mode,
                                      "bits_per_word":bits_per_word,"lsb_first":bool(lsb_first)}))
            if len(events)>=max_words: return events
    return events


def lin_pid_ok(pid: int):
    pid &= 0xff; ident=pid&0x3f
    b=[(ident>>i)&1 for i in range(6)]
    p0=b[0]^b[1]^b[2]^b[4]
    p1=1^(b[1]^b[3]^b[4]^b[5])
    return ((pid>>6)&1)==p0 and ((pid>>7)&1)==p1


def lin_checksum(values):
    total=0
    for v in values:
        total += int(v)&0xff
        if total>255: total=(total&0xff)+1
    return (~total)&0xff


def decode_lin(signal: LogicSignal, *, baud=19200.0, invert=False, bounds=None, max_frames=50000):
    if invert: signal=signal.inverted()
    T=1.0/float(baud)
    lo,hi=(signal.start_s,signal.end_s) if bounds is None else sorted(map(float,bounds))
    lows=[(a,b) for a,b,s in signal.high_low_intervals((lo,hi)) if s==0 and (b-a)>=12.5*T]
    events=[]
    for fi,(bs,be) in enumerate(lows):
        next_break=lows[fi+1][0] if fi+1<len(lows) else hi
        # Skip break delimiter, decode UART bytes until next break.
        start=max(be,bs+13*T)
        bytes_ev=decode_uart(signal,baud=baud,data_bits=8,parity="None",stop_bits=1,bounds=(start,next_break),max_frames=64)
        good=[e for e in bytes_ev if e.ok]
        if not good: continue
        vals=[int(e.details["value"]) for e in good]
        sync_idx=next((i for i,v in enumerate(vals) if v==0x55),None)
        if sync_idx is None or sync_idx+1>=len(vals):
            events.append(DecodeEvent(bs,min(next_break,be+2*T),"LIN FRAME","BREAK; SYNC not found",
                                     {"break_s":be-bs,"bytes":vals},ok=False)); continue
        vals=vals[sync_idx:]
        evs=good[sync_idx:]
        pid=vals[1]; data=vals[2:-1] if len(vals)>=3 else []; rx_checksum=vals[-1] if len(vals)>=3 else None
        classic=lin_checksum(data) if rx_checksum is not None else None
        enhanced=lin_checksum([pid]+data) if rx_checksum is not None else None
        ctype="unknown"; checksum_ok=False
        if rx_checksum is not None:
            if rx_checksum==enhanced: ctype="enhanced"; checksum_ok=True
            elif rx_checksum==classic: ctype="classic"; checksum_ok=True
        ok=lin_pid_ok(pid) and (rx_checksum is None or checksum_ok)
        summary=f"ID=0x{pid&0x3f:02X}  PID=0x{pid:02X}  DATA="+" ".join(f"{v:02X}" for v in data)
        if rx_checksum is not None: summary+=f"  CHK=0x{rx_checksum:02X} ({ctype}{' OK' if checksum_ok else ' BAD'})"
        events.append(DecodeEvent(bs,evs[-1].end_s,"LIN FRAME",summary,
                                 {"break_s":be-bs,"sync":0x55,"pid":pid,"pid_ok":lin_pid_ok(pid),
                                  "id":pid&0x3f,"data":data,"checksum":rx_checksum,
                                  "checksum_type":ctype,"checksum_ok":checksum_ok,"baud":float(baud)},ok=ok))
        if len(events)>=max_frames: break
    return events


def can_crc15(bits):
    crc=0
    for bit in bits:
        fb=((crc>>14)&1) ^ (int(bit)&1)
        crc=((crc<<1)&0x7fff)
        if fb: crc ^= 0x4599
    return crc & 0x7fff


def _destuff_until(raw_bits, target_len_func):
    out=[]; raw_i=0; run=0; last=None; stuff_error=False
    while raw_i < len(raw_bits):
        b=int(raw_bits[raw_i]); raw_i+=1
        if run==5:
            # Next transmitted bit must be complementary stuff bit and is discarded.
            if last is not None and b==last:
                stuff_error=True
                return out,raw_i,stuff_error,run,last
            run=0
            continue
        out.append(b)
        if last is None or b!=last:
            run=1; last=b
        else:
            run+=1
        target=target_len_func(out)
        if target is not None and len(out)>=target:
            # A stuff bit may still be required after the final CRC bit.
            if run==5 and raw_i<len(raw_bits):
                sb=int(raw_bits[raw_i])
                if sb==last: stuff_error=True
                else: raw_i+=1
            return out,raw_i,stuff_error,run,last
    return out,raw_i,True,run,last


def _can_target_len(bits):
    if len(bits)<14: return None
    ide=bits[13]
    if ide==0:
        if len(bits)<19: return None
        dlc=_bit_list_to_int(bits[15:19]) & 0x0f
        dlc=min(dlc,8)
        rtr=bits[12]
        return 34 + (0 if rtr else 8*dlc)
    if len(bits)<39: return None
    dlc=_bit_list_to_int(bits[35:39]) & 0x0f
    dlc=min(dlc,8)
    rtr=bits[32]
    return 54 + (0 if rtr else 8*dlc)


def decode_can(signal: LogicSignal, *, bitrate=500000.0, invert=False, bounds=None, max_frames=50000):
    if invert: signal=signal.inverted()
    T=1.0/float(bitrate)
    lo,hi=(signal.start_s,signal.end_s) if bounds is None else sorted(map(float,bounds))
    falling=signal.edges(rising=False,bounds=(lo,hi))
    all_edges=signal.edge_times
    candidates=[]
    for t in falling:
        idx=int(np.searchsorted(all_edges,t,side="left"))-1
        prev=signal.start_s if idx<0 else float(all_edges[idx])
        if float(t)-prev >= 2.5*T:
            candidates.append(float(t))
    events=[]; next_allowed=lo
    for start in candidates:
        if start<next_allowed: continue
        max_raw=220
        sample_t=start+(0.5+np.arange(max_raw))*T
        sample_t=sample_t[sample_t<=hi]
        if len(sample_t)<40: continue
        raw=signal.state_at(sample_t).tolist()
        if raw[0]!=0: continue
        bits,raw_used,stuff_error,_,_=_destuff_until(raw,_can_target_len)
        target=_can_target_len(bits)
        if target is None or len(bits)<target: continue
        ide=int(bits[13])
        if ide==0:
            ident=_bit_list_to_int(bits[1:12]); rtr=int(bits[12]); dlc=min(_bit_list_to_int(bits[15:19]),8)
            data_start=19
        else:
            ident=(_bit_list_to_int(bits[1:12])<<18)|_bit_list_to_int(bits[14:32]); rtr=int(bits[32]); dlc=min(_bit_list_to_int(bits[35:39]),8)
            data_start=39
        data=[]
        if not rtr:
            for i in range(dlc): data.append(_bit_list_to_int(bits[data_start+8*i:data_start+8*(i+1)]))
        crc_rx=_bit_list_to_int(bits[target-15:target])
        crc_calc=can_crc15(bits[:target-15]); crc_ok=(crc_rx==crc_calc)
        # delimiter, ACK slot, ACK delimiter, EOF(7) are not stuffed.
        tail=raw[raw_used:raw_used+10]
        delimiter_ok=len(tail)>=10 and tail[0]==1 and tail[2]==1 and all(x==1 for x in tail[3:10])
        ack=(len(tail)>=2 and tail[1]==0)
        frame_end=start+(raw_used+min(10,len(tail)))*T
        id_width=8 if ide else 3
        summary=f"ID=0x{ident:0{id_width}X} {'EXT' if ide else 'STD'}  {'RTR' if rtr else f'DLC={dlc}'}"
        if data: summary+="  DATA="+" ".join(f"{v:02X}" for v in data)
        summary+=f"  CRC {'OK' if crc_ok else 'BAD'}  ACK {'YES' if ack else 'NO'}"
        ok=(not stuff_error) and crc_ok and delimiter_ok
        events.append(DecodeEvent(start,frame_end,"CAN FRAME",summary,
                                 {"id":ident,"extended":bool(ide),"rtr":bool(rtr),"dlc":dlc,"data":data,
                                  "crc_received":crc_rx,"crc_calculated":crc_calc,"crc_ok":crc_ok,
                                  "stuff_error":stuff_error,"ack":ack,"delimiter_eof_ok":delimiter_ok,
                                  "bitrate":float(bitrate)},ok=ok))
        next_allowed=frame_end-0.25*T
        if len(events)>=max_frames: break
    return events


def _nmea_checksum(sentence_without_dollar_and_star):
    c=0
    for ch in sentence_without_dollar_and_star.encode("ascii",errors="ignore"): c ^= ch
    return c


def _nmea_latlon(value, hemi):
    if not value: return None
    x=float(value); deg=int(x//100); mins=x-deg*100; out=deg+mins/60.0
    if str(hemi).upper() in ("S","W"): out=-out
    return out


def parse_nmea_line(text):
    text=text.strip()
    result={"raw":text}
    if not text.startswith(("$","!")): return result
    body=text[1:]; rx=None
    if "*" in body:
        body,cs=body.split("*",1)
        try: rx=int(cs[:2],16)
        except Exception: rx=None
    calc=_nmea_checksum(body)
    result.update({"checksum_received":rx,"checksum_calculated":calc,"checksum_ok":rx is None or rx==calc})
    f=body.split(","); typ=f[0] if f else ""; result["sentence_type"]=typ
    try:
        if typ.endswith("GGA") and len(f)>=10:
            result.update({"utc":f[1],"latitude":_nmea_latlon(f[2],f[3]),"longitude":_nmea_latlon(f[4],f[5]),
                           "fix_quality":int(f[6] or 0),"satellites":int(f[7] or 0),"hdop":float(f[8]) if f[8] else None,
                           "altitude_m":float(f[9]) if f[9] else None})
        elif typ.endswith("RMC") and len(f)>=10:
            result.update({"utc":f[1],"status":f[2],"latitude":_nmea_latlon(f[3],f[4]),"longitude":_nmea_latlon(f[5],f[6]),
                           "speed_knots":float(f[7]) if f[7] else None,"course_deg":float(f[8]) if f[8] else None,"date":f[9]})
    except Exception as exc:
        result["parse_warning"]=str(exc)
    return result


def decode_gps_nmea(signal: LogicSignal, *, baud=9600.0, invert=False, bounds=None, pps: LogicSignal|None=None,
                    max_sentences=50000):
    uart=decode_uart(signal,baud=baud,data_bits=8,parity="None",stop_bits=1,invert=invert,bounds=bounds,max_frames=1000000)
    good=[e for e in uart if e.ok]
    events=[]; buf=[]; start=None; last=None
    for e in good:
        v=e.details["value"]
        if v in (ord('$'),ord('!')):
            buf=[v]; start=e.start_s; last=e.end_s; continue
        if not buf: continue
        buf.append(v); last=e.end_s
        if v==10 or len(buf)>2048:
            text=bytes(buf).decode("ascii",errors="replace").strip("\r\n")
            meta=parse_nmea_line(text)
            typ=meta.get("sentence_type","")
            summary=text
            events.append(DecodeEvent(float(start),float(last),"GPS NMEA",summary,meta,ok=bool(meta.get("checksum_ok",True))))
            buf=[]; start=None
            if len(events)>=max_sentences: break
    if pps is not None:
        rises=pps.edges(rising=True,bounds=bounds)
        periods=np.diff(rises) if len(rises)>=2 else np.array([])
        for i,t in enumerate(rises[:max_sentences]):
            details={"index":i}
            if i>0: details["period_s"]=float(rises[i]-rises[i-1]); details["period_error_us"]=float((rises[i]-rises[i-1]-1.0)*1e6)
            # nearest sentence start for observational timing only
            if events:
                starts=np.fromiter((ev.start_s for ev in events),dtype=np.float64)
                k=int(np.argmin(np.abs(starts-float(t))))
                details["nearest_sentence_type"]=events[k].details.get("sentence_type","")
                details["nearest_sentence_delta_s"]=float(events[k].start_s-float(t))
            events.append(DecodeEvent(float(t),float(t),"GPS PPS",f"PPS #{i}",details,ok=True))
    events.sort(key=lambda e:e.start_s)
    return events


def decode_gps_ubx(signal: LogicSignal, *, baud=9600.0, invert=False, bounds=None, max_packets=50000):
    uart=decode_uart(signal,baud=baud,data_bits=8,parity="None",stop_bits=1,invert=invert,bounds=bounds,max_frames=1000000)
    good=[e for e in uart if e.ok]
    vals=[e.details["value"] for e in good]
    events=[]; i=0
    while i+8<=len(vals):
        if vals[i]!=0xB5 or vals[i+1]!=0x62:
            i+=1; continue
        cls=vals[i+2]; mid=vals[i+3]; length=vals[i+4]|(vals[i+5]<<8); end=i+6+length+2
        if end>len(vals): break
        payload=vals[i+6:i+6+length]; ck_a=0; ck_b=0
        for v in vals[i+2:i+6+length]:
            ck_a=(ck_a+v)&0xff; ck_b=(ck_b+ck_a)&0xff
        ok=(vals[i+6+length]==ck_a and vals[i+6+length+1]==ck_b)
        summary=f"UBX class=0x{cls:02X} id=0x{mid:02X} len={length}  CK {'OK' if ok else 'BAD'}"
        events.append(DecodeEvent(good[i].start_s,good[end-1].end_s,"GPS UBX",summary,
                                 {"class":cls,"id":mid,"length":length,"payload_hex":bytes(payload).hex(" "),
                                  "checksum_ok":ok},ok=ok))
        i=end
        if len(events)>=max_packets: break
    return events