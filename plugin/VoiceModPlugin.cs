using System.Collections;
using System.Buffers.Binary;
using System.Reflection;
using System.Text;
using System.Text.Json;
using BepInEx;
using BepInEx.Unity.IL2CPP;
using HarmonyLib;
using UnityEngine;

namespace MabinogiMobileVoiceMod;

[BepInPlugin(PluginGuid, PluginName, PluginVersion)]
public sealed class VoiceModPlugin : BasePlugin
{
    public const string PluginGuid = "mabimoba.jp-voice-runtime";
    public const string PluginName = "Mabinogi Mobile JP Voice Runtime";
    public const string PluginVersion = "0.1.0";

    public override void Load()
    {
        var root = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location)
                   ?? Paths.PluginPath;

        VoiceRuntime.Initialize(root, Log.LogInfo, Log.LogWarning, Log.LogError);

        var harmony = new Harmony(PluginGuid);
        AudioSourcePatches.Install(harmony, Log.LogInfo, Log.LogWarning);
        Log.LogInfo("JP voice runtime loaded. Original resource packages remain untouched.");
    }
}

internal static class VoiceRuntime
{
    private static readonly Dictionary<string, string> Files = new(StringComparer.Ordinal);
    private static readonly Dictionary<string, AudioClip> Loaded = new(StringComparer.Ordinal);
    private static readonly HashSet<string> Pending = new(StringComparer.Ordinal);
    private static readonly HashSet<string> Missing = new(StringComparer.Ordinal);

    private static Action<string>? _info;
    private static Action<string>? _warning;
    private static Action<string>? _error;
    private static bool _enabled;

    public static void Initialize(
        string pluginDirectory,
        Action<string> info,
        Action<string> warning,
        Action<string> error)
    {
        _info = info;
        _warning = warning;
        _error = error;

        var manifestPath = Path.Combine(pluginDirectory, "voice_manifest.json");
        if (!File.Exists(manifestPath))
        {
            _warning?.Invoke($"Voice manifest not found: {manifestPath}");
            return;
        }

        try
        {
            using var document = JsonDocument.Parse(File.ReadAllText(manifestPath));
            if (!document.RootElement.TryGetProperty("clips", out var clips))
            {
                _warning?.Invoke("Voice manifest has no clips object.");
                return;
            }

            foreach (var clip in clips.EnumerateObject())
            {
                if (!clip.Value.TryGetProperty("file", out var file))
                    continue;

                var relative = file.GetString();
                if (string.IsNullOrWhiteSpace(relative))
                    continue;

                var absolute = Path.GetFullPath(Path.Combine(pluginDirectory, relative));
                Files[clip.Name] = absolute;
            }

            _enabled = Files.Count > 0;
            _info?.Invoke($"Loaded {Files.Count} JP voice mappings.");
        }
        catch (Exception ex)
        {
            _error?.Invoke($"Failed to read voice manifest: {ex}");
        }
    }

    public static AudioClip? TryGetReplacement(AudioClip? original)
    {
        if (!_enabled || original is null)
            return null;

        var name = original.name;
        if (string.IsNullOrWhiteSpace(name) || !Files.TryGetValue(name, out var path))
            return null;

        if (Loaded.TryGetValue(name, out var loaded))
            return loaded;

        if (Missing.Contains(name))
            return null;

        if (Pending.Add(name))
        {
            LoadClip(name, path);
        }

        return Loaded.TryGetValue(name, out loaded) ? loaded : null;
    }

    private static void LoadClip(string name, string path)
    {
        try
        {
            var bytes = File.ReadAllBytes(path);
            if (!TryDecodeWave(bytes, out var wave))
            {
                Pending.Remove(name);
                Missing.Add(name);
                _warning?.Invoke($"Unsupported WAV format for {name}");
                return;
            }

            var clip = AudioClip.Create(
                $"__JPVoice__{name}",
                wave.LengthSamples,
                wave.Channels,
                wave.Frequency,
                false);
            if (clip is null || !SetClipData(clip, wave.Samples))
            {
                Pending.Remove(name);
                Missing.Add(name);
                _warning?.Invoke($"Could not create AudioClip for {name}");
                return;
            }

            clip.hideFlags = HideFlags.DontUnloadUnusedAsset;
            Loaded[name] = clip;
            Pending.Remove(name);
            _info?.Invoke($"Loaded JP voice: {name}");
        }
        catch (Exception ex)
        {
            Pending.Remove(name);
            Missing.Add(name);
            _error?.Invoke($"Could not load {name}: {ex}");
        }
    }

    private static bool SetClipData(AudioClip clip, float[] samples)
    {
        // Unity's generated IL2CPP proxy may expose the array parameter as
        // float[] or as Il2CppStructArray<float>, depending on the interop
        // assemblies generated for the client. Resolve the instance method at
        // runtime so the plugin remains compatible with both forms.
        var method = typeof(AudioClip)
            .GetMethods(BindingFlags.Public | BindingFlags.Instance)
            .FirstOrDefault(candidate =>
                candidate.Name == "SetData" &&
                candidate.ReturnType == typeof(bool) &&
                candidate.GetParameters() is { Length: 2 } parameters &&
                parameters[1].ParameterType == typeof(int));
        if (method is null)
            return false;

        var parameterType = method.GetParameters()[0].ParameterType;
        object data = samples;
        if (!parameterType.IsInstanceOfType(samples))
        {
            var constructor = parameterType.GetConstructor(new[] { typeof(float[]) });
            if (constructor is null)
                return false;
            data = constructor.Invoke(new object[] { samples });
        }

        return method.Invoke(clip, new[] { data, (object)0 }) is true;
    }

