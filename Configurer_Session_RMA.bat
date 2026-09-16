@echo off
setlocal
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
    echo uv est introuvable. Executez d'abord Install-RMAPortal.ps1.
    pause
    exit /b 1
)

echo Ouverture du navigateur OmegaFlow pour connexion manuelle...
echo Le mot de passe OmegaFlow n'est jamais demande ni enregistre.
uv run rma-portal configure-session
echo.
pause
