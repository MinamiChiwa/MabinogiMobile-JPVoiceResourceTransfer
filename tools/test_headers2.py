import os,sys,ctypes,zlib,subprocess,io,wave,tempfile,struct
from pathlib import Path
os.chdir(r'D:\Mabimoba\tools\fsb5dll');sys.path.insert(0,r'D:\Mabimoba\tools\python-fsb5');from fsb5 import vorbis as v
L=v.vorbis
# set funcs
L.vorbis_encode_init_vbr.argtypes=[ctypes.POINTER(v.VorbisInfo),ctypes.c_long,ctypes.c_int,ctypes.c_float];L.vorbis_encode_init_vbr.restype=ctypes.c_int
L.vorbis_analysis_init.argtypes=[ctypes.POINTER(v.VorbisDSPState),ctypes.POINTER(v.VorbisInfo)];L.vorbis_analysis_init.restype=ctypes.c_int
L.vorbis_analysis_headerout.argtypes=[ctypes.POINTER(v.VorbisDSPState),ctypes.POINTER(v.VorbisComment),ctypes.POINTER(v.OggPacket),ctypes.POINTER(v.OggPacket),ctypes.POINTER(v.OggPacket)];L.vorbis_analysis_headerout.restype=ctypes.c_int
from fsb5.vorbis_headers import lookup
for rate,ch,target in [(22050,1,0x6a5436bf),(44100,1,0x84199294)]:
 print('TARGET',rate,ch,hex(target),'targetlen',len(lookup[target]))
 for q in [i/10 for i in range(-10,101)]:
  vi=v.VorbisInfo(); r=L.vorbis_encode_init_vbr(ctypes.byref(vi),ch,rate,q)
  if r: continue
  vd=v.VorbisDSPState();L.vorbis_analysis_init(ctypes.byref(vd),ctypes.byref(vi)); vc=v.VorbisComment(); idp=v.OggPacket();cp=v.OggPacket();sp=v.OggPacket();L.vorbis_analysis_headerout(ctypes.byref(vd),ctypes.byref(vc),ctypes.byref(idp),ctypes.byref(cp),ctypes.byref(sp));
  b=bytes(sp.packet[:sp.bytes]);crc=zlib.crc32(b)&0xffffffff
  if q >= 0 and q <= 0.5 or crc==target:print(q,len(b),hex(crc), 'MATCH' if crc==target else '')
  # no clear to avoid issues
