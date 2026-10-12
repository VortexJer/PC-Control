<#
.SYNOPSIS
  Installs PC-Control from this checkout and registers it in Claude Code.
.DESCRIPTION
  1. pip-installs the package (non-editable by default; -Editable for development).
  2. Runs `pc-control install`, which registers the MCP server, writes the skill, adds a managed block to ~/.claude/CLAUDE.md
     and registers the permission hooks. `uninstall.ps1` undoes all of it.
.PARAMETER Mode
  follow (default: follows Claude Code's permission mode) | auto | ask | strict | bypass
.PARAMETER AllowReads
  Let `windows` and `look` run without asking.
.PARAMETER Editable
  pip install -e (development).
#>
param(
    [ValidateSet('follow', 'auto', 'ask', 'strict', 'bypass')][string]$Mode = 'follow',
    [switch]$AllowReads,
    [switch]$Editable
)
$ErrorActionPreference = 'Stop'

$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { throw 'Python 3.10+ was not found in PATH. Install it from https://www.python.org/downloads/ and try again.' }
$ver = & python -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$ver -lt [version]'3.10') { throw "Python 3.10+ is required (found $ver)." }
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Warning "The 'claude' command was not found in PATH: install Claude Code first (https://claude.com/claude-code), then run this script again."
}

# Before 0.6 the package was called "pc-control"; it is now "claude-pc-control" and both install the same files (pc_control/), so an old
# install has to be removed BEFORE the new one goes in (removing it afterwards would delete the new code).
$prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'      # Windows PowerShell 5.1: stderr of a native command is fatal under 'Stop'
& python -c "import importlib.metadata as m, sys; sys.exit(0 if 'pc-control' in {(d.metadata['Name'] or '').lower().replace('_', '-') for d in m.distributions()} else 1)" 2>$null
$hasLegacy = ($LASTEXITCODE -eq 0); $ErrorActionPreference = $prev
if ($hasLegacy) {
    Write-Host "Removing the old 'pc-control' package (now 'claude-pc-control')..."
    & python -m pip uninstall -y pc-control
    if ($LASTEXITCODE -ne 0) { throw "Could not uninstall the old 'pc-control' package." }
}

$hasSource = Test-Path (Join-Path $PSScriptRoot 'pyproject.toml')
if ($Editable) {
    if (-not $hasSource) { throw '-Editable needs the full source folder (pyproject.toml next to this script).' }
    & python -m pip install -e $PSScriptRoot
} elseif ($hasSource) {
    & python -m pip install --upgrade $PSScriptRoot
} else {
    # only this script was downloaded: install the latest version straight from GitHub (no git needed)
    & python -m pip install --upgrade 'https://github.com/VortexJer/PC-Control/archive/refs/heads/main.zip'
}
if ($LASTEXITCODE -ne 0) { throw 'pip install failed.' }

$cliArgs = @('-m', 'pc_control.cli', 'install', '--mode', $Mode)
if ($AllowReads) { $cliArgs += '--allow-reads' }
& python @cliArgs
if ($LASTEXITCODE -ne 0) { throw 'pc-control install failed.' }
Write-Host "`nPC-Control installed. Open a NEW Claude Code session; the tools appear as PC-Control." -ForegroundColor Green
