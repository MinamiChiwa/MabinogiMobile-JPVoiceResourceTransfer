import sys,json,subprocess
p=r'D:\Mabimoba\release\MabinogiMobile_JPVoiceResourceTransfer_0.1.3';sys.path.insert(0,p);import transfer_jp_voice_resources as t
d=json.load(open(p+'/resource_cache.json',encoding='utf-8'));raw=open(p+'/resource_cache.bin','rb').read();x=next(x for x in d['clips'] if x['name']=='Vo_NPC_Amy_Brrrr_01');b=raw[x['offset']:x['offset']+x['size']];i=t.parse_fsb5(b);pcm,_,_=t.decode_fsb5_pcm(b);r=t.run_ffmpeg(['-f','s16le','-ar','22050','-ac','1','-i','pipe:0','-f','s16le','-ar','8000','-ac','1','pipe:1'],pcm)
for br in ['4k','6k','8k','10k','12k','16k']:
 e=t.run_ffmpeg(['-f','s16le','-ar','8000','-ac','1','-i','pipe:0','-c:a','libvorbis','-b:a',br,'-f','ogg','pipe:1'],r);ps=t.ogg_packets(e);c=b''.join(__import__('struct').pack('<H',len(z))+z for z in ps[3:]);print(br,len(c)+96)
