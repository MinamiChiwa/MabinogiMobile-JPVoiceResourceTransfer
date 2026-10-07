import sys,json
p=r'D:\Mabimoba\release\MabinogiMobile_JPVoiceResourceTransfer_0.1.3';sys.path.insert(0,p);import transfer_jp_voice_resources as t
d=json.load(open(p+'/resource_cache.json',encoding='utf-8'));raw=open(p+'/resource_cache.bin','rb').read();x=next(x for x in d['clips'] if x['name']=='Vo_NPC_Amy_Brrrr_01');b=raw[x['offset']:x['offset']+x['size']];i=t.parse_fsb5(b);pcm,_,_=t.decode_fsb5_pcm(b)
for rate in [22050,16000,11025,8000]:
 r=t.run_ffmpeg(['-f','s16le','-ar',str(i['frequency']),'-ac','1','-i','pipe:0','-f','s16le','-ar',str(rate),'-ac','1','pipe:1'],pcm)
 print('rate',rate,len(r),[len(t.encode_fsb5_at_quality(r,1,rate,q,b)) for q in [1,0]])
