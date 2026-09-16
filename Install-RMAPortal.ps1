#Requires -Version 5.1
<#
.SYNOPSIS
    Installe le Portail RMA sur ce PC de l'agence.
.DESCRIPTION
    Installe/localise uv, installe Python 3.14 gere par uv, installe les
    dependances depuis uv.lock, installe le navigateur Camoufox et applique
    les migrations Alembic. Ne demande jamais le mot de passe OmegaFlow.
#>

[CmdletBinding()]
param(
    [switch]$SkipCamoufox
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Get-UvCommand {
    $existing = Get-Command uv -ErrorAction SilentlyContinue
    if ($existing) {
        return $existing.Source
    }
    $localUv = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
    if (Test-Path $localUv) {
        return $localUv
    }
    return $null
}

Write-Step "Verification de uv (gestionnaire Python)"
$uvPath = Get-UvCommand
if (-not $uvPath) {
    Write-Host "uv est introuvable : installation en cours..."
    Invoke-Expression (Invoke-RestMethod -Uri "https://astral.sh/uv/install.ps1")
    $uvPath = Get-UvCommand
    if (-not $uvPath) {
        throw "L'installation de uv a echoue. Installez-le manuellement : https://docs.astral.sh/uv/"
    }
}
Write-Host "uv trouve : $uvPath"

Write-Step "Installation de Python 3.14 (gere par uv)"
& $uvPath python install 3.14

Write-Step "Installation des dependances (uv.lock)"
& $uvPath sync --frozen

if (-not $SkipCamoufox) {
    Write-Step "Installation du navigateur Camoufox"
    & $uvPath run camoufox fetch
} else {
    Write-Host "Installation de Camoufox ignoree (-SkipCamoufox)." -ForegroundColor Yellow
}

Write-Step "Application des migrations de base de donnees"
& $uvPath run alembic upgrade head

Write-Step "Installation terminee"
Write-Host ""
Write-Host "Prochaines etapes :" -ForegroundColor Green
Write-Host "  1. Executez Creer_Admin_RMA.bat pour creer le premier compte administrateur."
Write-Host "  2. Executez Configurer_Session_RMA.bat pour connecter le compte OmegaFlow partage de l'agence."
Write-Host "  3. Executez Demarrer_Portail_RMA.bat pour demarrer le portail (http://<ce-pc>:8765)."
Write-Host ""
