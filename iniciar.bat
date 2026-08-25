@echo off
setlocal
chcp 65001 >nul
title Estrategia Curso Downloader
pushd "%~dp0" 2>nul
if errorlevel 1 (
    echo ERRO: nao foi possivel acessar a pasta do programa.
    pause
    exit /b 1
)

echo.
echo Iniciando a preparacao automatica...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap.ps1" %*
set "CODIGO_SAIDA=%errorlevel%"
if not "%CODIGO_SAIDA%"=="0" (
    echo.
    echo Nao foi possivel iniciar. Veja a explicacao acima.
    pause
)
popd
exit /b %CODIGO_SAIDA%
