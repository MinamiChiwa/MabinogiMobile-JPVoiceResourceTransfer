import json,sys,struct,collections
sys.path.insert(0,r'D:\Mabimoba\release\MabinogiMobile_JPVoiceResourceTransfer_0.1.3');import transfer_jp_voice_resources as t
from pathlib import Path
p=Path(r'D:\Mabimoba\release\MabinogiMobile_JPVoiceResourceTransfer_0.1.3');d=json.loads((p/'resource_cache.json').read_text());raw=(p/'resource_cache.bin').read_bytes();z=collections.Counter()
for x in d['clips']:
 b=raw[x['offset']:x['offset']+x['size']]
 try:i=t.parse_fsb5(b);z[(i['frequency'],i['channels'],i['crc'])]+=1
 except:pass
for (r,c,crc),n in z.most_common():print(r,c,hex(crc),n)
