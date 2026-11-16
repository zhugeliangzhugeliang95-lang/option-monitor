$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
py -3 scripts/launch.py @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
