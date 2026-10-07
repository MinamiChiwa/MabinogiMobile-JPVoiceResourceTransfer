import sys
sys.path.insert(0,r"D:\Mabimoba\release\MabinogiMobile_JPVoiceResourceTransfer_0.1.3")
import transfer_jp_voice_resources as t
from pathlib import Path
from fmod_toolkit import fmod
for p in Path(r"D:\Mabimoba\diagnostic\full-length-feasibility-20261006\fsbank").glob('*.fsb'):
 try:
  b=p.read_bytes(); info=t.parse_fsb5(b); pcm,ch,fr=t.decode_fsb5_pcm(b)
  ok=''
  try:
   fmod.raw_to_wav(b,'sample',ch,fr); ok='FMOD'
  except Exception as e: ok='FMODERR:'+str(e)[:50]
  print(p.name,len(b),hex(info['crc']),ch,fr,info['sample_count'],len(pcm)//(2*ch),ok)
 except Exception as e: print(p.name,'ERR',str(e)[:120])
