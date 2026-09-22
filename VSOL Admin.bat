@echo off
title VSOL Admin
cd /d "%~dp0"
python vsol_admin.py
if errorlevel 1 (
    echo.
    echo Error al iniciar la aplicacion.
    pause
)