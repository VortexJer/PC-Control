<#
.SYNOPSIS
  Updates PC-Control from ANY earlier version (including the old "pcsight", which had no update command) and reinstalls it with
  the same settings.
.DESCRIPTION
  1. Gets the new code: if PC-Control runs from a git checkout, `git pull` there; otherwise the latest version from GitHub (no git needed).
  2. Runs the NEW code's `pc-control reinstall`: same mode/scope/options as before, and whatever an earlier version left behind
     (old server name, skill, CLAUDE.md block, hooks, permission rules) is removed. Open a NEW Claude Code session afterwards.
#>
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

# Windows will not let pip replace a program that is running, and the `pc-control-mcp` server of every open Claude Code session is one.
# A running program CAN be renamed: move the launchers aside (.old) so pip can write new ones; put them back if pip fails.
$script:moved = @()
function Move-RunningLaunchers {
    $r = Get-Python "import sysconfig; print(sysconfig.get_path('scripts')); print(sysconfig.get_path('scripts', 'nt_user'))"
    if (-not $r.ok) { return }
    foreach ($dir in ($r.out | Select-Object -Unique)) {
        foreach ($name in 'pc-control-mcp.exe', 'pc-control.exe') {
            $f = Join-Path $dir $name; $old = "$f.old"
            if (-not (Test-Path $f)) { continue }
            Remove-Item $old -Force -ErrorAction SilentlyContinue
            if (Test-Path $old) { $old = "$old.$PID" }
            try { Move-Item $f $old -Force -ErrorAction Stop; $script:moved += , @($f, $old) } catch { }
        }
    }
}
function Restore-Launchers {
    foreach ($m in $script:moved) { if (-not (Test-Path $m[0]) -and (Test-Path $m[1])) { Move-Item $m[1] $m[0] -Force -ErrorAction SilentlyContinue } }
}
# where the installed code lives (empty if PC-Control is not installed, e.g. only the old "pcsight" is)
$probe = Get-Python "import os, pc_control; print(os.path.dirname(os.path.dirname(os.path.abspath(pc_control.__file__))))"
$src = $probe.out
if ($probe.ok -and $src -and (Test-Path (Join-Path $src '.git')) -and (Test-Path (Join-Path $src 'pyproject.toml'))) {
    & git -C $src pull --ff-only
    if ($LASTEXITCODE -ne 0) { throw "git pull failed in $src" }
    Remove-LegacyPackage
    Move-RunningLaunchers
    & python -m pip install -q -e $src
} else {
    Remove-LegacyPackage
    Move-RunningLaunchers
    & python -m pip install -q --upgrade $zip
}
if ($LASTEXITCODE -ne 0) { Restore-Launchers; throw 'pip install failed.' }

& python -m pc_control.cli reinstall
if ($LASTEXITCODE -ne 0) { throw 'pc-control reinstall failed.' }
Write-Host "`nPC-Control is up to date. Open a NEW Claude Code session to load it." -ForegroundColor Green
