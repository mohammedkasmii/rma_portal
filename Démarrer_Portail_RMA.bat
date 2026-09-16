@echo off
setlocal
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
    echo uv est introuvable. Executez d'abord Install-RMAPortal.ps1.
    pause
    exit /b 1
)

echo Demarrage du portail RMA sur http://0.0.0.0:8765 ...
echo Laissez cette fenetre ouverte tant que le portail doit rester accessible.
echo Fermez la fenetre ou appuyez sur Ctrl+C pour arreter le serveur.
uv run rma-portal serve

echo.
echo Le serveur s'est arrete.
pause
