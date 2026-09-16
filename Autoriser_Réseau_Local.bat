@echo off
setlocal
echo Ce script doit etre execute en tant qu'administrateur.
echo Il ouvre le port TCP 8765 sur le profil Prive du pare-feu Windows,
echo afin que les autres postes de l'agence puissent acceder au portail.
echo.
pause

netsh advfirewall firewall add rule name="Portail RMA (8765)" dir=in action=allow protocol=TCP localport=8765 profile=private
if errorlevel 1 (
    echo Echec de l'ajout de la regle de pare-feu. Relancez ce script en tant qu'administrateur.
) else (
    echo Regle de pare-feu ajoutee avec succes pour le port 8765/TCP.
)
echo.
pause
