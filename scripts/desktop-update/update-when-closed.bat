@echo off
setlocal EnableExtensions

rem Promote the prepared local branch into the live install, then run the
rem Desktop updater against THAT branch (never -Channel main: on this fork it
rem would switch the checkout to origin/main and drop the local patches).
rem Nothing is force-stopped: close Hermes first and leave this window open.
if not defined HERMES_HOME set "HERMES_HOME=%LOCALAPPDATA%\hermes"
set "INSTALL_ROOT=%HERMES_HOME%\hermes-agent"
set "LIVE_BRANCH=local/delegated-kanban-guard"
set "PREPARED_BRANCH=update/prepared-upstream-20260925"
set "UPDATE_SCRIPT=%INSTALL_ROOT%\scripts\desktop-update\windows.ps1"

echo Hermes precisa estar totalmente fechado para atualizar.
echo Aguardando os processos Hermes/Hermes CLI terminarem...
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ^
  "$names = @('Hermes','hermes'); while (@(Get-Process -Name $names -ErrorAction SilentlyContinue).Count -gt 0) { Write-Host ('Ainda em execucao: ' + ((Get-Process -Name $names -ErrorAction SilentlyContinue | ForEach-Object { $_.ProcessName + ' (PID ' + $_.Id + ')' }) -join ', ')); Start-Sleep -Seconds 2 }"
if errorlevel 1 goto :fail_wait

for /f "delims=" %%B in ('git -C "%INSTALL_ROOT%" branch --show-current') do set "CURRENT_BRANCH=%%B"
if /i not "%CURRENT_BRANCH%"=="%LIVE_BRANCH%" (
    echo [ERRO] Instalacao esta na branch "%CURRENT_BRANCH%", esperado "%LIVE_BRANCH%". Nada foi alterado.
    goto :fail
)
git -C "%INSTALL_ROOT%" diff --quiet HEAD
if errorlevel 1 (
    echo [ERRO] Instalacao tem alteracoes locais nao commitadas. Nada foi alterado.
    goto :fail
)

echo Hermes fechado. Aplicando %PREPARED_BRANCH% (fast-forward apenas)...
git -C "%INSTALL_ROOT%" merge --ff-only "%PREPARED_BRANCH%"
if errorlevel 1 (
    echo [ERRO] Fast-forward recusado. Nada foi alterado.
    goto :fail
)

if not exist "%UPDATE_SCRIPT%" (
    echo [ERRO] Script de update nao encontrado: %UPDATE_SCRIPT%
    goto :fail
)
echo Sincronizando dependencias e rebuild do Desktop...
echo O update leva ~10-15 min e o log detalhado so aparece no fim. Uma linha "ainda rodando" a cada 30s prova que nao travou.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$p=Start-Process powershell.exe -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','\"%UPDATE_SCRIPT%\"','-InstallRoot','\"%INSTALL_ROOT%\"','-Branch','%LIVE_BRANCH%','-NoUi' -NoNewWindow -PassThru; $null=$p.Handle; $t=Get-Date; while(-not $p.WaitForExit(30000)){ '[{0:HH:mm}] ainda rodando ({1:N0} min) - nao abra o Hermes' -f (Get-Date),((Get-Date)-$t).TotalMinutes }; exit $p.ExitCode"
set "UPDATE_EXIT=%ERRORLEVEL%"

echo.
if "%UPDATE_EXIT%"=="0" (
    echo [OK] Update concluido. Voce pode reabrir o Hermes agora.
    pause
    exit /b 0
)
echo [ERRO] Update falhou com codigo %UPDATE_EXIT%.
echo Revise o log em %HERMES_HOME%\logs\desktop-update-handoff.log antes de reabrir o Hermes.
echo Para voltar: git -C "%INSTALL_ROOT%" reset --hard 2f9699f72b
pause
exit /b %UPDATE_EXIT%

:fail_wait
echo [ERRO] Nao foi possivel confirmar que Hermes esta fechado.
:fail
pause
exit /b 3
