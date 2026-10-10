# Windows x64 便携包构建

普通用户完整解压便携 ZIP 后双击 `run_transfer.bat` 或 `JPVoiceTransfer.exe`。不需要安装 Python、运行 pip 或联网。`install_dependencies.bat` 在便携包中只运行离线检查：Tk、UnityPy、FMOD 解码合成测试音频和多进程。客户端资源和日服音频不会包含在便携包中。已有合法取得的 `resource_cache.bin` / `resource_cache.json` 可放在 EXE 同目录复用；扫描缓存、日志同样保存在该目录，因此请解压到可写目录。

维护源码仅在 `tools/`。使用 Windows 上带 Tk 的 CPython 3.12 x64（构建验证为 3.12.0），在仓库外建立构建环境：

```powershell
python -m venv ..\portable-build\venv
..\portable-build\venv\Scripts\python.exe -m pip install --only-binary=:all: --no-binary=tpk_ar -r tools\requirements-build.lock
..\portable-build\venv\Scripts\python.exe tools\build_portable.py --output ..\portable-build
```

锁文件固定运行和构建依赖版本。tpk_ar 0.2.4 官方仅提供纯 Python 源码包，允许该包构建 wheel；原生依赖仅使用 wheel。需要离线复建时，先用已联网的构建环境执行 `pip wheel --wheel-dir wheelhouse -r tools/requirements-build.lock`，保存 wheelhouse 及每个文件 SHA-256；后续安装加 `--no-index --find-links wheelhouse`。不要上传客户端缓存或音频资源。

构建脚本执行 PyInstaller onedir，收集 UnityPy、FMOD、ASTC 和 CPU 配置数据及 Tk 运行库，排除非必要 numpy。随后复制 BAT、依赖锁和第三方许可证，生成 `BUILDINFO.json`（依赖版本与维护源码哈希）和 `CHECKSUMS.txt`，执行 EXE 自检及资源 worker 帮助检查。输出为所选构建目录 `dist/JPVoiceTransfer/`，必须整个目录分发。改变代码后需重新运行构建。

源码包 BAT 优先使用包内 `.venv`，其次验证 `python.exe`，最后验证 `py -3`，要求 Python 3.11+。源码 `install_dependencies.bat` 安装到工具目录 `.venv`，不会修改全局 Python；如 `wheelhouse/` 存在，则只从该目录离线安装。源码安装仍可能需要网络；便携用户不需要此步骤。

验证：在没有 Python 的 PATH、中文及空格目录下运行 `JPVoiceTransfer.exe --self-check` 和 `JPVoiceTransfer.exe --worker --help`；真实资源端到端验证需使用独立输出。`--self-check` 的音频为运行时生成的 128 帧测试信号，不读取客户端、不写入游戏目录。
