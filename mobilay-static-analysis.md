# mobilay 插件包只读静态分析

分析对象：

`C:\Users\aaa\Desktop\mobilay-不要查我DPS插件（怕别用用别怕）`

本次只读取文件、计算哈希、解析 PE 导入/导出和字符串，没有运行程序、启动游戏、写入游戏目录或尝试注入进程。

## 文件和签名

| 文件 | 大小 | SHA-256 | Authenticode |
| --- | ---: | --- | --- |
| `Lay.exe` | 9,216 B | `C9CB240E61C849BC967F88EDCF6026CA619C0D233E59257A129369BF95F6BC9A` | 未签名 |
| `r18bb4abfe2044835844c1184dfc9dc9a.exe` | 308,912,607 B | `34D0FD93701121077AE938140E0B9EB4446AB37C1A0D35CE0DD2399F83BC7D29` | 未签名 |
| `Resources\LayToggler\LayToggler.addon64` | 62,976 B | `5ECCB946E515C87BA3F61BB260C98BB194EE7E9C9F52770E961D40DFE3CB7272` | 未签名 |
| `DirectWriteForwarder.dll` | 481,544 B | `6C3F31CE627C2B89EF9B9A2C14D6298F0803A2F912563CACA27134D5510A2DE0` | Microsoft `.NET` 签名有效 |
| `Resources\dxvk\v2.7.1\d3d11.dll` | 4,452,366 B | `523DA2CD765DD51D99FE1644B24F9EE7EE6247E4C98953BCC449CE84CD303405` | 未签名 |
| `Resources\dxvk\v2.7.1\dxgi.dll` | 2,953,230 B | `EC02EB37620FF52361CB45376A4611FC4210D96E71D0363F1CC9807F151C01BE` | 未签名 |

## `LayToggler.addon64`

- PE 导入包含 `D3DCompile`、`GetForegroundWindow`、`GetAncestor`、`K32EnumProcessModules` 和命名管道 API。
- 导出名为 `DESCRIPTION`、`NAME`，字符串明确包含 `ReShadeRegisterAddon`、`ReShadeUnregisterAddon`、`ReShadeRegisterEvent`、`ReShadeUnregisterEvent`。
- 创建的管道名为 `\\.\pipe\LayToggler.%lu`。
- 内置的是 D3D11 顶点/像素着色器，使用 `BeforeTexture` 复制渲染纹理。
- 未发现 `OpenProcess`、`WriteProcessMemory`、`VirtualAllocEx`、`CreateRemoteThread`、`LoadLibrary`、`UnityPlayer`、`GameAssembly`、`Mabinogi`、`AudioSource` 或 `AudioClip` 字符串/导入。

因此它更像 ReShade 的图形层 Add-on：由 ReShade 进入游戏进程，在渲染事件中绘制或管理覆盖层，再通过本地管道和外部主程序通信。它不是一个可直接用于 Unity 音频替换的插件。

## `Lay.exe`

这是一个 .NET 启动/更新器。元数据包含 `Process.GetProcessesByName`、`ProcessModule`、`ProcessStartInfo`、`WaitForExit`、`lay-state.json`、`lay-pending.json`、`lay-update.lock` 和 SHA-256 校验流程；清单要求管理员权限。它的 PE 导入表为空（托管程序），目前没有看到远程内存写入 API。`lay-state.json` 指向主程序并保存同一个 SHA-256，说明启动器主要管理自身主程序版本，而不是修改游戏资源包。

## 主程序 `r18...exe`

可见的应用/依赖元数据包括：

- `SharpPcap`、`PacketDotNet`、`WinDivert`、`wpcap`、`PacketCapture`、`StartCapture`、`StopCapture`、`ParsePacket`、源/目标端口字段。
- `NamedPipeClientStream`、`ClientWebSocket` 和配置中的 `TargetPort: 16000`、WebSocket relay。
- `NAudio`、`AudioFileReader`、`MediaPlayer`。这些只能证明主程序能够播放或处理自身音频，不能证明它能改写游戏的 Unity `AudioClip`。
- `D3D11CreateDevice`、`IDXGISwapChain`、`OMSetRenderTargets`、`ClearRenderTargetView` 等覆盖层/捕获相关 API。
- `ReShade`、`DXVK`、`LayToggler.addon64` 路径，以及 `reshade_guide_*`、`aux_optimization_install_addon`、`aux_optimization_remove_reshade`、`aux_optimization_dxvk_auto_install` 等安装/移除引导字符串。

主程序中出现的 `WriteProcessMemory`、`ReadProcessMemory`、`SetWindowsHookEx` 等名字位于 .NET/Windows Desktop 互操作元数据或通用依赖字符串中，PE 导入表没有对应的直接导入；不能据此认定主程序正在对游戏做远程内存注入。相反，当前证据更支持“外部封包采集 + ReShade/DXVK 图形叠加 + 管道/WebSocket 通信”的组合。

## 对语音替换方案的意义

这个包可参考的是分层结构：外部控制程序、可卸载的图形 Add-on、本地 IPC、版本/哈希状态文件，以及把用户数据放在工具目录中。ReShade Add-on 的方式仍然会让第三方 DLL 进入游戏进程，并不能给反作弊提供豁免。

它不能直接作为语音替换实现，因为 ReShade 事件面向 D3D11/DXGI 渲染，不提供 Unity 音频资源或 `AudioSource` 重定向。要真正替换游戏内语音，仍需资源包改写、Unity/音频后端的进程内 Hook，或游戏外播放；前两种都比这个图形 Add-on 更接近反作弊敏感行为。

另外，主程序包含封包采集/WinDivert 相关能力。该能力会改变网络观测或转发面，不能把它当成“安全稳定”的依据；即使社区长期使用，也不能排除版本更新、区域服策略或账号会话校验导致的断线/下线。

## 限制

静态分析无法证明程序运行时最终写入了哪些路径、加载了哪些 DLL、是否启用了某条可选功能，也无法证明 Nexon/Nexon Games 的反作弊判定规则。要确认写文件和加载链，需要在隔离的测试环境做运行时文件/模块跟踪；本次没有进行该操作。
