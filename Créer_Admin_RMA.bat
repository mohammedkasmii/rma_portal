@echo off
setlocal
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
    echo uv est introuvable. Executez d'abord Install-RMAPortal.ps1.
    pause
    exit /b 1
)

uv run rma-portal create-admin
echo.
pause
