"""Protokolldatei mit Rotation (Audit P2: es gab keine Logdatei).

``<Datenordner>/logs/market_intel.log``, je Datei höchstens 2 MB, 5 ältere Dateien
(``market_intel.log.1`` … ``.5``). Protokolliert werden Start/Ende, Aufträge, Fehler mit
Stapelverfolgung – keine Suchbegriffe aus Seitenaufrufen und keine Inhalte von Notizen.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

LOG_NAME = "market_intel.log"
MAX_BYTES = 2 * 1024 * 1024
BACKUPS = 5
FORMAT = "%(asctime)s %(levelname)-7s %(threadName)s %(name)s: %(message)s"


def setup_logging(home: Path, level: int = logging.INFO) -> Path:
    """Rotierende Protokolldatei einrichten (idempotent). Gibt den Pfad zurück."""
    folder = home / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / LOG_NAME
    root = logging.getLogger("market_intel")
    for handler in root.handlers:
        if getattr(handler, "baseFilename", None) == str(path.resolve()):
            return path
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8", delay=True
    )
    handler.setFormatter(logging.Formatter(FORMAT))
    root.addHandler(handler)
    root.setLevel(level)
    return path


def log_files(home: Path) -> list[Path]:
    folder = home / "logs"
    return sorted(folder.glob(LOG_NAME + "*")) if folder.is_dir() else []
