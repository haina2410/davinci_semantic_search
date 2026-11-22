param([string] $InstallDir, [string] $ScriptsDir)

$ErrorActionPreference = 'Stop'
$exitCode = 0
try {
    if ((Read-Host 'Remove Davinci Semantic Search? Your project data will be kept [y/N]') -match '^\s*y(es)?\s*$') {
        & (Join-Path $PSScriptRoot 'uninstall.ps1') -InstallDir $InstallDir -ScriptsDir $ScriptsDir
        Write-Host 'Uninstall complete. Reopen Resolve to refresh its Scripts menu.'
    }
}
catch {
    Write-Host "Uninstall did not complete. $($_.Exception.Message)" -ForegroundColor Red
    $exitCode = 1
}
finally { [void](Read-Host 'Press Enter to close') }
exit $exitCode
