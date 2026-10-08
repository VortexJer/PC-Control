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

if ($Editable) {
    & python -m pip install -e $PSScriptRoot
} else {
    & python -m pip install --upgrade $PSScriptRoot
}
if ($LASTEXITCODE -ne 0) { throw 'pip install failed.' }

$cliArgs = @('-m', 'pc_control.cli', 'install', '--mode', $Mode)
if ($AllowReads) { $cliArgs += '--allow-reads' }
& python @args
if ($LASTEXITCODE -ne 0) { throw 'pc-control install failed.' }
Write-Host "`nPC-Control installed. Open a NEW Claude Code session; the tools appear as PC-Control." -ForegroundColor Green
