[CmdletBinding()]
param(
    [string] $Python,
    [string] $FFmpeg,
    [string] $FFprobe,
    [string] $Codex,
    [string] $WhisperCpu,
    [string] $WhisperVulkan,
    [string] $WhisperModel,
    [string] $WhisperVadModel,
    [switch] $EnableVulkan,
    [switch] $DisableVulkan,
    [string] $DataDir,
    [string] $InstallDir,
    [string] $ScriptsDir,
    [string] $ResolveDir
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'installation.ps1')

if ($DisableVulkan -and ($EnableVulkan -or $WhisperVulkan)) {
    throw 'Choose either -DisableVulkan or a Vulkan installation option.'
}

$sourceRoot = Split-Path -Parent $PSScriptRoot
$whisperRelease = 'v1.9.2'
if (-not $ScriptsDir) { $ScriptsDir = Get-DefaultScriptsDir }
$ScriptsDir = [IO.Path]::GetFullPath($ScriptsDir)
$menuScript = Get-MenuScriptPath $ScriptsDir
$launcher = Read-Launcher $menuScript
if (-not $InstallDir) {
    $InstallDir = if ($launcher.InstallDir) { $launcher.InstallDir } else { Get-DefaultInstallDir }
}
$InstallDir = Get-InstallPath $InstallDir
if ($launcher.InstallDir -and $launcher.InstallDir -ne $InstallDir) {
    throw "This menu already belongs to the installation at $($launcher.InstallDir)."
}
$record = Get-ManagedInstall $InstallDir
if ($record -and $record.menu_script -ne $menuScript) {
    throw "This installation belongs to a different Resolve script directory: $($record.menu_script)"
}

$configurationFile = Join-Path $InstallDir 'settings.json'
$exportShortcut = Join-Path $InstallDir 'Davinci Semantic Search - Export logs.lnk'
if (-not $record -and (Test-Path -LiteralPath $configurationFile)) {
    throw "The installation directory contains an unmanaged settings file: $configurationFile"
}
if ($record.export_shortcut -ne $exportShortcut -and (Test-Path -LiteralPath $exportShortcut)) {
    throw "The installation directory contains an unmanaged shortcut: $exportShortcut"
}
$previousFile = if ($launcher.ConfigFile) { $launcher.ConfigFile } else { $configurationFile }
$previous = if (Test-Path -LiteralPath $previousFile -PathType Leaf) {
    Get-Content -LiteralPath $previousFile -Raw -Encoding UTF8 | ConvertFrom-Json
}
$runtimeDir = Join-Path $InstallDir 'runtime'

function Get-ToolPath([string] $Command) {
    if (Test-Path -LiteralPath $Command -PathType Leaf) {
        return (Resolve-Path -LiteralPath $Command).Path
    }
    (Get-Command -Name $Command -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
}

function Select-Tool([string] $Selected, [string] $Saved, [string] $Default, [switch] $Optional) {
    if ($Selected) { return Get-ToolPath $Selected }
    if ($Saved -and (Test-Path -LiteralPath $Saved -PathType Leaf)) { return Get-ToolPath $Saved }
    if ($Optional) {
        return (Get-Command -Name $Default -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1).Source
    }
    Get-ToolPath $Default
}

function Find-ResolveInstallation([string] $Directory) {
    $roots = if ($Directory) {
        $Directory
    }
    else {
        Join-Path $env:ProgramFiles 'Blackmagic Design\DaVinci Resolve'
        Get-Process -Name Resolve -ErrorAction SilentlyContinue | ForEach-Object {
            if ($_.Path) { Split-Path -Parent $_.Path }
        }
    }
    foreach ($root in $roots) {
        $candidate = Join-Path $root 'Resolve.exe'
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return Get-Item -LiteralPath $candidate }
    }
    if ($Directory) { throw "Resolve.exe was not found in $Directory. Pass its containing directory with -ResolveDir." }
}

