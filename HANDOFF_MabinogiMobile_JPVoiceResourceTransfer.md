# MabinogiMobile 日服语音原位替换项目交接文档

> 2026-10-07 更新：当前正式包为 0.1.4，以下旧文档含已废弃结论，当前事实优先见 `diagnostic/strict-nine-validation/RESULT.md`。用户现在允许子代理；允许音质妥协和仅裁无声部分，要求保留全部语音。上次失败 9 条已通过资源包级离线验证（5 写入 + 4 字节相同）；超小槽采用真实标准 IMA ADPCM，保守只裁首尾严格全零，保持 10ms 安全边距。旧的“FSBank只支持Vorbis”“多样本Nao”“无法压入”的结论不成立。未重新验证8254全量/游戏内播放。正式代码 `release/MabinogiMobile_JPVoiceResourceTransfer_0.1.4/transfer_jp_voice_resources.py`，源码tools已同步；增量包 `release/JPVoiceResourceTransfer_0.1.4_update.zip`。请不要对原客户端写入、不得将非可启动diagnostic fixture冒充游戏副本。

更新时间：2026-10-06
工作目录：`D:\Mabimoba`
用户要求：采用严格原位资源替换；超过台服原资源槽的日服语音必须重新压缩后写入，不能因为超槽位直接跳过；用户明确要求不使用子代理。

## 1. 当前目标和边界

项目是离线资源转换工具，不使用运行时注入，不修改官方 manifest，不把资源写到游戏目录外的临时覆盖层。目标是：

- 读取日服客户端或包内缓存的日服语音资源；
- 找到台服 `.blob` 中对应的 Unity AudioClip / 外部 FSB5 语音资源；
- 在台服原 `.blob` 文件中原位写入；
- 保持每个 UnityFS bundle 在 `.blob` 中的偏移不变；
- 保持 bundle 长度不变；
- 保持 `.blob` 总长度不变；
- 当日服 FSB5 大于台服目标槽位时，解码、重采样/转单声道/尾部裁剪，并重新压缩到原槽位内，不能静默跳过。

原客户端路径（用户机器）：

- 台服：`C:\Game\MabinogiMobile_TW\appdata`
- 日服：`D:\Game\MabinogiMJapan\MabinogiMobile_JP`

干净台服备份曾使用：`C:\Game\MabinogiMobile_TW-back`

## 2. 当前主代码和分发包

主脚本：

- `D:\Mabimoba\release\MabinogiMobile_JPVoiceResourceTransfer_0.1.3\transfer_jp_voice_resources.py`

分发包目录：

- `D:\Mabimoba\release\MabinogiMobile_JPVoiceResourceTransfer_0.1.3`

包中已有：

- `ffmpeg.exe`
- `vgmstream\vgmstream-cli.exe` 及其 DLL
- `transfer_jp_voice_resources.py`
- `resource_transfer_launcher.py`
- `run_transfer.bat`
- `README.txt`
- `resource_cache.bin` / `resource_cache.json`
- 包内 `audio` WAV 缓存

主脚本已经让 `run_transfer.bat` 自动带上 `--preserve-structure`。原位模式中的主要校验包括：

- `.blob` 长度保持不变；
- UnityFS bundle 的起始偏移保持不变；
- bundle 长度保持不变；
- 资源仍位于原资源槽；
- 非目标对象不能变化；
- 生成后的 FSB5 需要能由随包 vgmstream 解码。

关键函数位置（按当前脚本）：

- `find_ffmpeg()`：约第 104 行
- `find_vgmstream()`：约第 134 行
- `decode_fsb5_pcm()`：约第 172 行
- `parse_fsb5()`：约第 263 行
- `encode_fsb5_at_quality()`：约第 420 行
- `compress_fsb5_to_capacity()`：约第 453 行
- `replace_matches_preserve_structure()`：约第 1193 行

## 3. 已验证成功的部分

### 3.1 FSB5 / UnityFS 原位写入结构

单个 `Vo_NPC_Nao_Angry_01` 原位替换已验证：

- 生成结果仍为 FSB5；
- bundle 长度不变；
- bundle 在 `.blob` 中的偏移不变；
- `.blob` 长度不变；
- 资源数据写回原资源槽。

### 3.2 随包 vgmstream 解码

台服/日服资源中的 Vorbis setup ID 有些不是普通 Ogg 头。随包 `vgmstream-cli.exe` 能可靠解码这些原始 FSB5 资源，尤其是 22.05 kHz 和 FMOD 外部 setup ID 资源。

### 3.3 超槽位压缩实验

`Vo_NPC_Amy_Brrrr_01`：

- 台服目标槽容量：6016 字节；
- 日服资源：11776 字节；
- 当前实验生成结果：3751 字节；
- 实验使用 8 kHz、单声道、Vorbis quality 1；
- 原音约 3.216 秒，实验结果约 1.608 秒，发生尾部裁剪；
- FSB5 / bundle / `.blob` 结构均能保持。

