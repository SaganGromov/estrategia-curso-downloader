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

where py >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERRO: Python nao foi encontrado.
    echo Instale o Python 3 em https://www.python.org/downloads/windows/
    echo e marque a opcao "Add Python to PATH" durante a instalacao.
    echo.
    pause
    popd
    exit /b 1
)

py -c "import requests, selenium" >nul 2>&1
if errorlevel 1 (
    echo Instalando as dependencias necessarias...
    py -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo ERRO: nao foi possivel instalar as dependencias.
        pause
        popd
        exit /b 1
    )
)

echo.
echo Abrindo o painel local no Microsoft Edge...
py estrategia_download_edge_any.py %*
set "CODIGO_SAIDA=%errorlevel%"
if not "%CODIGO_SAIDA%"=="0" (
    echo.
    echo O programa terminou com erro. Veja a mensagem acima.
    pause
)
popd
exit /b %CODIGO_SAIDA%
