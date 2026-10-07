import os,sys,struct,ctypes,traceback,json
from pathlib import Path
os.chdir(r'D:\Mabimoba\tools\fsb5dll')
sys.path.insert(0,r'D:\Mabimoba\tools\python-fsb5')
from fsb5 import vorbis as v
from fsb5.vorbis_headers import lookup
# assign funcs
L=v.vorbis
L.vorbis_synthesis_headerin.argtypes=[ctypes.POINTER(v.VorbisInfo),ctypes.POINTER(v.VorbisComment),ctypes.POINTER(v.OggPacket)]
L.vorbis_synthesis_headerin.restype=ctypes.c_int
L.vorbis_analysis_init.argtypes=[ctypes.POINTER(v.VorbisDSPState),ctypes.POINTER(v.VorbisInfo)]; L.vorbis_analysis_init.restype=ctypes.c_int
L.vorbis_analysis_buffer.argtypes=[ctypes.POINTER(v.VorbisDSPState),ctypes.c_int];L.vorbis_analysis_buffer.restype=ctypes.POINTER(ctypes.POINTER(ctypes.c_float))
L.vorbis_analysis_wrote.argtypes=[ctypes.POINTER(v.VorbisDSPState),ctypes.c_int];L.vorbis_analysis_wrote.restype=ctypes.c_int
L.vorbis_block_init.argtypes=[ctypes.POINTER(v.VorbisDSPState),ctypes.c_void_p];L.vorbis_block_init.restype=ctypes.c_int
L.vorbis_analysis_blockout.argtypes=[ctypes.POINTER(v.VorbisDSPState),ctypes.c_void_p];L.vorbis_analysis_blockout.restype=ctypes.c_int
L.vorbis_analysis.argtypes=[ctypes.c_void_p,ctypes.POINTER(v.OggPacket)];L.vorbis_analysis.restype=ctypes.c_int
L.vorbis_bitrate_addblock.argtypes=[ctypes.c_void_p];L.vorbis_bitrate_addblock.restype=ctypes.c_int
L.vorbis_bitrate_flushpacket.argtypes=[ctypes.POINTER(v.VorbisDSPState),ctypes.POINTER(v.OggPacket)];L.vorbis_bitrate_flushpacket.restype=ctypes.c_int
L.vorbis_block_clear.argtypes=[ctypes.c_void_p];L.vorbis_block_clear.restype=ctypes.c_int
# initialize custom headers
name='Vo_NPC_Amy_Brrrr_01'
p=Path(r'D:\Mabimoba\release\MabinogiMobile_JPVoiceResourceTransfer_0.1.3'); d=json.loads((p/'resource_cache.json').read_text());raw=(p/'resource_cache.bin').read_bytes();x=next(x for x in d['clips'] if x['name']==name);b=raw[x['offset']:x['offset']+x['size']]
import importlib.util
spec=importlib.util.spec_from_file_location('t',p/'transfer_jp_voice_resources.py');t=importlib.util.module_from_spec(spec);sys.modules['t']=t;spec.loader.exec_module(t)
info2=t.parse_fsb5(b); pcm,ch,rate=t.decode_fsb5_pcm(b)
# make custom packets from lookup and helper
idp=v.rebuild_id_header(ch,rate,0x100,0x800)
comp=v.rebuild_comment_header(); setp=v.rebuild_setup_header(lookup[info2['crc']])
vi=v.VorbisInfo();vc=v.VorbisComment()
for label,pkt in [('id',idp),('com',comp),('set',setp)]:
 r=L.vorbis_synthesis_headerin(ctypes.byref(vi),ctypes.byref(vc),ctypes.byref(pkt)); print(label,'headerin',r)
print('vi',vi.channels,vi.rate,vi.codec_setup)
vd=v.VorbisDSPState(); print('analysis_init',L.vorbis_analysis_init(ctypes.byref(vd),ctypes.byref(vi)))
# block struct from header
class Oggpack(ctypes.Structure):
 _fields_=[('endbyte',ctypes.c_long),('endbit',ctypes.c_int),('buffer',ctypes.POINTER(ctypes.c_char)),('ptr',ctypes.POINTER(ctypes.c_char)),('storage',ctypes.c_long)]
class Block(ctypes.Structure):
 _fields_=[('pcm',ctypes.POINTER(ctypes.POINTER(ctypes.c_float))),('opb',Oggpack),('lW',ctypes.c_long),('W',ctypes.c_long),('nW',ctypes.c_long),('pcmend',ctypes.c_int),('mode',ctypes.c_int),('eofflag',ctypes.c_int),('granulepos',ctypes.c_longlong),('sequence',ctypes.c_longlong),('vd',ctypes.POINTER(v.VorbisDSPState)),('localstore',ctypes.c_void_p),('localtop',ctypes.c_long),('localalloc',ctypes.c_long),('totaluse',ctypes.c_long),('reap',ctypes.c_void_p),('glue_bits',ctypes.c_long),('time_bits',ctypes.c_long),('floor_bits',ctypes.c_long),('res_bits',ctypes.c_long),('internal',ctypes.c_void_p)]
block=Block(); print('blockinit',L.vorbis_block_init(ctypes.byref(vd),ctypes.byref(block)))
# feed 0.1 sec silence
frames=rate//10
ptr=L.vorbis_analysis_buffer(ctypes.byref(vd),frames); print('ptr',ptr)
for c in range(ch):
 for i in range(frames): ptr[c][i]=0.0
print('wrote',L.vorbis_analysis_wrote(ctypes.byref(vd),frames))
print('wrote eof',L.vorbis_analysis_wrote(ctypes.byref(vd),0))
pack=v.OggPacket(); outs=[]
for loop in range(100):
 r=L.vorbis_analysis_blockout(ctypes.byref(vd),ctypes.byref(block));
 if r==0: break
 print('blockout',r, 'W',block.W,'pcmend',block.pcmend,'eof',block.eofflag)
 print('analysis',L.vorbis_analysis(ctypes.byref(block),ctypes.byref(pack)))
 print('add',L.vorbis_bitrate_addblock(ctypes.byref(block)))
 while True:
  q=L.vorbis_bitrate_flushpacket(ctypes.byref(vd),ctypes.byref(pack));
  if not q:break
  outs.append(bytes(pack.packet[:pack.bytes]));print('packet',len(outs),pack.bytes,pack.e_o_s,pack.granulepos)
print('outs',len(outs),[len(x) for x in outs])
