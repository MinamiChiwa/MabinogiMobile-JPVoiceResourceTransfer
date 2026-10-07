import sys,json,tempfile,subprocess
from pathlib import Path
p=Path(r'D:\Mabimoba\release\MabinogiMobile_JPVoiceResourceTransfer_0.1.3');sys.path.insert(0,str(p));import transfer_jp_voice_resources as t
d=json.loads((p/'resource_cache.json').read_text(encoding='utf-8'));raw=(p/'resource_cache.bin').read_bytes();x=next(x for x in d['clips'] if x['name']=='Vo_NPC_Amy_Brrrr_01');b=raw[x['offset']:x['offset']+x['size']];i=t.parse_fsb5(b);pcm,_,_=t.decode_fsb5_pcm(b);r=t.run_ffmpeg(['-f','s16le','-ar','22050','-ac','1','-i','pipe:0','-f','s16le','-ar','8000','-ac','1','pipe:1'],pcm);q=t.encode_fsb5_at_quality(r,1,8000,0,t.retarget_fsb5_template(b,1,8000));td=Path(tempfile.mkdtemp());f=td/'x.fsb';f.write_bytes(q);v=p/'vgmstream/vgmstream-cli.exe';
for args in [['-I',str(f)],['-m',str(f)],['-v',str(f)]]:
 z=subprocess.run([str(v),*args],capture_output=True,text=True,cwd=str(v.parent));print(args[0],z.returncode,z.stdout[:2000],z.stderr[:2000])
print('crc',hex(t.parse_fsb5(q)['crc']))

