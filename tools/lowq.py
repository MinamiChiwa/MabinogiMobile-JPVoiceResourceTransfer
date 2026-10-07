import sys,json
p=r'D:\Mabimoba\release\MabinogiMobile_JPVoiceResourceTransfer_0.1.3';sys.path.insert(0,p)
import transfer_jp_voice_resources as t
d=json.load(open(p+'/resource_cache.json',encoding='utf-8'));raw=open(p+'/resource_cache.bin','rb').read();x=next(x for x in d['clips'] if x['name']=='Vo_NPC_Amy_Brrrr_01');b=raw[x['offset']:x['offset']+x['size']];i=t.parse_fsb5(b);pcm,_,_=t.decode_fsb5_pcm(b)
for q in [-2,-1,-0.5,0,0.5,1]:
 try: print(q,len(t.encode_fsb5_at_quality(pcm,i['channels'],i['frequency'],q,b)))
 except Exception as e: print(q,e)
