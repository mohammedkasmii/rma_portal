"""Windows script verification that never touches the real machine.

Spec section 12 asks for "Windows installer syntax/smoke validation that
does not change the real machine during automated tests" -- so this suite
only parses/greps the scripts, it never executes Install-RMAPortal.ps1 or
launches a browser.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent

POWERSHELL_SCRIPTS = ["Install-RMAPortal.ps1"]
BATCH_SCRIPTS = [
    "Créer_Admin_RMA.bat",
    "Configurer_Session_RMA.bat",
    "Démarrer_Portail_RMA.bat",
    "Autoriser_Réseau_Local.bat",
]

_FORBIDDEN_SNIPPETS = ["rm -rf", "format c:", "del /s /q c:\\", "shutdown", "diskpart"]

powershell = shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")


@pytest.mark.skipif(powershell is None, reason="PowerShell not available on this system")
@pytest.mark.parametrize("name", POWERSHELL_SCRIPTS)
def test_powershell_script_has_valid_syntax(name: str):
    path = REPO_ROOT / name
    assert path.exists(), path

    command = (
        "$errs = $null; "
        f"[void][System.Management.Automation.Language.Parser]::ParseFile('{path}', [ref]$null, [ref]$errs); "
        "exit $errs.Count"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"parse errors in {name}: {result.stdout}\n{result.stderr}"


@pytest.mark.parametrize("name", POWERSHELL_SCRIPTS)
def test_powershell_script_never_executes_real_actions_in_ci(name: str):
    """A cheap guard against accidentally wiring the installer into CI."""
    content = (REPO_ROOT / name).read_text(encoding="utf-8")
    lowered = content.lower()
    for snippet in _FORBIDDEN_SNIPPETS:
        assert snippet not in lowered, f"{name} contains forbidden snippet: {snippet}"


@pytest.mark.parametrize("name", BATCH_SCRIPTS)
def test_batch_script_exists_and_is_safe(name: str):
    path = REPO_ROOT / name
    assert path.exists(), path
    content = path.read_text(encoding="utf-8")
    assert content.strip(), f"{name} is empty"
    lowered = content.lower()
    for snippet in _FORBIDDEN_SNIPPETS:
        assert snippet not in lowered, f"{name} contains forbidden snippet: {snippet}"


@pytest.mark.parametrize(
    "name", ["Créer_Admin_RMA.bat", "Configurer_Session_RMA.bat", "Démarrer_Portail_RMA.bat"]
)
def test_batch_scripts_invoke_rma_portal_cli_from_their_own_directory(name: str):
    content = (REPO_ROOT / name).read_text(encoding="utf-8")
    assert 'cd /d "%~dp0"' in content
    assert "uv run rma-portal" in content


def test_firewall_script_targets_the_documented_port():
    content = (REPO_ROOT / "Autoriser_Réseau_Local.bat").read_text(encoding="utf-8")
    assert "8765" in content
    assert "profile=private" in content.lower()


def test_readme_documents_all_scripts():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    for name in ["Install-RMAPortal.ps1", *BATCH_SCRIPTS]:
        assert name in readme, f"{name} is not mentioned in README.md"
