import sys,json,time
p=r'D:\Mabimoba\release\MabinogiMobile_JPVoiceResourceTransfer_0.1.3';sys.path.insert(0,p);import transfer_jp_voice_resources as t
d=json.load(open(p+'/resource_cache.json',encoding='utf-8'));raw=open(p+'/resource_cache.bin','rb').read();x=next(x for x in d['clips'] if x['name']=='Vo_NPC_Amy_Brrrr_01');b=raw[x['offset']:x['offset']+x['size']];st=time.time();y=t.compress_fsb5_to_capacity(b,6016);print('result',None if y is None else (len(y[0]),y[1],y[2:]),'secs',time.time()-st); 
if y:print(t.parse_fsb5(y[0])['channels'],t.parse_fsb5(y[0])['frequency'],t.parse_fsb5(y[0])['sample_count'])

