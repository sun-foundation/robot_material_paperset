param(
    [Parameter(Mandatory=$true)][string]$PythonPath,
    [switch]$StartNow
)
$ErrorActionPreference='Stop'
$taskRepo=Split-Path -Parent $PSScriptRoot
$taskScript=Join-Path $PSScriptRoot 'watch_library.py'
$taskConfig=Join-Path $taskRepo '.local\config.json'
if(!(Test-Path -LiteralPath $taskConfig)){throw 'Missing .local/config.json'}
if(!(Test-Path -LiteralPath $PythonPath)){throw 'Python runtime missing'}
$taskIdentity=[System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$taskName='RobotMaterialPaperset_LocalSync'
$taskExisting=Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if($taskExisting -and !($taskExisting.Actions.Arguments -like ('*'+$taskScript+'*'))){throw 'A different task already uses this name; nothing was overwritten'}
$taskArgs='"'+$taskScript+'" --config "'+$taskConfig+'"'
$taskAction=New-ScheduledTaskAction -Execute $PythonPath -Argument $taskArgs -WorkingDirectory $taskRepo
$taskTrigger=New-ScheduledTaskTrigger -AtLogOn -User $taskIdentity
$taskPrincipal=New-ScheduledTaskPrincipal -UserId $taskIdentity -LogonType Interactive -RunLevel Limited
$taskSettings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 2)
Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $taskTrigger -Principal $taskPrincipal -Settings $taskSettings -Description 'Watch this local literature library and sync its curated public exports to sun-foundation/robot_material_paperset.' -Force | Out-Null
if($StartNow){Start-ScheduledTask -TaskName $taskName}
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName,State
