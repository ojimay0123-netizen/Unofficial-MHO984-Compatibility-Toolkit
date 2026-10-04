#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""Offline protocol analyzer for decoded MHO984 datasets.

Supported in this beta: UART, RS-232, RS-485(UART payload), I2C, SPI,
LIN, Classic CAN, GPS NMEA, GPS UBX and GPS PPS correlation.
"""
from __future__ import annotations

import csv
import json
import math
import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

import viewer_mho984_r13_5_timebase_calibrated as vw
from protocol_overlay import save_active_overlay, clear_active_overlay, active_overlay_path
from protocol_decode import (
    LogicSignal, DecodeEvent, make_logic_from_digital_events,
    decode_uart, decode_i2c, decode_spi, decode_lin, decode_can,
    decode_gps_nmea, decode_gps_ubx,
)

APP_VERSION = "0.1.0-beta.9"
DEFAULT_CAL_PPM = 2.091228967


def _search_time(axis, value, side="left"):
    if hasattr(axis, "xinc") and hasattr(axis, "xorig") and hasattr(axis, "xref"):
        if axis.xinc == 0:
            return 0
        p = (float(value) - float(axis.xorig)) / float(axis.xinc) + float(axis.xref)
        return int(math.floor(p) if side == "left" else math.ceil(p))
    return int(np.searchsorted(np.asarray(axis), value, side=side))


def _time_at(axis, indices):
    indices = np.asarray(indices, dtype=np.float64)
    if hasattr(axis, "xinc") and hasattr(axis, "xorig") and hasattr(axis, "xref"):
        return (indices - float(axis.xref)) * float(axis.xinc) + float(axis.xorig)
    return np.asarray(axis, dtype=np.float64)[indices.astype(np.int64)]


class DatasetSignals:
    def __init__(self, dataset: Path):
        self.dataset = vw.resolve_dataset_root(Path(dataset))
        self.analog, self.digital = vw.discover_channels(self.dataset)
        self._analog_cache = {}
        self._digital_cache = {}

    @property
    def analog_names(self):
        return [x[0] for x in self.analog]

    @property
    def digital_names(self):
        return [x[0] for x in self.digital]

    @property
    def source_names(self):
        return self.digital_names + self.analog_names

    def _analog_source(self, name):
        for nm, bp, pp in self.analog:
            if nm == name:
                if bp not in self._analog_cache:
                    self._analog_cache[bp] = vw.load_analog(bp, pp)
                return self._analog_cache[bp]
        raise ValueError(f"Analog source not found: {name}")

    def _digital_source(self, mode="Raw", ppm=DEFAULT_CAL_PPM):
        if not self.digital:
            raise ValueError("This dataset has no D0-D15 digital event source")
        path = self.digital[0][1]
        key = (path, str(mode), float(ppm))
        if key not in self._digital_cache:
            self._digital_cache[key] = vw.load_digital_events(
                path,
                calibration_mode=mode,
                calibration_ppm=float(ppm),
                calibration_reference_s=0.0,
            )
        return self._digital_cache[key]

    def digital_logic(self, name, *, mode="Raw", ppm=DEFAULT_CAL_PPM):
        bit = int(name[1:])
        times, bus, _meta = self._digital_source(mode=mode, ppm=ppm)
        return make_logic_from_digital_events(times, bus, bit, name=name)

    def _auto_threshold(self, x, y, bounds=None, max_samples=200000):
        n = len(y)
        i0, i1 = 0, n
        if bounds is not None:
            lo, hi = sorted(map(float, bounds))
            i0=max(0,min(n,_search_time(x,lo,"left")))
            i1=max(i0,min(n,_search_time(x,hi,"right")))
        count=max(0,i1-i0)
        if count <= 0: raise ValueError("Selected range contains no analog samples")
        step=max(1,int(math.ceil(count/max_samples)))
        vals=np.asarray(y[i0:i1:step],dtype=np.float64)
        vals=vals[np.isfinite(vals)]
        if vals.size==0: raise ValueError("Analog source has no finite samples")
        lo_v,hi_v=np.percentile(vals,[1,99])
        return float((lo_v+hi_v)/2)

    def _logic_from_chunk_reader(self, x, length, reader, *, threshold, hysteresis=0.0,
                                 bounds=None, invert=False, name=""):
        n=int(length)
        i0,i1=0,n
        lo_t=float(_time_at(x,[0])[0]); hi_t=float(_time_at(x,[n-1])[0]) if n else lo_t
        if bounds is not None:
            b0,b1=sorted(map(float,bounds)); i0=max(0,min(n,_search_time(x,b0,"left"))); i1=max(i0,min(n,_search_time(x,b1,"right")))
            lo_t=max(lo_t,b0); hi_t=min(hi_t,b1)
        if i1-i0 < 2: raise ValueError("Not enough analog samples")
        h=max(0.0,float(hysteresis)); upper=float(threshold)+h/2; lower=float(threshold)-h/2
        chunk=1_000_000; edge_idx=[]; edge_state=[]
        first=float(reader(i0,i0+1)[0]); state=1 if first>=threshold else 0; initial=state
        prev=first; pos=i0+1
        while pos<i1:
            end=min(i1,pos+chunk)
            arr=np.asarray(reader(pos,end),dtype=np.float64)
            if arr.size==0: break
            ext=np.concatenate(([prev],arr))
            if h<=0:
                b=ext>=threshold
                ch=np.flatnonzero(b[1:]!=b[:-1])
                for c in ch:
                    a=float(ext[c]); z=float(ext[c+1]); idx=pos+int(c)
                    frac=0.5 if z==a else (threshold-a)/(z-a); frac=float(np.clip(frac,0,1))
                    edge_idx.append((idx-1)+frac); state=int(b[c+1]); edge_state.append(state)
            else:
                up=np.flatnonzero((ext[:-1]<upper)&(ext[1:]>=upper))
                dn=np.flatnonzero((ext[:-1]>lower)&(ext[1:]<=lower))
                candidates=sorted([(int(c),1,upper) for c in up]+[(int(c),0,lower) for c in dn], key=lambda q:q[0])
                for c,new,level in candidates:
                    if new==state: continue
                    a=float(ext[c]); z=float(ext[c+1]); idx=pos+int(c)
                    frac=0.5 if z==a else (level-a)/(z-a); frac=float(np.clip(frac,0,1))
                    edge_idx.append((idx-1)+frac); edge_state.append(new); state=new
            prev=float(arr[-1]); pos=end
        edge_times=_time_at(x,np.asarray(edge_idx,dtype=np.float64)) if edge_idx else np.empty(0,dtype=np.float64)
        states=np.asarray(edge_state,dtype=np.uint8)
        sig=LogicSignal(lo_t,hi_t,initial,edge_times,states,name=name)
        return sig.inverted(name=name) if invert else sig

    def analog_logic(self, name, *, threshold="Auto", hysteresis=0.0, bounds=None, invert=False):
        x,y,_pre=self._analog_source(name)
        thr=self._auto_threshold(x,y,bounds) if str(threshold).strip().lower()=="auto" else float(threshold)
        sig=self._logic_from_chunk_reader(x,len(y),lambda a,b:y[a:b],threshold=thr,hysteresis=hysteresis,bounds=bounds,invert=invert,name=name)
        return sig,thr

    def differential_logic(self, a_name, b_name, *, threshold=0.0, hysteresis=0.0, bounds=None, invert=False):
        xa,ya,_=self._analog_source(a_name); xb,yb,_=self._analog_source(b_name)
        n=min(len(ya),len(yb))
        # MHO decoder creates the same time axis for synchronized analog channels; reject materially different axes.
        for attr in ("xinc","xorig","xref"):
            if hasattr(xa,attr) and hasattr(xb,attr) and not math.isclose(float(getattr(xa,attr)),float(getattr(xb,attr)),rel_tol=0,abs_tol=1e-15):
                raise ValueError("Differential analog channels do not share the same time axis")
        thr=float(threshold)
        return self._logic_from_chunk_reader(xa,n,lambda s,e:np.asarray(ya[s:e],dtype=np.float64)-np.asarray(yb[s:e],dtype=np.float64),
                                             threshold=thr,hysteresis=hysteresis,bounds=bounds,invert=invert,name=f"{a_name}-{b_name}"),thr

    def logic(self, name, *, threshold="Auto", hysteresis=0.0, bounds=None, invert=False,
              digital_mode="Raw", ppm=DEFAULT_CAL_PPM):
        if name.startswith("D") and name[1:].isdigit():
            sig=self.digital_logic(name,mode=digital_mode,ppm=ppm)
            if bounds is not None:
                # No copy needed; decoder functions apply bounds separately.
                pass
            return sig,None
        if name.startswith("CHAN"):
            return self.analog_logic(name,threshold=threshold,hysteresis=hysteresis,bounds=bounds,invert=invert)
        raise ValueError(f"Unknown source: {name}")


class ProtocolAnalyzer(tk.Tk):
    PROTOCOLS=["UART","RS-232","RS-485","I2C","SPI","LIN","CAN Classic","GPS NMEA + PPS","GPS UBX"]
    def __init__(self,dataset=None):
        super().__init__(); self.title(f"MHO984 Protocol Analyzer {APP_VERSION}"); self.geometry("1540x920"); self.minsize(1180,720)
        self.dataset_path=Path(dataset).resolve() if dataset else None; self.ds=None; self.events=[]; self.logic_for_plot=[]; self.q=queue.Queue(); self.busy=False
        self.last_decode_config=None; self.last_protocol=""
        self._build(); self.after(100,self._drain)
        if self.dataset_path and self.dataset_path.exists(): self.load_dataset(self.dataset_path)

    def _build(self):
        top=ttk.Frame(self,padding=8); top.pack(fill="x")
        ttk.Button(top,text="データセットを開く...",command=self.choose_dataset).pack(side="left")
        self.dataset_var=tk.StringVar(value="未選択"); ttk.Label(top,textvariable=self.dataset_var).pack(side="left",padx=10)
        ttk.Label(top,text="Digital time:").pack(side="right")
        self.ppm_var=tk.StringVar(value=f"{DEFAULT_CAL_PPM:.9f}"); ttk.Entry(top,textvariable=self.ppm_var,width=13).pack(side="right",padx=(2,8))
        self.time_mode=tk.StringVar(value="Raw"); ttk.Combobox(top,textvariable=self.time_mode,values=["Raw","補正後"],state="readonly",width=8).pack(side="right")

        pan=ttk.Panedwindow(self,orient="horizontal"); pan.pack(fill="both",expand=True,padx=8,pady=(0,8))
        left=ttk.Frame(pan,padding=6); right=ttk.Frame(pan,padding=6); pan.add(left,weight=0); pan.add(right,weight=1)
        cfg=ttk.LabelFrame(left,text="プロトコル設定",padding=8); cfg.pack(fill="x")
        self.protocol=tk.StringVar(value="UART"); ttk.Label(cfg,text="Protocol").grid(row=0,column=0,sticky="w"); cb=ttk.Combobox(cfg,textvariable=self.protocol,values=self.PROTOCOLS,state="readonly",width=22); cb.grid(row=0,column=1,columnspan=2,sticky="ew",pady=2); cb.bind("<<ComboboxSelected>>",lambda e:self._preset())
        self.source_vars=[tk.StringVar() for _ in range(4)]; self.source_labels=[]; self.source_combos=[]
        for i in range(4):
            lab=ttk.Label(cfg,text=f"Source {i+1}"); lab.grid(row=1+i,column=0,sticky="w",pady=2); self.source_labels.append(lab)
            combo=ttk.Combobox(cfg,textvariable=self.source_vars[i],state="readonly",width=22); combo.grid(row=1+i,column=1,columnspan=2,sticky="ew",pady=2); self.source_combos.append(combo)
        self.baud=tk.StringVar(value="9600"); self.param2=tk.StringVar(value="8"); self.param3=tk.StringVar(value="None"); self.param4=tk.StringVar(value="1")
        params=[("Baud/Bitrate",self.baud),("Param 2",self.param2),("Param 3",self.param3),("Param 4",self.param4)]
        self.param_labels=[]
        for j,(txt,var) in enumerate(params):
            lab=ttk.Label(cfg,text=txt); lab.grid(row=5+j,column=0,sticky="w",pady=2); self.param_labels.append(lab); ttk.Entry(cfg,textvariable=var,width=17).grid(row=5+j,column=1,columnspan=2,sticky="ew",pady=2)
        self.threshold=tk.StringVar(value="Auto"); self.hyst=tk.StringVar(value="0.0"); self.invert=tk.BooleanVar(value=False)
        ttk.Label(cfg,text="Analog threshold").grid(row=9,column=0,sticky="w",pady=2); ttk.Entry(cfg,textvariable=self.threshold,width=12).grid(row=9,column=1,sticky="ew")
        ttk.Label(cfg,text="Hysteresis [V]").grid(row=10,column=0,sticky="w",pady=2); ttk.Entry(cfg,textvariable=self.hyst,width=12).grid(row=10,column=1,sticky="ew")
        ttk.Checkbutton(cfg,text="Polarity invert",variable=self.invert).grid(row=11,column=0,columnspan=3,sticky="w",pady=2)
        self.start_var=tk.StringVar(); self.end_var=tk.StringVar(); ttk.Label(cfg,text="解析開始 [s]").grid(row=12,column=0,sticky="w"); ttk.Entry(cfg,textvariable=self.start_var).grid(row=12,column=1,columnspan=2,sticky="ew"); ttk.Label(cfg,text="解析終了 [s]").grid(row=13,column=0,sticky="w"); ttk.Entry(cfg,textvariable=self.end_var).grid(row=13,column=1,columnspan=2,sticky="ew")
        self.decode_btn=ttk.Button(cfg,text="デコード実行",command=self.decode); self.decode_btn.grid(row=14,column=0,columnspan=3,sticky="ew",pady=(10,3))
        self.auto_overlay=tk.BooleanVar(value=True)
        ttk.Checkbutton(cfg,text="デコード後、Viewerへプロトコル帯を自動反映",variable=self.auto_overlay).grid(row=15,column=0,columnspan=3,sticky="w",pady=(3,2))
        overlay_row=ttk.Frame(cfg); overlay_row.grid(row=16,column=0,columnspan=3,sticky="ew",pady=2)
        ttk.Button(overlay_row,text="Viewerへ反映",command=self.save_viewer_overlay).pack(side="left",expand=True,fill="x",padx=(0,2))
        ttk.Button(overlay_row,text="帯をクリア",command=self.clear_viewer_overlay).pack(side="left",expand=True,fill="x",padx=(2,0))
        ttk.Button(cfg,text="CSV保存...",command=self.export_csv).grid(row=17,column=0,columnspan=3,sticky="ew",pady=2); ttk.Button(cfg,text="JSON保存...",command=self.export_json).grid(row=18,column=0,columnspan=3,sticky="ew",pady=2)
        cfg.columnconfigure(1,weight=1)
        note=("Analog入力はThreshold/Hysteresisで論理化します。\nRS-485でSource1/2がAnalogならA-B差動として復号します。\nCANはClassic CANのみ。CAN FDは未対応です。\n解析結果は補助情報であり安全・認証・校正用途には使用しないでください。")
        ttk.Label(left,text=note,justify="left",wraplength=360).pack(fill="x",pady=8)
        self.status=tk.StringVar(value="待機中"); ttk.Label(left,textvariable=self.status).pack(anchor="w")

        self.fig=Figure(figsize=(10,4),dpi=100); self.ax=self.fig.add_subplot(111); self.canvas=FigureCanvasTkAgg(self.fig,master=right); self.canvas.get_tk_widget().pack(fill="both",expand=True)
        tb=NavigationToolbar2Tk(self.canvas,right,pack_toolbar=False); tb.update(); tb.pack(fill="x")
        table_frame=ttk.Frame(right); table_frame.pack(fill="both",expand=True,pady=(5,0)); cols=("time","end","type","ok","summary"); self.tree=ttk.Treeview(table_frame,columns=cols,show="headings",height=13)
        widths=(130,130,110,50,680)
        for c,w in zip(cols,widths): self.tree.heading(c,text=c); self.tree.column(c,width=w,anchor="w")
        sy=ttk.Scrollbar(table_frame,orient="vertical",command=self.tree.yview); self.tree.configure(yscrollcommand=sy.set); self.tree.pack(side="left",fill="both",expand=True); sy.pack(side="right",fill="y"); self.tree.bind("<<TreeviewSelect>>",self._selected)
        self.details=tk.Text(right,height=8,wrap="word",font=("Consolas",9)); self.details.pack(fill="x",pady=(5,0)); self.details.configure(state="disabled")
        self._preset()

    def choose_dataset(self):
        p=filedialog.askdirectory(parent=self,title="mho984_* データセットを選択");
        if p: self.load_dataset(Path(p))

    def load_dataset(self,p):
        try:
            self.ds=DatasetSignals(p); self.dataset_path=self.ds.dataset; self.dataset_var.set(str(self.dataset_path)); vals=self.ds.source_names
            for cb in self.source_combos: cb.configure(values=[""]+vals)
            defaults=(self.ds.digital_names+self.ds.analog_names)
            for i,v in enumerate(self.source_vars):
                if i<len(defaults): v.set(defaults[i])
            self.status.set(f"Loaded: Analog {len(self.ds.analog_names)}, Digital {len(self.ds.digital_names)}")
            self._preset()
        except Exception as exc: messagebox.showerror("Dataset error",str(exc),parent=self)

    def _preset(self):
        p=self.protocol.get(); labels=["Source1","Source2","Source3","Source4"]; plabels=["Baud/Bitrate","Param 2","Param 3","Param 4"]
        defaults=None
        if p in ("UART","RS-232"): labels=["RX","(unused)","(unused)","(unused)"]; plabels=["Baud","Data bits","Parity","Stop bits"]; defaults=("9600","8","None","1")
        elif p=="RS-485": labels=["A / RX","B (Analog diff)","(unused)","(unused)"]; plabels=["Baud","Data bits","Parity","Stop bits"]; defaults=("115200","8","None","1")
        elif p=="I2C": labels=["SCL","SDA","(unused)","(unused)"]; plabels=["(unused)","(unused)","(unused)","(unused)"]; defaults=("","","","")
        elif p=="SPI": labels=["SCLK","MOSI","MISO","CS (optional)"]; plabels=["Mode 0-3","Bits/word","Bit order MSB/LSB","CS active Low/High"]; defaults=("0","8","MSB","Low")
        elif p=="LIN": labels=["LIN","(unused)","(unused)","(unused)"]; plabels=["Baud","(unused)","(unused)","(unused)"]; defaults=("19200","","","")
        elif p=="CAN Classic": labels=["CAN RX","(unused)","(unused)","(unused)"]; plabels=["Bitrate","(unused)","(unused)","(unused)"]; defaults=("500000","","","")
        elif p=="GPS NMEA + PPS": labels=["GPS UART","PPS (optional)","(unused)","(unused)"]; plabels=["Baud","(unused)","(unused)","(unused)"]; defaults=("9600","","","")
        elif p=="GPS UBX": labels=["GPS UART","(unused)","(unused)","(unused)"]; plabels=["Baud","(unused)","(unused)","(unused)"]; defaults=("9600","","","")
        for l,t in zip(self.source_labels,labels): l.configure(text=t)
        for l,t in zip(self.param_labels,plabels): l.configure(text=t)
        if defaults:
            self.baud.set(defaults[0]); self.param2.set(defaults[1]); self.param3.set(defaults[2]); self.param4.set(defaults[3])
        # Clear sources that are optional/unused when switching protocol. Required sources keep current selections.
        required = {
            "UART": 1, "RS-232": 1, "RS-485": 1, "I2C": 2, "SPI": 1,
            "LIN": 1, "CAN Classic": 1, "GPS NMEA + PPS": 1, "GPS UBX": 1,
        }.get(p, 1)
        for i in range(required, 4):
            self.source_vars[i].set("")
        # Fill required empty sources from the available list without overriding explicit user choices.
        if self.ds is not None:
            available = self.ds.source_names
            for i in range(required):
                if not self.source_vars[i].get() and i < len(available):
                    self.source_vars[i].set(available[i])
        if p=="RS-232": self.invert.set(True)
        elif p not in ("RS-232",): self.invert.set(False)

    def _bounds(self):
        s=self.start_var.get().strip(); e=self.end_var.get().strip();
        if not s and not e: return None
        if not s or not e: raise ValueError("解析開始/終了は両方入力するか、両方空欄にしてください")
        return tuple(sorted((float(s),float(e))))

    def _logic(self,name,bounds,invert=None):
        if not name: raise ValueError("入力Sourceを選択してください")
        mode=self.time_mode.get(); ppm=float(self.ppm_var.get()); inv=self.invert.get() if invert is None else bool(invert)
        return self.ds.logic(name,threshold=self.threshold.get(),hysteresis=float(self.hyst.get() or 0),bounds=bounds,invert=inv,digital_mode=mode,ppm=ppm)[0]

    def decode(self):
        if self.busy:
            return
        if self.ds is None:
            messagebox.showwarning("Dataset", "データセットを開いてください", parent=self)
            return
        # Snapshot all Tk variables on the UI thread. Tkinter variables must not be read from the worker thread.
        try:
            cfg = {
                "bounds": self._bounds(),
                "protocol": self.protocol.get(),
                "sources": [v.get() for v in self.source_vars],
                "invert": bool(self.invert.get()),
                "mode": self.time_mode.get(),
                "ppm": float(self.ppm_var.get()),
                "threshold": self.threshold.get().strip() or "Auto",
                "hysteresis": float(self.hyst.get() or 0),
                "p1": self.baud.get().strip(),
                "p2": self.param2.get().strip(),
                "p3": self.param3.get().strip(),
                "p4": self.param4.get().strip(),
            }
        except Exception as exc:
            messagebox.showerror("設定エラー", str(exc), parent=self)
            return

        self.last_decode_config = dict(cfg)
        self.last_protocol = str(cfg.get("protocol", ""))
        self.busy = True
        self.decode_btn.configure(state="disabled")
        self.status.set("デコード中...")

        def worker():
            try:
                bounds = cfg["bounds"]
                p = cfg["protocol"]
                src = cfg["sources"]

                def logic(name, invert_override=None):
                    if not name:
                        raise ValueError("入力Sourceを選択してください")
                    inv = cfg["invert"] if invert_override is None else bool(invert_override)
                    return self.ds.logic(
                        name,
                        threshold=cfg["threshold"],
                        hysteresis=cfg["hysteresis"],
                        bounds=bounds,
                        invert=inv,
                        digital_mode=cfg["mode"],
                        ppm=cfg["ppm"],
                    )[0]

                plot_logic = []
                if p in ("UART", "RS-232"):
                    a = logic(src[0]); plot_logic = [a]
                    ev = decode_uart(a, baud=float(cfg["p1"]), data_bits=int(cfg["p2"]),
                                     parity=cfg["p3"], stop_bits=float(cfg["p4"]),
                                     invert=False, bounds=bounds)
                elif p == "RS-485":
                    if src[0].startswith("CHAN") and src[1].startswith("CHAN"):
                        threshold = 0.0 if cfg["threshold"].lower() == "auto" else float(cfg["threshold"])
                        a, _ = self.ds.differential_logic(
                            src[0], src[1], threshold=threshold, hysteresis=cfg["hysteresis"],
                            bounds=bounds, invert=cfg["invert"]
                        )
                    else:
                        a = logic(src[0])
                    plot_logic = [a]
                    ev = decode_uart(a, baud=float(cfg["p1"]), data_bits=int(cfg["p2"]),
                                     parity=cfg["p3"], stop_bits=float(cfg["p4"]), bounds=bounds)
                    for e in ev:
                        e.kind = "RS-485 BYTE"
                elif p == "I2C":
                    scl = logic(src[0]); sda = logic(src[1]); plot_logic = [scl, sda]
                    ev = decode_i2c(scl, sda, bounds=bounds)
                elif p == "SPI":
                    clk = logic(src[0])
                    mosi = logic(src[1]) if src[1] else None
                    miso = logic(src[2]) if src[2] else None
                    cs = logic(src[3]) if src[3] else None
                    plot_logic = [x for x in (clk, mosi, miso, cs) if x is not None]
                    ev = decode_spi(
                        clk, mosi, miso, cs=cs, mode=int(cfg["p1"]), bits_per_word=int(cfg["p2"]),
                        lsb_first=cfg["p3"].upper().startswith("L"),
                        cs_active_low=not cfg["p4"].lower().startswith("h"), bounds=bounds
                    )
                elif p == "LIN":
                    a = logic(src[0]); plot_logic = [a]
                    ev = decode_lin(a, baud=float(cfg["p1"]), bounds=bounds)
                elif p == "CAN Classic":
                    a = logic(src[0]); plot_logic = [a]
                    ev = decode_can(a, bitrate=float(cfg["p1"]), bounds=bounds)
                elif p == "GPS NMEA + PPS":
                    a = logic(src[0])
                    pps = logic(src[1], invert_override=False) if src[1] else None
                    plot_logic = [x for x in (a, pps) if x is not None]
                    ev = decode_gps_nmea(a, baud=float(cfg["p1"]), bounds=bounds, pps=pps)
                elif p == "GPS UBX":
                    a = logic(src[0]); plot_logic = [a]
                    ev = decode_gps_ubx(a, baud=float(cfg["p1"]), bounds=bounds)
                else:
                    raise ValueError(p)
                self.q.put(("ok", ev, plot_logic, bounds))
            except Exception as exc:
                self.q.put(("err", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _drain(self):
        try:
            while True:
                x=self.q.get_nowait()
                if x[0]=="ok":
                    self.events=x[1]; self.logic_for_plot=x[2]; self._show_results(x[3]); self.status.set(f"完了: {len(self.events):,} events"); self.busy=False; self.decode_btn.configure(state="normal")
                    if self.auto_overlay.get():
                        self.save_viewer_overlay(silent=True)
                else: self.busy=False; self.decode_btn.configure(state="normal"); self.status.set("エラー"); messagebox.showerror("Decode error",f"{type(x[1]).__name__}: {x[1]}",parent=self)
        except queue.Empty: pass
        self.after(100,self._drain)

    def _show_results(self,bounds):
        for k in self.tree.get_children(): self.tree.delete(k)
        for i,e in enumerate(self.events): self.tree.insert("", "end", iid=str(i), values=(f"{e.start_s:.9g}",f"{e.end_s:.9g}",e.kind,"OK" if e.ok else "ERR",e.summary))
        self.ax.clear(); signals=self.logic_for_plot
        if signals:
            lo=max(s.start_s for s in signals); hi=min(s.end_s for s in signals)
            if bounds is not None: lo=max(lo,bounds[0]); hi=min(hi,bounds[1])
            if self.events: lo=max(lo,min(e.start_s for e in self.events)-1e-6); hi=min(hi,max(e.end_s for e in self.events)+1e-6)
            # Avoid plotting millions of transitions at once.
            max_edges=30000
            for idx,sig in enumerate(signals):
                et,es=sig.transitions_in(lo,hi); step=max(1,int(math.ceil(len(et)/max_edges))); et=et[::step]; es=es[::step]
                state=sig.state_at(lo); xs=[lo]; ys=[state+idx*1.5]
                for t,st in zip(et,es): xs.extend([float(t),float(t)]); ys.extend([ys[-1],int(st)+idx*1.5])
                xs.append(hi); ys.append(ys[-1]); self.ax.plot(xs,ys,drawstyle="steps-post",label=sig.name)
            self.ax.set_xlim(lo,hi); self.ax.set_yticks([i*1.5+0.5 for i in range(len(signals))]); self.ax.set_yticklabels([s.name for s in signals]); self.ax.grid(True,axis="x",alpha=.3); self.ax.legend(loc="upper right")
        self.ax.set_xlabel("Time [s]"); self.fig.tight_layout(); self.canvas.draw_idle()

    def _selected(self,_event=None):
        sel=self.tree.selection();
        if not sel: return
        e=self.events[int(sel[0])]; self.details.configure(state="normal"); self.details.delete("1.0","end"); self.details.insert("1.0",json.dumps(e.to_dict(),ensure_ascii=False,indent=2)); self.details.configure(state="disabled")
        span=max(e.end_s-e.start_s,1e-6); self.ax.set_xlim(e.start_s-2*span,e.end_s+2*span); self.canvas.draw_idle()

    def save_viewer_overlay(self, silent=False):
        if self.ds is None:
            if not silent: messagebox.showwarning("Viewer overlay","データセットを開いてください",parent=self)
            return
        if not self.events:
            if not silent: messagebox.showwarning("Viewer overlay","先にデコードを実行してください",parent=self)
            return
        try:
            cfg=dict(self.last_decode_config or {})
            cfg["protocol"] = self.last_protocol or self.protocol.get()
            cfg["source"] = "Protocol Analyzer"
            path=save_active_overlay(self.ds.dataset,protocol=cfg["protocol"],events=self.events,config=cfg)
            self.status.set(f"Viewer帯へ反映: {path.name} / {len(self.events):,} events")
            if not silent:
                messagebox.showinfo("Viewer overlay","Viewerへ反映しました。\nViewerが開いている場合は自動で更新されます。",parent=self)
        except Exception as exc:
            if not silent: messagebox.showerror("Viewer overlay",str(exc),parent=self)
            else: self.status.set(f"Viewer帯の保存失敗: {exc}")

    def clear_viewer_overlay(self):
        if self.ds is None:
            return
        try:
            clear_active_overlay(self.ds.dataset)
            self.status.set("Viewerのプロトコル帯をクリアしました")
        except Exception as exc:
            messagebox.showerror("Viewer overlay",str(exc),parent=self)

    def export_csv(self):
        if not self.events: return
        p=filedialog.asksaveasfilename(parent=self,defaultextension=".csv",filetypes=[("CSV","*.csv")]);
        if not p: return
        with open(p,"w",newline="",encoding="utf-8-sig") as f:
            w=csv.writer(f); w.writerow(["start_s","end_s","kind","ok","summary","details_json"])
            for e in self.events: w.writerow([e.start_s,e.end_s,e.kind,e.ok,e.summary,json.dumps(e.details,ensure_ascii=False)])

    def export_json(self):
        if not self.events: return
        p=filedialog.asksaveasfilename(parent=self,defaultextension=".json",filetypes=[("JSON","*.json")]);
        if p: Path(p).write_text(json.dumps([e.to_dict() for e in self.events],ensure_ascii=False,indent=2),encoding="utf-8")


if __name__=="__main__":
    import sys
    dataset=sys.argv[1] if len(sys.argv)>1 else None
    ProtocolAnalyzer(dataset).mainloop()