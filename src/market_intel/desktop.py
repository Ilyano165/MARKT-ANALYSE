"""Starter für die installierte Windows-Anwendung (auch unter Linux/macOS nutzbar).

- Ohne Argumente: lokale Oberfläche auf 127.0.0.1 starten und im Browser öffnen.
- Mit Argumenten: wie die Kommandozeile ``mi`` (z. B. ``MarketIntel.exe research …``).

Einstellungen, Datenbank und Ausgaben liegen in einem Datenordner je Benutzer – nicht im
(schreibgeschützten) Programmordner und nicht im zufälligen Arbeitsordner:
Windows ``%LOCALAPPDATA%\\YellotoolsMarketIntel``, sonst ``~/.local/share/yellotools-market-intel``;
mit ``MI_HOME`` frei wählbar. Beim ersten Start wird dort eine kommentierte ``market_intel.toml``
mit absoluten Pfaden angelegt; eine vorhandene Datei wird nie überschrieben. Eine
Deinstallation entfernt den Datenordner nicht.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path

APP_DIR_WINDOWS = "YellotoolsMarketIntel"
APP_DIR_OTHER = "yellotools-market-intel"
CONFIG_NAME = "market_intel.toml"


def app_home() -> Path:
    """Datenordner je Benutzer."""
    explicit = os.environ.get("MI_HOME")
    if explicit:
        return Path(explicit)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_DIR_WINDOWS
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_DIR_OTHER


def default_config(home: Path) -> str:
    """Erstkonfiguration mit absoluten Pfaden (unabhängig vom Arbeitsordner)."""
    root = home.resolve().as_posix()
    return f"""# Einstellungen für Yellotools Market Intelligence (angelegt beim ersten Start).
# KEINE Zugangsdaten in diese Datei schreiben (wird beim Laden abgelehnt).
# Alle Optionen mit Erklärung: market_intel.example.toml im Quellcode bzw. README.

database_url = "sqlite:///{root}/market_intel.db"
output_dir = "{root}/out"

# Kontaktadresse für den User-Agent: SEC und Wikimedia verlangen eine Kontaktangabe.
# Bitte eine Funktionsadresse der Firma eintragen, z. B.:
# contact_email = "vertrieb@ihre-firma.de"

[llm]
# Optionale lokale KI über Ollama. Ohne KI funktioniert alles regelbasiert.
enabled = false
"""


def prepare_home(home: Path) -> Path:
    """Datenordner und Erstkonfiguration anlegen; gibt den Pfad der Konfiguration zurück."""
    home.mkdir(parents=True, exist_ok=True)
    (home / "out").mkdir(exist_ok=True)
    config = home / CONFIG_NAME
    if not config.exists():
        config.write_text(default_config(home), encoding="utf-8")
    return config


def free_port(preferred: int) -> int:
    """Bevorzugten Port nehmen, wenn frei; sonst einen freien Port vom System."""
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return int(probe.getsockname()[1])
    raise OSError("kein freier Port auf 127.0.0.1")


def _with_config(argv: list[str], config: Path) -> list[str]:
    return argv if "--config" in argv else ["--config", str(config), *argv]


def main(argv: list[str] | None = None) -> int:
    """Einstiegspunkt der installierten Anwendung."""
    from .cli import main as cli_main

    argv = list(sys.argv[1:] if argv is None else argv)
    config = prepare_home(app_home())
    starter = argparse.ArgumentParser(add_help=False)
    starter.add_argument("--no-browser", action="store_true")
    starter.add_argument("--port", type=int, default=8765)
    known, rest = starter.parse_known_args(argv)
    if rest:  # Kommandozeilenmodus wie „mi …“ – Argumente unverändert weitergeben
        return cli_main(_with_config(argv, config))
    port = free_port(known.port)
    url = f"http://127.0.0.1:{port}/"
    print("Yellotools Market Intelligence")
    print(f"Datenordner: {config.parent}")
    print(f"Oberfläche:  {url}")
    print("Zum Beenden dieses Fenster schließen (oder Strg+C).")
    if not known.no_browser:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    return cli_main(_with_config(["ui", "--port", str(port)], config))


if __name__ == "__main__":
    sys.exit(main())
