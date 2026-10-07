import os,sys,json,subprocess,tempfile,struct
os.chdir(r'D:\Mabimoba\tools\fsb5dll')
from fsb5 import load
from pathlib import Path
import imageio_ffmpeg
cache=Path(r'D:\Mabimoba\release\MabinogiMobile_JPVoiceResourceTransfer_0.1.3')
d=json.loads((cache/'resource_cache.json').read_text(encoding='utf-8')); raw=(cache/'resource_cache.bin').read_bytes(); x=next(x for x in d['clips'] if x['name']=='Vo_NPC_Nao_Angry_01'); b=raw[x['offset']:x['offset']+x['size']]; f=load(b); ogg=bytes(f.rebuild_sample(f.samples[0]));
ff=imageio_ffmpeg.get_ffmpeg_exe(); td=Path(tempfile.mkdtemp(prefix='fcheck-')); (td/'in.ogg').write_bytes(ogg)
for q in ['0','1','2','3','4','5']:
 out=td/f'q{q}.ogg'; p=subprocess.run([ff,'-y','-i',str(td/'in.ogg'),'-c:a','libvorbis','-q:a',q,str(out)],capture_output=True,text=True); print('q',q,'rc',p.returncode,'size',out.stat().st_size if out.exists() else None); print(p.stderr[-300:]);
 if out.exists(): print((out.read_bytes()[:100]).hex())
print(td)
