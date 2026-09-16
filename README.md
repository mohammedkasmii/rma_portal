# Portail RMA

Portail interne de suivi des dossiers RMA. Il lit en **lecture seule** la file
« Dossiers en instance d'accord » (filtre « Garage agréé ») du portail
OmegaFlow, et affiche les dossiers, leurs dates et leur statut de traitement
aux employés de l'agence sur le réseau local. Voir `docs/architecture.md` et
`docs/omegaflow-contract.md` pour les détails techniques.

Le portail n'envoie, ne valide et ne supprime jamais d'information dans
OmegaFlow.

## Prérequis

- Windows 10/11.
- Aucun besoin de Node.js.
- Un accès internet ponctuel pour l'installation (téléchargement de uv,
  Python 3.14 et du navigateur Camoufox).

## Installation

Depuis le dossier du portail, dans PowerShell :

```powershell
.\Install-RMAPortal.ps1
```

Ce script :

1. Installe ou localise `uv` (gestionnaire Python).
2. Installe Python 3.14 (géré par `uv`, aucune installation système requise).
3. Installe les dépendances exactes depuis `uv.lock`.
4. Installe le navigateur Camoufox utilisé pour lire OmegaFlow.
5. Applique les migrations de base de données (Alembic).

À la fin, il affiche les trois étapes suivantes (créer un administrateur,
configurer la session OmegaFlow, démarrer le portail).

Si vous devez ré-appliquer uniquement les dépendances ou les migrations :

```powershell
uv sync --frozen
uv run alembic upgrade head
```

## Créer le premier administrateur

Double-cliquez sur `Créer_Admin_RMA.bat`, ou en ligne de commande :

```powershell
uv run rma-portal create-admin
```

Renseignez un identifiant, un nom affiché et un mot de passe (10 caractères
minimum). Vous pourrez ensuite créer les autres comptes employés depuis la
page **Utilisateurs** du portail, une fois connecté en tant qu'administrateur.

## Configurer la session OmegaFlow

Double-cliquez sur `Configurer_Session_RMA.bat`. Un navigateur s'ouvre :

1. Connectez-vous manuellement avec le compte OmegaFlow partagé de l'agence.
2. Validez la session si OmegaFlow le demande.
3. Fermez la fenêtre du navigateur une fois connecté.

Le mot de passe OmegaFlow **n'est jamais demandé ni enregistré** par cette
application : seule la session du navigateur (profil persistant) est
conservée localement, dans `%LOCALAPPDATA%\RMAPortal\browser-profile`.

Cette étape doit être répétée si le bandeau du tableau de bord indique que la
session doit être reconnectée.

## Démarrer le portail

Double-cliquez sur `Démarrer_Portail_RMA.bat`, ou :

```powershell
uv run rma-portal serve
```

Le portail écoute sur `http://0.0.0.0:8765` (un seul worker Uvicorn). Depuis
un autre poste de l'agence, utilisez `http://<nom-ou-ip-du-serveur>:8765`.

Pour autoriser l'accès depuis les autres postes du réseau local, exécutez une
fois, en tant qu'administrateur, `Autoriser_Réseau_Local.bat` (ouvre le port
TCP 8765 sur le profil **Privé** du pare-feu Windows).

La synchronisation avec OmegaFlow s'exécute automatiquement toutes les 5
minutes tant que le serveur tourne ; un bouton « Actualiser maintenant » est
aussi disponible sur le tableau de bord.

## Récupération / dépannage

| Symptôme | Action |
|---|---|
| Bandeau « session OmegaFlow doit être reconnectée » | Relancez `Configurer_Session_RMA.bat`. |
| Le portail ne démarre pas | Vérifiez que le port 8765 n'est pas déjà utilisé, puis relancez `Démarrer_Portail_RMA.bat`. |
| Mot de passe employé oublié | Un administrateur peut le réinitialiser depuis la page **Utilisateurs**. |
| Base corrompue / réinstallation complète | Fermez le portail, supprimez `%LOCALAPPDATA%\RMAPortal`, relancez `Install-RMAPortal.ps1`, `Créer_Admin_RMA.bat` puis `Configurer_Session_RMA.bat`. Cette opération réinitialise aussi la référence (baseline) : tous les dossiers actuellement dans la file seront réappris sans notification. |
| Mettre à jour après un changement de code | `uv sync --frozen` puis `uv run alembic upgrade head`, puis relancer `Démarrer_Portail_RMA.bat`. |

## Tests et vérifications (pour la maintenance du code)

```powershell
uv sync --frozen
uv run ruff check .
uv run pytest
uv run alembic upgrade head
```

## Emplacement des données

Toutes les données du portail (base SQLite, profil de navigateur, journaux)
sont stockées sous `%LOCALAPPDATA%\RMAPortal`, jamais dans le dossier du
code source.
