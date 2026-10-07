using LibCpp2IL;
using LibCpp2IL.BinaryStructures;
using LibCpp2IL.Metadata;

namespace MabinogiMobileVoiceMod.RegistrationFix.Bootstrap;

public static class RegistrationFixBootstrap
{
    private const ulong CodeRegistrationAddress = 0x18CCBE8D0;
    private const ulong MetadataRegistrationAddress = 0x18F577F70;
    private static bool initialized;

    public static void Initialize()
    {
        if (initialized)
            return;

        initialized = true;
        Il2CppBinary.OnRegistrationStructLocationFailure += OnRegistrationReadFailure;
    }

    private static void OnRegistrationReadFailure(
        Il2CppBinary binary,
        Il2CppMetadata metadata,
        ref Il2CppCodeRegistration? codeRegistration,
        ref Il2CppMetadataRegistration? metadataRegistration)
    {
        codeRegistration ??= binary.ReadReadableAtVirtualAddress<Il2CppCodeRegistration>(CodeRegistrationAddress);
        metadataRegistration ??= binary.ReadReadableAtVirtualAddress<Il2CppMetadataRegistration>(MetadataRegistrationAddress);
    }
}
