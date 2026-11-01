# Process steps for update-when-closed.bat, kept out of the .bat because cmd.exe
# mangles PowerShell's % and quotes inside -Command strings.
#   -Op StopServeLoop : stop the Tailscale serve-remote.cmd loop; prints how many were running
#   -Op CloseHermes   : close Hermes windows gracefully, force what is left after -GraceSeconds;
#                       exit 1 when something survives
# -DryRun reports what would happen and changes nothing.
param(
    [Parameter(Mandatory = $true)][ValidateSet('StopServeLoop', 'CloseHermes')][string]$Op,
    [int]$GraceSeconds = 60,
    [switch]$DryRun
)

function Format-Procs($procs) { ($procs | ForEach-Object { '{0}({1})' -f $_.ProcessName, $_.Id }) -join ', ' }

if ($Op -eq 'StopServeLoop') {
    $loops = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'cmd.exe' -and $_.CommandLine -like '*serve-remote.cmd*' })
    if (-not $DryRun) { $loops | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } }
    Write-Output $loops.Count
    exit 0
}

$names = @('Hermes', 'hermes')
$running = @(Get-Process -Name $names -ErrorAction SilentlyContinue)
if ($DryRun) { Write-Output ('  fecharia: ' + (Format-Procs $running)); exit 0 }

$running | Where-Object { $_.MainWindowHandle -ne 0 } | ForEach-Object { [void]$_.CloseMainWindow() }
$start = Get-Date
while (@(Get-Process -Name $names -ErrorAction SilentlyContinue).Count -gt 0 -and ((Get-Date) - $start).TotalSeconds -lt $GraceSeconds) {
    Start-Sleep -Seconds 3
    Write-Output ('  aguardando: ' + (Format-Procs @(Get-Process -Name $names -ErrorAction SilentlyContinue)))
}
$left = @(Get-Process -Name $names -ErrorAction SilentlyContinue)
if ($left.Count) {
    Write-Output ('  forcando: ' + (Format-Procs $left))
    $left | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
}
if (@(Get-Process -Name $names -ErrorAction SilentlyContinue).Count) { exit 1 }
exit 0