function Save-VerifiedDownload([string] $Url, [string] $Destination, [string] $Sha256) {
    if ((Test-Path -LiteralPath $Destination -PathType Leaf) -and
        (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash -eq $Sha256) {
        return
    }
    $temporary = "$Destination.$([guid]::NewGuid().ToString('N')).download"
    try {
        Write-Host "Downloading $([IO.Path]::GetFileName($Destination))..."
        & curl.exe --fail --location --silent --show-error --retry 2 --output $temporary $Url
        if ($LASTEXITCODE -ne 0) { throw "Download failed: $Url" }
        if ((Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash -ne $Sha256) {
            throw "Unexpected SHA-256: $temporary"
        }
        Move-Item -LiteralPath $temporary -Destination $Destination -Force
    }
    finally {
        if (Test-Path -LiteralPath $temporary -PathType Leaf) {
            Remove-Item -LiteralPath $temporary
        }
    }
}

function Read-ResolvePython([string] $Interpreter) {
    # Run in Resolve's scripting host to see the Python it actually loads.
    $probe = @'
import json
import os
import sqlite3
import sys

if sys.version_info >= (3, 10) and sys.maxsize > 2**32:
    info = {
        'version': '.'.join(map(str, sys.version_info[:3])),
        'executable': os.path.join(sys.base_prefix, 'python.exe'),
    }
    print('DAVINCI_SEARCH_PYTHON=' + json.dumps(info), flush=True)
'@
    $startInfo = [Diagnostics.ProcessStartInfo]::new($Interpreter, ('-l py3 -x "' + $probe + '"'))
    $startInfo.WorkingDirectory = Split-Path -Parent $Interpreter
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = [Diagnostics.Process]::Start($startInfo)
    try {
        $output = $process.StandardOutput.ReadToEndAsync()
        $null = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(10000)) {
            $process.Kill()
            return
        }
        if ($process.ExitCode -eq 0) {
            $match = [regex]::Match($output.GetAwaiter().GetResult(), '(?m)^DAVINCI_SEARCH_PYTHON=(\{[^\r\n]+\})')
            if ($match.Success) { return ($match.Groups[1].Value | ConvertFrom-Json) }
        }
    }
    finally { $process.Dispose() }
}

function Get-LegacyResolvePython([string] $Directory) {
    $interpreter = Join-Path $Directory 'fuscript.exe'
    if (-not (Test-Path -LiteralPath $interpreter -PathType Leaf)) {
        throw "fuscript.exe was not found in $Directory. Repair the Resolve Studio 21.0.x installation or pass its program directory with -ResolveDir."
    }
    $hostPython = Read-ResolvePython $interpreter
    if ($hostPython) { return $hostPython }

    $version = '3.14.10'
    $setupName = "python-$version-amd64.exe"
    $manual = 'Install 64-bit Python for all users from https://www.python.org/downloads/release/python-31410/ and run this installer again.'
    Write-Host 'Resolve Studio 21.0.x could not load a compatible 64-bit Python (3.10 or newer).'
    try {
        $answer = Read-Host "Download and install Python $version (x64) from python.org for all users? Administrator approval may be required [y/N]"
    }
    catch { throw "Python setup needs confirmation. $manual" }
    if ($answer -notmatch '^\s*y(es)?\s*$') { throw "Python installation declined. $manual" }

    $package = Join-Path $runtimeDir "$([guid]::NewGuid().ToString('N'))-$setupName"
    try {
        New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
        Save-VerifiedDownload "https://www.python.org/ftp/python/$version/$setupName" $package '92480239264744fe0386a1bd8275786fefa126e3129e56a68e22b52c73329965'
        $setup = Start-Process -FilePath $package -ArgumentList @(
            '/passive', '/norestart', 'InstallAllUsers=1', 'PrependPath=0', 'Include_launcher=0', 'Include_test=0'
        ) -PassThru -Wait
    }
    catch { throw "Python setup did not complete. $manual Details: $($_.Exception.Message)" }
    finally { Remove-Item -LiteralPath $package -ErrorAction SilentlyContinue }

    if ($setup.ExitCode -in @(1602, 1223, -2147023294, -2147023673)) {
        throw "Python setup was cancelled. $manual"
    }
    if ($setup.ExitCode -in @(1641, 3010)) {
        throw 'Python setup requires a Windows restart. Restart Windows, then run the plugin installer again.'
    }
    if ($setup.ExitCode -ne 0) { throw "Python setup failed with exit code $($setup.ExitCode). $manual" }
    $hostPython = Read-ResolvePython $interpreter
    if (-not $hostPython) {
        throw 'Python setup completed, but Resolve still could not load a compatible Python. See the Resolve Studio 21.0.x checks in the README, then retry.'
    }
    $hostPython
}

function Install-Runtime([string] $Name, [string[]] $Executables, [string] $Url, [string] $Sha256) {
    $directory = Join-Path $runtimeDir $Name
    if ($Executables | Where-Object { -not (Test-Path -LiteralPath (Join-Path $directory $_) -PathType Leaf) }) {
        $archive = "$directory.zip"
        Save-VerifiedDownload $Url $archive $Sha256
        $staging = "$directory.$([guid]::NewGuid().ToString('N')).staging"
        try {
            Expand-Archive -LiteralPath $archive -DestinationPath $staging
            foreach ($executable in $Executables) {
                if (-not (Test-Path -LiteralPath (Join-Path $staging $executable) -PathType Leaf)) {
                    throw "The $Name archive is missing $executable."
                }
            }
            if (Test-Path -LiteralPath $directory) {
                $old = Get-Item -LiteralPath (Resolve-Path -LiteralPath $directory).Path
                if (-not $old.PSIsContainer -or ($old.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
                    $old.Parent.FullName -ne (Resolve-Path -LiteralPath $runtimeDir).Path) {
                    throw "Unexpected runtime path: $directory"
                }
                [IO.Directory]::Move($old.FullName, "$directory.$([guid]::NewGuid().ToString('N')).staging")
            }
            [IO.Directory]::Move($staging, $directory)
        }
        finally {
            if (Test-Path -LiteralPath $staging -PathType Container) {
                $resolved = (Resolve-Path -LiteralPath $staging).Path
                if ((Split-Path -Parent $resolved) -eq (Resolve-Path -LiteralPath $runtimeDir).Path) {
                    Remove-Item -LiteralPath $resolved -Recurse -Force
                }
            }
        }
    }
    foreach ($executable in $Executables) { Get-ToolPath (Join-Path $directory $executable) }
}

$resolve = Find-ResolveInstallation $ResolveDir
$hostPython = if ($resolve.VersionInfo.FileMajorPart -eq 21 -and $resolve.VersionInfo.FileMinorPart -eq 0) {
    Get-LegacyResolvePython $resolve.DirectoryName
}
$Python = if ($Python) {
    Get-ToolPath $Python
}
elseif ($previous.python -and (Test-Path -LiteralPath $previous.python -PathType Leaf)) {
    Get-ToolPath $previous.python
}
elseif ($hostPython) {
    if (-not (Test-Path -LiteralPath $hostPython.executable -PathType Leaf)) {
        throw 'Resolve can load Python, but its python.exe could not be located for worker processes. Pass a 64-bit Python 3.10+ executable with -Python.'
    }
    Get-ToolPath $hostPython.executable
}
else {
    if ($resolve) {
        $bundled = Join-Path $resolve.DirectoryName 'ResolvePython\ResolvePython.exe'
        if (Test-Path -LiteralPath $bundled -PathType Leaf) { Get-ToolPath $bundled }
    }
}
if (-not $Python) {
    throw 'Python could not be located. Use DaVinci Resolve Studio 21.0.x or newer. For a custom location, pass -ResolveDir with the directory containing Resolve.exe.'
}
& $Python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) and sys.maxsize > 2**32 else 1)'
if ($LASTEXITCODE -ne 0) { throw '64-bit Python 3.10+ is required. Pass its absolute path with -Python.' }
$FFmpeg = Select-Tool $FFmpeg $previous.ffmpeg 'ffmpeg' -Optional
$FFprobe = Select-Tool $FFprobe $previous.ffprobe 'ffprobe' -Optional
$Codex = Select-Tool $Codex $previous.codex 'codex' -Optional
if (-not $Codex) { $Codex = 'codex' }

