import os,sys,ctypes,zlib
os.chdir(r'D:\Mabimoba\tools\fsb5dll');sys.path.insert(0,r'D:\Mabimoba\tools\python-fsb5');from fsb5 import vorbis as v
L=v.vorbis
L.vorbis_encode_init.argtypes=[ctypes.POINTER(v.VorbisInfo),ctypes.c_long,ctypes.c_long,ctypes.c_long,ctypes.c_long,ctypes.c_long];L.vorbis_encode_init.restype=ctypes.c_int
L.vorbis_analysis_init.argtypes=[ctypes.POINTER(v.VorbisDSPState),ctypes.POINTER(v.VorbisInfo)];L.vorbis_analysis_init.restype=ctypes.c_int
L.vorbis_analysis_headerout.argtypes=[ctypes.POINTER(v.VorbisDSPState),ctypes.POINTER(v.VorbisComment),ctypes.POINTER(v.OggPacket),ctypes.POINTER(v.OggPacket),ctypes.POINTER(v.OggPacket)];L.vorbis_analysis_headerout.restype=ctypes.c_int
from fsb5.vorbis_headers import lookup
for ch,rate,target in [(1,22050,0x6a5436bf),(2,44100,0x90021eee)]:
 print('target',ch,rate,hex(target),len(lookup[target]))
 found=[]
 for b in range(1000,192001,1000):
  vi=v.VorbisInfo();
  r=L.vorbis_encode_init(ctypes.byref(vi),ch,rate,-1,b,-1)
  if r:continue
  vd=v.VorbisDSPState();L.vorbis_analysis_init(ctypes.byref(vd),ctypes.byref(vi));vc=v.VorbisComment();a=v.OggPacket();bb=v.OggPacket();sp=v.OggPacket();L.vorbis_analysis_headerout(ctypes.byref(vd),ctypes.byref(vc),ctypes.byref(a),ctypes.byref(bb),ctypes.byref(sp));x=bytes(sp.packet[:sp.bytes]);crc=zlib.crc32(x)&0xffffffff
  if crc==target: print('MATCH',b,len(x));found.append(b); break
  if b in [8000,12000,16000,20000,24000,32000,40000,48000,64000,96000,128000]:print(b,len(x),hex(crc))
 print('found',found)
