<#
.SYNOPSIS
  Removes PC-Control completely: Claude Code registration, skill, CLAUDE.md block, hooks, permissions, data folder and the package.
.PARAMETER KeepData
  Keep the ~/.pc-control data folder (allow list, approvals).
.PARAMETER KeepPackage
  Keep the pip package (only undo the Claude Code integration).
#>
param([switch]$KeepData, [switch]$KeepPackage)
$ErrorActionPreference = 'Stop'

if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw 'Python was not found in PATH.' }
$cliArgs = @('-m', 'pc_control.cli', 'uninstall')
if ($KeepData) { $cliArgs += '--keep-data' }
& python @args
if ($LASTEXITCODE -ne 0) { Write-Warning 'pc-control uninstall reported a problem (is the package still installed?). Continuing.' }

if (-not $KeepPackage) {
    & python -m pip uninstall -y pc-control
}
Write-Host "`nPC-Control removed. Open a new Claude Code session to see the change." -ForegroundColor Green
