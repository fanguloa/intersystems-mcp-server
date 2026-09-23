param(
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
$Repo = $PSScriptRoot
$Uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $Uv) {
    throw "uv is required and was not found on PATH."
}

if (-not $Apply) {
    Write-Output "Dry run:"
    Write-Output "  uv tool install --editable `"$Repo`""
    Write-Output "  isc-ai setup --apply"
    Write-Output "Re-run with -Apply to execute these local commands."
    exit 0
}

& $Uv.Source tool install --editable $Repo
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& isc-ai setup --apply
exit $LASTEXITCODE
