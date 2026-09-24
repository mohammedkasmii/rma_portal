# Portail RMA

Portail interne de suivi des dossiers RMA. Il lit en **lecture seule** la file
« Dossiers en instance d'accord » (filtre « Garage agréé ») du portail
OmegaFlow, et affiche les dossiers, leurs dates et leur statut de traitement
aux employés de l'agence sur le réseau local. Voir `docs/architecture.md`,
`docs/omegaflow-contract.md` et `docs/multi-workflow-system-design.md` pour les
détails techniques et la fondation des prochains workflows.

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

## Connecter la session OmegaFlow

**Fonctionnement normal :** sur le tableau de bord, la carte « Session
OmegaFlow » affiche un bouton **Se connecter** (ou **Reconnecter** si la
session a expiré). Tout employé connecté au portail peut cliquer dessus,
pas seulement un administrateur.

1. Cliquez sur **Se connecter** / **Reconnecter**.
2. Un navigateur s'ouvre **sur l'ordinateur qui exécute le Portail RMA**
   (pas forcément votre poste, si le portail tourne sur un autre PC).
3. Connectez-vous manuellement avec le compte OmegaFlow partagé de
   l'agence, et validez la session si OmegaFlow le demande.
4. Fermez la fenêtre du navigateur. Le portail vérifie immédiatement la
   session (sans attendre les 5 minutes) et met à jour la carte : **Session
   active** si la connexion a réussi, **Session expirée** sinon.

Le mot de passe OmegaFlow **n'est jamais demandé ni enregistré** par cette
application : seule la session du navigateur (profil persistant) est
conservée localement, dans `%LOCALAPPDATA%\RMAPortal\browser-profile`. Une
seule fenêtre de connexion peut être ouverte à la fois ; un second clic
pendant qu'une fenêtre est déjà ouverte n'en ouvre pas une deuxième.

**Outil de secours :** `Configurer_Session_RMA.bat` fait la même chose
depuis une invite de commande sur le serveur (utile en l'absence
d'employé devant le tableau de bord, par exemple lors de l'installation
initiale ou depuis une session bureau à distance). Ce n'est plus le
fonctionnement normal, mais il reste disponible et fonctionne à
l'identique.

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

## Journaux (logs)

Le portail écrit des journaux techniques (connexions OmegaFlow,
synchronisations, requêtes web lentes ou en erreur) en plus de l'affichage
console d'Uvicorn :

- **Fichier** : `%LOCALAPPDATA%\RMAPortal\logs\rma-portal.log` (encodage
  UTF-8, rotation automatique à 5 Mo, 3 fichiers de sauvegarde conservés).
- **Console** : les mêmes messages s'affichent aussi dans le terminal, à
  côté des lignes d'Uvicorn, pendant que `Démarrer_Portail_RMA.bat` tourne.

Pour suivre les journaux en direct (PowerShell) :

```powershell
Get-Content "$env:LOCALAPPDATA\RMAPortal\logs\rma-portal.log" -Tail 100 -Wait
```

Chaque tentative de connexion OmegaFlow et chaque synchronisation reçoit un
identifiant de corrélation (`operation_id`, par exemple `sync-3f9a1c2b` pour
une synchronisation ou `conn-7ab2e910` pour une connexion) visible sur
chaque ligne du journal. Pour retrouver une synchronisation en échec ou
anormalement lente :

1. Repérez la ligne `stage=synchronization outcome=FAILED` (ou une durée
   `elapsed_ms`/`duration_ms` très élevée sur la ligne de résumé
   `stage=synchronization outcome=...`).
2. Notez son `operation_id`.
3. Filtrez le fichier sur cet identifiant pour voir le déroulé complet
   (démarrage du navigateur, restauration de session, navigation,
   sélection du filtre, recherche, pagination, lecture des détails,
   enregistrement en base, nettoyage du navigateur) :

```powershell
Select-String -Path "$env:LOCALAPPDATA\RMAPortal\logs\rma-portal.log" -Pattern "operation_id=sync-3f9a1c2b"
```

Une étape bloquée apparaît comme un `stage=... outcome=START` sans ligne
`outcome=OK`/`outcome=FAILED` correspondante juste après.

## Récupération / dépannage

| Symptôme | Action |
|---|---|
| Bandeau « Votre session OmegaFlow a expiré » | Cliquez sur **Reconnecter** dans le bandeau ou sur la carte de session du tableau de bord. |
| Le portail ne démarre pas | Vérifiez que le port 8765 n'est pas déjà utilisé, puis relancez `Démarrer_Portail_RMA.bat`. |
| Mot de passe employé oublié | Un administrateur peut le réinitialiser depuis la page **Utilisateurs**. |
| Personne devant le tableau de bord pour se reconnecter | Utilisez `Configurer_Session_RMA.bat` sur le serveur (outil de secours). |
| Base corrompue / réinstallation complète | Fermez le portail, supprimez `%LOCALAPPDATA%\RMAPortal`, relancez `Install-RMAPortal.ps1`, `Créer_Admin_RMA.bat`, puis reconnectez la session OmegaFlow (tableau de bord ou `Configurer_Session_RMA.bat`). Cette opération réinitialise aussi la référence (baseline) : tous les dossiers actuellement dans la file seront réappris sans notification. |
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
