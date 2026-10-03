#!/usr/bin/env python3
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from protocol_decode import (
    LogicSignal, decode_uart, decode_i2c, decode_spi, decode_lin, decode_can,
    decode_gps_nmea, decode_gps_ubx, can_crc15, lin_checksum,
)


def signal_from_bits(bits, bit_time, idle_before=4, idle_after=4, name="S"):
    seq=[1]*idle_before+list(bits)+[1]*idle_after
    et=[]; es=[]; st=seq[0]
    for i,v in enumerate(seq[1:],1):
        if v!=st: et.append(i*bit_time); es.append(v); st=v
    return LogicSignal(0,len(seq)*bit_time,seq[0],et,es,name=name)

def uart_stream(vals):
    out=[]
    for v in vals: out += [0]+[(int(v)>>i)&1 for i in range(8)]+[1]
    return out

def bits_of(v,n): return [(v>>(n-1-i))&1 for i in range(n)]

# UART / RS-232 inversion
s=signal_from_bits(uart_stream([0x55,0x41]),1/115200)
e=decode_uart(s,baud=115200); assert [x.details['value'] for x in e]==[0x55,0x41] and all(x.ok for x in e)
e=decode_uart(s.inverted(),baud=115200,invert=True); assert [x.details['value'] for x in e]==[0x55,0x41]

# I2C: address 0x50 write, data 0x12
T=1e-6; se=[];ss=[];de=[];ds=[];t=0;scl=1;sda=1
sda=0;de.append(T);ds.append(0);t=1.5*T;scl=0;se.append(t);ss.append(0)
ibits=bits_of(0xA0,8)+[0]+bits_of(0x12,8)+[0]
for b in ibits:
    if sda!=b:sda=b;de.append(t+.1*T);ds.append(b)
    scl=1;se.append(t+.4*T);ss.append(1);scl=0;se.append(t+.8*T);ss.append(0);t+=T
scl=1;se.append(t+.3*T);ss.append(1)
if sda!=0:sda=0;de.append(t+.1*T);ds.append(0)
sda=1;de.append(t+.6*T);ds.append(1);t+=T
SCL=LogicSignal(0,t+T,1,se,ss,name='SCL'); SDA=LogicSignal(0,t+T,1,de,ds,name='SDA')
ie=decode_i2c(SCL,SDA); assert any(x.details.get('address')==0x50 for x in ie); assert any(x.details.get('byte')==0x12 for x in ie)

# SPI mode 0 MOSI 0xA5
T=1e-6; bits=bits_of(0xA5,8); ce=[];cs=[];me=[];ms=[]; cse=[T];css=[0];mosi=bits[0];t=T
for i,b in enumerate(bits):
    if i>0 and b!=mosi: mosi=b;me.append(t);ms.append(b)
    ce += [t+.25*T,t+.75*T]; cs += [1,0]; t+=T
cse.append(t);css.append(1)
CLK=LogicSignal(0,t+T,0,ce,cs,name='CLK'); MOSI=LogicSignal(0,t+T,bits[0],me,ms,name='MOSI'); CS=LogicSignal(0,t+T,1,cse,css,name='CS')
sp=decode_spi(CLK,MOSI,None,cs=CS,mode=0,bits_per_word=8); assert sp[0].details['mosi']==0xA5

# LIN enhanced checksum
T=1/19200; ident=0x12; b=[(ident>>i)&1 for i in range(6)]; p0=b[0]^b[1]^b[2]^b[4]; p1=1^(b[1]^b[3]^b[4]^b[5]); pid=ident|(p0<<6)|(p1<<7); data=[1,2,3]; chk=lin_checksum([pid]+data); vals=[0x55,pid]+data+[chk]
seq=[1]*4+[0]*14+[1]*2+uart_stream(vals)+[1]*5; lin=signal_from_bits(seq,T,idle_before=0,idle_after=0)
le=decode_lin(lin,baud=19200); assert le and le[0].ok and le[0].details['id']==ident

# CAN Classic standard 0x123, 2 bytes
ident=0x123; data=[0x11,0x22]; base=[0]+bits_of(ident,11)+[0,0,0]+bits_of(2,4)+sum((bits_of(x,8) for x in data),[]); crc=can_crc15(base); logical=base+bits_of(crc,15)
raw=[];last=None;run=0
for bit in logical:
    if run==5: raw.append(1-last);run=0
    raw.append(bit)
    if last is None or bit!=last:last=bit;run=1
    else:run+=1
if run==5:raw.append(1-last)
raw += [1,0,1]+[1]*7
can=signal_from_bits(raw,1/500000,idle_before=5,idle_after=5,name='CAN'); ca=decode_can(can,bitrate=500000); assert ca and ca[0].ok and ca[0].details['id']==0x123 and ca[0].details['data']==data

# GPS NMEA
line=b'$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A\r\n'; gps=signal_from_bits(uart_stream(line),1/9600); ne=decode_gps_nmea(gps,baud=9600); assert ne and ne[0].ok and ne[0].details['sentence_type']=='GPRMC'
# GPS UBX
payload=[1,2,3,4]; core=[1,7,len(payload)&255,(len(payload)>>8)&255]+payload; a=bk=0
for v in core:a=(a+v)&255;bk=(bk+a)&255
ubx=[0xB5,0x62]+core+[a,bk]; ue=decode_gps_ubx(signal_from_bits(uart_stream(ubx),1/9600),baud=9600); assert ue and ue[0].ok
print('protocol_decoder_selftest: PASS')
