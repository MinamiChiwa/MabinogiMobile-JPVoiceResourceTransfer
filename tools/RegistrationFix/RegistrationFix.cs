using BepInEx.Preloader.Core.Patching;
using LibCpp2IL;
using LibCpp2IL.BinaryStructures;
using LibCpp2IL.Metadata;

namespace MabinogiMobileVoiceMod.RegistrationFix;

[PatcherPluginInfo(
    "mabinogimobile.jpvoicemod.registrationfix",
    "Mabinogi Mobile JP Voice Mod registration fix",
    "0.1.7")]
public sealed class RegistrationFixPatcher : BasePatcher
{
    // These addresses are for the Mabinogi Mobile TW client build supplied by
    // the user (GameAssembly.dll SHA-256 is recorded in the package notes).
    private const ulong CodeRegistrationAddress = 0x18CCBE8D0;
    private const ulong MetadataRegistrationAddress = 0x18F577F70;

    public override void Initialize()
    {
        Il2CppBinary.OnRegistrationStructLocationFailure += OnRegistrationReadFailure;
        Log.LogInfo($"Registered fixed IL2CPP registration addresses: code=0x{CodeRegistrationAddress:X}, metadata=0x{MetadataRegistrationAddress:X}");
    }

    private void OnRegistrationReadFailure(
        Il2CppBinary binary,
        Il2CppMetadata metadata,
        ref Il2CppCodeRegistration? codeRegistration,
        ref Il2CppMetadataRegistration? metadataRegistration)
    {
        try
        {
            codeRegistration ??= binary.ReadReadableAtVirtualAddress<Il2CppCodeRegistration>(CodeRegistrationAddress);
            metadataRegistration ??= binary.ReadReadableAtVirtualAddress<Il2CppMetadataRegistration>(MetadataRegistrationAddress);
            Log.LogInfo($"Supplied fixed IL2CPP registrations for metadata v{metadata.MetadataVersion:0.0}");
        }
        catch (Exception ex)
        {
            Log.LogError($"Unable to read fixed IL2CPP registration addresses: {ex}");
        }
    }
}
