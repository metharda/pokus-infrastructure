# Managed by pokus-infra (contrib/windows/pokus-wsl.ps1). Local changes will be overwritten.
#
# Optional Windows helper for a Pokus lab in WSL2 (docs/providers/wsl2.md).
# Bootstrap never runs it and nothing in the lab depends on it.
#
# Windows stops an idle WSL distribution and may put the workstation to sleep
# while the lab works. Start-PokusLab starts a hidden keep-alive process
# (wsl.exe -d <distro> --exec /usr/bin/sleep infinity) and, for as long as it
# runs, asks Windows not to sleep (SetThreadExecutionState with
# ES_CONTINUOUS | ES_SYSTEM_REQUIRED). Stop-PokusLab ends both. The display may
# still turn off.
#
# Use: copy this file to your Windows user profile and dot-source it from your
# PowerShell profile ($PROFILE):
#
#   . "$HOME\pokus-wsl.ps1"
#   $env:POKUS_WSL_DISTRO = 'Ubuntu-24.04'   # the name `wsl --list` shows
#
# Then `Start-PokusLab`, `Stop-PokusLab` and `Get-PokusLab`. With
# POKUS_WSL_DISTRO set, the `wsl` function below also starts the keep-alive
# whenever you open that distribution with plain `wsl`.
#
# TODO(verify): run on Windows. Only parsing and the wsl wrapper's argument
# handling were tested (PowerShell 7 on Linux, with the Windows calls stubbed).

Set-StrictMode -Version 3.0

$script:PokusStateFile = Join-Path $env:LOCALAPPDATA 'Pokus\wsl-keepalive.json'

function Get-PokusLab {
    <#
    .SYNOPSIS
    Show the running keep-alive, or nothing if none is running.
    #>
    [CmdletBinding()]
    param()
    if (-not (Test-Path -LiteralPath $script:PokusStateFile)) { return $null }
    $state = Get-Content -LiteralPath $script:PokusStateFile -Raw | ConvertFrom-Json
    if (-not (Get-Process -Id $state.wsl -ErrorAction SilentlyContinue)) {
        Remove-Item -LiteralPath $script:PokusStateFile -Force
        return $null
    }
    return $state
}

function Start-PokusLab {
    <#
    .SYNOPSIS
    Keep the WSL distribution running and Windows awake until Stop-PokusLab.
    #>
    [CmdletBinding()]
    param([string]$Distro = $env:POKUS_WSL_DISTRO)
    if (-not $Distro) { throw 'Pass -Distro <name> or set $env:POKUS_WSL_DISTRO (see `wsl --list`).' }
    $running = Get-PokusLab
    if ($running) {
        Write-Verbose "keep-alive for $($running.distro) is already running"
        return $running
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $script:PokusStateFile) | Out-Null
    # The hidden PowerShell process holds the execution state on its own thread
    # and waits for the keep-alive; when wsl.exe ends, Windows may sleep again.
    $holder = @'
param([string]$Distro, [string]$StateFile)
$signature = '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);'
$kernel32 = Add-Type -MemberDefinition $signature -Name PokusPower -Namespace Pokus -PassThru
$continuousSystemRequired = [uint32]2147483649  # ES_CONTINUOUS (0x80000000) | ES_SYSTEM_REQUIRED (0x1)
[void]$kernel32::SetThreadExecutionState($continuousSystemRequired)
$wsl = Start-Process -FilePath wsl.exe -ArgumentList @('-d', $Distro, '--exec', '/usr/bin/sleep', 'infinity') -WindowStyle Hidden -PassThru
@{ distro = $Distro; holder = $PID; wsl = $wsl.Id } | ConvertTo-Json | Set-Content -LiteralPath $StateFile -Encoding UTF8
$wsl.WaitForExit()
'@
    $holderFile = Join-Path (Split-Path -Parent $script:PokusStateFile) 'wsl-keepalive-holder.ps1'
    Set-Content -LiteralPath $holderFile -Value $holder -Encoding UTF8
    $arguments = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
        '-File', $holderFile, '-Distro', $Distro, '-StateFile', $script:PokusStateFile)
    Start-Process -FilePath powershell.exe -ArgumentList $arguments -WindowStyle Hidden | Out-Null
    for ($i = 0; $i -lt 50 -and -not (Get-PokusLab); $i++) { Start-Sleep -Milliseconds 200 }
    $state = Get-PokusLab
    if (-not $state) { throw "The keep-alive for $Distro did not start; run: wsl -d $Distro" }
    return $state
}

function Stop-PokusLab {
    <#
    .SYNOPSIS
    End the keep-alive; the distribution may stop and Windows may sleep again.
    #>
    [CmdletBinding()]
    param()
    $state = Get-PokusLab
    if (-not $state) { return }
    Stop-Process -Id $state.wsl -ErrorAction SilentlyContinue
    Stop-Process -Id $state.holder -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $script:PokusStateFile -Force -ErrorAction SilentlyContinue
}

function wsl {
    <#
    .SYNOPSIS
    wsl.exe, starting the Pokus keep-alive first when the lab distribution is opened.
    #>
    $management = @('--shutdown', '--terminate', '-t', '--unregister', '--list', '-l', '--status',
        '--update', '--version', '-v', '--help', '--export', '--import', '--set-version', '--set-default', '-s')
    $opensShell = -not ($args | Where-Object { $management -contains $_ })
    if ($env:POKUS_WSL_DISTRO -and $opensShell) {
        $named = [array]::IndexOf([string[]]$args, '-d')
        if ($named -lt 0) { $named = [array]::IndexOf([string[]]$args, '--distribution') }
        $target = if ($named -ge 0 -and $named + 1 -lt $args.Count) { $args[$named + 1] } else { $env:POKUS_WSL_DISTRO }
        if ($target -eq $env:POKUS_WSL_DISTRO) { Start-PokusLab -Distro $target | Out-Null }
    }
    & wsl.exe @args
}
