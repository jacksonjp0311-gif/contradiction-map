# One entry point: .\Start-ContradictionMap.ps1
# Arguments after the script are passed directly to the Python CLI.
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) { throw 'Python 3.11+ is required. Install Python from python.org, then reopen PowerShell.' }
    & $pythonCommand.Source -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)"
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or newer is required.' }
    if ($args.Count -eq 0) {
        & $pythonCommand.Source -m contradiction_map serve --open
    } else {
        & $pythonCommand.Source -m contradiction_map @args
    }
    $resultCode = $LASTEXITCODE
} finally { Pop-Location }
exit $resultCode
