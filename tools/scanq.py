import os,sys,ctypes,zlib
os.chdir(r'D:\Mabimoba\tools\fsb5dll');sys.path.insert(0,r'D:\Mabimoba\tools\python-fsb5');from fsb5 import vorbis as v
from fsb5.vorbis_headers import lookup
L=v.vorbis;L.vorbis_encode_init_vbr.argtypes=[ctypes.POINTER(v.VorbisInfo),ctypes.c_long,ctypes.c_int,ctypes.c_float];L.vorbis_encode_init_vbr.restype=ctypes.c_int;L.vorbis_analysis_init.argtypes=[ctypes.POINTER(v.VorbisDSPState),ctypes.POINTER(v.VorbisInfo)];L.vorbis_analysis_init.restype=ctypes.c_int;L.vorbis_analysis_headerout.argtypes=[ctypes.POINTER(v.VorbisDSPState),ctypes.POINTER(v.VorbisComment),ctypes.POINTER(v.OggPacket),ctypes.POINTER(v.OggPacket),ctypes.POINTER(v.OggPacket)];L.vorbis_analysis_headerout.restype=ctypes.c_int
target=0x6a5436bf
for q in [i/1000 for i in range(-1000,1001)]:
 vi=v.VorbisInfo();
 if L.vorbis_encode_init_vbr(ctypes.byref(vi),1,22050,q):continue
 vd=v.VorbisDSPState();L.vorbis_analysis_init(ctypes.byref(vd),ctypes.byref(vi));vc=v.VorbisComment(); a=v.OggPacket();b=v.OggPacket();sp=v.OggPacket();L.vorbis_analysis_headerout(ctypes.byref(vd),ctypes.byref(vc),ctypes.byref(a),ctypes.byref(b),ctypes.byref(sp));x=bytes(sp.packet[:sp.bytes]); c=zlib.crc32(x)&0xffffffff
 if c==target:print('MATCH',q,len(x));break
 if q in (0,.1,.2,.3,.4,.45,.46,.47,.48,.49,.5):print(q,len(x),hex(c))