    private readonly record struct WaveData(
        int Channels,
        int Frequency,
        int LengthSamples,
        float[] Samples);

    private static bool TryDecodeWave(byte[] bytes, out WaveData wave)
    {
        wave = default;
        if (bytes.Length < 12 || Encoding.ASCII.GetString(bytes, 0, 4) != "RIFF" ||
            Encoding.ASCII.GetString(bytes, 8, 4) != "WAVE")
            return false;

        ushort format = 0;
        ushort channels = 0;
        uint frequency = 0;
        ushort bitsPerSample = 0;
        int dataOffset = -1;
        int dataLength = 0;

        var cursor = 12;
        while (cursor + 8 <= bytes.Length)
        {
            var chunkId = Encoding.ASCII.GetString(bytes, cursor, 4);
            var chunkLength = BinaryPrimitives.ReadInt32LittleEndian(bytes.AsSpan(cursor + 4, 4));
            if (chunkLength < 0 || cursor + 8 + chunkLength > bytes.Length)
                return false;

            if (chunkId == "fmt " && chunkLength >= 16)
            {
                var chunk = bytes.AsSpan(cursor + 8, chunkLength);
                format = BinaryPrimitives.ReadUInt16LittleEndian(chunk);
                channels = BinaryPrimitives.ReadUInt16LittleEndian(chunk[2..]);
                frequency = BinaryPrimitives.ReadUInt32LittleEndian(chunk[4..]);
                bitsPerSample = BinaryPrimitives.ReadUInt16LittleEndian(chunk[14..]);
            }
            else if (chunkId == "data")
            {
                dataOffset = cursor + 8;
                dataLength = chunkLength;
            }

            cursor += 8 + chunkLength + (chunkLength & 1);
        }

        if (format is not (1 or 3) || channels == 0 || frequency == 0 ||
            dataOffset < 0 || dataLength <= 0 || bitsPerSample is not (8 or 16 or 24 or 32))
            return false;

        var bytesPerSample = bitsPerSample / 8;
        var frameSize = bytesPerSample * channels;
        if (frameSize <= 0 || dataLength % frameSize != 0)
            return false;

        var sampleCount = dataLength / frameSize;
        var samples = new float[sampleCount * channels];
        var source = bytes.AsSpan(dataOffset, dataLength);
        var target = 0;
        for (var i = 0; i < source.Length; i += bytesPerSample)
        {
            samples[target++] = format == 3
                ? BitConverter.Int32BitsToSingle(BinaryPrimitives.ReadInt32LittleEndian(source[i..]))
                : bitsPerSample switch
                {
                    8 => (source[i] - 128) / 128f,
                    16 => BinaryPrimitives.ReadInt16LittleEndian(source[i..]) / 32768f,
                    24 => Read24Bit(source[i..]) / 8388608f,
                    32 => BinaryPrimitives.ReadInt32LittleEndian(source[i..]) / 2147483648f,
                    _ => 0f,
                };
        }

        wave = new WaveData(channels, checked((int)frequency), sampleCount, samples);
        return true;
    }

    private static int Read24Bit(ReadOnlySpan<byte> bytes)
    {
        var value = bytes[0] | (bytes[1] << 8) | (bytes[2] << 16);
        return (value & 0x800000) != 0 ? value | unchecked((int)0xFF000000) : value;
    }
}

internal static class AudioSourcePatches
{
    public static void Install(Harmony harmony, Action<string> info, Action<string> warning)
    {
        Patch(harmony, "PlayOneShot", new[] { typeof(AudioClip), typeof(float) }, nameof(PrefixPlayOneShot), info, warning);
        Patch(harmony, "PlayOneShot", new[] { typeof(AudioClip) }, nameof(PrefixPlayOneShot), info, warning);
        Patch(harmony, "Play", Type.EmptyTypes, nameof(PrefixPlay), info, warning);
        Patch(harmony, "Play", new[] { typeof(double) }, nameof(PrefixPlay), info, warning);
        Patch(harmony, "PlayDelayed", new[] { typeof(float) }, nameof(PrefixPlay), info, warning);
        Patch(harmony, "PlayScheduled", new[] { typeof(double) }, nameof(PrefixPlay), info, warning);
        Patch(harmony, "PlayClipAtPoint", new[] { typeof(AudioClip), typeof(Vector3), typeof(float) }, nameof(PrefixPlayOneShot), info, warning);
    }

    private static void Patch(
        Harmony harmony,
        string methodName,
        Type[] argumentTypes,
        string prefixName,
        Action<string> info,
        Action<string> warning)
    {
        try
        {
            var target = AccessTools.Method(typeof(AudioSource), methodName, argumentTypes);
            if (target is null)
            {
                warning($"Unity method not found: AudioSource.{methodName}");
                return;
            }

            var prefix = AccessTools.Method(typeof(AudioSourcePatches), prefixName);
            harmony.Patch(target, prefix: new HarmonyMethod(prefix));
            info($"Patched AudioSource.{methodName}");
        }
        catch (Exception ex)
        {
            warning($"Could not patch AudioSource.{methodName}: {ex.Message}");
        }
    }

    private static void PrefixPlayOneShot(ref AudioClip clip)
    {
        var replacement = VoiceRuntime.TryGetReplacement(clip);
        if (replacement is not null)
            clip = replacement;
    }

    private static void PrefixPlay(AudioSource __instance)
    {
        var replacement = VoiceRuntime.TryGetReplacement(__instance.clip);
        if (replacement is not null)
            __instance.clip = replacement;
    }
}
