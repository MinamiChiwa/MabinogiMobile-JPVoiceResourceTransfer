import sys,json
p=r'D:\Mabimoba\release\MabinogiMobile_JPVoiceResourceTransfer_0.1.3';sys.path.insert(0,p);import transfer_jp_voice_resources as t
d=json.load(open(p+'/resource_cache.json',encoding='utf-8'));raw=open(p+'/resource_cache.bin','rb').read();x=next(x for x in d['clips'] if x['name']=='Vo_NPC_Amy_Brrrr_01');b=raw[x['offset']:x['offset']+x['size']];i=t.parse_fsb5(b);pcm,_,_=t.decode_fsb5_pcm(b);r=t.resample_pcm(pcm,1,22050,1,8000);tpl=t.retarget_fsb5_template(b,1,8000)
for q in [6,5,4,3,2,1,0]:
 for frac in [.95,.9,.85,.8,.75,.67,.5,.33,.25]:
  fr=2;rr=r[:max(fr,int(len(r)*frac)//fr*fr)];z=t.encode_fsb5_at_quality(rr,1,8000,q,tpl)
  if len(z)<=3968:print(q,frac,len(z));break

