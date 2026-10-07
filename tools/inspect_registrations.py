import struct
import sys

with open(sys.argv[1], 'rb') as f:
    data = f.read()
pe = struct.unpack_from('<I', data, 0x3c)[0]
num_sections = struct.unpack_from('<H', data, pe + 6)[0]
opt_size = struct.unpack_from('<H', data, pe + 20)[0]
opt = pe + 24
image_base = struct.unpack_from('<Q', data, opt + 24)[0]
sec_start = opt + opt_size
sections = []
for i in range(num_sections):
    off = sec_start + i * 40
    name = data[off:off+8].rstrip(b'\0').decode('ascii', errors='replace')
    vs, va, rs, ro = struct.unpack_from('<IIII', data, off+8)
    sections.append((name, va, vs, ro, rs))
print('Image base', hex(image_base))
print('Sections', sections)

def raw(addr):
    rva = addr-image_base
    for name, va, vs, ro, rs in sections:
        if va <= rva < va + rs:
            return ro + rva-va
    raise ValueError(f'Not mapped: {addr:X}')

for arg in sys.argv[2:]:
    addr = int(arg, 16)
    offset = raw(addr)
    print(f'VA {addr:X}, file offset {offset:X}')
    for i in range(24):
        v = struct.unpack_from('<Q', data, offset + i*8)[0]
        print(f'{addr+i*8:016X}: {v:016X} ({v})')
