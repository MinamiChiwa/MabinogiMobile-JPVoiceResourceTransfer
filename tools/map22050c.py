import sys,subprocess,wave,tempfile,zlib
from pathlib import Path
sys.path.insert(0,r'D:\Mabimoba\tools');from oggparse import ogg_packets
ff=r'D:\Mabimoba\release\MabinogiMobile_JPVoiceResourceTransfer_0.1.3\ffmpeg.exe';td=Path(tempfile.mkdtemp());w=td/'x.wav'
with wave.open(str(w),'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(22050);f.writeframes(b'\0\0'*2205)
for i in range(0,201):
 q=i/10;r=subprocess.run([ff,'-hide_banner','-loglevel','error','-i',str(w),'-c:a','libvorbis','-q:a',str(q),'-f','ogg','pipe:1'],capture_output=True)
 if r.returncode:continue
 p=ogg_packets(r.stdout);crc=zlib.crc32(p[2])&0xffffffff
 if crc==1783903935:print('MATCH',q,len(p[2]))
 if i%10==0:print(q,len(p[2]),hex(crc))
