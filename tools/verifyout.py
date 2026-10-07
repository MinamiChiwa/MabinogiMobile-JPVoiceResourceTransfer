import sys,json,tempfile,subprocess
from pathlib import Path
p=Path(r'D:\Mabimoba\release\MabinogiMobile_JPVoiceResourceTransfer_0.1.3');sys.path.insert(0,str(p));import transfer_jp_voice_resources as t
d=json.loads((p/'resource_cache.json').read_text(encoding='utf-8'));raw=(p/'resource_cache.bin').read_bytes();
for name,cap in [('Vo_NPC_Nao_Angry_01',6000),('Vo_NPC_Amy_Brrrr_01',3968)]:
 x=next(x for x in d['clips'] if x['name']==name);b=raw[x['offset']:x['offset']+x['size']]; y=t.compress_fsb5_to_capacity(b,cap)[0];td=Path(tempfile.mkdtemp());f=td/'x.fsb';f.write_bytes(y);r=subprocess.run([str(p/'vgmstream/vgmstream-cli.exe'),'-m',str(f)],capture_output=True,text=True,cwd=str(p/'vgmstream'));print(name,len(y),r.returncode,r.stdout[:500],r.stderr[:500])

