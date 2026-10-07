import sys,json,traceback
p=r'D:\Mabimoba\release\MabinogiMobile_JPVoiceResourceTransfer_0.1.3';sys.path.insert(0,p);import transfer_jp_voice_resources as t
from fmod_toolkit import fmod
for name in ['Vo_NPC_Nao_Angry_01','Vo_NPC_Amy_Brrrr_01']:
 d=json.load(open(p+'/resource_cache.json',encoding='utf-8'));raw=open(p+'/resource_cache.bin','rb').read();x=next(x for x in d['clips'] if x['name']==name);b=raw[x['offset']:x['offset']+x['size']];i=t.parse_fsb5(b);pcm,_,_=t.decode_fsb5_pcm(b);trim=pcm[:len(pcm)//2//2*2];y=t.encode_fsb5_at_quality(trim,i['channels'],i['frequency'],0,b);print(name,len(y),hex(t.parse_fsb5(y)['crc']));
 try:w=fmod.raw_to_wav(y,name,i['channels'],i['frequency']);print('wav',len(w))
 except Exception:traceback.print_exc()


