<#
.SYNOPSIS
    Install or upgrade the isc-ai CLI with uv, then apply the local AI-client setup.

.DESCRIPTION
    Dry run by default: prints the exact commands and changes nothing. -Apply runs them.

    Two sources, chosen automatically:
      * local  - this script sits in a clone (pyproject.toml next to it): editable install
                 of that clone, so a 'git pull' is all an upgrade needs.
      * remote - anywhere else: installs the release tag -Ref straight from GitLab with
                 the git credentials you already use (no clone, no API token).

    It never installs uv, Node or an AI client, never edits PATH or profiles and never
    contacts IRIS.

.EXAMPLE
    .\install.ps1                 # dry run from a clone
.EXAMPLE
    .\install.ps1 -Apply          # install this clone and run 'isc-ai setup --apply'
.EXAMPLE
    .\install.ps1 -Apply -Remote -Ref v0.5.1 -Transport https
.NOTES
    Maintainer: Felipe Angulo <felipe.angulo@intersystems.com>
    https://gitlab.iscinternal.com/isc-ai-squad/isc-ai
#>
param(
    [switch]$Apply,
    # Install from GitLab even when the script sits in a clone.
    [switch]$Remote,
    # Release tag (or branch) for a remote install.
    [string]$Ref = "",
    [ValidateSet("ssh", "https")]
    [string]$Transport = "ssh"
)

$ErrorActionPreference = "Stop"

# Default release for a remote install. Kept equal to isc_ai.__version__ by
# tests/test_distribution.py, so a release bumps both in the same commit.
$ISC_AI_REF = "v0.5.1"
$CoreUrls = @{
    ssh   = "git+ssh://git@gitlab.iscinternal.com/isc-ai-squad/isc-ai.git"
    https = "git+https://gitlab.iscinternal.com/isc-ai-squad/isc-ai.git"
}

# Extensions this installer knows how to probe and, if reachable, install.
# Baked in on purpose: no URL is ever discovered at runtime, only probed. Each one is
# probed and installed over the same transport as the core, so an SSH-only user is
# never sent to an HTTPS credential prompt.
$Extensions = @(
    @{
        Name  = "isc-ai-technical"
        ssh   = "ssh://git@gitlab.iscinternal.com/isc-ai-squad/isc-ai-technical.git"
        https = "https://gitlab.iscinternal.com/isc-ai-squad/isc-ai-technical.git"
    }
)
$ExtensionAccessUrl = "https://gitlab.iscinternal.com/groups/isc-ai-squad/technical-users"
# pre-first-release: set this to the first published tag once isc-ai-technical
# cuts one (MANUAL-2); until then it installs whatever HEAD of the default
# branch currently is, same behaviour as before this ref was pulled into a
# named, grep-able variable.
$ISC_AI_TECHNICAL_REF = "main"

$Uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $Uv) {
    throw "uv is required and was not found on PATH. Install it with: powershell -ExecutionPolicy ByPass -c `"irm https://astral.sh/uv/install.ps1 | iex`" and open a new terminal."
}

$Repo = $PSScriptRoot
$IsClone = $Repo -and (Test-Path (Join-Path $Repo "pyproject.toml"))
if ($IsClone -and -not $Remote) {
    $Source = @("--editable", $Repo)
    $SourceLabel = "local clone $Repo"
} else {
    if (-not $Ref) { $Ref = $ISC_AI_REF }
    $Source = @("$($CoreUrls[$Transport])@$Ref")
    $SourceLabel = "GitLab $Ref ($Transport)"
}

