# Mabinogi Mobile 日服语音资源转移工具

这是一个离线资源包重建工具：从用户选择的日服客户端读取 FSB5 语音，复制台服客户端到新的输出目录，再重建受影响的 UnityFS/.blob 资源包。原始日服和台服目录只读，官方 manifest 不会被修改。

当前发布版本是 **0.1.5-nojp**。为避免在 Git 仓库中分发客户端语音，本版本不包含日服语音缓存和 WAV 归档；运行时必须选择用户自己的日服客户端目录。

## 普通用户使用

1. 从 `release/MabinogiMobile_JPVoiceResourceTransfer_0.1.5_nojp.zip` 解压工具。
2. 双击 `run_transfer.bat`；需要 Python 3.11 或更新版本。
3. 在窗口中选择日服客户端根目录、台服目录和空的输出目录。
4. 默认范围是 `Vo_NPC_Aranwen_`（阿蘭雯）语音；选择全部语音可进行全量替换。
5. 点击开始并等待报告完成。运行中可以点击停止；停止后的输出副本不能直接使用。
6. 只有输出目录 `appdata\patch-report.json` 中的 `transaction_status` 为 `committed` 且 `failed` 为空时，才进行游戏测试。

首次扫描日服全部 `.blob` 可能较慢，后续会复用工具目录中的扫描缓存。详细过程保存在 `run_transfer.log`。

## 风险和资源说明

完整重建会改变受影响 `.blob` 的大小、偏移和哈希。启动器、完整性检查或反作弊系统可能修复、拒绝或阻止修改后的客户端，也可能出现地图加载失败、语音缺失、强制下线或账号风险。请使用可丢弃的客户端副本和测试账号，不要覆盖正在使用的客户端。

本工具及其资源处理流程仅供学习、研究和个人离线测试。使用者应自行确保拥有相关客户端和资源的使用权限。客户端语音资源不得上传、分享或再次分发；如使用了语音归档，请在下载后 24 小时内自觉删除。

## 开发者验证

```powershell
python -m unittest discover -s tests -p 'test_*.py'
```

正式路线是完整 UnityFS/.blob 重建，不使用运行时注入，也不修改官方 manifest。`diagnostic/`、`dependencies/`、`external/` 和含日服资源的完整包不会提交到本仓库。
