import sys,struct,tempfile,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import mho984_bin_decoder_r12_7 as d

def fixture(path,loss=32,kind='CH2',actual_total=False):
    chunks=[]
    for name in ['CH1',kind]:
        h=bytearray(112);struct.pack_into('<IIIII',h,0,112,1,1,1000,0)
        struct.pack_into('<fdddII',h,20,1.0,0.0,0.001,0.0,1,1)
        dh=bytearray(44);dh[:len(name)]=name.encode();struct.pack_into('<IIHHII',dh,24,1,44,1,4,4000,0)
        chunks.extend([h,dh,struct.pack('<1000f',*range(1000))])
    payload=b''.join(chunks);size=16+len(payload)
    path.write_bytes(b'RG03'+struct.pack('<III',size-loss if actual_total else size,0,2)+payload[:-loss] if loss else b'RG03'+struct.pack('<III',size,0,2)+payload)

with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);p=root/'capture.bin'
    for loss,kind,actual,opt,ok in [(0,'CH2',False,False,True),(32,'CH2',False,False,False),(32,'CH2',False,True,True),(32,'CH2',True,False,True),(44,'CH2',False,True,False),(31,'CH2',False,True,False),(32,'LA',False,True,False)]:
        fixture(p,loss,kind,actual)
        try:
            fh,rs,blob=d.parse_rg03(p,recover_incomplete_analog_tail=opt)
            assert ok
            if loss: assert d.validate_common_timebase(rs)['full_record_complete'] is False
            blob.close()
        except d.BinFormatError:
            assert not ok,(loss,kind,actual,opt)
    fixture(p)
    original=p.read_bytes()
    result=d.decode_to_dataset(p,root/'output',copy_memory_bin=False,recover_incomplete_analog_tail=True)
    assert p.read_bytes()==original
    acq=json.loads((root/'output/acquisition.json').read_text())
    assert acq['synchronization']['analog_full_record_complete'] is False
    assert acq['synchronization']['analog_common_overlap_points']==992
    print('PASS: 7 parser cases, full decode, incomplete metadata, source unchanged')


import subprocess
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);p=root/'capture.bin';fixture(p)
    h,rs,b=d.parse_rg03(p);assert h['incomplete_source_recovered'];b.close()
    for opts,success in [([],True),(['--strict-bin'],False),(['--recover-incomplete-analog-tail'],True)]:
        out=root/('out'+str(len(opts))+str(success))
        r=subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/'mho984_bin_decoder_r12_7.py'),str(p),str(out),'--no-copy',*opts],capture_output=True)
        assert (r.returncode==0)==success,r.stderr
        if success:
            assert (out/'INCOMPLETE_ANALOG_WARNING.txt').exists()
            import numpy as np
            bins=list((out/'analog').glob('*.bin'));assert len(bins)==2
            for binary in bins:
                samples=np.fromfile(binary,dtype='<f4');assert len(samples)==992
                assert np.array_equal(samples,np.arange(992,dtype=np.float32))
print('PASS: default API, default CLI, strict rejection, legacy option, warning file, exact samples')
