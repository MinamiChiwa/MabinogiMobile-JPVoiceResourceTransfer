import sys,re,traceback
from pathlib import Path
p=Path(r'D:\Mabimoba\release\MabinogiMobile_JPVoiceResourceTransfer_0.1.3');sys.path.insert(0,str(p));import transfer_jp_voice_resources as t
U=t.load_unitypy(); twroot=Path(r'C:\Game\MabinogiMobile_TW-back')/'appdata'; target,_=t.scan_tw_targets(twroot,U,[re.compile('Vo_NPC_Amy_Brrrr_01',re.I)],1,False);print('target',target[0]); full,byname,st=t.load_resource_cache(p);jp,kind=t.select_jp_clip(target[0].name,target[0].container,full,byname);tw=target[0];tw.blob_path=Path(r'D:\Mabimoba\test-preserve-Amy4\appdata')/tw.blob_path.relative_to(twroot);m={'tw':tw,'jp':jp,'match_kind':kind};
try: print(t.replace_matches_preserve_structure(Path(r'D:\Mabimoba\test-preserve-Amy4\appdata'),[m],U))
except Exception: traceback.print_exc()
