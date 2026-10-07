import sys,json,traceback
from pathlib import Path
p=Path(r'D:\Mabimoba\release\MabinogiMobile_JPVoiceResourceTransfer_0.1.3');sys.path.insert(0,str(p));import transfer_jp_voice_resources as t;U=t.load_unitypy(); blob=Path(r'C:\Game\MabinogiMobile_TW-back\appdata\AssetPackages\0515.blob').read_bytes(); slices=list(t.iter_unityfs_slices(blob)); off=2455270; bundle=dict(slices)[off]; env=U.load(bundle);obj=next(o for o in env.objects if int(o.path_id)==4116516575601495025);c=obj.read();print('before',c.m_Frequency,c.m_Channels,c.m_Length);c.m_Frequency=8000;c.m_Length=3.05;c.save();print('saved clip');
try:r=env.file.save('original');print('saved bundle',len(r))
except Exception:traceback.print_exc()
