import os,sys,json,struct,subprocess,tempfile,binascii
from pathlib import Path
os.chdir(r'D:\Mabimoba\tools\fsb5dll')
from fsb5 import load
sys.path.insert(0,r'D:\Mabimoba\tools')
from oggparse import ogg_packets
import imageio_ffmpeg
cache=Path(r'D:\Mabimoba\release\MabinogiMobile_JPVoiceResourceTransfer_0.1.3'); d=json.loads((cache/'resource_cache.json').read_text(encoding='utf-8')); raw=(cache/'resource_cache.bin').read_bytes(); x=next(x for x in d['clips'] if x['name']=='Vo_NPC_Nao_Angry_01'); orig=raw[x['offset']:x['offset']+x['size']]; f=load(orig); s=f.samples[0]
td=Path(tempfile.mkdtemp(prefix='fpack-')); src=td/'src.ogg'; src.write_bytes(bytes(f.rebuild_sample(s))); out=td/'q0.ogg'; ff=imageio_ffmpeg.get_ffmpeg_exe(); subprocess.run([ff,'-y','-i',str(src),'-c:a','libvorbis','-q:a','0',str(out)],check=True,capture_output=True)
p=ogg_packets(out.read_bytes()); custom=b''.join(struct.pack('<H',len(z))+z for z in p[3:]); print('packets',len(p), 'custom',len(custom), 'sizes', [len(z) for z in p[:3]])
# Preserve header and sample header, modify dataSize and CRC, pad? 
header=bytearray(orig[:60]); struct.pack_into('<I',header,20,len(custom)) # dataSize offset? header fields: id4,version4,num4,sampleHeaders4,name4,data4 => offset20
sh=bytearray(orig[60:96]); struct.pack_into('<I',sh,12,binascii.crc32(p[2])&0xffffffff); new=bytes(header)+bytes(sh)+custom
print('new len',len(new), 'header',load(new).header, 'sample',load(new).samples[0]); ogg=bytes(load(new).rebuild_sample(load(new).samples[0])); print('rebuilt ogg',len(ogg),ogg[:4])
# decode to pcm and compare
wave=td/'out.wav'; subprocess.run([ff,'-y','-i','pipe:0','-f','wav',str(wave)],input=ogg,capture_output=True,check=True); print('wav',wave.stat().st_size); print(td)
