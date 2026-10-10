"""Codec boundaries and independent runtime checks for real FSB IMA output."""
import io
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import wave

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "tools"
sys.path.insert(0, str(ROOT / "tools"))
import ima_fsb_encoder as encoder

# Reference Xbox IMA decoder, independently specified by the public decoder
# format: predictor is output first, 63 low-first nibbles follow, nibble 64 is
# unused. It does not call any production encoder helper.
STEP = (
    7,8,9,10,11,12,13,14,16,17,19,21,23,25,28,31,34,37,41,45,50,55,
    60,66,73,80,88,97,107,118,130,143,157,173,190,209,230,253,279,307,
    337,371,408,449,494,544,598,658,724,796,876,963,1060,1166,1282,1411,
    1552,1707,1878,2066,2272,2499,2749,3024,3327,3660,4026,4428,4871,
    5358,5894,6484,7132,7845,8630,9493,10442,11487,12635,13899,15289,
    16818,18500,20350,22385,24623,27086,29794,32767,
)
INDEX = (-1,-1,-1,-1,2,4,6,8,-1,-1,-1,-1,2,4,6,8)


def reference_decode(bank):
    headers_size, names_size = struct.unpack_from("<II", bank, 12)
    frames = struct.unpack_from("<Q", bank, 60)[0] >> 34
    start = 60 + headers_size + names_size
    samples = []
    for offset in range(start, start + ((frames + 63) // 64) * 36, 36):
        predictor, index, reserved = struct.unpack_from("<hBB", bank, offset)
        if index > 88 or reserved:
            raise ValueError("Invalid IMA block header")
        samples.append(predictor)
        for nibble_index in range(63):
            byte = bank[offset + 4 + nibble_index // 2]
            code = (byte >> (4 * (nibble_index % 2))) & 15
            step = STEP[index]
            delta = step // 8
            if code & 1:
                delta += step // 4
            if code & 2:
                delta += step // 2
            if code & 4:
                delta += step
            predictor = max(-32768, min(32767, predictor + (-delta if code & 8 else delta)))
            index = max(0, min(88, index + INDEX[code]))
            samples.append(predictor)
    return struct.pack("<" + "h" * frames, *samples[:frames])


def waveform(frames):
    values = [round(12000 * math.sin(index * 0.17) + 3000 * math.cos(index * 0.31)) for index in range(frames)]
    return struct.pack("<" + "h" * frames, *values)


class ImaFsbEncoderTests(unittest.TestCase):
    def test_sizes_and_logical_frames_cross_block_boundaries(self):
        for frames in (1,2,63,64,65,127,128,1791,1792,1793):
            with self.subTest(frames=frames):
                pcm = waveform(frames)
                bank = encoder.encode_pcm(pcm, 1, 1460)
                self.assertEqual(len(bank), encoder.encoded_size(frames))
                self.assertEqual(struct.unpack_from("<I", bank, 24)[0], 7)
                self.assertEqual(struct.unpack_from("<Q", bank, 60)[0] >> 34, frames)
                self.assertEqual(struct.unpack_from("<I", bank, 72)[0], 1460)
                decoded = struct.unpack("<" + "h" * frames, reference_decode(bank))
                source = struct.unpack("<" + "h" * frames, pcm)
                # Each real block starts with an exact PCM predictor; this
                # checks the first frame on both sides of every block boundary.
                for frame in range(0, frames, 64):
                    self.assertEqual(decoded[frame], source[frame])
                self.assertEqual(len(decoded), frames)
                self.assertEqual(len(bank), 96 + struct.unpack_from("<I",bank,20)[0])

    def test_slot_limit_includes_container_and_data_alignment(self):
        self.assertEqual(encoder.encoded_size(1792), 1120)
        self.assertGreater(encoder.encoded_size(1793), 1120)
        self.assertEqual(encoder.encoded_size(6656), 3840)
        self.assertGreater(encoder.encoded_size(6657), 3872)

    def test_reference_fixed_nibbles_low_first_and_unused_tail(self):
        # Known fixture independent of encoding decisions. Step-index 0 and
        # nibble 4 produce 7 then 17; low nibble 4 then high nibble 0 produce
        # 7 then 8. This proves the reference is checking nibble orientation.
        bank = bytearray(encoder.encode_pcm(struct.pack("<hhh",0,0,0),1,8000))
        struct.pack_into("<hBB",bank,96,0,0,0)
        bank[100]=0x04
        self.assertEqual(struct.unpack("<hhh",reference_decode(bank)),(0,7,8))

    def test_quality_is_not_a_silence_substitute(self):
        source = waveform(256)
        decoded = reference_decode(encoder.encode_pcm(source,1,8000))
        a=struct.unpack("<256h",source);b=struct.unpack("<256h",decoded)
        rms_error=math.sqrt(sum((x-y)**2 for x,y in zip(a,b))/len(a))
        self.assertLess(rms_error,600)
        self.assertGreater(max(b)-min(b),20000)

    def test_invalid_pcm_and_unsupported_channels_fail_explicitly(self):
        for pcm,channels,frequency in ((b"",1,8000),(b"x",1,8000),(b"\0\0",2,8000),(b"\0\0",1,0),(b"\0\0",1,1.5)):
            with self.subTest(pcm=pcm,channels=channels,frequency=frequency):
                with self.assertRaises(ValueError):
                    encoder.encode_pcm(pcm,channels,frequency)
        for count in (-1,1<<30,0.5,True):
            with self.assertRaises(ValueError):
                encoder.encoded_size(count)

    @unittest.skipUnless((PACKAGE/"vgmstream"/"vgmstream-cli.exe").is_file(), "Bundled vgmstream is unavailable")
    def test_vgmstream_complete_decode_at_real_low_rates(self):
        executable=PACKAGE/"vgmstream"/"vgmstream-cli.exe"
        for frames,frequency in ((1,1460),(63,1460),(64,1460),(65,1460),(1792,1460),(6656,2030)):
            with self.subTest(frames=frames,frequency=frequency), tempfile.TemporaryDirectory() as temporary:
                fsb=Path(temporary)/"source.fsb";wav=Path(temporary)/"decoded.wav"
                bank=encoder.encode_pcm(waveform(frames),1,frequency);fsb.write_bytes(bank)
                completed=subprocess.run([str(executable),"-o",str(wav),str(fsb)],capture_output=True)
                self.assertEqual(completed.returncode,0,completed.stderr.decode(errors="replace"))
                with wave.open(str(wav),"rb") as output:
                    self.assertEqual((output.getnframes(),output.getnchannels(),output.getframerate()),(frames,1,frequency))
                    self.assertEqual(output.readframes(frames),reference_decode(bank))

    @unittest.skipUnless(
        os.environ.get("MABIMOBA_RUN_FMOD_TESTS") == "1"
        and (PACKAGE / "fsbank" / "fmod.dll").is_file(),
        "Optional native FMOD integration test (FSBank runtime not in final package)",
    )
    def test_native_fmod_complete_decode_matches_reference(self):
        script = '''
import io,json,os,sys,wave
os.environ['PYFMODEX_DLL_PATH']=sys.argv[1]
from fmod_toolkit import fmod
bank=sys.stdin.buffer.read()
raw=next(iter(fmod.raw_to_wav(bank,'integration',1,int(sys.argv[2])).values()))
with wave.open(io.BytesIO(raw),'rb') as wav:
    result={'frames':wav.getnframes(),'rate':wav.getframerate(),'channels':wav.getnchannels(),'pcm_hex':wav.readframes(wav.getnframes()).hex()}
print(json.dumps(result))
'''
        for frames,frequency in ((65,1460),(1792,1460),(6656,2030)):
            with self.subTest(frames=frames,frequency=frequency):
                bank=encoder.encode_pcm(waveform(frames),1,frequency)
                done=subprocess.run([sys.executable,"-c",script,str(PACKAGE/"fsbank"/"fmod.dll"),str(frequency)],input=bank,capture_output=True)
                self.assertEqual(done.returncode,0,done.stderr.decode(errors="replace"))
                output=json.loads(done.stdout)
                self.assertEqual((output['frames'],output['channels'],output['rate']),(frames,1,frequency))
                self.assertEqual(bytes.fromhex(output['pcm_hex']),reference_decode(bank))


if __name__=="__main__":
    unittest.main()
