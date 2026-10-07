import subprocess,tempfile,wave,struct,os,sys
from pathlib import Path
ff=r'C:\Program Files\Virtual Desktop Streamer\ffmpeg.exe'; td=Path(tempfile.mkdtemp()); w=td/'x.wav';
with wave.open(str(w),'wb') as f:f.setnchannels(2); f.setsampwidth(2);f.setframerate(44100); f.writeframes(b'\\0\\0'*(2*44100//10)*2)
for q in range(0,11):
 out=td/f'q{q}.ogg'; subprocess.run([ff,'-y','-hide_banner','-loglevel','error','-i',str(w),'-c:a','libvorbis','-q:a',str(q),str(out)],check=True); d=out.read_bytes(); pos=0; ps=[]; cur=bytearray()
 while pos<len(d):
  nseg=d[pos+26]; seg=d[pos+27:pos+27+nseg]; bodypos=pos+27+nseg; body=d[bodypos:bodypos+sum(seg)]; off=0
  for n in seg:
   cur+=body[off:off+n]; off+=n
   if n<255: ps.append(bytes(cur));cur.clear()
  pos=bodypos+len(body)
 import zlib
 print(q,len(ps[2]),hex(zlib.crc32(ps[2])&0xffffffff))
print(td)