New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
$managedFFmpegRuntimes = @($record.managed_ffmpeg_runtimes | Where-Object { $_ })
if (-not $FFmpeg -or -not $FFprobe) {
    $ffmpegRelease = '8.1.2'
    $ffmpegName = "ffmpeg-$ffmpegRelease-essentials_build"
    $ffmpegUrl = "https://github.com/GyanD/codexffmpeg/releases/download/$ffmpegRelease/$ffmpegName.zip"
    $ffmpegRuntime = "ffmpeg-$ffmpegRelease"
    $ffmpegDirectory = Join-Path $runtimeDir $ffmpegRuntime
    if ($ffmpegRuntime -notin $managedFFmpegRuntimes -and
        ((Test-Path -LiteralPath $ffmpegDirectory) -or (Test-Path -LiteralPath "$ffmpegDirectory.zip"))) {
        $ffmpegRuntime += "-$([guid]::NewGuid().ToString('N'))"
    }
    $managedFFmpeg, $managedFFprobe = Install-Runtime $ffmpegRuntime @(
        "$ffmpegName\bin\ffmpeg.exe", "$ffmpegName\bin\ffprobe.exe"
    ) $ffmpegUrl 'db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec'
    if (-not $FFmpeg) { $FFmpeg = $managedFFmpeg }
    if (-not $FFprobe) { $FFprobe = $managedFFprobe }
    if ($ffmpegRuntime -notin $managedFFmpegRuntimes) { $managedFFmpegRuntimes += $ffmpegRuntime }
}
$cpuUrl = "https://github.com/ggml-org/whisper.cpp/releases/download/$whisperRelease/whisper-bin-x64.zip"
if ($WhisperCpu -or ($previous.whisper_cpu -and (Test-Path -LiteralPath $previous.whisper_cpu -PathType Leaf))) {
    $WhisperCpu = Select-Tool $WhisperCpu $previous.whisper_cpu 'whisper-cli'
}
else {
    $WhisperCpu = Install-Runtime "whisper-cpu-$whisperRelease" 'Release\whisper-cli.exe' $cpuUrl '49dcc16de826f20bd53d44f947a1ae49dfa81f86cad67a64d80820cb192d674a'
}

