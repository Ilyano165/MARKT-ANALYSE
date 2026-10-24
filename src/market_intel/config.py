"""Einstellungen aus einer TOML-Datei – ohne Geheimnisse.

Es gibt bewusst keine Einstellung für kostenpflichtige Dienste. Optionale Zugangsdaten
(kostenloser Companies-House-Schlüssel) kommen nur aus Umgebungsvariablen.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

_FORBIDDEN_KEY = re.compile(r"pass|secret|token|api[_-]?key|credential", re.IGNORECASE)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

ENV_COMPANIES_HOUSE_KEY = "MI_COMPANIES_HOUSE_KEY"


class ConfigError(ValueError):
    """Konfiguration ungültig."""


@dataclass(frozen=True, slots=True)
class LLMSettings:
    """Optionale lokale KI. Standard: aus."""

    enabled: bool = False
    provider: str = "ollama"  # ollama | openai_compatible (beide lokal)
    base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen3:8b"
    timeout_seconds: float = 120.0
    allow_non_loopback: bool = False


@dataclass(frozen=True, slots=True)
class Settings:
    """Validierte Einstellungen."""

    database_url: str = "sqlite:///market_intel.db"
    contact_email: str = ""
    output_dir: Path = Path("out")
    enabled_sources: tuple[str, ...] = ()
    disabled_sources: tuple[str, ...] = ()
    max_requests_per_run: int = 2000
    website_max_pages: int = 8
    http_timeout_seconds: float = 30.0
    max_attempts: int = 3
    rate_limits: tuple[tuple[str, float], ...] = ()  # Quelle → Mindestabstand in Sekunden
    refresh_days: int = 30  # Quelle gilt so lange als „bereits untersucht“
    products_file: Path | None = None  # eigene Produktmatrix (sonst mitgelieferte)
    use_cases_file: Path | None = None  # eigene Anwendungsmatrix
    opportunity_file: Path | None = None  # eigenes Bewertungsmodell/Segmente
    llm: LLMSettings = field(default_factory=LLMSettings)

    @property
    def user_agent(self) -> str:
        """Eindeutiger User-Agent; mit Kontakt, wenn konfiguriert (Pflicht z. B. bei SEC).

        Format nach Wikimedia-User-Agent-Policy: ``Client/Version (Kontakt) Bibliothek/Version``.
        """
        import httpx

        from . import __version__

        contact = f" (mailto:{self.contact_email})" if self.contact_email else ""
        return f"YellotoolsMarketIntel/{__version__}{contact} httpx/{httpx.__version__}"

    @property
    def companies_house_key(self) -> str | None:
        """Kostenloser Schlüssel aus der Umgebung oder ``None``."""
        return os.environ.get(ENV_COMPANIES_HOUSE_KEY) or None


_DEFAULT_LLM = LLMSettings()
_DEFAULT = Settings()


def _reject_secrets(data: dict[str, object], path: str = "") -> None:
    for key, value in data.items():
        if _FORBIDDEN_KEY.search(key):
            raise ConfigError(
                f"Einstellung {path}{key} sieht nach einem Geheimnis aus; Zugangsdaten gehören "
                "in Umgebungsvariablen, nie in die Datei"
            )
        if isinstance(value, dict):
            _reject_secrets(value, f"{path}{key}.")


def _rate_limits(raw: object) -> tuple[tuple[str, float], ...]:
    if not isinstance(raw, dict):
        raise ConfigError("[rate_limits] muss eine Tabelle Quelle = Sekunden sein")
    out = []
    for key, value in raw.items():
        seconds = float(value)
        if seconds < 0.1:
            raise ConfigError(f"rate_limits.{key}: mindestens 0.1 Sekunden")
        out.append((str(key), seconds))
    return tuple(out)


def _optional_path(value: object) -> Path | None:
    return Path(str(value)) if value not in (None, "") else None


def load_settings(path: Path | None) -> Settings:
    """Liest die Datei; ohne Datei gelten die Standardwerte."""
    if path is None or not path.exists():
        return Settings()
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    _reject_secrets(raw)
    llm_raw = raw.get("llm", {})
    if not isinstance(llm_raw, dict):
        raise ConfigError("[llm] muss eine Tabelle sein")
    llm = LLMSettings(
        enabled=bool(llm_raw.get("enabled", False)),
        provider=str(llm_raw.get("provider", _DEFAULT_LLM.provider)),
        base_url=str(llm_raw.get("base_url", _DEFAULT_LLM.base_url)),
        model=str(llm_raw.get("model", _DEFAULT_LLM.model)),
        timeout_seconds=float(llm_raw.get("timeout_seconds", _DEFAULT_LLM.timeout_seconds)),
        allow_non_loopback=bool(llm_raw.get("allow_non_loopback", False)),
    )
    contact = str(raw.get("contact_email", ""))
    if contact and not _EMAIL.match(contact):
        raise ConfigError(f"contact_email {contact!r} ist keine gültige Adresse")
    return Settings(
        database_url=str(raw.get("database_url", _DEFAULT.database_url)),
        contact_email=contact,
        output_dir=Path(str(raw.get("output_dir", "out"))),
        enabled_sources=tuple(str(s) for s in raw.get("enabled_sources", [])),
        disabled_sources=tuple(str(s) for s in raw.get("disabled_sources", [])),
        max_requests_per_run=int(raw.get("max_requests_per_run", 2000)),
        website_max_pages=int(raw.get("website_max_pages", 8)),
        http_timeout_seconds=float(raw.get("http_timeout_seconds", 30.0)),
        max_attempts=max(1, min(6, int(raw.get("max_attempts", 3)))),
        rate_limits=_rate_limits(raw.get("rate_limits", {})),
        refresh_days=int(raw.get("refresh_days", 30)),
        products_file=_optional_path(raw.get("products_file")),
        use_cases_file=_optional_path(raw.get("use_cases_file")),
        opportunity_file=_optional_path(raw.get("opportunity_file")),
        llm=llm,
    )
