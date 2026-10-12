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

# where the installed code lives (empty if PC-Control is not installed, e.g. only the old "pcsight" is)
$src = & python -c "import os, pc_control; print(os.path.dirname(os.path.dirname(os.path.abspath(pc_control.__file__))))" 2>$null
if ($LASTEXITCODE -eq 0 -and $src -and (Test-Path (Join-Path $src '.git')) -and (Test-Path (Join-Path $src 'pyproject.toml'))) {
    & git -C $src pull --ff-only
    if ($LASTEXITCODE -ne 0) { throw "git pull failed in $src" }
    & python -m pip install -q -e $src
} else {
    & python -m pip install -q --upgrade $zip
}
if ($LASTEXITCODE -ne 0) { throw 'pip install failed.' }

& python -m pc_control.cli reinstall
if ($LASTEXITCODE -ne 0) { throw 'pc-control reinstall failed.' }
Write-Host "`nPC-Control is up to date. Open a NEW Claude Code session to load it." -ForegroundColor Green
