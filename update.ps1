<#
.SYNOPSIS
  Updates PC-Control from ANY earlier version (including the old "pcsight", which had no update command) and reinstalls it with
  the same settings.
.DESCRIPTION
  1. Gets the new code: if PC-Control runs from a git checkout, `git pull` there; otherwise the latest version from GitHub (no git needed).
  2. Runs the NEW code's `pc-control reinstall`: same mode/scope/options as before, and whatever an earlier version left behind
     (old server name, skill, CLAUDE.md block, hooks, permission rules) is removed. Open a NEW Claude Code session afterwards.
#>
param([switch]$StopServers)
$ErrorActionPreference = 'Stop'
$zip = 'https://github.com/VortexJer/PC-Control/archive/refs/heads/main.zip'
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw 'Python was not found in PATH.' }

# Before 0.6 the package was called "pc-control"; it is now "claude-pc-control" and both install the same files (pc_control/), so the old
# one has to go BEFORE the new one is installed (removing it afterwards would delete the new code).
function Get-Python([string]$code) {
    # a probe that may fail (the package is not there): Windows PowerShell 5.1 turns ANY stderr of a native command into a terminating
    # error under 'Stop', even with 2>$null, so the probe runs under 'Continue' and the caller reads ok / out
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { $out = & python -c $code 2>$null; return @{ ok = ($LASTEXITCODE -eq 0); out = $out } } finally { $ErrorActionPreference = $prev }
}
function Remove-LegacyPackage {
    $r = Get-Python "import importlib.metadata as m, sys; sys.exit(0 if 'pc-control' in {(d.metadata['Name'] or '').lower().replace('_', '-') for d in m.distributions()} else 1)"
    if ($r.ok) {
        Write-Host "Removing the old 'pc-control' package (now 'claude-pc-control')..."
        & python -m pip uninstall -y pc-control
        if ($LASTEXITCODE -ne 0) { throw "Could not uninstall the old 'pc-control' package." }
    }
}

# Windows will not let pip replace `pc-control-mcp.exe` while it runs (one server per open Claude Code session that uses PC-Control), and
# the launcher cannot even be renamed. Say so BEFORE touching anything, or stop those servers when asked with -StopServers.
$servers = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*pc-control-mcp.ex[e]*' })
if ($servers.Count) {
    $ids = ($servers | ForEach-Object { $_.ProcessId }) -join ', '
    if (-not $StopServers) {
        throw "PC-Control's server is running (processes ${ids}; each open Claude Code session that uses it has one) and Windows will not let it be replaced meanwhile. Close those sessions and run this again, or run it with -StopServers (their PC-Control tools stop until you open a new session)."
    }
    $servers | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Write-Host "Stopped the running PC-Control server processes: $ids"
}
# where the installed code lives (empty if PC-Control is not installed, e.g. only the old "pcsight" is)
$probe = Get-Python "import os, pc_control; print(os.path.dirname(os.path.dirname(os.path.abspath(pc_control.__file__))))"
$src = $probe.out
if ($probe.ok -and $src -and (Test-Path (Join-Path $src '.git')) -and (Test-Path (Join-Path $src 'pyproject.toml'))) {
    & git -C $src pull --ff-only
    if ($LASTEXITCODE -ne 0) { throw "git pull failed in $src" }
    Remove-LegacyPackage
    & python -m pip install -q -e $src
} else {
    Remove-LegacyPackage
    & python -m pip install -q --upgrade $zip
}
if ($LASTEXITCODE -ne 0) { throw 'pip install failed.' }

& python -m pc_control.cli reinstall
if ($LASTEXITCODE -ne 0) { throw 'pc-control reinstall failed.' }
Write-Host "`nPC-Control is up to date. Open a NEW Claude Code session to load it." -ForegroundColor Green