$managedVulkan = $false
if ($WhisperVulkan) {
    $WhisperVulkan = Get-ToolPath $WhisperVulkan
}
elseif (-not $DisableVulkan) {
    if ($EnableVulkan -or $previous.managed_vulkan) {
        $vulkanUrl = 'https://github.com/SubtitleEdit/support-files/releases/download/whispercpp-192/whisper-vulkan-x64.zip'
        $WhisperVulkan = Install-Runtime "whisper-vulkan-$whisperRelease" 'whisper-cli.exe' $vulkanUrl 'a220f2063bf60c18406beb37d2001d7da6cfd2253d5b38b4c78719a56bedd2c0'
        $managedVulkan = $true
    }
    elseif ($previous.whisper_vulkan -and (Test-Path -LiteralPath $previous.whisper_vulkan -PathType Leaf)) {
        $WhisperVulkan = Get-ToolPath $previous.whisper_vulkan
    }
}

if (-not $WhisperModel) {
    if ($previous.whisper_model -and (Test-Path -LiteralPath $previous.whisper_model -PathType Leaf)) {
        $WhisperModel = $previous.whisper_model
    }
    else {
        $modelName = 'ggml-large-v3-turbo-q5_0.bin'
        $WhisperModel = Join-Path $InstallDir $modelName
        $modelRevision = '5359861c739e955e79d9a303bcbc70fb988958b1'
        $url = "https://huggingface.co/ggerganov/whisper.cpp/resolve/$modelRevision/$modelName"
        Save-VerifiedDownload $url $WhisperModel '394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2'
    }
}
$WhisperModel = (Resolve-Path -LiteralPath $WhisperModel).Path
if (-not $WhisperVadModel) {
    if ($previous.whisper_vad_model -and (Test-Path -LiteralPath $previous.whisper_vad_model -PathType Leaf)) {
        $WhisperVadModel = $previous.whisper_vad_model
    }
    else {
        $vadModelName = 'ggml-silero-v6.2.0.bin'
        $WhisperVadModel = Join-Path $InstallDir $vadModelName
        $vadRevision = '9ffd54a1e1ee413ddf265af9913beaf518d1639b'
        $url = "https://huggingface.co/ggml-org/whisper-vad/resolve/$vadRevision/$vadModelName"
        Save-VerifiedDownload $url $WhisperVadModel '2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987'
    }
}
if (-not (Test-Path -LiteralPath $WhisperVadModel -PathType Leaf)) {
    throw "Silero VAD model not found: $WhisperVadModel"
}
$WhisperVadModel = (Resolve-Path -LiteralPath $WhisperVadModel).Path
if (-not $DataDir) { $DataDir = $previous.data_dir }
if (-not $DataDir) { $DataDir = Join-Path $InstallDir 'data' }
$DataDir = (New-Item -ItemType Directory -Path $DataDir -Force).FullName
New-Item -ItemType Directory -Path $ScriptsDir -Force | Out-Null

