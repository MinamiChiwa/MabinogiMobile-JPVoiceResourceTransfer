param(
    [string]$Python = "python",
    [Parameter(Mandatory = $true)][string]$Output
)
$ErrorActionPreference = "Stop"
& $Python "$PSScriptRoot\build_portable.py" --output $Output
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
