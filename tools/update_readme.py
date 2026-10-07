from pathlib import Path
p=Path(r'D:\Mabimoba\release\MabinogiMobile_JPVoiceResourceTransfer_0.1.3\README.txt')
s=p.read_text(encoding='utf-8')
s=s.replace('   python -m pip install -r requirements.txt\n','   python -m pip install -r requirements.txt\n\n   本包已经附带 `ffmpeg.exe` 和 `vgmstream\\vgmstream-cli.exe`。它们用于把超出\n   台服原资源槽的 FSB5 Vorbis 语音解码、重采样并重新压缩；不需要另外安装\n   ffmpeg，也不会把 WAV/Ogg 直接写进游戏资源。\n')
s=s.replace('python transfer_jp_voice_resources.py --tw-root "台服游戏根目录" --cache-dir . --output-copy "新副本游戏根目录" --max-clips 0','python transfer_jp_voice_resources.py --tw-root "台服游戏根目录" --cache-dir . --output-copy "新副本游戏根目录" --max-clips 0 --preserve-structure')
marker='风险和限制\n----------\n'
section='''严格原位替换\n--------------\n\n`run_transfer.bat` 使用 `--preserve-structure`。每个替换后的 FSB5 资源仍写在\n原来的资源文件偏移，整个 UnityFS bundle 的长度、bundle 在 `.blob` 中的偏移、\n以及 `.blob` 总长度都会在写入后校验保持不变。资源小于原槽时尾部补零；资源\n超过原槽时，工具先尝试保持原采样率和声道数，再按高到低的 Vorbis 质量档重编码。\n仍放不下时会降低采样率、必要时转单声道，并只裁去尾部一小段，报告会记录\n`compression_quality`、`output_frequency` 和 `format_adjustment`。流程不会因为\n“超过槽位”直接跳过语音；只有 FSB5 结构损坏或磁盘/客户端文件无法读写时才会\n报错停止。\n\n随包的 vgmstream 用于解码包含非标准 Vorbis setup ID 的语音，避免 22.05 kHz\n等资源因解析表不全而被跳过。最终写入的仍是 FSB5 Vorbis，资源槽、bundle 和\n`.blob` 的物理布局不变。\n\n'''
s=s.replace(marker,section+marker)
p.write_text(s,encoding='utf-8')
