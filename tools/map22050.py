import subprocess,wave,tempfile,zlib
from pathlib import Path
ff=r'D:\Mabimoba\release\MabinogiMobile_JPVoiceResourceTransfer_0.1.3\ffmpeg.exe';td=Path(tempfile.mkdtemp()); w=td/'x.wav'
with wave.open(str(w),'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(22050);f.writeframes(b'\0\0'*2205)
def ps(d):
 out=[];c=bytearray();p=0
 while p<len(d):
  n=d[p+26];s=d[p+27:p+27+n];bp=p+27+n;b=d[bp:bp+sum(s)];o=0
  for x in s:c.extend(b[o:o+x]);o+=x;out += [bytes(c)] if x<255 else []; c.clear() if x<255 else None
  p=bp+len(b)
 return out
for i in range(0,201):
 q=i/10; o=td/'o'; r=subprocess.run([ff,'-y','-hide_banner','-loglevel','error','-i',str(w),'-c:a','libvorbis','-q:a',str(q),str(o)],capture_output=True)
 if r.returncode:continue
 p=ps(o.read_bytes());crc=zlib.crc32(p[2])&0xffffffff
 if crc==1783903935:print('MATCH',q,len(p[2]))
 if i%10==0:print(q,len(p[2]),hex(crc))
