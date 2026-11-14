# Builds the Windows installer: out\StrataLauncher-<version>-x64.msi
#
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
#
# Needs: Python 3.10 or newer, and the .NET SDK 6 or newer (for the WiX tool). It installs PyInstaller and this
# project into a build environment of its own (out\venv) and WiX 5 as a .NET tool. Nothing else on the PC changes.
param(
    [string]$Python = "python",
    [string]$Version = "",          # default: the version in src\strata_launcher\__init__.py
    [string]$OutDir = "",           # default: out\ in the project folder
    [string]$ProjectUrl = "",       # shown under "Apps" in Windows' settings; optional
    [string]$WixVersion = "5.0.2",
    [string]$AppDir = ""            # a program folder PyInstaller already made: only the .msi is built (used to make a test upgrade)
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $OutDir) { $OutDir = Join-Path $root "out" }
New-Item -ItemType Directory -Force $OutDir | Out-Null

function Run($exe, [string[]]$arguments) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "$exe $($arguments -join ' ') ended with code $LASTEXITCODE" }
}

if (-not $Version) {
    $init = Get-Content (Join-Path $root "src\strata_launcher\__init__.py") -Raw
    if ($init -notmatch '__version__ = "([^"]+)"') { throw "No version found in __init__.py" }
    $Version = $Matches[1]
}
Write-Host "Building Strata Launcher $Version"

# 1. The program as a folder that runs without Python.
$dist = Join-Path $OutDir "dist"
$app = Join-Path $dist "StrataLauncher"
if ($AppDir) {
    $app = (Resolve-Path $AppDir).Path
} else {
    $venv = Join-Path $OutDir "venv"
    if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) { Run $Python @("-m", "venv", $venv) }
    $py = Join-Path $venv "Scripts\python.exe"
    Run $py @("-m", "pip", "install", "--quiet", "--upgrade", "pip")
    Run $py @("-m", "pip", "install", "--quiet", "--upgrade", "--force-reinstall", "--no-deps", $root)
    Run $py @("-m", "pip", "install", "--quiet", $root, "pyinstaller>=6.6")
    Run $py @("-m", "PyInstaller", (Join-Path $root "packaging\strata-launcher.spec"), "--noconfirm", "--distpath", $dist, "--workpath", (Join-Path $OutDir "build"))
}
if (-not (Test-Path (Join-Path $app "StrataLauncher.exe"))) { throw "PyInstaller did not make StrataLauncher.exe" }

# 2. WiX, as a .NET tool kept in the project's out\tools (not installed for the whole PC).
$tools = Join-Path $root "out\tools"
$wix = Join-Path $tools "wix.exe"
if (-not (Test-Path $wix)) { Run "dotnet" @("tool", "install", "wix", "--version", $WixVersion, "--tool-path", $tools) }
Run $wix @("extension", "add", "WixToolset.Util.wixext/$WixVersion")

# 3. The installer.
$msi = Join-Path $OutDir "StrataLauncher-$Version-x64.msi"
$wixArgs = @("build", (Join-Path $PSScriptRoot "Package.wxs"), "-arch", "x64", "-ext", "WixToolset.Util.wixext",
             "-d", "Version=$Version", "-d", "SourceDir=$app", "-d", "IconFile=$(Join-Path $root 'src\strata_launcher\assets\icon.ico')",
             "-o", $msi)
if ($ProjectUrl) { $wixArgs += @("-d", "ProjectUrl=$ProjectUrl") }
Run $wix $wixArgs
Write-Host "Made $msi ($([math]::Round((Get-Item $msi).Length / 1MB, 1)) MB)"
