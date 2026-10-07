import sys,json,tempfile,subprocess,struct,wave,os,statistics,glob
from pathlib import Path
p=Path(r'D:\Mabimoba\release\MabinogiMobile_JPVoiceResourceTransfer_0.1.3');sys.path.insert(0,str(p));import transfer_jp_voice_resources as t
d=json.loads((p/'resource_cache.json').read_text(encoding='utf-8'));raw=(p/'resource_cache.bin').read_bytes();x=next(x for x in d['clips'] if x['name']=='Vo_NPC_Nao_Angry_01');b=raw[x['offset']:x['offset']+x['size']];pcm,_,_=t.decode_fsb5_pcm(b);i=t.parse_fsb5(b)
for q in [0,5]:
 e=t.run_ffmpeg(['-f','s16le','-ar','22050','-ac','1','-i','pipe:0','-c:a','libvorbis','-q:a',str(q),'-f','ogg','pipe:1'],pcm);pack=t.ogg_packets(e);custom=b''.join(struct.pack('<H',len(z))+z for z in pack[3:]);h=bytearray(b[:i['header_size']]);struct.pack_into('<I',h,20,len(custom));sh=bytearray(b[i['sample_header_start']:i['sample_header_end']]);struct.pack_into('<I',sh,i['crc_offset']-i['sample_header_start'],i['crc']);out=bytes(h)+bytes(sh)+b[i['sample_header_end']:i['payload_start']]+custom;td=Path(tempfile.mkdtemp());f=td/'x.fsb';f.write_bytes(out);wav=td/'x.wav';r=subprocess.run([str(p/'vgmstream/vgmstream-cli.exe'),'-o',str(wav),str(f)],capture_output=True,text=True,cwd=str(p/'vgmstream'));print('q',q,'rc',r.returncode,'size',wav.stat().st_size if wav.exists() else None,'err',r.stderr[:200]);
 if wav.exists():
  w=wave.open(str(wav),'rb');bb=w.readframes(w.getnframes());vals=struct.unpack('<%dh'%(len(bb)//2),bb);print('rms',statistics.mean(abs(v) for v in vals),'max',max(abs(v) for v in vals),td)


