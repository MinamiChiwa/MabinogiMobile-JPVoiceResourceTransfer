import ctypes
import os
from pathlib import Path

directory = Path(r'D:\Mabimoba\external\FSB5.Converter-research\FSBank\lib\x64')
os.add_dll_directory(str(directory))
library = ctypes.WinDLL(str(directory / 'fsbank.dll'))


class Subsound(ctypes.Structure):
    _fields_ = [
        ('fileNames', ctypes.POINTER(ctypes.c_char_p)),
        ('fileData', ctypes.POINTER(ctypes.c_void_p)),
        ('fileDataLengths', ctypes.POINTER(ctypes.c_uint)),
        ('numFiles', ctypes.c_uint),
        ('overrideFlags', ctypes.c_uint),
        ('overrideQuality', ctypes.c_uint),
        ('desiredSampleRate', ctypes.c_float),
        ('percentOptimizedRate', ctypes.c_float),
    ]


library.FSBank_Init.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_char_p]
library.FSBank_Init.restype = ctypes.c_int
library.FSBank_Build.argtypes = [
    ctypes.POINTER(Subsound), ctypes.c_uint, ctypes.c_int,
    ctypes.c_uint, ctypes.c_uint, ctypes.c_char_p, ctypes.c_char_p,
]
library.FSBank_Build.restype = ctypes.c_int
library.FSBank_Release.argtypes = []
library.FSBank_Release.restype = ctypes.c_int

wav = r'D:\Mabimoba\diagnostic\full-length-feasibility-20261006\fsbank\amy-11025.wav'
out = r'D:\Mabimoba\diagnostic\full-length-feasibility-20261006\fsbank\try-wrapper.fsb'
cache = r'D:\Mabimoba\.fsbcache-wrapper3'
Path(cache).mkdir(exist_ok=True)
for value in [wav, os.fsencode(wav), wav.replace('\\', '/')]:
    print('value', repr(value))
    names = (ctypes.c_char_p * 1)()
    names[0] = value.encode() if isinstance(value, str) else value
    subsound = Subsound()
    subsound.fileNames = names
    subsound.numFiles = 1
    subsound.overrideFlags = 0
    subsound.overrideQuality = 5
    print('size', ctypes.sizeof(Subsound), 'name', names[0])
    print('init', library.FSBank_Init(0, 0, 1, os.fsencode(cache)))
    print('build', library.FSBank_Build(ctypes.byref(subsound), 1, 3, 2, 5, None, os.fsencode(out)))
    print('release', library.FSBank_Release())
