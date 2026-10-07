using Mono.Cecil;
using Mono.Cecil.Cil;

if (args.Length != 3)
{
    Console.Error.WriteLine("usage: PatchCpp2IL <input> <helper> <output>");
    return 2;
}

var inputPath = Path.GetFullPath(args[0]);
var helperPath = Path.GetFullPath(args[1]);
var outputPath = Path.GetFullPath(args[2]);

var readerParameters = new ReaderParameters { ReadSymbols = false, InMemory = true };
using var core = AssemblyDefinition.ReadAssembly(inputPath, readerParameters);
using var helper = AssemblyDefinition.ReadAssembly(helperPath, readerParameters);

var apiType = core.MainModule.GetType("Cpp2IL.Core.Cpp2IlApi")
              ?? throw new InvalidOperationException("Cpp2IlApi type not found");
var target = apiType.Methods.FirstOrDefault(m =>
    m.Name == "InitializeLibCpp2Il" &&
    m.Parameters.Count == 4 &&
    m.Parameters[0].ParameterType.FullName == "System.String")
             ?? throw new InvalidOperationException("InitializeLibCpp2Il overload not found");

var helperType = helper.MainModule.GetType("MabinogiMobileVoiceMod.RegistrationFix.Bootstrap.RegistrationFixBootstrap")
                ?? throw new InvalidOperationException("Bootstrap type not found");
var helperMethod = helperType.Methods.FirstOrDefault(m => m.Name == "Initialize" && m.IsStatic && m.Parameters.Count == 0)
                   ?? throw new InvalidOperationException("Bootstrap Initialize method not found");

var importedCall = core.MainModule.ImportReference(helperMethod);
var il = target.Body.GetILProcessor();
il.InsertBefore(target.Body.Instructions[0], il.Create(OpCodes.Call, importedCall));

Directory.CreateDirectory(Path.GetDirectoryName(outputPath)!);
core.Write(outputPath);
Console.WriteLine($"Patched {target.FullName}");
Console.WriteLine($"Output: {outputPath}");
return 0;
