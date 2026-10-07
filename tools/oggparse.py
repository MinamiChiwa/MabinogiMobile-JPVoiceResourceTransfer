import struct,zlib,glob

def ogg_packets(data):
    packets=[]; cur=bytearray(); pos=0
    while pos<len(data):
      if data[pos:pos+4]!=b'OggS': raise ValueError(pos)
      nseg=data[pos+26]; segs=data[pos+27:pos+27+nseg]; bodypos=pos+27+nseg; body=data[bodypos:bodypos+sum(segs)]; off=0
      for n in segs:
        cur += body[off:off+n]; off+=n
        if n<255: packets.append(bytes(cur)); cur.clear()
      pos=bodypos+len(body)
    return packets
for fn in glob.glob(r'C:\Users\aaa\AppData\Local\Temp\fcheck-*\q*.ogg'):
 p=ogg_packets(open(fn,'rb').read()); print(fn.split('\\')[-1], [len(x) for x in p[:3]], [hex(zlib.crc32(x)&0xffffffff) for x in p[:3]])