注意：这只能证明“物理压缩和结构写入”可行，不能证明游戏 FMOD 一定能播放该重编码结果。

## 4. 当前阻塞问题：ffmpeg 直接重编码不可靠

当前 `encode_fsb5_at_quality()` 使用 ffmpeg/libvorbis 生成普通 Vorbis Ogg packet，然后写入 FSB5，保留模板的 FMOD setup ID，并清空旧 seek table。

已观察到：

- vgmstream 对部分重编码结果可以解码；
- FSB5 解析正常；
- 但 UnityPy/FMOD 对部分重编码结果会出现 `VERSION` 或 `OSError: exception: access violation`；
- 因此不能把当前 ffmpeg 输出直接当作最终可交付方案。

根因：游戏的 FSB5 Vorbis 使用 FMOD 外部 setup/codebook。仅把普通 libvorbis 的音频 packet 填入、再保留 FMOD setup ID，不足以保证 FMOD 编码/解码兼容。

不要恢复“保留旧 CRC + 任意 ffmpeg packet”的做法作为最终实现。vgmstream 能解码不等于 Unity/FMOD 能稳定播放。

## 5. 最新关键发现：已有 libvorbis 1.3.2 DLL 和 python-fsb5

工作区已有：

- `D:\Mabimoba\tools\fsb5dll\libogg.dll`
- `D:\Mabimoba\tools\fsb5dll\libvorbis.dll`
- `D:\Mabimoba\tools\fsb5dll\libvorbisenc.dll`

这些 DLL 来自 libvorbis 1.3.2 工程/构建。源码也在：