function Probe-Extension {
    # Returns one of: ok / no-ref / not-found / forbidden / auth-failed
    param([string]$Url, [string]$Ref)
    # GIT_TERMINAL_PROMPT=0 and GCM_INTERACTIVE=never: fail immediately instead
    # of hanging on a terminal prompt or a Git Credential Manager login window
    # when no cached git credentials are available.
    # The env vars are set/cleared around the call in a try/finally so a
    # terminating error from `git` itself (this script runs with
    # $ErrorActionPreference = "Stop") can never leave them set for every
    # later command in this process. Inside the probe, stderr is data: Windows
    # PowerShell turns any git warning into a terminating error under "Stop".
    $env:GIT_TERMINAL_PROMPT = "0"
    $env:GCM_INTERACTIVE = "never"
    $setSsh = -not $env:GIT_SSH_COMMAND -and -not $env:GIT_SSH
    if ($setSsh) { $env:GIT_SSH_COMMAND = "ssh -o BatchMode=yes" }
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = & git ls-remote $Url $Ref 2>&1 | ForEach-Object { "$_" }
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
        Remove-Item Env:\GIT_TERMINAL_PROMPT -ErrorAction SilentlyContinue
        Remove-Item Env:\GCM_INTERACTIVE -ErrorAction SilentlyContinue
        if ($setSsh) { Remove-Item Env:\GIT_SSH_COMMAND -ErrorAction SilentlyContinue }
    }
    if ($exitCode -eq 0) {
        # A reachable repository without that branch or tag (e.g. still empty) lists nothing:
        # installing it would fail the whole uv call, core included.
        if (@($output | Where-Object { $_ -match "^[0-9a-f]{40}\s" }).Count -gt 0) { return "ok" }
        return "no-ref"
    }
    $text = [string]::Join("`n", $output)
    if ($text -match "Authentication failed|could not read Username|terminal prompts disabled|401|Permission denied|Host key verification failed") {
        return "auth-failed"
    }
    if ($text -match "not found|Repository not found|404") {
        return "not-found"
    }
    return "forbidden"
}

function Report-Extensions {
    # Pre-flight extension probing, before anything is installed. Uses the same
    # git credentials as any other clone; never a separate API token. A 403/404
    # skips just that extension and keeps going: an unreachable extension repo
    # never fails the whole install. An authentication failure stops here,
    # loudly, with a login instruction -- it means the user's git credentials
    # cannot even be evaluated yet. Returns the --with arguments for uv.
    $with = @()
    foreach ($extension in $Extensions) {
        $url = $extension[$Transport]
        $result = Probe-Extension -Url $url -Ref $ISC_AI_TECHNICAL_REF
        switch ($result) {
            "ok" {
                Write-Host "  extension $($extension.Name): reachable, installed together with isc-ai"
                $with += @("--with", "git+$($url)@$ISC_AI_TECHNICAL_REF")
            }
            "no-ref" {
                Write-Warning "Skipping extension $($extension.Name): reachable, but '$ISC_AI_TECHNICAL_REF' is not published there yet."
            }
            "not-found" {
                Write-Warning "Skipping extension $($extension.Name): not found or no access (GitLab returns 404 for both). Request access: $ExtensionAccessUrl"
            }
            "forbidden" {
                Write-Warning "Skipping extension $($extension.Name): no access (forbidden). Request access: $ExtensionAccessUrl"
            }
            "auth-failed" {
                Write-Error "Cannot reach extension $($extension.Name): log in to GitLab first (run 'git ls-remote $url' once to authenticate), then re-run this installer." -ErrorAction Continue
                exit 1
            }
        }
    }
    return , $with
}

Write-Output "Source: $SourceLabel"
$With = Report-Extensions
# The extensions ride on the same source as the core: a bare 'isc-ai' would be
# resolved from the public package index, where that name is not ours.
$InstallArgs = @("tool", "install") + $Source + $With

if (-not $Apply) {
    Write-Output "Dry run:"
    Write-Output "  uv $($InstallArgs -join ' ')"
    Write-Output "  isc-ai setup --apply"
    Write-Output "Re-run with -Apply to execute these local commands."
    exit 0
}

& $Uv.Source @InstallArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# The fresh executable may not be on PATH in this session yet.
$BinDir = (& $Uv.Source tool dir --bin).Trim()
$IscAi = Join-Path $BinDir "isc-ai.exe"
if (-not (Test-Path $IscAi)) { $IscAi = "isc-ai" }
& $IscAi setup --apply
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if (-not (Get-Command isc-ai -ErrorAction SilentlyContinue)) {
    Write-Warning "$BinDir is not on PATH: run 'uv tool update-shell' and open a new terminal."
}
& $IscAi --version
exit $LASTEXITCODE
