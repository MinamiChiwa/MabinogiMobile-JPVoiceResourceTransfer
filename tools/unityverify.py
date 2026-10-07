import sys,json,traceback
from pathlib import Path
p=Path(r'D:\Mabimoba\release\MabinogiMobile_JPVoiceResourceTransfer_0.1.3');sys.path.insert(0,str(p));import transfer_jp_voice_resources as t;U=t.load_unitypy();blob=Path(r'D:\Mabimoba\test-preserve-Amy9\appdata\AssetPackages\0515.blob').read_bytes();bundle=dict(t.iter_unityfs_slices(blob))[2455270];env=U.load(bundle);obj=next(o for o in env.objects if int(o.path_id)==4116516575601495025);c=obj.read();print(c.m_Frequency,c.m_Length); 
try: print('samples',len(c.samples))
except Exception:traceback.print_exc()
