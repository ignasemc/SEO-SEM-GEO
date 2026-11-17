@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Triada - Conectar con Claude

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto sinpython
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 goto sinpython

echo.
echo   Preparando el conector de Triada para Claude. Tarda 1 o 2 minutos...
echo.
if not exist ".venv\Scripts\python.exe" %PY% -m venv .venv
if errorlevel 1 goto fallo
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -e ".[claude]" -q
if errorlevel 1 goto fallo
".venv\Scripts\python.exe" -m triada.cli conectar-claude
pause
exit /b 0

:sinpython
echo.
echo   Triada necesita Python 3.11 o superior y no se ha encontrado.
echo   1. Se abrira la pagina de descarga de Python.
echo   2. Durante la instalacion marca la casilla "Add python.exe to PATH".
echo   3. Vuelve a hacer doble clic en este archivo.
echo.
start https://www.python.org/downloads/
pause
exit /b 1

:fallo
echo.
echo   No se pudieron instalar los componentes. Comprueba tu conexion a internet
echo   y vuelve a intentarlo.
pause
exit /b 1
