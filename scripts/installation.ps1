function Get-DefaultScriptsDir {
    Join-Path $env:APPDATA 'Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility'
}

function Get-DefaultInstallDir {
    Join-Path $env:LOCALAPPDATA 'DavinciSemanticSearch'
}

function Get-MenuScriptPath([string] $Directory) {
    Join-Path $Directory 'Davinci Semantic Search.py'
}

function Get-InstallPath([string] $Directory) {
    $path = [IO.Path]::GetFullPath($Directory)
    if ($path -eq [IO.Path]::GetPathRoot($path)) {
        throw 'Use a dedicated installation directory, such as the default DavinciSemanticSearch directory.'
    }
    $path.TrimEnd('\', '/')
}

function Read-LauncherValue([string] $Source, [string] $Name) {
    $match = [regex]::Match($Source, "(?m)^${Name} = (`"[^\r\n]*`")\r?$")
    if ($match.Success) {
        ConvertFrom-Json -InputObject $match.Groups[1].Value
    }
}

function Read-Launcher([string] $Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return
    }
    $source = [IO.File]::ReadAllText($Path)
    $config = Read-LauncherValue $source 'CONFIG_FILE'
    if (-not $config -or -not ($source.Contains('from davinci_search.window import open_window'))) {
        throw "The menu script is not managed by Davinci Semantic Search: $Path"
    }
    @{
        ConfigFile = $config
        InstallDir = Read-LauncherValue $source 'INSTALL_ROOT'
    }
}

function Write-AtomicText([string] $Path, [string] $Text) {
    $temporary = "$Path.$([guid]::NewGuid().ToString('N')).tmp"
    try {
        [IO.File]::WriteAllText($temporary, $Text, [Text.UTF8Encoding]::new($false))
        if ([IO.File]::Exists($Path)) {
            [IO.File]::Replace($temporary, $Path, [NullString]::Value)
        }
        else {
            [IO.File]::Move($temporary, $Path)
        }
    }
    finally {
        if ([IO.File]::Exists($temporary)) {
            [IO.File]::Delete($temporary)
        }
    }
}

function Get-ManagedInstall([string] $Directory) {
    $marker = Join-Path $Directory 'installation.json'
    if (Test-Path -LiteralPath $marker -PathType Leaf) {
        $data = Get-Content -LiteralPath $marker -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($data.product -ne 'davinci-semantic-search' -or $data.root -ne $Directory) {
            throw "Invalid Davinci Semantic Search installation record: $marker"
        }
        $data
    }
}

function Test-IsBelow([string] $Path, [string] $Directory) {
    $prefix = $Directory.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    $Path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
}
