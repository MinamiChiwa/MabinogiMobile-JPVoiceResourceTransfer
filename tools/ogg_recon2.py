import os,sys,json,struct,subprocess,tempfile,zlib,binascii
from pathlib import Path
os.chdir(r'D:\Mabimoba\tools\fsb5dll')
from fsb5 import load
sys.path.insert(0,r'D:\Mabimoba\tools')
from oggparse import ogg_packets
import imageio_ffmpeg
cache=Path(r'D:\Mabimoba\release\MabinogiMobile_JPVoiceResourceTransfer_0.1.3'); d=json.loads((cache/'resource_cache.json').read_text(encoding='utf-8')); raw=(cache/'resource_cache.bin').read_bytes(); x=next(x for x in d['clips'] if x['name']=='Vo_NPC_Nao_Angry_01'); f=load(raw[x['offset']:x['offset']+x['size']]); s=f.samples[0]
# dummy headers from q1 input, use actual source quality q1
ff=imageio_ffmpeg.get_ffmpeg_exe(); td=Path(tempfile.mkdtemp()); import wave
w=td/'x.wav';
with wave.open(str(w),'wb') as h:h.setnchannels(1);h.setsampwidth(2);h.setframerate(44100);h.writeframes(b'\0\0'*4410)
dummy=td/'d.ogg';subprocess.run([ff,'-y','-hide_banner','-loglevel','error','-i',str(w),'-c:a','libvorbis','-q:a','1',str(dummy)],check=True)
headers=ogg_packets(dummy.read_bytes())[:3]
# source custom packets
b=s.data; pos=0; aps=[]
while pos+2<=len(b):
 n=struct.unpack_from('<H',b,pos)[0];pos+=2
 if n==0:break
 aps.append(b[pos:pos+n]);pos+=n
# Ogg crc
def crc(data):
 c=0
 for by in data:
  c ^= by<<24
  for _ in range(8): c=((c<<1)^0x04c11db7)&0xffffffff if c&0x80000000 else (c<<1)&0xffffffff
 return c
def page(packet,seq,flag,granule,serial=1):
 seg=[]; off=0
 while off<len(packet):
  n=min(255,len(packet)-off); seg.append(n); off+=n
 if not packet or len(packet)%255==0: seg.append(0)
 h=bytearray(struct.pack('<4sBBQIIIB',b'OggS',0,flag,granule,serial,seq,0,len(seg))); h.extend(seg); h.extend(packet); struct.pack_into('<I',h,22,crc(h)); return bytes(h)
out=bytearray(); seq=0
for h in headers: out+=page(h,seq,2 if seq==0 else 0,0); seq+=1
for i,p in enumerate(aps):out+=page(p,seq,4 if i==len(aps)-1 else 0,int(s.samples*(i+1)/len(aps)));seq+=1
opf=td/'recon.ogg';opf.write_bytes(out);print('recon',len(out), 'packets',len(aps), 'target',len(bytes(f.rebuild_sample(s))))
# ffmpeg decode
wav=td/'recon.wav';p=subprocess.run([ff,'-y','-hide_banner','-loglevel','warning','-i',str(opf),str(wav)],capture_output=True,text=True);print('rc',p.returncode,p.stderr, 'wav',wav.stat().st_size if wav.exists() else None);print(td)

