# isc-ai workstation bootstrap for Windows.
#
#   irm <pages-url>/install.ps1 | iex
#   & ([scriptblock]::Create((irm <pages-url>/install.ps1))) -Yes -SkipWizard
#   .\bootstrap\install.ps1 -DryRun
#
# Installs everything an isc-ai workstation needs, skipping whatever is already there:
# git, uv, Node.js LTS, the Perforce command-line client, engram, the isc-ai CLI (and its
# extensions), the Perforce MCP server, the AI clients, the recommended MCP suite in every
# client, and then opens 'isc-ai wizard' for the IRIS environments and their credentials.
#
# Safe to run again: every step checks first and only acts on what is missing.
# It never stores a credential, never contacts IRIS and never edits a project.
# The file is ASCII on purpose: it is fetched and run with 'irm | iex', where a byte order
# mark or a non-ASCII character would depend on how the web server labels the charset.

function Install-IscAi {
    [CmdletBinding()]
    param(
        # Release tag of isc-ai to install.
        [string]$Ref = "",
        [ValidateSet("ssh", "https")]
        [string]$Transport = "ssh",
        # Install isc-ai from a local clone instead of GitLab (offline installs and tests).
        [string]$From = "",
        # Install the Perforce MCP server from a local clone instead of GitLab.
        [string]$PerforceFrom = "",
        # Print what would be done and change nothing.
        [switch]$DryRun,
        # Do not ask for confirmations (AI clients are installed without asking).
        [switch]$Yes,
        [switch]$SkipPerforce,
        [switch]$SkipEngram,
        [switch]$SkipClients,
        [switch]$SkipSuite,
        [switch]$SkipWizard
    )

    $ErrorActionPreference = "Stop"
    # Kept equal to isc_ai.__version__ by tests/test_distribution.py.
    $ISC_AI_REF = "v0.4.1"
    $GitLabHost = "gitlab.iscinternal.com"
    $CoreRepo = @{
        ssh   = "git@gitlab.iscinternal.com:isc-ai-squad/isc-ai.git"
        https = "https://gitlab.iscinternal.com/isc-ai-squad/isc-ai.git"
    }
    $PerforceMcpRepo = @{
        ssh   = "ssh://git@gitlab.iscinternal.com/isc-ai-squad/perforce-mcp-server.git"
        https = "https://gitlab.iscinternal.com/isc-ai-squad/perforce-mcp-server.git"
    }
    # The Perforce MCP server has no release tag yet: it follows its default branch.
    $PerforceMcpRef = "master"
    $EngramVersion = "3.0.0"
    $SshKeysPage = "https://gitlab.iscinternal.com/-/user_settings/ssh_keys"
    # Tests and sandboxes set this so no installer touches the persistent user PATH.
    $NoPersist = [bool]$env:ISC_AI_BOOTSTRAP_NO_PERSIST
    if (-not $Ref) { $Ref = $ISC_AI_REF }
    $Interactive = [Environment]::UserInteractive -and -not $Yes -and -not [Console]::IsInputRedirected

    $script:Results = New-Object System.Collections.Generic.List[object]
    $script:RegistryPathAtStart = Get-RegistryPath

    Write-Host ""
    Write-Host "isc-ai bootstrap  (isc-ai $Ref, transport $Transport$(if ($DryRun) { ', DRY RUN' }))" -ForegroundColor Cyan
    Write-Host "Every step checks first; nothing that is already installed is touched." -ForegroundColor DarkGray

    # ------------------------------------------------------------------ prerequisites
    Step "Package manager (winget)"
    $HasWinget = [bool](Get-Command winget -ErrorAction SilentlyContinue)
    if ($HasWinget) { Done "winget" "available" }
    else { Fail "winget" "missing: install 'App Installer' from the Microsoft Store, then run this again" }

    Ensure-Winget "git" "Git.Git" "git"
    Ensure-Uv
    Ensure-Winget "node" "OpenJS.NodeJS.LTS" "Node.js LTS"
    if (-not $SkipPerforce) { Ensure-Winget "p4" "Perforce.P4V" "Perforce command-line client (P4 Apps)" }
    if (-not $SkipEngram) { Ensure-Engram $EngramVersion }

    if (-not (Get-Command git -ErrorAction SilentlyContinue) -or -not (Get-Command uv -ErrorAction SilentlyContinue)) {
        if (-not $DryRun) { return (Finish "git and uv are required to continue.") }
    }

    # ------------------------------------------------------------------ GitLab access
    $Remote = -not $From
    if ($Remote) {
        Step "Network access to $GitLabHost"
        $resolved = $false
        try { [void][Net.Dns]::GetHostAddresses($GitLabHost); $resolved = $true } catch { }
        if (-not $resolved) {
            Fail "network" "$GitLabHost does not resolve: connect the InterSystems VPN (Cisco Secure Client). A customer VPN (for example GlobalProtect for ACHS) does not reach the internal GitLab; switch only for the install."
            return (Finish "Cannot reach the internal GitLab.")
        }
        Done "network" "$GitLabHost resolves"
        if ($Transport -eq "ssh") {
            if (-not (Ensure-SshAccess)) { return (Finish "SSH access to GitLab is not working.") }
        }
    }

    # ------------------------------------------------------------------ isc-ai
    Step "isc-ai CLI $Ref"
    $installed = Get-IscAiVersion
    if ($installed -and ("v$installed" -eq $Ref) -and $Remote) {
        Done "isc-ai" "isc-ai $installed already installed"
    } elseif ($DryRun) {
        $src = if ($Remote) { "GitLab $Ref ($Transport)" } else { $From }
        Plan "isc-ai" "would install isc-ai from $src and run 'isc-ai setup --apply'"
    } else {
        if ($installed) {
            Write-Host "  replacing isc-ai $installed" -ForegroundColor DarkGray
            [void](Invoke-Native uv @("tool", "uninstall", "isc-ai") -AllowFailure -PassThruCode -Show)
        }
        $installer = $null
        $temp = $null
        if ($Remote) {
            $temp = Join-Path ([IO.Path]::GetTempPath()) ("isc-ai-bootstrap-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
            Invoke-Native git @("clone", "--quiet", "--depth", "1", "--branch", $Ref, $CoreRepo[$Transport], $temp) | Out-Null
            $installer = Join-Path $temp "install.ps1"
            $installerArgs = @("-Apply", "-Remote", "-Ref", $Ref, "-Transport", $Transport)
        } else {
            $installer = Join-Path (Resolve-Path $From).Path "install.ps1"
            $installerArgs = @("-Apply")
        }
        try {
            # install.ps1 prints the whole setup report: keep it in a log, show only the lines
            # a person needs, and the tail of the log if something fails.
            $log = Join-Path ([IO.Path]::GetTempPath()) "isc-ai-install.log"
            $lines = Invoke-Native powershell.exe (@("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $installer) + $installerArgs) -AllowFailure
            $code = $LASTEXITCODE
            $lines | Set-Content -Path $log -Encoding UTF8
            $lines | Where-Object { $_ -match "^(Source:|Skipping extension|\s*extension |Installed \d|\s*\+ |isc-ai \d)" } |
                ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
            if ($code -ne 0) { $lines | Select-Object -Last 30 | ForEach-Object { Write-Host "    $_" } }
            Write-Host "    full log: $log" -ForegroundColor DarkGray
        } finally {
            if ($temp) { Remove-Item -Recurse -Force $temp -ErrorAction SilentlyContinue }
        }
        Add-SessionPath (Invoke-Native uv @("tool", "dir", "--bin") | Select-Object -Last 1).Trim()
        $installed = Get-IscAiVersion
        if ($code -eq 0 -and $installed) { Done "isc-ai" "isc-ai $installed installed, setup applied" }
        else { Fail "isc-ai" "install.ps1 exited with $code"; return (Finish "isc-ai did not install.") }
    }
    if (-not $DryRun) {
        if ($NoPersist) { Write-Host "  (sandbox: persistent PATH left unchanged)" -ForegroundColor DarkGray }
        else { Invoke-Native uv @("tool", "update-shell") -AllowFailure | Out-Null }
    }

    # ------------------------------------------------------------------ Perforce MCP
    if (-not $SkipPerforce) {
        Step "Perforce MCP server"
        if (Get-Command perforce-mcp-server -ErrorAction SilentlyContinue) {
            Done "perforce-mcp-server" "already installed"
        } elseif ($DryRun) {
            $src = if ($PerforceFrom) { $PerforceFrom } else { "$($PerforceMcpRepo[$Transport])@$PerforceMcpRef" }
            Plan "perforce-mcp-server" "would install from $src"
        } elseif (-not $PerforceFrom -and -not $Remote) {
            Skip "perforce-mcp-server" "local install (-From) without -PerforceFrom: not installed"
        } elseif (-not $PerforceFrom -and -not (Test-GitAccess $PerforceMcpRepo[$Transport] $PerforceMcpRef)) {
            Skip "perforce-mcp-server" "no access to the repository: ask the isc-ai-squad group for it"
        } else {
            $src = if ($PerforceFrom) { (Resolve-Path $PerforceFrom).Path } else { "git+$($PerforceMcpRepo[$Transport])@$PerforceMcpRef" }
            $code = Invoke-Native uv @("tool", "install", $src) -AllowFailure -PassThruCode -Show
            if ($code -eq 0 -and (Get-Command perforce-mcp-server -ErrorAction SilentlyContinue)) { Done "perforce-mcp-server" "installed" }
            else { Fail "perforce-mcp-server" "uv exited with $code" }
        }
    }

    $IscAi = Get-Command isc-ai -ErrorAction SilentlyContinue
    if (-not $IscAi -and -not $DryRun) { return (Finish "isc-ai is not on PATH.") }

    # ------------------------------------------------------------------ AI clients and MCP suite
    if (-not $SkipClients) {
        Step "AI clients (OpenCode, VS Code, Claude Code)"
        if ($DryRun) { Plan "clients" "would run 'isc-ai clients --apply'" }
        elseif ($Interactive) {
            # Interactive: isc-ai asks before installing each missing client.
            $code = Invoke-Native isc-ai @("clients", "--apply") -Tty
            Refresh-SessionPath
            if ($code -eq 0) { Done "clients" "checked" } else { Fail "clients" "isc-ai clients exited with $code" }
        } else {
            $report = Invoke-IscAiJson @("clients", "--apply", "--yes")
            Refresh-SessionPath
            if ($report.Code -eq 0 -and $report.Json) {
                $states = @($report.Json.results | ForEach-Object { "$($_.client) $($_.action)" }) -join ", "
                Done "clients" $states
            } else { Fail "clients" "isc-ai clients exited with $($report.Code)" }
        }
    }
    if (-not $SkipSuite) {
        Step "Recommended MCP suite (the 7 MCP servers, in every client)"
        # A server whose command is missing would be registered broken: leave it out.
        $suite = @("intersystems-iris", "perforce-mcp-server", "atlassian-cloud", "engram", "context7", "markitdown", "playwright")
        $needs = @{ "perforce-mcp-server" = "perforce-mcp-server"; "engram" = "engram"; "playwright" = "npx" }
        $selected = @($suite | Where-Object { -not $needs.ContainsKey($_) -or (Get-Command $needs[$_] -ErrorAction SilentlyContinue) })
        $left = @($suite | Where-Object { $selected -notcontains $_ })
        if ($DryRun) { Plan "suite" "would register: $($selected -join ', ')" }
        else {
            $changes = 0; $pending = @(); $failedServers = @()
            foreach ($server in $selected) {
                $report = Invoke-IscAiJson @("suite", "--apply", "--server", $server)
                if ($report.Code -ne 0 -or -not $report.Json) { $failedServers += $server; continue }
                $changes += @($report.Json.changes).Count
                $pending += @($report.Json.findings | Where-Object { $_.code -eq "VALUE_UNKNOWN" } |
                    ForEach-Object { ($_.message -split " ")[0] })
            }
            $pending = @($pending | Sort-Object -Unique)
            if ($failedServers.Count) { Fail "suite" "isc-ai suite failed for: $($failedServers -join ', ')" }
            else { Done "suite" "$($selected.Count) MCP servers registered ($changes changes)" }
            if ($left.Count) { Skip "suite" "not registered, command missing: $($left -join ', ')" }
            if ($pending.Count) { Skip "suite values" "still to fill in: $($pending -join ', ') (isc-ai wizard asks for them)" }
        }
    }

    # ------------------------------------------------------------------ credentials
    if (-not $SkipWizard) {
        Step "Credentials and IRIS environments (isc-ai wizard)"
        if ($DryRun) { Plan "wizard" "would open 'isc-ai wizard'" }
        elseif (-not $Interactive) { Skip "wizard" "not interactive: run 'isc-ai wizard' in a terminal" }
        else {
            $code = Invoke-Native isc-ai @("wizard") -Tty
            if ($code -eq 0) { Done "wizard" "completed" } else { Skip "wizard" "closed with ${code}: run 'isc-ai wizard' again when ready" }
        }
    }

    return (Finish $null)
}

# ---------------------------------------------------------------------- helpers

function Step([string]$Title) {
    Write-Host ""
    Write-Host "== $Title" -ForegroundColor White
}

function Record([string]$Name, [string]$State, [string]$Detail, [string]$Color) {
    $script:Results.Add([pscustomobject]@{ Item = $Name; State = $State; Detail = $Detail })
    Write-Host ("  [{0}] {1}: {2}" -f $State, $Name, $Detail) -ForegroundColor $Color
}
function Done([string]$Name, [string]$Detail) { Record $Name "OK" $Detail "Green" }
function Plan([string]$Name, [string]$Detail) { Record $Name "PLAN" $Detail "Cyan" }
function Skip([string]$Name, [string]$Detail) { Record $Name "SKIP" $Detail "Yellow" }
function Fail([string]$Name, [string]$Detail) { Record $Name "FAIL" $Detail "Red" }

function Invoke-Native {
    # Runs a native command without letting stderr become a terminating error under
    # Windows PowerShell 5.1. Returns the output, or the exit code with -PassThruCode.
    param([string]$Command, [string[]]$Arguments, [switch]$AllowFailure, [switch]$PassThruCode, [switch]$Show,
          [switch]$Tty)
    if ($Tty) {
        # Prompts need the real console: no pipe on stdin or stdout.
        $p = Start-Process -FilePath (Get-Command $Command).Source -ArgumentList $Arguments -NoNewWindow -Wait -PassThru
        return $p.ExitCode
    }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        # Stderr is folded into plain lines: under Windows PowerShell 5.1 a redirected stderr
        # line would otherwise surface as a NativeCommandError record.
        if ($Show) { & $Command @Arguments 2>&1 | ForEach-Object { "    $_" } | Out-Host; $output = @() }
        else { $output = & $Command @Arguments 2>&1 | ForEach-Object { "$_" } }
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previous }
    if ($PassThruCode) { if ($output) { $output | Out-Host }; return $code }
    if ($code -ne 0 -and -not $AllowFailure) {
        throw "$Command $($Arguments -join ' ') failed ($code): $(($output | Select-Object -Last 5) -join ' ')"
    }
    return $output
}

function Invoke-IscAiJson([string[]]$Arguments) {
    $lines = Invoke-Native isc-ai $Arguments -AllowFailure
    $code = $LASTEXITCODE
    $text = ($lines | Where-Object { $_ -notmatch "^(note|nota|warning|advertencia):" }) -join "`n"
    $json = $null
    $start = $text.IndexOf("{")
    if ($start -ge 0) { try { $json = $text.Substring($start) | ConvertFrom-Json } catch { $json = $null } }
    return [pscustomobject]@{ Code = $code; Json = $json }
}

function Get-RegistryPath {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    return @("$machine;$user".Split(";") | Where-Object { $_ })
}

function Add-SessionPath([string]$Dir) {
    if ($Dir -and (Test-Path $Dir) -and -not (@($env:Path.Split(";")) -contains $Dir)) {
        $env:Path = "$Dir;$env:Path"
    }
}

function Refresh-SessionPath {
    # Installers add their directory to the persistent PATH; bring only those new entries
    # into this session, so the order chosen by the user is kept.
    foreach ($dir in Get-RegistryPath) {
        if (-not ($script:RegistryPathAtStart -contains $dir)) { Add-SessionPath $dir }
    }
}

function Add-UserPath([string]$Dir) {
    Add-SessionPath $Dir
    if ($NoPersist) { return }
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not (@("$user".Split(";")) -contains $Dir)) {
        [Environment]::SetEnvironmentVariable("Path", (@($Dir) + @("$user".Split(";") | Where-Object { $_ }) -join ";"), "User")
    }
}

function Ensure-Winget([string]$Command, [string]$Id, [string]$Label) {
    Step $Label
    $found = Get-Command $Command -ErrorAction SilentlyContinue
    if ($found) { Done $Command "already installed ($($found.Source))"; return }
    if (-not $HasWinget) { Fail $Command "missing and winget is not available: install $Label manually"; return }
    if ($DryRun) { Plan $Command "would run: winget install --id $Id --exact"; return }
    Write-Host "  installing $Label with winget (Windows may ask for administrator approval)" -ForegroundColor DarkGray
    $code = Invoke-Native winget @("install", "--id", $Id, "--exact", "--silent", "--accept-package-agreements",
        "--accept-source-agreements", "--disable-interactivity") -AllowFailure -PassThruCode
    Refresh-SessionPath
    $found = Get-Command $Command -ErrorAction SilentlyContinue
    if ($found) { Done $Command "installed ($($found.Source))" }
    else { Fail $Command "winget exited with $code and '$Command' is still not on PATH: open a new terminal and run this again" }
}

function Ensure-Uv {
    Step "uv (Python tool installer)"
    $found = Get-Command uv -ErrorAction SilentlyContinue
    if ($found) { Done "uv" "already installed ($($found.Source))"; return }
    if ($DryRun) { Plan "uv" "would run the official installer from https://astral.sh/uv/install.ps1"; return }
    if ($NoPersist) { $env:UV_NO_MODIFY_PATH = "1" }
    $code = Invoke-Native powershell.exe @("-NoProfile", "-ExecutionPolicy", "ByPass", "-Command",
        "irm https://astral.sh/uv/install.ps1 | iex") -AllowFailure -PassThruCode
    Add-SessionPath (Join-Path $env:USERPROFILE ".local\bin")
    Refresh-SessionPath
    $found = Get-Command uv -ErrorAction SilentlyContinue
    if ($found) { Done "uv" "installed ($($found.Source))" } else { Fail "uv" "installer exited with $code" }
}

function Ensure-Engram([string]$Version) {
    Step "engram $Version (persistent memory MCP)"
    $found = Get-Command engram -ErrorAction SilentlyContinue
    if ($found) { Done "engram" "already installed ($($found.Source))"; return }
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "arm64" } else { "amd64" }
    $asset = "engram_${Version}_windows_$arch.zip"
    $base = "https://github.com/Gentleman-Programming/engram/releases/download/v$Version"
    $target = Join-Path $env:LOCALAPPDATA "engram\bin"
    if ($DryRun) { Plan "engram" "would download $asset, verify its SHA-256 and extract it to $target"; return }
    $temp = Join-Path ([IO.Path]::GetTempPath()) ("engram-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Force $temp | Out-Null
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing -Uri "$base/$asset" -OutFile (Join-Path $temp $asset)
        Invoke-WebRequest -UseBasicParsing -Uri "$base/checksums.txt" -OutFile (Join-Path $temp "checksums.txt")
        $expected = (Get-Content (Join-Path $temp "checksums.txt") | Where-Object { $_ -match [regex]::Escape($asset) } |
            Select-Object -First 1) -split "\s+" | Select-Object -First 1
        $actual = (Get-FileHash -Algorithm SHA256 (Join-Path $temp $asset)).Hash
        if (-not $expected -or $expected.ToLower() -ne $actual.ToLower()) {
            Fail "engram" "checksum mismatch for ${asset}: not installed"
            return
        }
        New-Item -ItemType Directory -Force $target | Out-Null
        Expand-Archive -Force -Path (Join-Path $temp $asset) -DestinationPath $temp
        $exe = Get-ChildItem -Path $temp -Recurse -Filter engram.exe | Select-Object -First 1
        Copy-Item -Force $exe.FullName (Join-Path $target "engram.exe")
        Add-UserPath $target
        Done "engram" "installed $Version in $target (SHA-256 verified)"
    } catch {
        Fail "engram" "download failed: $($_.Exception.Message)"
    } finally {
        Remove-Item -Recurse -Force $temp -ErrorAction SilentlyContinue
    }
}

