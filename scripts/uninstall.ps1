[CmdletBinding(SupportsShouldProcess)]
param(
    [string] $InstallDir,
    [string] $ScriptsDir
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'installation.ps1')

if (-not $ScriptsDir) { $ScriptsDir = Get-DefaultScriptsDir }
$menuScript = Get-MenuScriptPath ([IO.Path]::GetFullPath($ScriptsDir))
$launcher = Read-Launcher $menuScript
if (-not $InstallDir) {
    $InstallDir = if ($launcher.InstallDir) { $launcher.InstallDir } else { Get-DefaultInstallDir }
}
$InstallDir = Get-InstallPath $InstallDir
if ($launcher.InstallDir -and $launcher.InstallDir -ne $InstallDir) {
    throw "This menu belongs to a different installation: $($launcher.InstallDir)"
}

$record = Get-ManagedInstall $InstallDir
if ($record -and $record.menu_script -ne $menuScript) {
    throw "This installation belongs to a different Resolve script directory: $($record.menu_script)"
}
if ($launcher.InstallDir -and -not $record) {
    throw "Installation record is missing: $InstallDir"
}

function Assert-LocalDirectory([IO.DirectoryInfo] $Directory) {
    if ($Directory.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Refusing to remove through a reparse point: $($Directory.FullName)"
    }
}

$targets = @()
if ($record) {
    $root = Get-Item -LiteralPath $InstallDir
    Assert-LocalDirectory $root
    $targets = @(Get-ChildItem -LiteralPath $root.FullName -Directory |
        Where-Object Name -Match '^app-[0-9a-f]{32}$')
    $runtime = Join-Path $root.FullName 'runtime'
    if (Test-Path -LiteralPath $runtime -PathType Container) {
        $directory = Get-Item -LiteralPath $runtime
        Assert-LocalDirectory $directory
        $targets += @(Get-ChildItem -LiteralPath $directory.FullName -Force |
            Where-Object {
                ($_.PSIsContainer -and $_.Name -match '^whisper-(cpu|vulkan)-v\d+\.\d+\.\d+(\.[0-9a-f]{32}\.staging)?$') -or
                (-not $_.PSIsContainer -and $_.Name -match '^whisper-(cpu|vulkan)-v\d+\.\d+\.\d+\.zip$')
            })
    }
    $config = Join-Path $root.FullName 'settings.json'
    $settings = if (Test-Path -LiteralPath $config -PathType Leaf) {
        Get-Content -LiteralPath $config -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    $protected = @($settings.data_dir, $settings.whisper_model, $menuScript) |
        Where-Object { $_ } | ForEach-Object { [IO.Path]::GetFullPath($_).TrimEnd('\', '/') }
    foreach ($target in $targets) {
        $path = (Resolve-Path -LiteralPath $target.FullName).Path
        if (-not (Test-IsBelow $path $root.FullName)) { throw "Unexpected removal path: $path" }
        if ($protected | Where-Object { $_ -eq $path -or (Test-IsBelow $_ $path) }) {
            throw "The installer files overlap a preserved path: $path"
        }
        if ($target.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Refusing to remove a reparse point: $path"
        }
        if ($target.PSIsContainer -and (Get-ChildItem -LiteralPath $path -Recurse -Force -Attributes ReparsePoint |
                Select-Object -First 1)) {
            throw "Refusing to remove a directory containing a reparse point: $path"
        }
    }
}

if ($launcher -and $PSCmdlet.ShouldProcess($menuScript, 'Remove Resolve menu entry')) {
    Remove-Item -LiteralPath $menuScript
}
$exportShortcut = Join-Path $InstallDir 'Davinci Semantic Search - Export logs.lnk'
if ($record.export_shortcut -eq $exportShortcut -and (Test-Path -LiteralPath $exportShortcut -PathType Leaf) -and
        $PSCmdlet.ShouldProcess($exportShortcut, 'Remove the log export shortcut')) {
    Remove-Item -LiteralPath $exportShortcut
}
foreach ($target in $targets) {
    if ($PSCmdlet.ShouldProcess($target.FullName, 'Remove installed plugin files')) {
        Remove-Item -LiteralPath $target.FullName -Recurse -Force
    }
}
Write-Output 'Project indexes, transcripts, media and the model are preserved.'
