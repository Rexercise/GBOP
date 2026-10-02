# Run in a normal PowerShell as the Windows user who runs MT5.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (!(Test-Path 'config.json')) { throw 'Create config.json from config.example.json first, with exact MT5 symbols and the private bridge token.' }
py -3 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Install Python 3 x64 from python.org first.' }
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& '.\.venv\Scripts\python.exe' bridge.py --once
if ($LASTEXITCODE -ne 0) { throw 'Live bridge verification failed; no task was installed. Check bridge.log.' }
$Python = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
$Script = Join-Path $PSScriptRoot 'bridge.py'
$Config = Join-Path $PSScriptRoot 'config.json'
$User = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
# Keep token readable only to this user and local SYSTEM/Administrators.
icacls $Config /inheritance:r /grant:r "${User}:(F)" '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not restrict config permissions.' }
$Action = New-ScheduledTaskAction -Execute $Python -Argument "`"$Script`" --config `"$Config`"" -WorkingDirectory $PSScriptRoot
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
$Principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'GBOP Market Bridge' -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Force | Out-Null
Start-ScheduledTask -TaskName 'GBOP Market Bridge'
Write-Host 'Verified and started GBOP Market Bridge. Disconnect RDP instead of signing out. After reboot, log into Windows once; the task resumes automatically.'
