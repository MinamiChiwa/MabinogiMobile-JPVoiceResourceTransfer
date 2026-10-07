import sys,json
p=r'D:\Mabimoba\release\MabinogiMobile_JPVoiceResourceTransfer_0.1.3';sys.path.insert(0,p);import transfer_jp_voice_resources as t
d=json.load(open(p+'/resource_cache.json',encoding='utf-8'));raw=open(p+'/resource_cache.bin','rb').read();x=next(x for x in d['clips'] if x['name']=='Vo_NPC_Amy_Brrrr_01');b=raw[x['offset']:x['offset']+x['size']];i=t.parse_fsb5(b);pcm,_,_=t.decode_fsb5_pcm(b);r=t.run_ffmpeg(['-f','s16le','-ar','22050','-ac','1','-i','pipe:0','-f','s16le','-ar','8000','-ac','1','pipe:1'],pcm)
for pct in [1,0.999,0.995,0.99,0.98,0.95,0.9]:
 rr=r[:int(len(r)*pct)//2*2];print(pct,len(rr),len(t.encode_fsb5_at_quality(rr,1,8000,0,b)))
