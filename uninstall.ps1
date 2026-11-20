<#
.SYNOPSIS
  Removes PC-Control completely: Claude Code registration, skill, CLAUDE.md block, hooks, permissions, data folder and the package.
.PARAMETER KeepData
  Keep the ~/.pc-control data folder (allow list, approvals).
.PARAMETER KeepPackage
  Keep the pip package (only undo the Claude Code integration).
.PARAMETER Client
  claude (default) or codex. The package is kept while the other client is installed.
#>
param([ValidateSet('claude', 'codex')][string]$Client = 'claude', [switch]$KeepData, [switch]$KeepPackage)
$ErrorActionPreference = 'Stop'

if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw 'Python was not found in PATH.' }
$cliArgs = @('-m', 'pc_control.cli', 'uninstall', '--client', $Client)
if ($KeepData) { $cliArgs += '--keep-data' }
& python @cliArgs
if ($LASTEXITCODE -ne 0) { Write-Warning 'pc-control uninstall reported a problem (is the package still installed?). Continuing.' }

$otherClient = if ($Client -eq 'codex') { 'claude' } else { 'codex' }
$otherHome = if ($otherClient -eq 'codex') {
    if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
} else { Join-Path $env:USERPROFILE '.claude' }
$otherInstalled = Test-Path (Join-Path $otherHome 'pc-control-install.json')
if ($otherInstalled) { Write-Host 'Keeping the shared Python package: the other client is still installed.' }
if (-not $KeepPackage -and -not $otherInstalled) {
    # the package is "claude-pc-control" since 0.6 and was "pc-control" before: remove whichever (or both) is there
    & python -m pip uninstall -y claude-pc-control pc-control
}
Write-Host "`nPC-Control removed from $Client. Open a new session to see the change." -ForegroundColor Green
