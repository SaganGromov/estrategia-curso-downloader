@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

where py >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERRO: Python nao foi encontrado.
    echo Instale o Python 3 em https://www.python.org/downloads/windows/
    echo e marque a opcao "Add Python to PATH" durante a instalacao.
    echo.
    pause
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
        exit /b 1
    )
)

py estrategia_download_edge_any.py %*
set "CODIGO_SAIDA=%errorlevel%"
echo.
if not "%CODIGO_SAIDA%"=="0" echo O programa terminou com erro. Veja a mensagem acima.
pause
exit /b %CODIGO_SAIDA%
