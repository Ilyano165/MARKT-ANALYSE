"""Datensicherung und Wiederherstellung der SQLite-Datenbank (Audit P2-7, NFR-09).

* Sicherung über die SQLite-Online-Backup-API – konsistent auch während die Anwendung läuft
  und der Worker schreibt (WAL). Jede Sicherung wird mit ``PRAGMA integrity_check`` geprüft;
  eine fehlerhafte Sicherung wird gelöscht und als Fehler gemeldet, nie als Erfolg.
* Ablage: ``<Datenordner>/backups/market_intel-JJJJMMTT-HHMMSS.db``; es bleiben die letzten
  ``KEEP`` Sicherungen.
* Wiederherstellung nur bei beendeter Anwendung (``mi backup restore DATEI``): Die Sicherung
  wird geprüft, der aktuelle Stand wird vorher selbst gesichert („vor-wiederherstellung“).
* PostgreSQL: nicht über diese Funktion – dort ``pg_dump`` verwenden (wird gemeldet).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import Engine

KEEP = 10
PREFIX = "market_intel-"


class BackupError(RuntimeError):
    """Sicherung oder Wiederherstellung nicht möglich (mit Begründung)."""


@dataclass(frozen=True, slots=True)
class BackupInfo:
    path: Path
    created: datetime
    size: int
    companies: int | None


def sqlite_path(engine: Engine) -> Path:
    if engine.dialect.name != "sqlite" or not engine.url.database:
        raise BackupError(
            "Sicherung über die Anwendung nur für SQLite. Für PostgreSQL bitte pg_dump nutzen."
        )
    return Path(engine.url.database).resolve()


def _check(path: Path) -> int:
    """Integrität prüfen; gibt die Zahl der Unternehmen zurück."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        try:
            result = con.execute("PRAGMA integrity_check").fetchone()
        except sqlite3.DatabaseError as exc:
            raise BackupError(f"{path.name} ist keine lesbare Datenbank: {exc}") from exc
        if not result or result[0] != "ok":
            raise BackupError(f"Integritätsprüfung fehlgeschlagen: {result}")
        try:
            return int(con.execute("SELECT count(*) FROM company").fetchone()[0])
        except sqlite3.DatabaseError as exc:
            raise BackupError(f"keine Market-Intelligence-Datenbank: {exc}") from exc
    finally:
        con.close()


def _copy(source: Path, target: Path) -> None:
    src = sqlite3.connect(str(source))
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def create_backup(engine: Engine, folder: Path, label: str = "") -> BackupInfo:
    """Konsistente, geprüfte Sicherung anlegen; alte Sicherungen über ``KEEP`` entfernen."""
    source = sqlite_path(engine)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    target = folder / f"{PREFIX}{stamp}{('-' + label) if label else ''}.db"
    _copy(source, target)
    try:
        companies = _check(target)
    except BackupError:
        target.unlink(missing_ok=True)
        raise
    prune(folder)
    return BackupInfo(target, datetime.now(UTC), target.stat().st_size, companies)


def list_backups(folder: Path) -> list[BackupInfo]:
    if not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.glob(f"{PREFIX}*.db"), reverse=True):
        stat = path.stat()
        out.append(BackupInfo(path, datetime.fromtimestamp(stat.st_mtime, UTC), stat.st_size, None))
    return out


def prune(folder: Path, keep: int = KEEP) -> list[Path]:
    removed = []
    for info in list_backups(folder)[keep:]:
        info.path.unlink(missing_ok=True)
        removed.append(info.path)
    return removed


def backup_if_due(engine: Engine, folder: Path, hours: int = 24) -> BackupInfo | None:
    """Automatische Sicherung beim Start, höchstens einmal je ``hours`` Stunden."""
    existing = list_backups(folder)
    if existing and datetime.now(UTC) - existing[0].created < timedelta(hours=hours):
        return None
    return create_backup(engine, folder, "auto")


def restore_backup(backup: Path, database: Path, folder: Path) -> BackupInfo | None:
    """Sicherung zurückspielen (Anwendung muss beendet sein). Gibt die Vorab-Sicherung zurück."""
    if not backup.is_file():
        raise BackupError(f"Sicherung {backup} nicht gefunden")
    _check(backup)
    safety = None
    if database.exists():
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        safety_path = folder / f"{PREFIX}{stamp}-vor-wiederherstellung.db"
        _copy(database, safety_path)
        safety = BackupInfo(safety_path, datetime.now(UTC), safety_path.stat().st_size,
                            _check(safety_path))  # fmt: skip
    _copy(backup, database)
    _check(database)
    return safety
