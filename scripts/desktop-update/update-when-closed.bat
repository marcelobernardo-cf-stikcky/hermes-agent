@echo off
setlocal EnableExtensions

rem Update Hermes only after the Desktop and local CLI/gateway processes exit.
rem This wrapper deliberately does not force-stop anything: close Hermes from
rem the Desktop first, then leave this window open while the update runs.
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..") do set "INSTALL_ROOT=%%~fI"
for %%I in ("%INSTALL_ROOT%\..") do set "HERMES_HOME=%%~fI"
set "UPDATE_SCRIPT=%INSTALL_ROOT%\scripts\desktop-update\windows.ps1"

if not exist "%UPDATE_SCRIPT%" (
    echo [ERRO] Script de update nao encontrado:
    echo        %UPDATE_SCRIPT%
    pause
    exit /b 2
)

echo Hermes precisa estar totalmente fechado para atualizar.
echo Aguardando os processos Hermes/Hermes CLI terminarem...

powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ^
  "$names = @('Hermes','hermes'); while (@(Get-Process -Name $names -ErrorAction SilentlyContinue).Count -gt 0) { Write-Host ('Ainda em execucao: ' + ((Get-Process -Name $names -ErrorAction SilentlyContinue | ForEach-Object { $_.ProcessName + ' (PID ' + $_.Id + ')' }) -join ', ')); Start-Sleep -Seconds 2 }"
if errorlevel 1 (
    echo [ERRO] Nao foi possivel confirmar que Hermes esta fechado.
    pause
    exit /b 3
)

echo Hermes fechado. Iniciando o update...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%UPDATE_SCRIPT%" -InstallRoot "%INSTALL_ROOT%" -Channel main -NoUi
set "UPDATE_EXIT=%ERRORLEVEL%"

echo.
if "%UPDATE_EXIT%"=="0" (
    echo [OK] Update concluido. Voce pode reabrir o Hermes agora.
) else (
    echo [ERRO] Update falhou com codigo %UPDATE_EXIT%.
    echo Revise o log em %HERMES_HOME%\logs\desktop-update-handoff.log antes de reabrir o Hermes.
)
pause
exit /b %UPDATE_EXIT%
