import sys,json,tempfile,subprocess
from pathlib import Path
p=Path(r'D:\Mabimoba\release\MabinogiMobile_JPVoiceResourceTransfer_0.1.3');sys.path.insert(0,str(p));import transfer_jp_voice_resources as t
d=json.loads((p/'resource_cache.json').read_text(encoding='utf-8'));raw=(p/'resource_cache.bin').read_bytes();x=next(x for x in d['clips'] if x['name']=='Vo_NPC_Amy_Brrrr_01');b=raw[x['offset']:x['offset']+x['size']];y=t.compress_fsb5_to_capacity(b,3968);q=y[0];td=Path(tempfile.mkdtemp());f=td/'x.fsb';f.write_bytes(q);r=subprocess.run([str(p/'vgmstream/vgmstream-cli.exe'),'-m',str(f)],capture_output=True,text=True,cwd=str(p/'vgmstream'));print(y[1:],r.returncode,r.stdout,r.stderr)