function Test-GitAccess([string]$Url, [string]$Ref) {
    $env:GIT_TERMINAL_PROMPT = "0"
    $env:GCM_INTERACTIVE = "never"
    $setSsh = -not $env:GIT_SSH_COMMAND
    if ($setSsh) { $env:GIT_SSH_COMMAND = "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20" }
    try {
        $out = Invoke-Native git @("ls-remote", $Url, $Ref) -AllowFailure
        return ($LASTEXITCODE -eq 0) -and (@($out | Where-Object { $_ -match "^[0-9a-f]{40}\s" }).Count -gt 0)
    } finally {
        Remove-Item Env:\GIT_TERMINAL_PROMPT, Env:\GCM_INTERACTIVE -ErrorAction SilentlyContinue
        if ($setSsh) { Remove-Item Env:\GIT_SSH_COMMAND -ErrorAction SilentlyContinue }
    }
}

function Ensure-SshAccess {
    Step "SSH access to GitLab"
    $sshDir = Join-Path $env:USERPROFILE ".ssh"
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        if (Test-GitAccess $CoreRepo.ssh "refs/tags/$Ref") {
            Done "ssh" "your SSH key reaches isc-ai-squad/isc-ai and tag $Ref exists"
            return $true
        }
        $key = @("id_ed25519.pub", "id_ecdsa.pub", "id_rsa.pub") | ForEach-Object { Join-Path $sshDir $_ } |
            Where-Object { Test-Path $_ } | Select-Object -First 1
        if ($DryRun) {
            if ($key) { Plan "ssh" "key $key exists but GitLab rejects it: it would be copied and the SSH keys page opened" }
            else { Plan "ssh" "no SSH key: one would be generated with ssh-keygen -t ed25519" }
            return $true
        }
        if (-not $Interactive) {
            Fail "ssh" "GitLab rejects SSH (no key, key not added, or tag $Ref missing). Add your public key at $SshKeysPage"
            return $false
        }
        if (-not $key) {
            Write-Host "  No SSH key found. Creating one (choose a passphrase you will remember)." -ForegroundColor Yellow
            New-Item -ItemType Directory -Force $sshDir | Out-Null
            & ssh-keygen -t ed25519 -C "$env:USERNAME@intersystems" -f (Join-Path $sshDir "id_ed25519") | Out-Host
            $key = Join-Path $sshDir "id_ed25519.pub"
        }
        Get-Content $key -Raw | Set-Clipboard
        Write-Host "  Your public key ($key) is in the clipboard." -ForegroundColor Yellow
        Write-Host "  In the page that opens: 'Add new key', paste it, save." -ForegroundColor Yellow
        Start-Process $SshKeysPage
        Read-Host "  Press Enter when the key is saved in GitLab (attempt $attempt of 3)" | Out-Null
    }
    Fail "ssh" "GitLab still rejects the key. Check that you pasted the .pub file, or run again with -Transport https"
    return $false
}

