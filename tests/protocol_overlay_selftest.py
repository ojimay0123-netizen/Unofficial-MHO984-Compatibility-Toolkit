import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tempfile
from protocol_decode import DecodeEvent
from protocol_overlay import save_active_overlay, load_active_overlay, clear_active_overlay

with tempfile.TemporaryDirectory() as td:
    ds=Path(td)
    ev=[
        DecodeEvent(0.1,0.2,'UART',"0x41 'A'",{'value':65},True),
        DecodeEvent(0.25,0.35,'CAN FRAME','ID=0x123 DLC=1',{'id':0x123},True),
        DecodeEvent(0.4,0.45,'UART','framing error',{},False),
    ]
    p=save_active_overlay(ds,protocol='mixed-test',events=ev,config={'sources':['D0'],'mode':'Raw'})
    assert p.is_file()
    doc=load_active_overlay(ds)
    assert doc['event_count']==3
    assert doc['events'][0]['summary']=="0x41 'A'"
    assert doc['events'][2]['ok'] is False
    assert clear_active_overlay(ds)
    assert load_active_overlay(ds) is None
print('protocol_overlay_selftest: PASS')
