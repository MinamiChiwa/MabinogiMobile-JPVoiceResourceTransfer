import os,sys,struct,ctypes
os.chdir(r'D:\Mabimoba\tools\fsb5dll');sys.path.insert(0,r'D:\Mabimoba\tools\python-fsb5')
from fsb5 import vorbis as v
L=v.vorbis
L.vorbis_encode_init_vbr.argtypes=[ctypes.POINTER(v.VorbisInfo),ctypes.c_long,ctypes.c_int,ctypes.c_float];L.vorbis_encode_init_vbr.restype=ctypes.c_int
L.vorbis_analysis_init.argtypes=[ctypes.POINTER(v.VorbisDSPState),ctypes.POINTER(v.VorbisInfo)];L.vorbis_analysis_init.restype=ctypes.c_int
L.vorbis_analysis_buffer.argtypes=[ctypes.POINTER(v.VorbisDSPState),ctypes.c_int];L.vorbis_analysis_buffer.restype=ctypes.POINTER(ctypes.POINTER(ctypes.c_float))
L.vorbis_analysis_wrote.argtypes=[ctypes.POINTER(v.VorbisDSPState),ctypes.c_int];L.vorbis_analysis_wrote.restype=ctypes.c_int
class Oggpack(ctypes.Structure):
 _fields_=[('endbyte',ctypes.c_long),('endbit',ctypes.c_int),('buffer',ctypes.POINTER(ctypes.c_char)),('ptr',ctypes.POINTER(ctypes.c_char)),('storage',ctypes.c_long)]
class Block(ctypes.Structure):
 _fields_=[('pcm',ctypes.POINTER(ctypes.POINTER(ctypes.c_float))),('opb',Oggpack),('lW',ctypes.c_long),('W',ctypes.c_long),('nW',ctypes.c_long),('pcmend',ctypes.c_int),('mode',ctypes.c_int),('eofflag',ctypes.c_int),('granulepos',ctypes.c_longlong),('sequence',ctypes.c_longlong),('vd',ctypes.POINTER(v.VorbisDSPState)),('localstore',ctypes.c_void_p),('localtop',ctypes.c_long),('localalloc',ctypes.c_long),('totaluse',ctypes.c_long),('reap',ctypes.c_void_p),('glue_bits',ctypes.c_long),('time_bits',ctypes.c_long),('floor_bits',ctypes.c_long),('res_bits',ctypes.c_long),('internal',ctypes.c_void_p)]
L.vorbis_block_init.argtypes=[ctypes.POINTER(v.VorbisDSPState),ctypes.POINTER(Block)];L.vorbis_block_init.restype=ctypes.c_int
L.vorbis_analysis_blockout.argtypes=[ctypes.POINTER(v.VorbisDSPState),ctypes.POINTER(Block)];L.vorbis_analysis_blockout.restype=ctypes.c_int
L.vorbis_analysis.argtypes=[ctypes.POINTER(Block),ctypes.POINTER(v.OggPacket)];L.vorbis_analysis.restype=ctypes.c_int
L.vorbis_bitrate_addblock.argtypes=[ctypes.POINTER(Block)];L.vorbis_bitrate_addblock.restype=ctypes.c_int
L.vorbis_bitrate_flushpacket.argtypes=[ctypes.POINTER(v.VorbisDSPState),ctypes.POINTER(v.OggPacket)];L.vorbis_bitrate_flushpacket.restype=ctypes.c_int
vi=v.VorbisInfo(); print('initvbr',L.vorbis_encode_init_vbr(ctypes.byref(vi),1,22050,0.0)); print('codec',vi.codec_setup)
vd=v.VorbisDSPState(); print('analysisinit',L.vorbis_analysis_init(ctypes.byref(vd),ctypes.byref(vi)))
bl=Block(); print('block',L.vorbis_block_init(ctypes.byref(vd),ctypes.byref(bl)))
p=L.vorbis_analysis_buffer(ctypes.byref(vd),2205)
for i in range(2205):p[0][i]=0
print('wrote',L.vorbis_analysis_wrote(ctypes.byref(vd),2205),L.vorbis_analysis_wrote(ctypes.byref(vd),0))
for z in range(10):
 q=L.vorbis_analysis_blockout(ctypes.byref(vd),ctypes.byref(bl));print('blockout',q,bl.pcmend)
 if q<=0:break
 print('analysis',L.vorbis_analysis(ctypes.byref(bl),ctypes.byref(v.OggPacket())));
