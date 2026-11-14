# Tests the Windows installer end to end, against the stand-in for Strata (tools\fake_strata). No real Strata is used.
#
#   packaging\windows\test-installer.ps1 -Msi out\StrataLauncher-0.1.0-x64.msi [-NewerMsi out\StrataLauncher-0.1.1-x64.msi] [-Python python]
#
# It installs the .msi for the signed-in person, checks what it put where, runs the installed program's self-test,
# upgrades to the newer .msi while the launcher is open (when one is given), uninstalls, and checks nothing is left
# except the settings. It ends with code 1 if any check failed.
param(
    [Parameter(Mandatory = $true)][string]$Msi,
    [string]$NewerMsi = "",
    [string]$Python = "python",     # only for the stand-in Strata, which is a Python script
    [string]$Work = ""
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $Work) { $Work = Join-Path ([IO.Path]::GetTempPath()) "strata-launcher-installer-test" }
if (Test-Path $Work) { Remove-Item -Recurse -Force $Work }
New-Item -ItemType Directory -Force $Work | Out-Null
$Msi = (Resolve-Path $Msi).Path
if ($NewerMsi) { $NewerMsi = (Resolve-Path $NewerMsi).Path }

$install = Join-Path $env:LOCALAPPDATA "Programs\Strata Launcher"
$exe = Join-Path $install "StrataLauncher.exe"
$shortcut = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Strata Launcher.lnk"
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$settings = Join-Path $env:APPDATA "Strata Launcher"
$failed = @()

function Check([string]$name, [bool]$ok) {
    if ($ok) { Write-Host "ok:     $name" } else { $script:failed += $name; Write-Host "FAILED: $name" }
}
function RunMsi([string[]]$arguments) {
    (Start-Process msiexec.exe -ArgumentList $arguments -Wait -PassThru).ExitCode
}
function Products {
    # What Windows Installer itself knows about: every installed product called "Strata Launcher".
    # Context 0 means installed for this person only; 1 would mean for all users of the PC.
    $installer = New-Object -ComObject WindowsInstaller.Installer
    $type = $installer.GetType()
    $codes = @($type.InvokeMember("Products", "GetProperty", $null, $installer, $null)) | ForEach-Object { [string]$_ }
    foreach ($code in $codes) {
        try { $name = [string]$type.InvokeMember("ProductInfo", "GetProperty", $null, $installer, @($code, "ProductName")) } catch { continue }
        if ($name -eq "Strata Launcher") {
            [pscustomobject]@{
                Code = $code
                Version = [string]$type.InvokeMember("ProductInfo", "GetProperty", $null, $installer, @($code, "VersionString"))
                Context = [string]$type.InvokeMember("ProductInfo", "GetProperty", $null, $installer, @($code, "AssignmentType"))
            }
        }
    }
}
function RunValue { (Get-ItemProperty $runKey -ErrorAction SilentlyContinue)."Strata Launcher" }
function MsiVersion([string]$path) {
    $installer = New-Object -ComObject WindowsInstaller.Installer
    $db = $installer.GetType().InvokeMember("OpenDatabase", "InvokeMethod", $null, $installer, @($path, 0))
    $view = $db.GetType().InvokeMember("OpenView", "InvokeMethod", $null, $db, @("SELECT Value FROM Property WHERE Property = 'ProductVersion'"))
    $view.GetType().InvokeMember("Execute", "InvokeMethod", $null, $view, $null) | Out-Null
    $record = $view.GetType().InvokeMember("Fetch", "InvokeMethod", $null, $view, $null)
    $record.GetType().InvokeMember("StringData", "GetProperty", $null, $record, 1)
}

Write-Host "--- before"
Check "not installed before the test" (-not (Test-Path $exe) -and -not @(Products).Count)
$hadSettings = Test-Path $settings
if (-not $hadSettings) { New-Item -ItemType Directory -Force $settings | Out-Null }
$marker = Join-Path $settings "installer-test-marker.txt"
Set-Content -LiteralPath $marker -Value "kept"

