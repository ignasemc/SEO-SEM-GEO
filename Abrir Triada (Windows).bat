@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Triada - SEO, SEM y GEO

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto sinpython
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 goto sinpython

if exist ".venv\Scripts\python.exe" goto comprobar
echo.
echo   Preparando Triada por primera vez. Tarda 1 o 2 minutos...
echo.
%PY% -m venv .venv
if errorlevel 1 goto fallo
goto instalar

:comprobar
rem Si Triada se ha actualizado (pyproject.toml distinto), se instalan los componentes nuevos
if not exist ".venv\triada-instalado.toml" goto actualizar
fc /b pyproject.toml ".venv\triada-instalado.toml" >nul 2>nul
if errorlevel 1 goto actualizar
goto arrancar

:actualizar
echo.
echo   Actualizando los componentes de Triada. Tarda 1 o 2 minutos...
echo.

:instalar
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -e . -q
if errorlevel 1 goto fallo
copy /y pyproject.toml ".venv\triada-instalado.toml" >nul

:arrancar
".venv\Scripts\python.exe" -m triada.app
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
echo   y vuelve a intentarlo. Si persiste, borra la carpeta .venv y repite.
if not exist ".venv\triada-instalado.toml" if exist ".venv" rmdir /s /q ".venv"
pause
exit /b 1