$appDir = Join-Path $InstallDir "app-$([guid]::NewGuid().ToString('N'))"
$moduleDir = Join-Path $appDir 'davinci_search'
New-Item -ItemType Directory -Path $moduleDir -Force | Out-Null
Get-ChildItem -LiteralPath (Join-Path $sourceRoot 'davinci_search') -File -Filter '*.py' |
    Copy-Item -Destination $moduleDir

$configuration = [ordered]@{
    data_dir = $DataDir
    python = $Python
    ffmpeg = $FFmpeg
    ffprobe = $FFprobe
    codex = $Codex
    whisper_cpu = $WhisperCpu
    whisper_vulkan = $WhisperVulkan
    managed_vulkan = $managedVulkan
    whisper_model = $WhisperModel
    whisper_vad_model = $WhisperVadModel
}
$installation = [ordered]@{
    product = 'davinci-semantic-search'
    root = $InstallDir
    menu_script = $menuScript
    export_shortcut = $exportShortcut
    managed_ffmpeg_runtimes = $managedFFmpegRuntimes
}
$entry = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'resolve_entry.py'))
$script = @(
    '# -*- coding: utf-8 -*-'
    ('INSTALL_ROOT = ' + (ConvertTo-Json -Compress -InputObject $InstallDir))
    ('WORKSPACE_ROOT = ' + (ConvertTo-Json -Compress -InputObject $appDir))
    ('CONFIG_FILE = ' + (ConvertTo-Json -Compress -InputObject $configurationFile))
    ''
    $entry
) -join [Environment]::NewLine
$releaseVersionFile = Join-Path $sourceRoot 'version.txt'
$releaseVersion = if (Test-Path -LiteralPath $releaseVersionFile -PathType Leaf) {
    [IO.File]::ReadAllText($releaseVersionFile).Trim()
}
if ($releaseVersion -notmatch '^v[0-9]+\.[0-9]+\.[0-9]+(?:-(?:beta|preview)\.[0-9]+)?$') { $releaseVersion = 'development' }
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($exportShortcut)
$shortcut.TargetPath = $Python
$shortcut.Arguments = '-I "' + (Join-Path $moduleDir 'diagnostics.py') + '" "' + $InstallDir + '"'
$shortcut.WorkingDirectory = $InstallDir
$shortcut.Description = 'Export DaVinci Semantic Search logs for support'
Write-AtomicText (Join-Path $InstallDir 'version.txt') $releaseVersion
Write-AtomicText (Join-Path $InstallDir 'installation.json') ($installation | ConvertTo-Json)
Write-AtomicText $configurationFile ($configuration | ConvertTo-Json)
$shortcut.Save()
Write-AtomicText $menuScript $script
Write-Output "Resolve script: $menuScript"
Write-Output "Project index: $DataDir"
Write-Output "Export logs shortcut: $exportShortcut"
if ($hostPython) {
    Write-Output "Resolve scripting Python: $($hostPython.version). Restart Resolve and open Davinci Semantic Search from Workspace > Scripts."
}