Write-Host "--- install $(Split-Path $Msi -Leaf)"
$code = RunMsi @("/i", "`"$Msi`"", "/qn", "/norestart", "/l*v", "`"$Work\install.log`"")
Check "the installer ended with code 0 (it was $code)" ($code -eq 0)
Check "the program is in $install" (Test-Path $exe)
Check "the Start menu entry exists" (Test-Path $shortcut)
$found = @(Products)
Check "Windows lists it once, as version $(MsiVersion $Msi)" ($found.Count -eq 1 -and $found[0].Version -eq (MsiVersion $Msi))
Check "it is installed for this person only, not for all users" ($found.Count -eq 1 -and $found[0].Context -eq "0" -and -not (Test-Path "$env:ProgramFiles\Strata Launcher"))
Check "start at sign-in is off after an install" (-not (RunValue))

Write-Host "--- the installed program's self-test (against the stand-in for Strata)"
$env:STRATA_LAUNCHER_CONFIG_DIR = Join-Path $Work "settings"
$env:FAKE_STRATA_HOME = Join-Path $Work "fake"
$env:FAKE_STRATA_PYTHON = $Python
& $Python (Join-Path $root "tools\make_demo_config.py") $env:STRATA_LAUNCHER_CONFIG_DIR | Out-Null
$p = Start-Process $exe -ArgumentList "--selftest", "--selftest-seconds", "4" -PassThru
$null = $p.Handle                                            # keeps the exit code readable after it ends
if (-not $p.WaitForExit(180000)) { Stop-Process -Id $p.Id -Force }
# Whatever happened, the stand-in server must not stay behind.
& $Python (Join-Path $root "tools\fake_strata\fake_strata.py") launch --stop | Out-Null
$report = Join-Path $env:STRATA_LAUNCHER_CONFIG_DIR "logs\selftest.txt"
if (Test-Path $report) { Get-Content $report | ForEach-Object { Write-Host "    $_" } }
Check "the self-test ended with code 0 (it was $($p.ExitCode))" ($p.ExitCode -eq 0)
Check "the self-test says PASS" ((Test-Path $report) -and ((Get-Content $report -Raw) -match "selftest: PASS"))

# What the launcher's own switch writes; it must survive an upgrade and be removed by an uninstall.
New-ItemProperty -Path $runKey -Name "Strata Launcher" -Value "`"$exe`" --at-login" -PropertyType String -Force | Out-Null

$last = $Msi
if ($NewerMsi) {
    Write-Host "--- upgrade to $(Split-Path $NewerMsi -Leaf) while the launcher is open"
    $open = Start-Process $exe -PassThru
    Start-Sleep -Seconds 6
    Check "the launcher is open before the upgrade" (-not $open.HasExited)
    $code = RunMsi @("/i", "`"$NewerMsi`"", "/qn", "/norestart", "/l*v", "`"$Work\upgrade.log`"")
    Check "the upgrade ended with code 0 (it was $code)" ($code -eq 0)
    Start-Sleep -Seconds 2
    Check "the open launcher was closed by the upgrade" ($open.HasExited)
    $found = @(Products)
    Check "Windows lists it once, now as version $(MsiVersion $NewerMsi)" ($found.Count -eq 1 -and $found[0].Version -eq (MsiVersion $NewerMsi))
    Check "it is still installed for this person only" ($found.Count -eq 1 -and $found[0].Context -eq "0")
    Check "the program is still in place" (Test-Path $exe)
    Check "start at sign-in is still on after the upgrade" ([bool](RunValue))
    Check "the settings of the test are still there" (Test-Path (Join-Path $env:STRATA_LAUNCHER_CONFIG_DIR "config.json"))
    $last = $NewerMsi
}

Write-Host "--- uninstall"
$open = Start-Process $exe -PassThru
Start-Sleep -Seconds 5
$code = RunMsi @("/x", "`"$last`"", "/qn", "/norestart", "/l*v", "`"$Work\uninstall.log`"")
Check "the uninstall ended with code 0 (it was $code)" ($code -eq 0)
Start-Sleep -Seconds 2
Check "the open launcher was closed by the uninstall" ($open.HasExited)
Check "the program folder is gone" (-not (Test-Path $install))
Check "the Start menu entry is gone" (-not (Test-Path $shortcut))
Check "Windows no longer lists it" (-not @(Products).Count)
Check "the start-at-sign-in entry is gone" (-not (RunValue))
Check "the person's settings folder was kept" (Test-Path $marker)
Check "no launcher is still running" (-not (Get-Process StrataLauncher -ErrorAction SilentlyContinue))

Remove-Item -LiteralPath $marker -Force -ErrorAction SilentlyContinue
if (-not $hadSettings) { Remove-Item -LiteralPath $settings -Recurse -Force -ErrorAction SilentlyContinue }
Remove-Item Env:\STRATA_LAUNCHER_CONFIG_DIR, Env:\FAKE_STRATA_HOME, Env:\FAKE_STRATA_PYTHON -ErrorAction SilentlyContinue

Write-Host ""
if ($failed.Count) {
    Write-Host "$($failed.Count) check(s) failed. The installer logs are in $Work"
    exit 1
}
Write-Host "Every check passed."
exit 0
