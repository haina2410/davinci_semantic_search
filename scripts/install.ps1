[CmdletBinding()]
param(
    [string] $Python,
    [string] $FFmpeg,
    [string] $FFprobe,
    [string] $Codex,
    [string] $WhisperCpu,
    [string] $WhisperVulkan,
    [string] $WhisperModel,
    [switch] $EnableVulkan,
    [switch] $DisableVulkan,
    [string] $DataDir,
    [string] $InstallDir,
    [string] $ScriptsDir
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
if (-not $record -and (Test-Path -LiteralPath $configurationFile)) {
    throw "The installation directory contains an unmanaged settings file: $configurationFile"
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

function Select-Tool([string] $Selected, [string] $Saved, [string] $Default) {
    if ($Selected) { return Get-ToolPath $Selected }
    if ($Saved -and (Test-Path -LiteralPath $Saved -PathType Leaf)) { return Get-ToolPath $Saved }
    Get-ToolPath $Default
}

function Save-VerifiedDownload([string] $Url, [string] $Destination, [string] $Sha256) {
    if ((Test-Path -LiteralPath $Destination -PathType Leaf) -and
        (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash -eq $Sha256) {
        return
    }
    $temporary = "$Destination.$([guid]::NewGuid().ToString('N')).download"
    try {
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

function Install-Whisper([string] $Name, [string] $Executable, [string] $Url, [string] $Sha256) {
    $directory = Join-Path $runtimeDir "$Name-$whisperRelease"
    $path = Join-Path $directory $Executable
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        $archive = "$directory.zip"
        Save-VerifiedDownload $Url $archive $Sha256
        $staging = "$directory.$([guid]::NewGuid().ToString('N')).staging"
        try {
            Expand-Archive -LiteralPath $archive -DestinationPath $staging
            if (-not (Test-Path -LiteralPath (Join-Path $staging $Executable) -PathType Leaf)) {
                throw "The Whisper archive is missing $Executable."
            }
            if (Test-Path -LiteralPath $directory) {
                $old = Get-Item -LiteralPath (Resolve-Path -LiteralPath $directory).Path
                if (-not $old.PSIsContainer -or ($old.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
                    $old.Parent.FullName -ne (Resolve-Path -LiteralPath $runtimeDir).Path) {
                    throw "Unexpected Whisper runtime path: $directory"
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
    Get-ToolPath $path
}

if (-not $Python -and -not ($previous.python -and (Test-Path -LiteralPath $previous.python -PathType Leaf))) {
    $uv = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue
    if ($uv) {
        $env:UV_CACHE_DIR = Join-Path $InstallDir 'uv-cache'
        $foundPython = & $uv.Source python find 3.13 2>$null
        if ($LASTEXITCODE -eq 0) { $Python = $foundPython }
    }
}
$Python = Select-Tool $Python $previous.python 'python'
& $Python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) and sys.maxsize > 2**32 else 1)'
if ($LASTEXITCODE -ne 0) { throw '64-bit Python 3.10+ is required. Pass its absolute path with -Python.' }
$FFmpeg = Select-Tool $FFmpeg $previous.ffmpeg 'ffmpeg'
$FFprobe = Select-Tool $FFprobe $previous.ffprobe 'ffprobe'
$Codex = Select-Tool $Codex $previous.codex 'codex'

New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
$cpuUrl = "https://github.com/ggml-org/whisper.cpp/releases/download/$whisperRelease/whisper-bin-x64.zip"
if ($WhisperCpu -or ($previous.whisper_cpu -and (Test-Path -LiteralPath $previous.whisper_cpu -PathType Leaf))) {
    $WhisperCpu = Select-Tool $WhisperCpu $previous.whisper_cpu 'whisper-cli'
}
else {
    $WhisperCpu = Install-Whisper 'whisper-cpu' 'Release\whisper-cli.exe' $cpuUrl '49dcc16de826f20bd53d44f947a1ae49dfa81f86cad67a64d80820cb192d674a'
}

$managedVulkan = $false
if ($WhisperVulkan) {
    $WhisperVulkan = Get-ToolPath $WhisperVulkan
}
elseif (-not $DisableVulkan) {
    if ($EnableVulkan -or $previous.managed_vulkan) {
        $vulkanUrl = 'https://github.com/SubtitleEdit/support-files/releases/download/whispercpp-192/whisper-vulkan-x64.zip'
        $WhisperVulkan = Install-Whisper 'whisper-vulkan' 'whisper-cli.exe' $vulkanUrl 'a220f2063bf60c18406beb37d2001d7da6cfd2253d5b38b4c78719a56bedd2c0'
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
        $url = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$modelName"
        Save-VerifiedDownload $url $WhisperModel '394221709cd5ad1f40c46e6031ca61bce88931e6e088c294c6d5a55ffa7e2'
    }
}
$WhisperModel = (Resolve-Path -LiteralPath $WhisperModel).Path
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
}
$installation = [ordered]@{
    product = 'davinci-semantic-search'
    root = $InstallDir
    menu_script = $menuScript
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
Write-AtomicText (Join-Path $InstallDir 'installation.json') ($installation | ConvertTo-Json)
Write-AtomicText $configurationFile ($configuration | ConvertTo-Json)
Write-AtomicText $menuScript $script
Write-Output "Resolve script: $menuScript"
Write-Output "Index and logs: $DataDir"
