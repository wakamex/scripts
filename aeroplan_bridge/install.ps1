# Registers the Aeroplan bridge native messaging host for the current user, writes the browser
# launcher, and adds it to the Startup folder so the bridge browser opens at logon.
# Run from the folder that contains extension\ and host\:  powershell -File install.ps1
$ErrorActionPreference = "Stop"
$Name = "ca.mihaicosma.aeroplan_bridge"
$ExtensionId = "jeajfjhfhihlkjmbojgepccpjdolejfc"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = (Get-Command python).Source

$Launcher = Join-Path $Root "host\run_host.bat"
# -I ignores PYTHONHOME/PYTHONPATH a parent process may pass down (a mismatched stdlib crashes the host).
# stderr goes to a file because Chrome discards it, and a crash before logging would leave no trace.
Set-Content -Encoding ASCII -Path $Launcher -Value "@echo off`r`n`"$Python`" -I -u `"%~dp0aeroplan_bridge_host.py`" %* 2>> `"%LOCALAPPDATA%\aeroplan_bridge\host.err`""

$Manifest = Join-Path $Root "host\$Name.json"
@{
    name = $Name
    description = "Aeroplan bridge host"
    path = $Launcher
    type = "stdio"
    allowed_origins = @("chrome-extension://$ExtensionId/")
} | ConvertTo-Json | ForEach-Object { [IO.File]::WriteAllText($Manifest, $_, (New-Object Text.UTF8Encoding $false)) }  # Chrome rejects a UTF-8 BOM

# Chrome, Chrome for Testing and Chromium each read their own registry key.
foreach ($Browser in "Google\Chrome", "Google\Chrome for Testing", "Chromium") {
    $Key = "HKCU:\Software\$Browser\NativeMessagingHosts\$Name"
    New-Item -Path $Key -Force | Out-Null
    Set-ItemProperty -Path $Key -Name "(default)" -Value $Manifest
}
"registered $Name -> $Manifest (python $Python)"

# Chrome for Testing still accepts --load-extension, which branded Chrome no longer does.
$Chrome = Get-ChildItem (Join-Path $Root "browser") -Recurse -Filter chrome.exe | Sort-Object FullName -Descending | Select-Object -First 1
if (-not $Chrome) { throw "no Chrome for Testing under $Root\browser; run: npx @puppeteer/browsers install chrome@stable --path $Root\browser" }
$Start = Join-Path $Root "launch_browser.bat"
$Args = "--user-data-dir=`"$Root\chrome-data`" --load-extension=`"$Root\extension`" --no-first-run --no-default-browser-check"
Set-Content -Encoding ASCII -Path $Start -Value "@echo off`r`nstart `"`" `"$($Chrome.FullName)`" $Args https://www.aircanada.com/aeroplan/redeem/availability/outbound?org0=YOW^&dest0=YVR^&departureDate0=2026-12-01^&ADT=1^&YTH=0^&CHD=0^&INF=0^&INS=0^&lang=en-CA^&tripType=O"

$Shell = New-Object -ComObject WScript.Shell
$Link = $Shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Startup")) "Aeroplan bridge.lnk"))
$Link.TargetPath = $Start
$Link.WindowStyle = 7  # minimized
$Link.Save()
"launcher $Start (browser $($Chrome.FullName)); Startup shortcut added"
