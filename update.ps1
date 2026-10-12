<#
.SYNOPSIS
  Updates PC-Control from ANY earlier version (including the old "pcsight") and reinstalls it with the same settings.
.DESCRIPTION
  - Installed from a git checkout (pip install -e): git pull, refresh the package.
  - Installed as a package (or only the old "pcsight" is there): install the latest version from GitHub (no git needed).
  Then `pc-control install` runs again with the recorded mode/scope, and removes whatever an earlier version left behind
  (old server name, skill, CLAUDE.md block, hooks, permission rules). Open a NEW Claude Code session afterwards.
#>
$ErrorActionPreference = 'Stop'
$zip = 'https://github.com/VortexJer/PC-Control/archive/refs/heads/main.zip'

if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw 'Python was not found in PATH.' }

& python -c "import pc_control.cli" 2>$null
if ($LASTEXITCODE -eq 0) {
    & python -m pc_control.cli update
    if ($LASTEXITCODE -ne 0) { throw 'pc-control update failed (see the message above).' }
} else {
    # PC-Control is not installed (or only the old "pcsight" is): install the latest version, then register it.
    & python -m pip install --upgrade $zip
    if ($LASTEXITCODE -ne 0) { throw 'pip install failed.' }
    & python -m pc_control.cli install
    if ($LASTEXITCODE -ne 0) { throw 'pc-control install failed.' }
}
Write-Host "`nPC-Control is up to date. Open a NEW Claude Code session to load it." -ForegroundColor Green
