"""NOVA finden, starten und einen Zugang für MARKT-ANALYSE einrichten – über NOVAs eigene CLI.

Verwendet ausschließlich die vorhandenen NOVA-Befehle (siehe NOVA ``docs/integration-api.md`` §2
und ``docs/install-windows.md`` §3):

- ``nova service start|status`` – Hintergrunddienst auf ``127.0.0.1:8765``
- ``nova integrations create --name markt-analyse --scope chat:complete --scope models:read``
  – gibt den Schlüssel **einmal** als JSON aus; NOVA speichert nur einen Hash.
- ``nova integrations rotate markt-analyse`` – falls die Integration schon existiert.

Der erzeugte Schlüssel geht direkt in den sicheren Speicher (Windows-Anmeldeinformations-
verwaltung) und wird weder angezeigt noch protokolliert.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .secrets import SecretStore

INTEGRATION_NAME = "markt-analyse"
SCOPES = ("chat:complete", "models:read")


class NovaSetupError(RuntimeError):
    """Verständliche Meldung, ohne Schlüssel."""


def find_cli() -> Path | None:
    """``nova.exe`` der Installation (``%LOCALAPPDATA%\\Programs\\NOVA``), ``NOVA_CLI``, PATH."""
    explicit = os.environ.get("NOVA_CLI")
    if explicit and Path(explicit).is_file():
        return Path(explicit)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        installed = Path(base) / "Programs" / "NOVA" / "nova.exe"
        if installed.is_file():
            return installed
    found = shutil.which("nova")
    return Path(found) if found else None


def _run(cli: Path, *args: str, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW: kein Konsolenfenster
    try:
        return subprocess.run(
            [str(cli), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=flags,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NovaSetupError(f"NOVA-Befehl konnte nicht ausgeführt werden: {exc}") from exc


def _json(stdout: str) -> dict[str, Any]:
    start = stdout.find("{")
    try:
        data = json.loads(stdout[start:]) if start >= 0 else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise NovaSetupError("NOVA hat keine auswertbare Antwort geliefert")
    return data


def service_status(cli: Path) -> dict[str, Any]:
    return _json(_run(cli, "service", "status", timeout=20).stdout)


def start_service(cli: Path) -> dict[str, Any]:
    """NOVA-Hintergrunddienst starten (ohne Fenster; kehrt zurück, wenn er läuft)."""
    result = _run(cli, "service", "start", timeout=120)
    if result.returncode != 0:
        raise NovaSetupError(
            "NOVA ließ sich nicht starten: " + (result.stderr.strip()[-300:] or "ohne Meldung")
        )
    return _json(result.stdout)


def create_access(cli: Path, store: SecretStore) -> str:
    """Integrationsschlüssel erzeugen (oder erneuern) und sicher ablegen; gibt das Präfix zurück."""
    result = _run(
        cli,
        "integrations",
        "create",
        "--name",
        INTEGRATION_NAME,
        *[part for scope in SCOPES for part in ("--scope", scope)],
    )
    if result.returncode != 0:
        # Existiert bereits → neuen Schlüssel erzeugen (alter wird sofort ungültig)
        result = _run(cli, "integrations", "rotate", INTEGRATION_NAME)
    if result.returncode != 0:
        raise NovaSetupError(
            "NOVA-Zugang konnte nicht angelegt werden: "
            + (result.stderr.strip()[-300:] or "ohne Meldung")
        )
    data = _json(result.stdout)
    key = str(data.get("api_key") or "")
    if not key.startswith("nova_"):
        raise NovaSetupError("NOVA hat keinen Zugangsschlüssel geliefert")
    store.set("nova", key)
    return str(data.get("key_prefix") or key[:17])
