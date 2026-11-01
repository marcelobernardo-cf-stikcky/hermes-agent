@echo off
setlocal EnableExtensions
title Hermes update - NAO FECHE ESTA JANELA

rem Closed-app update for this fork, fully driven from here:
rem   1. closes Hermes (graceful, then forced after a grace period)
rem   2. fast-forwards LIVE_BRANCH to PREPARED_BRANCH (refuses anything else)
rem   3. runs the official Desktop updater against LIVE_BRANCH (never -Channel
rem      main: on this fork that would switch to origin/main and drop patches)
rem   4. reopens Hermes and closes itself on success.
rem Needs update-when-closed.ps1 beside it. Run a COPY outside the repo: step 2 rewrites this file inside the checkout,
rem and cmd.exe reads a running .bat incrementally.
rem Usage: update-when-closed.bat <prepared-branch>
if not defined HERMES_HOME set "HERMES_HOME=%LOCALAPPDATA%\hermes"
set "INSTALL_ROOT=%HERMES_HOME%\hermes-agent"
set "LIVE_BRANCH=local/delegated-kanban-guard"
set "PREPARED_BRANCH=%~1"
set "UPDATE_SCRIPT=%INSTALL_ROOT%\scripts\desktop-update\windows.ps1"
set "DESKTOP_EXE=%INSTALL_ROOT%\apps\desktop\release\win-unpacked\Hermes.exe"
set "GRACE_SECONDS=60"
set "PS_HELPER=%~dp0update-when-closed.ps1"
if not exist "%PS_HELPER%" (
    echo [ERRO] Falta %PS_HELPER% ao lado deste .bat. Nada foi alterado.
    goto :fail
)
if "%PREPARED_BRANCH%"=="" (
    echo [ERRO] Uso: %~nx0 ^<branch-preparada^>
    goto :fail
)

for /f "delims=" %%B in ('git -C "%INSTALL_ROOT%" branch --show-current') do set "CURRENT_BRANCH=%%B"
if /i not "%CURRENT_BRANCH%"=="%LIVE_BRANCH%" (
    echo [ERRO] Instalacao esta na branch "%CURRENT_BRANCH%", esperado "%LIVE_BRANCH%". Nada foi alterado.
    goto :fail
)
git -C "%INSTALL_ROOT%" merge-base --is-ancestor HEAD "%PREPARED_BRANCH%"
if errorlevel 1 (
    echo [ERRO] %PREPARED_BRANCH% nao e fast-forward de %LIVE_BRANCH%. Nada foi alterado.
    goto :fail
)
for /f "delims=" %%S in ('git -C "%INSTALL_ROOT%" rev-parse --short HEAD') do set "ROLLBACK_SHA=%%S"

echo [1/4] Fechando o Hermes (ate %GRACE_SECONDS%s com calma, depois forcado)...
rem The Tailscale serve loop (serve-remote.cmd) respawns hermes.exe 15s after it dies,
rem i.e. mid-update: stop the loop first and restart it at the end if it was running.
set "SERVE_REMOTE_VBS=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\hermes-serve-remote.vbs"
set "SERVE_REMOTE_WAS_RUNNING="
for /f %%C in ('powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%PS_HELPER%" -Op StopServeLoop') do if not "%%C"=="0" set "SERVE_REMOTE_WAS_RUNNING=1"
if defined SERVE_REMOTE_WAS_RUNNING echo   loop serve-remote (Tailscale) pausado; volta no fim.
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%PS_HELPER%" -Op CloseHermes -GraceSeconds %GRACE_SECONDS%
if errorlevel 1 (
    echo [ERRO] Nao consegui fechar todos os processos Hermes. Nada foi alterado.
    goto :reopen_fail
)

git -C "%INSTALL_ROOT%" diff --quiet HEAD
if errorlevel 1 (
    echo [ERRO] Instalacao tem alteracoes locais nao commitadas. Nada foi alterado.
    goto :reopen_fail
)
echo [2/4] Aplicando %PREPARED_BRANCH% (fast-forward apenas; rollback = %ROLLBACK_SHA%)...
git -C "%INSTALL_ROOT%" tag -f "pre-update-%ROLLBACK_SHA%" "%ROLLBACK_SHA%" >nul
git -C "%INSTALL_ROOT%" merge --ff-only "%PREPARED_BRANCH%"
if errorlevel 1 (
    echo [ERRO] Fast-forward recusado. Nada foi alterado.
    goto :reopen_fail
)

echo [3/4] Dependencias + rebuild do Desktop (~10-20 min). Uma linha a cada 30s prova que nao travou.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$p=Start-Process powershell.exe -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','\"%UPDATE_SCRIPT%\"','-InstallRoot','\"%INSTALL_ROOT%\"','-Branch','%LIVE_BRANCH%','-NoUi' -NoNewWindow -PassThru; $null=$p.Handle; $t=Get-Date; while(-not $p.WaitForExit(30000)){ '  [{0:HH:mm}] ainda rodando ({1:N0} min) - nao abra o Hermes' -f (Get-Date),((Get-Date)-$t).TotalMinutes }; exit $p.ExitCode"
set "UPDATE_EXIT=%ERRORLEVEL%"

echo.
if "%UPDATE_EXIT%"=="0" (
    echo [4/4] Update concluido. Reabrindo o Hermes...
    call :restart_serve_remote
    if exist "%DESKTOP_EXE%" start "" "%DESKTOP_EXE%"
    echo Esta janela fecha sozinha em 20s.
    timeout /t 20 >nul
    exit /b 0
)
echo [ERRO] Update falhou com codigo %UPDATE_EXIT%.
echo Log: %HERMES_HOME%\logs\desktop-update-handoff.log
echo Voltar ao estado anterior: git -C "%INSTALL_ROOT%" reset --hard pre-update-%ROLLBACK_SHA%
goto :reopen_fail

:restart_serve_remote
if defined SERVE_REMOTE_WAS_RUNNING if exist "%SERVE_REMOTE_VBS%" (
    echo   religando o loop serve-remote ^(Tailscale^)...
    start "" wscript.exe "%SERVE_REMOTE_VBS%"
)
exit /b 0

:reopen_fail
echo Reabrindo o Hermes para voce nao ficar sem ele...
call :restart_serve_remote
if exist "%DESKTOP_EXE%" start "" "%DESKTOP_EXE%"
:fail
pause
exit /b 3