function Get-IscAiVersion {
    if (-not (Get-Command isc-ai -ErrorAction SilentlyContinue)) { return $null }
    $out = Invoke-Native isc-ai @("--version") -AllowFailure | Select-Object -Last 1
    if ($out -match "isc-ai\s+(\S+)") { return $Matches[1] }
    return $null
}

function Finish([string]$StopReason) {
    Write-Host ""
    Write-Host "== Verification" -ForegroundColor White
    if (-not $DryRun -and -not $StopReason -and (Get-Command isc-ai -ErrorAction SilentlyContinue)) {
        $previous = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try { $audit = (& isc-ai audit 2>$null | Out-String) | ConvertFrom-Json } catch { $audit = $null }
        finally { $ErrorActionPreference = $previous }
        if ($audit) {
            $state = "errors $($audit.summary.errors), warnings $($audit.summary.warnings)"
            if ($audit.summary.errors -eq 0) { Done "audit" $state } else { Fail "audit" "${state}: run 'isc-ai audit' for the detail" }
        }
    }
    foreach ($tool in "git", "uv", "node", "npx", "p4", "engram", "isc-ai", "perforce-mcp-server", "opencode", "code", "claude") {
        $cmd = Get-Command $tool -ErrorAction SilentlyContinue
        $script:Results.Add([pscustomobject]@{ Item = "on PATH: $tool"; State = $(if ($cmd) { "OK" } else { "-" }); Detail = $(if ($cmd) { $cmd.Source } else { "not found" }) })
    }
    Write-Host ""
    $script:Results | Format-Table -AutoSize -Wrap | Out-String -Width 160 | Write-Host
    $failed = @($script:Results | Where-Object { $_.State -eq "FAIL" })
    if ($StopReason) {
        Write-Host "STOPPED: $StopReason Fix the FAIL lines above and run the same command again." -ForegroundColor Red
    } elseif ($failed.Count) {
        Write-Host "Finished with $($failed.Count) problem(s): fix the FAIL lines and run the same command again." -ForegroundColor Yellow
    } elseif ($DryRun) {
        Write-Host "Dry run complete: nothing was changed." -ForegroundColor Cyan
    } else {
        Write-Host "Ready. Open a NEW terminal (PATH changed), then start your AI client inside your project folder." -ForegroundColor Green
    }
    $global:LASTEXITCODE = $(if ($StopReason -or $failed.Count) { 1 } else { 0 })
}

# 'irm | iex' cannot pass parameters: run with defaults. As a file or a scriptblock, the
# arguments flow through to the function. Never 'exit': under 'iex' it would close the
# user's terminal.
Install-IscAi @args
