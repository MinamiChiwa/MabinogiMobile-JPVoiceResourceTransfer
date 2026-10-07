from pathlib import Path
from fmod_toolkit import fmod
p=Path(r'D:\Mabimoba\diagnostic\full-length-feasibility-20261006\fsbank\opt-8k-q1.fsb')
b=p.read_bytes()
try:
 w=fmod.raw_to_wav(b,"wrapper",1,8000)
 print('ok', {k:len(v) for k,v in w.items()})
except Exception as e:
 import traceback;traceback.print_exc()


