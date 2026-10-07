# Mabinogi Mobile 语音资源层检查

检查时间：2026-10-06  
检查方式：只读，不启动游戏，不修改两个客户端目录。

## 检查对象

- 港澳台服：`C:\Game\MabinogiMobile_TW\appdata`
- 日服：`D:\Game\MabinogiMJapan\MabinogiMobile_JP`

## 资源容器结果

两个客户端都使用三类固定目录：

- `AssetPackages`
- `PlatformResourcePackages`
- `ResourcesPackage`

目录内主要是编号 `.blob` 文件和一个 `index`。`.blob` 不是普通音频文件，而是自定义外层容器，里面串联了 UnityFS AssetBundle；AssetBundle 内部才包含 `AudioClip` 等资源。

| 目录 | 港澳台服 | 日服 |
|---|---:|---:|
| `AssetPackages` `.blob` 数量 | 722 | 725 |
| `AssetPackages` 数据量 | 12,876,836,591 B | 12,974,021,938 B |
| `AssetPackages/index` | 11,022,504 B | 11,125,196 B |
| `PlatformResourcePackages` `.blob` 数量 | 54 | 49 |
| `ResourcesPackage` `.blob` 数量 | 95 | 94 |
| `ResourcesPackage/index` | 3,346,901 B | 3,391,633 B |

港澳台服与日服的 `index` 和每个包的内容哈希均不同，因此不能通过“把日服同名 blob 放进港澳台服目录”实现安全覆盖。两边的包编号也不是同一映射。

## 校验与加载证据

港澳台服的 `59826-obd-manifest` 是 zlib 压缩的 JSON 清单。解压后包含 1,068 个文件记录，并对资源包、`GameAssembly.dll` 等文件记录对象哈希和大小。清单列出的资源路径只有固定目录下的编号包及 `Starter` 包，例如：

```text
AssetPackages\\0000.blob
AssetPackages\\index
AssetPackages\\Starter\\0000.blob
PlatformResourcePackages\\0000.blob
ResourcesPackage\\0000.blob
ResourcesPackage\\index
```

没有发现 `mods`、`override`、额外资源包目录或用户资源搜索路径。`global-metadata.dat` 和生成的 `MM.Client.Framework.dll` 中可以看到以下资源 API/字段：

- `FileSystemPlatformResourceAPI`
- `PackagePlatformResourceAPI`
- `PackageFileLocation`
- `AssetPackagesFileListUrl`
- `ResourcePackageFileListUrl`
- `PlatformResourcePackageFileListUrl`
- `AssetPackageOutputRootDir`
- `PlatformResourcePackageOutputDir`
- `AssetPackagesPatchRootUrl`
- `ResourcePackagePatchRootUrl`

这些接口描述的是官方补丁下载、文件清单和固定包路径，没有独立的 mod 覆盖层入口。

## 对可行方案的结论

### 原生额外资源包

当前版本没有可确认的入口。新增一个目录、一个 `.blob` 或一个自定义 `index` 不会自动参与资源查找；即使手工让它被读取，也需要同时修改包索引、资源路径映射和校验清单，已经不属于无侵入覆盖。

### 原生包内替换

技术上可以研究解包、替换 Unity `AudioClip`、重新压缩并重建外层索引，但这会改变被 `obd-manifest` 记录的对象和文件哈希。启动时的资源校验可能触发重新下载或修复，且反作弊/完整性检测风险高。它会直接影响客户端本地文件，不能承诺在线安全。

### 运行时替换

现有 BepInEx 方案可以在不改原始资源包的情况下替换已经加载的 `AudioSource`，但会加载 `winhttp.dll`、BepInEx、Harmony 和自定义 DLL。它仍然改变进程加载链和运行时行为，之前的强制下线无法排除与反作弊或会话校验相关，不能承诺在线安全稳定。

### 游戏外部音频层

如果目标是降低账号风险，唯一能明显避开客户端文件校验和进程注入的是游戏外部的本地音频提示/播放器。这种方式不能把语音真正替换成游戏内 `AudioClip`，但不会改资源包，也不需要 DLL 注入。

## 当前建议

不继续制作声称“在线安全”的原生资源替换包，也不建议把现有运行时注入包用于主账号。若继续实验，应使用可丢弃的客户端副本和测试账号；任何修改 `.blob`、`index` 或 `obd-manifest` 的版本都应视为会触发修复和反作弊风险的实验版本。

