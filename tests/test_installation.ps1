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
    $exportShortcut = Join-Path $installDir 'Davinci Semantic Search - Export logs.lnk'
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
    $shell = New-Object -ComObject WScript.Shell
    if (-not (Test-Path -LiteralPath $exportShortcut -PathType Leaf)) { throw 'The export shortcut is missing.' }
    $firstExportArguments = $shell.CreateShortcut($exportShortcut).Arguments

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
    $updatedShortcut = $shell.CreateShortcut($exportShortcut)
    if ($updatedShortcut.TargetPath -ne $Python -or $updatedShortcut.Arguments -eq $firstExportArguments) {
        throw 'Upgrade did not update the log export shortcut.'
    }
    if ($updatedShortcut.Arguments -notmatch '^-I "([^"]+)" "([^"]+)"$' -or $Matches[2] -ne $installDir) {
        throw 'The export shortcut does not target this installation.'
    }
    $exporter = $Matches[1]

    Move-Item -LiteralPath $config -Destination "$config.backup"
    try {
        @'
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import runpy
import sys
from zipfile import ZipFile

class Resolve:
    def GetVersion(self):
        return [21, 2, 0, 4, ""]

_, menu, install_dir, exporter = sys.argv
with redirect_stdout(StringIO()):
    launcher = runpy.run_path(menu, init_globals={"resolve": Resolve(), "bmd": object()})
assert Path(exporter) == Path(launcher["WORKSPACE_ROOT"]) / "davinci_search" / "diagnostics.py"
sys.argv = [exporter, install_dir]
os.startfile = lambda directory: None
with redirect_stdout(StringIO()):
    runpy.run_path(exporter, run_name="__main__")
path, = (Path(install_dir) / "exports").glob("*.zip")
with ZipFile(path) as bundle:
    events = [json.loads(line) for line in bundle.read("events.jsonl").splitlines()]
    assert json.loads(bundle.read("system.json"))["resolve_version"] == "21.2.0.4"
    failure = events[-1]
    assert (failure["event"], failure["step"], failure["error"]) == ("resolve_error", "settings", "FileNotFoundError")
    assert install_dir not in bundle.read("events.jsonl").decode()
'@ | & $Python - $menuScript $installDir $exporter
        if ($LASTEXITCODE -ne 0) { throw 'Could not export logs after a startup failure.' }
    }
    finally { Move-Item -LiteralPath "$config.backup" -Destination $config }

    & $uninstall -ScriptsDir $menu
    & $uninstall -ScriptsDir $menu -InstallDir $installDir
    if ((Test-Path -LiteralPath $menuScript) -or (Test-Path -LiteralPath $exportShortcut) -or
        (Get-ChildItem -LiteralPath $installDir -Filter 'app-*')) {
        throw 'Uninstall left the menu, export shortcut, or application files in place.'
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
