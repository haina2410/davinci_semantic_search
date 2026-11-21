[CmdletBinding()]
param([string] $Python = 'python')

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$cache = (New-Item -ItemType Directory -Path (Join-Path $repo '.cache') -Force).FullName
$root = (New-Item -ItemType Directory -Path (Join-Path $cache "install-test-$([guid]::NewGuid().ToString('N'))")).FullName
$install = Join-Path $repo 'scripts/install.ps1'
$uninstall = Join-Path $repo 'scripts/uninstall.ps1'
$Python = (Get-Command $Python -CommandType Application | Select-Object -First 1).Source

try {
    $installDir = Join-Path $root 'installed'
    $menu = Join-Path $root 'menu'
    $data = Join-Path $root 'saved index'
    $model = Join-Path $root 'model.bin'
    $source = Join-Path $root 'source.wav'
    $menuScript = Join-Path $menu 'Davinci Semantic Search.py'
    $config = Join-Path $installDir 'settings.json'
    Set-Content -LiteralPath $model -Value 'fixture'
    Set-Content -LiteralPath $source -Value 'fixture'
    $options = @{
        InstallDir = $installDir; ScriptsDir = $menu; DataDir = $data
        Python = $Python; FFmpeg = $Python; FFprobe = $Python; Codex = $Python
        WhisperCpu = $Python; WhisperModel = $model
    }
    & $install @options
    $before = Get-Content -LiteralPath $config -Raw | ConvertFrom-Json
    if ($before.whisper_vulkan) { throw 'A fresh installation should use CPU.' }

    $index = Join-Path $data 'index.sqlite3'
    @'
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as db:
    db.execute("CREATE TABLE fixture(raw TEXT)")
    db.execute("INSERT INTO fixture VALUES (?)", ("Saved transcript",))
'@ | & $Python - $index
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the saved index.' }
    $indexHash = (Get-FileHash -LiteralPath $index).Hash
    $firstLauncher = Get-Content -LiteralPath $menuScript -Raw

    & $install -ScriptsDir $menu
    $after = Get-Content -LiteralPath $config -Raw | ConvertFrom-Json
    if ($after.data_dir -ne $before.data_dir -or $after.whisper_model -ne $before.whisper_model) {
        throw 'Upgrade did not preserve the configured data and model paths.'
    }
    if ((Get-Content -LiteralPath $menuScript -Raw) -eq $firstLauncher) {
        throw 'Upgrade did not point the launcher to a fresh application copy.'
    }

    & $uninstall -ScriptsDir $menu
    & $uninstall -ScriptsDir $menu -InstallDir $installDir
    if ((Test-Path -LiteralPath $menuScript) -or (Get-ChildItem -LiteralPath $installDir -Filter 'app-*')) {
        throw 'Uninstall left the menu or application files in place.'
    }
    if ((Get-FileHash -LiteralPath $index).Hash -ne $indexHash -or
        -not (Test-Path -LiteralPath $model) -or -not (Test-Path -LiteralPath $source)) {
        throw 'Uninstall changed the saved index, model, or media.'
    }
    Write-Output 'Install, upgrade, and repeated uninstall preserve the saved index.'
}
finally {
    $resolved = (Resolve-Path -LiteralPath $root).Path
    if ((Split-Path -Parent $resolved) -eq $cache -and (Split-Path -Leaf $resolved) -match '^install-test-[0-9a-f]{32}$') {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
