# Mabinogi Mobile 日服语音资源转移工具

这是一个离线资源包重建工具：从用户选择的日服客户端读取 FSB5 语音，复制台服客户端到新的输出目录，再重建受影响的 UnityFS/.blob 资源包。原始日服和台服目录只读，官方 manifest 不会被修改。

当前维护版本是 **0.1.6.2**。Windows x64 便携包包含 Python、Tk 和依赖，完整解压后直接运行，无需安装 Python、pip 或联网。公开分发包不包含客户端语音；选择自己的日服目录，或将自己已有的 `resource_cache.bin` 和 `resource_cache.json` 放在程序旁边。

## 普通用户使用

1. 完整解压 `MabinogiMobile_JPVoiceResourceTransfer_0.1.6.2_portable_nojp.zip`，保留 `_internal` 目录。
2. 双击 `run_transfer.bat` 或 `JPVoiceTransfer.exe`。`install_dependencies.bat` 在便携包中只做离线运行环境检查。
3. 在窗口中选择日服客户端根目录、台服目录和空的输出目录。输出名称原样保留，已有空的 `MabinogiMobile_TW` 可以直接使用；台服来源应选择另一份纯净副本，例如 `MabinogiMobile_TW-back`。尚不存在的输出路径也支持。
4. 默认范围是 `Vo_NPC_Aranwen_`（阿蘭雯）语音；选择全部语音可进行全量替换。
5. 点击开始并等待报告完成。运行中可以点击停止；停止后的输出副本不能直接使用。
6. 等待扫描、复制、替换和校验、报告收尾各阶段完成。成功后进度固定100%并弹窗；取消或失败的副本不能作为完成品。
7. 查看输出目录 `appdata\patch-report.json` 和 `replacement-summary.txt`。报告的提交状态描述文件处理结果；来源音频相同的对象不能被计作有效的语言变化。

首次扫描日服全部 `.blob` 可能较慢，后续会复用工具目录中的扫描缓存。详细过程保存在 `run_transfer.log`。

## Flora 与语音覆盖

已对现有工具缓存和8254个台服语音对象做资源哈希审计。634个对象的缓存音频与纯净台服相同，其中包含Flora的 `OpenShop_01`、`Thank_01`、`Wow_01`、`Wow_02`。这四句在旧报告中显示“replaced”，但音频没有变化。现有WAV归档中的这四个文件也是同一段重复音频，不能用于补齐对应日语台词。

0.1.6.2会单独报告来源音频相同的对象，避免把写入成功误称为日语覆盖完成。哈希相同本身不判断语种；所有同类对象仍需核实来源内容。缺少可靠日语来源时，程序不会猜测或用其他角色、其他台词替代。详见 `docs/RELEASE_0.1.6.2.md`。

## 风险和资源说明

完整重建会改变受影响 `.blob` 的大小、偏移和哈希。启动器、完整性检查或反作弊系统可能修复、拒绝或阻止修改后的客户端，也可能出现地图加载失败、语音缺失、强制下线或账号风险。请使用可丢弃的客户端副本和测试账号，不要覆盖正在使用的客户端。

本工具及其资源处理流程仅供学习、研究和个人离线测试。使用者应自行确保拥有相关客户端和资源的使用权限。客户端语音资源不得上传、分享或再次分发；如使用了语音归档，请在下载后 24 小时内自觉删除。

## 开发者验证

唯一维护源码位于 `tools/`，测试直接引用该目录。`release/0.1.5_nojp` 属于历史发布快照；修改工具后应重新构建，避免手工维护多份入口。

```powershell
python -m unittest discover -s tests -p 'test_*.py'
python -m compileall -q tools tests
```

便携构建与依赖版本见 `tools/build_portable.py`、`tools/requirements-runtime.lock` 和 `tools/requirements-build.lock`。新环境验证仍应补充独立Windows虚拟机验收；在开发机器清空Python路径的测试只覆盖便携运行边界。

正式路线是完整 UnityFS/.blob 重建，不使用运行时注入，也不修改官方 manifest。`diagnostic/`、`dependencies/`、`external/` 和含日服资源的完整包不会提交到本仓库。
