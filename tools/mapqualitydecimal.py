import subprocess,tempfile,wave,os,zlib
from pathlib import Path
ff=r'C:\Program Files\Virtual Desktop Streamer\ffmpeg.exe'; td=Path(tempfile.mkdtemp()); w=td/'x.wav';
with wave.open(str(w),'wb') as f:f.setnchannels(2);f.setsampwidth(2);f.setframerate(44100);f.writeframes(b'\0\0'*(2*44100//10)*2)
def packets(d):
 ps=[];cur=bytearray();pos=0
 while pos<len(d):
  nseg=d[pos+26]; seg=d[pos+27:pos+27+nseg]; bp=pos+27+nseg; body=d[bp:bp+sum(seg)]; off=0
  for n in seg:
   cur+=body[off:off+n];off+=n
   if n<255:ps.append(bytes(cur));cur.clear()
  pos=bp+len(body)
 return ps
for q in [4.6,4.7,4.8,4.9,5.0,5.1,5.2,5.3,5.4,5.5]:
 out=td/f'q{q}.ogg'; p=subprocess.run([ff,'-y','-hide_banner','-loglevel','error','-i',str(w),'-c:a','libvorbis','-q:a',str(q),str(out)],capture_output=True); 
 if p.returncode: print('fail',q,p.stderr); continue
 x=packets(out.read_bytes())[2]; print(q,len(x),hex(zlib.crc32(x)&0xffffffff))
