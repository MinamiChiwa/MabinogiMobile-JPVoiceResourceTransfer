# Mabinogi Mobile JP Voice Runtime

This is a BepInEx 6 IL2CPP plugin prototype for the 64-bit Windows client. It
does not replace or rewrite the TW resource packages. It intercepts Unity
`AudioSource` playback and redirects matching clip names to WAV files exported
from the JP client.

## Build

Install the BepInEx 6 IL2CPP Windows x64 pack into a disposable copy of the
game first. Do not install it into the live client until the loader has been
verified. Set the following MSBuild properties to the pack's `BepInEx/core`
directory and the Unity 2021.3 module reference directory:

```powershell
$bep = 'C:\path\to\BepInEx\core'
$unity = 'C:\path\to\UnityEngine\modules'
dotnet build .\MabinogiMobileVoiceMod.csproj -c Release `
  -p:BEPINEX_CORE=$bep -p:UNITY_MODULES=$unity
```

The Unity module reference DLLs can be obtained from the `UnityEngine.Modules`
2021.3 NuGet package. The first disposable launch generates game-specific
IL2CPP wrappers in `BepInEx/interop`. Rebuild against those exact wrappers
before using the plugin with the game:

```powershell
$interop = 'C:\path\to\game\BepInEx\interop'
dotnet build .\MabinogiMobileVoiceMod.csproj -c Release `
  -p:BEPINEX_CORE=$bep -p:BEPINEX_INTEROP=$interop
```

`BEPINEX_INTEROP` is optional only for an initial compile check. It should be
set for the final DLL so array parameters use the game's generated IL2CPP
signatures.

## Install layout

Keep the plugin and extracted data together:

```text
BepInEx/plugins/MabinogiMobileVoiceMod/
  MabinogiMobileVoiceMod.dll
  voice_manifest.json
  audio/*.wav
```

Copy the complete `dist/MabinogiMobileVoiceMod` directory from this project to
that plugin directory; keep the DLL, manifest, and `audio` folder together.
The plugin reads only these external files and never edits the TW `.blob`
packages. Keep a backup of the client and disable/remove the plugin before
online troubleshooting. The game's BlackCipher/launcher may reject third-party
DLL injection, which can only be confirmed with a disposable test copy.

The first time a clip is played, the plugin reads and decodes the external WAV
on the Unity thread, then caches the generated `AudioClip`. This may cause a
short hitch for a large line; later plays use the cached JP clip. BepInEx logs
each successful load and failed decode.
