import os,sys,ctypes,zlib
os.chdir(r'D:\Mabimoba\tools\fsb5dll');sys.path.insert(0,r'D:\Mabimoba\tools\python-fsb5');from fsb5 import vorbis as v
from fsb5.vorbis_headers import lookup
L=v.vorbis;L.vorbis_encode_init_vbr.argtypes=[ctypes.POINTER(v.VorbisInfo),ctypes.c_long,ctypes.c_long,ctypes.c_float];L.vorbis_encode_init_vbr.restype=ctypes.c_int;L.vorbis_analysis_init.argtypes=[ctypes.POINTER(v.VorbisDSPState),ctypes.POINTER(v.VorbisInfo)];L.vorbis_analysis_init.restype=ctypes.c_int;L.vorbis_analysis_headerout.argtypes=[ctypes.POINTER(v.VorbisDSPState),ctypes.POINTER(v.VorbisComment),ctypes.POINTER(v.OggPacket),ctypes.POINTER(v.OggPacket),ctypes.POINTER(v.OggPacket)];L.vorbis_analysis_headerout.restype=ctypes.c_int
for rate in [8000,11000,11025,16000,22050,24000,32000,44100,48000]:
 for ch in [1,2]:
  cs=[]
  for qi in range(-100,1001):
   q=qi/100
   vi=v.VorbisInfo();
   if L.vorbis_encode_init_vbr(ctypes.byref(vi),ch,rate,q):continue
   vd=v.VorbisDSPState();L.vorbis_analysis_init(ctypes.byref(vd),ctypes.byref(vi));vc=v.VorbisComment();a=v.OggPacket();b=v.OggPacket();sp=v.OggPacket();L.vorbis_analysis_headerout(ctypes.byref(vd),ctypes.byref(vc),ctypes.byref(a),ctypes.byref(b),ctypes.byref(sp));bb=bytes(sp.packet[:sp.bytes]);c=zlib.crc32(bb)&0xffffffff
   if c in [0x6a5436bf,0x84199294,0x90021eee,0xbec759ec,0xd6e0bbd4,0x910d8ea5,0xd7913109]:cs.append((hex(c),q,len(bb)))
  if cs:print(rate,ch,cs)
