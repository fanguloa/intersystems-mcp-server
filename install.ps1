param(
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
$Repo = $PSScriptRoot
$Uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $Uv) {
    throw "uv is required and was not found on PATH."
}

# Extensions this installer knows how to probe and, if reachable, install.
# Baked in on purpose: no URL is ever discovered at runtime, only probed.
$Extensions = @(
    @{ Name = "isc-ai-technical"; Url = "https://gitlab.iscinternal.com/isc-ai-squad/isc-ai-technical.git" }
)
$ExtensionAccessUrl = "https://gitlab.iscinternal.com/groups/isc-ai-squad/technical-users"
# pre-first-release: set this to the first published tag once isc-ai-technical
# cuts one (MANUAL-2); until then it installs whatever HEAD of the default
# branch currently is, same behaviour as before this ref was pulled into a
# named, grep-able variable.
$ISC_AI_TECHNICAL_REF = "main"

function Probe-Extension {
    # Returns one of: ok / not-found / forbidden / auth-failed
    param([string]$Url)
    # GIT_TERMINAL_PROMPT=0: fail immediately instead of hanging on a
    # credential prompt when no cached git credentials are available.
    # The env var is set/cleared around the call in a try/finally so a
    # terminating error from `git` itself (this script runs with
    # $ErrorActionPreference = "Stop") can never leave it set for every
    # later command in this process.
    $env:GIT_TERMINAL_PROMPT = "0"
    try {
        $output = & git ls-remote $Url HEAD 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        Remove-Item Env:\GIT_TERMINAL_PROMPT -ErrorAction SilentlyContinue
    }
    if ($exitCode -eq 0) { return "ok" }
    $text = [string]::Join("`n", $output)
    if ($text -match "Authentication failed|could not read Username|terminal prompts disabled|401") {
        return "auth-failed"
    }
    if ($text -match "not found|Repository not found|404") {
        return "not-found"
    }
    return "forbidden"
}

function Report-Extensions {
    # Pre-flight extension probing. Uses the same git credentials as any other
    # clone; never a separate API token. A 403/404 skips just that extension
    # and keeps going: an unreachable extension repo never fails the whole
    # install. An authentication failure stops here, loudly, with a login
    # instruction -- it means the user's git credentials cannot even be
    # evaluated yet.
    param([bool]$DoApply)
    foreach ($extension in $Extensions) {
        $result = Probe-Extension -Url $extension.Url
        switch ($result) {
            "ok" {
                if ($DoApply) {
                    Write-Output "Installing extension $($extension.Name)..."
                    & uv tool install --with "git+$($extension.Url)@$ISC_AI_TECHNICAL_REF" isc-ai
                } else {
                    Write-Output "  extension $($extension.Name): reachable, would install with -Apply"
                }
            }
            "not-found" {
                Write-Warning "Skipping extension $($extension.Name): not found or no access (GitLab returns 404 for both). Request access: $ExtensionAccessUrl"
            }
            "forbidden" {
                Write-Warning "Skipping extension $($extension.Name): no access (forbidden). Request access: $ExtensionAccessUrl"
            }
            "auth-failed" {
                Write-Error "Cannot reach extension $($extension.Name): log in to GitLab first (run 'git ls-remote $($extension.Url)' once to authenticate), then re-run this installer."
                exit 1
            }
        }
    }
}

if (-not $Apply) {
    Write-Output "Dry run:"
    Write-Output "  uv tool install --editable `"$Repo`""
    Write-Output "  isc-ai setup --apply"
    Write-Output "Re-run with -Apply to execute these local commands."
    Report-Extensions -DoApply $false
    exit 0
}

& $Uv.Source tool install --editable $Repo
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& isc-ai setup --apply
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Report-Extensions -DoApply $true
exit $LASTEXITCODE