- `D:\Mabimoba\tools\libvorbis-1.3.2`
- VS 工程：`D:\Mabimoba\tools\libvorbis-1.3.2\win32\VS2010\`

已通过 raw GitHub 下载 python-fsb5 代码到：

- `D:\Mabimoba\tools\python-fsb5`

关键文件：

- `tools\python-fsb5\fsb5\vorbis.py`
- `tools\python-fsb5\fsb5\vorbis_headers.py`
- `tools\python-fsb5\fsb5\__init__.py`

`vorbis_headers.py` 包含 FMOD FSB5 setup header lookup，其中已确认包含：

- `0x84199294`
- `0x6a5436bf`
- 以及其他游戏资源使用的 setup ID。

## 6. setup ID 对照实验结果

从 `resource_cache.json` / `resource_cache.bin` 统计到的主要资源：

- 44100 Hz 单声道：`0x84199294`，6649 条
- 22050 Hz 单声道：`0x6a5436bf`，357 条
- 44100 Hz 双声道：`0x90021eee`，218 条
- 44100 Hz 双声道：`0xbec759ec`，5 条
- 44100 Hz 单声道：`0xd6e0bbd4`，4 条
- 48000 Hz 单声道：`0x910d8ea5`，4 条
- 22050 Hz 双声道：`0xd7913109`，4 条

用 `tools\fsb5dll` 中 libvorbis 1.3.2 生成 setup header，计算 setup packet 的 zlib CRC，得到：

- 44100 Hz 单声道 `0x84199294`：`vorbis_encode_init_vbr` quality 约 `0.10` / `0.11` 可完全匹配；setup 长度 3006。
- 44100 Hz 双声道 `0x90021eee`：quality 约 `0.47`–`0.49` 可匹配；setup 长度 4140。
- 44100 Hz 双声道 `0xbec759ec`：quality 约 `0.33`–`0.34` 可匹配。
- `0xd6e0bbd4`：标准 libvorbis 在部分 32000/44100 配置、较高 quality 可匹配。
- 48000 Hz 单声道 `0x910d8ea5`：quality 约 `0.10` 可匹配。
- 22050 Hz 单声道 `0x6a5436bf`：在 libvorbis 1.3.2 的 VBR quality -10.00 到 10.00（以及 bitrate 粗扫）中没有找到匹配；这类资源仍需要特殊处理/进一步确认。

这说明至少一部分 FMOD setup 其实对应 libvorbis 1.3.2 的标准编码配置；不能简单假设所有 setup 都是完全不同的编码器。

## 7. 最有希望的下一步路线

尝试用游戏实际 setup header 初始化 libvorbis 的 decoder-side `vorbis_info`，再补齐 encoder-side psychoacoustic/highlevel 状态，使同一套 FMOD codebook 能用于分析编码。

已做的最小实验：

1. `vorbis_synthesis_headerin()` 依次读入自制 ID header、comment header、FMOD setup header；
2. `vorbis_analysis_init()` 返回 0；
3. 进入 `vorbis_analysis()` 时出现 stack overflow。

解释：仅通过 `vorbis_synthesis_headerin()` 得到的是 decoder 配置，缺少 encoder 需要的 `psy_param` / `psy_g_param` / highlevel setup 等状态。`vorbis_analysis_init()` 的返回值 0 不能证明配置完整。

建议路线：

1. 创建一份标准 `vorbis_info`，调用 `vorbis_encode_init_vbr()` 得到完整 encoder psy/highlevel 配置；
2. 创建另一份 `vorbis_info`，用 FMOD setup header 调用 `vorbis_synthesis_headerin()` 得到 FMOD 的 modes/maps/floors/residues/books；
3. 从标准 encoder 的内部 `codec_setup_info` 拷贝 encoder-only 字段：
   - `psys`
   - `psy_param[]`
   - `psy_g_param`
   - `bi`
   - 必要时 `hi`
4. 保留 FMOD setup 的：
   - blocksizes
   - modes/maps
   - floors/residues
   - `book_param[]`
5. 将 `fullbooks` 置空，让 `vorbis_analysis_init()` 按 FMOD `book_param[]` 重新执行 `vorbis_book_init_encode()`；
6. 用 `vorbis_analysis_buffer()` / `vorbis_analysis_wrote()` / `vorbis_analysis_blockout()` / `vorbis_analysis()` / `vorbis_bitrate_addblock()` / `vorbis_bitrate_flushpacket()` 生成音频 packet；
7. 把生成的音频 packet（不含 Ogg 三个头）写回 FSB5 的 16 位 packet 长度表；
8. 用 vgmstream、UnityPy，并最终用实际游戏验证。

libvorbis 内部结构定义可参考：

- `tools\libvorbis-1.3.2\lib\codec_internal.h`
- `tools\libvorbis-1.3.2\lib\psy.h`
- `tools\libvorbis-1.3.2\lib\bitrate.h`
- `tools\libvorbis-1.3.2\lib\highlevel.h`
- `tools\libvorbis-1.3.2\include\vorbis\codec.h`

关键结构：`codec_setup_info`。Windows 下 `long` 是 4 字节，ctypes 结构定义要按 Windows ABI；指针是 8 字节。

## 8. 实验脚本和可复用命令

实验脚本位于：

- `D:\Mabimoba\tools\test_custom_encode.py`：尝试直接以 FMOD setup 做 analysis，最终在 `vorbis_analysis()` 触发 stack overflow。
- `D:\Mabimoba\tools\test_standard_encode.py`：标准 libvorbis 1.3.2 encoder 生成 packet，验证 ctypes 结构和普通 encoder 流程。
- `D:\Mabimoba\tools\test_headers.py`
- `D:\Mabimoba\tools\test_headers2.py`
- `D:\Mabimoba\tools\scanq.py`
- `D:\Mabimoba\tools\scanbit.py`
- `D:\Mabimoba\tools\scantargets.py`
- `D:\Mabimoba\tools\crcset.py`

运行 Python 时，若使用 `tools\python-fsb5`，必须把当前工作目录设为 `D:\Mabimoba\tools\fsb5dll`，这样 `load_lib()` 能找到三枚 DLL，例如：

```powershell
Set-Location D:\Mabimoba\tools\fsb5dll
$env:PYTHONPATH = 'D:\Mabimoba\tools\python-fsb5'
python D:\Mabimoba\tools\test_standard_encode.py
```

分发包的原位转换入口：

```powershell
python D:\Mabimoba\release\MabinogiMobile_JPVoiceResourceTransfer_0.1.3\transfer_jp_voice_resources.py --help
```

## 9. 当前实现不能承诺的事项

在兼容 encoder 完成并用实际客户端确认前，不应对用户承诺：

- 所有超槽位语音都能被游戏播放；
- vgmstream 能解码就代表 Unity/FMOD 一定可用；
- 所有 22050 Hz `0x6a5436bf` 资源都能由普通 ffmpeg 直接重编码；
- 原位资源改写不会触发客户端完整性/反作弊策略。当前工具只是离线修改本地资源，不能提供官方兼容或反作弊保证。

## 10. 交接时优先检查

接手后先做以下检查：

1. 打开 `transfer_jp_voice_resources.py`，确认没有把普通 ffmpeg 输出直接当作最终 FMOD 编码器；
2. 查看 `tools\fsb5dll` DLL 的架构和加载是否正常；
3. 先完成一个 `Vo_NPC_Amy_Brrrr_01`（22050 Hz、`0x6a5436bf`）和一个 `Vo_NPC_Nao_Angry_01`（44100 Hz、`0x84199294`）的自定义 setup 编码测试；
4. 用 vgmstream 检查 FSB5，再用 UnityPy 检查资源读取；
5. 只在这两类资源都通过后，才修改压缩策略和 README 的最终承诺；
6. 最终必须在干净台服副本上做单条资源、超槽位资源、多个 bundle 和整包的逐级测试。

## 11. 相关目录

- 主分发包：`D:\Mabimoba\release\MabinogiMobile_JPVoiceResourceTransfer_0.1.3`
- 原位测试 Amy：`D:\Mabimoba\test-preserve-Amy9`
- 原位测试 Nao：`D:\Mabimoba\test-preserve-Nao2`
- Amy 报告：`D:\Mabimoba\reports-preserve-Amy9`
- Nao 报告：`D:\Mabimoba\reports-preserve-Nao2`
- 干净台服备份：`C:\Game\MabinogiMobile_TW-back`

