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

# UART parity success + parity-error path
def uart_frame_with_parity(v, parity="even"):
    db=[(int(v)>>i)&1 for i in range(8)]
    ones=sum(db)
    if parity=="even": pb=ones & 1
    elif parity=="odd": pb=1-(ones & 1)
    else: raise ValueError(parity)
    return [0]+db+[pb]+[1]

upe=signal_from_bits(uart_frame_with_parity(0x53,"even"),1/57600)
uev=decode_uart(upe,baud=57600,data_bits=8,parity="Even",stop_bits=1)
assert uev and uev[0].ok and uev[0].details["value"]==0x53 and uev[0].details["parity_ok"] is True
bad_bits=uart_frame_with_parity(0x53,"even"); bad_bits[-2] ^= 1
upb=signal_from_bits(bad_bits,1/57600)
ube=decode_uart(upb,baud=57600,data_bits=8,parity="Even",stop_bits=1)
assert ube and not ube[0].ok and ube[0].details["parity_ok"] is False

# CAN helpers for extended-ID, RTR and CRC-negative regressions
def stuff_can(logical, ack=0):
    raw=[]; last=None; run=0
    for bit in logical:
        if run==5:
            raw.append(1-last); run=0
        raw.append(bit)
        if last is None or bit!=last:
            last=bit; run=1
        else:
            run+=1
    if run==5:
        raw.append(1-last)
    raw += [1,ack,1]+[1]*7
    return raw

# Extended 29-bit data frame
ext_id=0x1ABCDE3; ext_data=[0x5A]
ext_base=[0]+bits_of((ext_id>>18)&0x7FF,11)+[1,1]+bits_of(ext_id&0x3FFFF,18)+[0,0,0]+bits_of(1,4)+bits_of(ext_data[0],8)
ext_crc=can_crc15(ext_base)
ext_sig=signal_from_bits(stuff_can(ext_base+bits_of(ext_crc,15)),1/500000,idle_before=5,idle_after=5,name="CAN_EXT")
ext_ev=decode_can(ext_sig,bitrate=500000)
assert ext_ev and ext_ev[0].ok and ext_ev[0].details["extended"] is True
assert ext_ev[0].details["id"]==ext_id and ext_ev[0].details["data"]==ext_data

# Standard RTR frame
rtr_id=0x321
rtr_base=[0]+bits_of(rtr_id,11)+[1,0,0]+bits_of(0,4)
rtr_crc=can_crc15(rtr_base)
rtr_sig=signal_from_bits(stuff_can(rtr_base+bits_of(rtr_crc,15)),1/500000,idle_before=5,idle_after=5,name="CAN_RTR")
rtr_ev=decode_can(rtr_sig,bitrate=500000)
assert rtr_ev and rtr_ev[0].ok and rtr_ev[0].details["rtr"] is True and rtr_ev[0].details["id"]==rtr_id

# Deliberately wrong CRC must decode as a frame but fail integrity.
bad_crc=(crc ^ 1) & 0x7FFF
bad_can=signal_from_bits(stuff_can(base+bits_of(bad_crc,15)),1/500000,idle_before=5,idle_after=5,name="CAN_BADCRC")
bad_ce=decode_can(bad_can,bitrate=500000)
assert bad_ce and not bad_ce[0].ok and bad_ce[0].details["crc_ok"] is False

# GPS GGA parse + bad checksum + PPS timing correlation
gga_body="GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,"
gga_cs=0
for ch in gga_body.encode("ascii"): gga_cs ^= ch
gga=(f"${gga_body}*{gga_cs:02X}\r\n").encode("ascii")
gga_sig=signal_from_bits(uart_stream(gga),1/9600)
gga_ev=decode_gps_nmea(gga_sig,baud=9600)
assert gga_ev and gga_ev[0].ok and gga_ev[0].details["sentence_type"]=="GPGGA"
assert gga_ev[0].details["fix_quality"]==1 and gga_ev[0].details["satellites"]==8

bad_nmea=(f"${gga_body}*00\r\n").encode("ascii")
bad_ne=decode_gps_nmea(signal_from_bits(uart_stream(bad_nmea),1/9600),baud=9600)
assert bad_ne and not bad_ne[0].ok and bad_ne[0].details["checksum_ok"] is False

pps=LogicSignal(0.0,2.0,0,[0.25,0.26,1.25,1.26],[1,0,1,0],name="PPS")
with_pps=decode_gps_nmea(gga_sig,baud=9600,pps=pps)
pps_events=[x for x in with_pps if x.kind=="GPS PPS"]
assert len(pps_events)==2
assert abs(pps_events[1].details["period_s"]-1.0)<1e-12

print('protocol_decoder_selftest: PASS')
