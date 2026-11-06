"""Starter der installierten Anwendung: Datenordner je Benutzer, Erstkonfiguration, Modi."""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import pytest

from market_intel import desktop
from market_intel.config import load_settings


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "Daten mit Leerzeichen"
    monkeypatch.setenv("MI_HOME", str(target))
    return target


def test_home_and_first_config(home: Path) -> None:
    assert desktop.app_home() == home
    config = desktop.prepare_home(home)
    settings = load_settings(config)
    assert settings.database_url == f"sqlite:///{home.resolve().as_posix()}/market_intel.db"
    assert Path(settings.output_dir).is_absolute() and (home / "out").is_dir()
    config.write_text('database_url = "sqlite:///eigene.db"\n', "utf-8")
    desktop.prepare_home(home)  # vorhandene Einstellungen werden nie überschrieben
    assert "eigene.db" in config.read_text("utf-8")


@pytest.mark.skipif(desktop.os.name != "nt", reason="nur unter Windows (läuft im Windows-Build)")
def test_windows_home_is_local_appdata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MI_HOME", raising=False)
    local = desktop.os.environ["LOCALAPPDATA"]
    assert desktop.app_home() == Path(local) / "YellotoolsMarketIntel"


def test_cli_mode_passes_arguments_unchanged(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr("market_intel.cli.main", lambda argv: calls.append(argv) or 0)
    assert desktop.main(["ui", "--port", "9000"]) == 0
    assert desktop.main(["--config", "x.toml", "sources"]) == 0
    config = str(home / "market_intel.toml")
    assert calls == [
        ["--config", config, "ui", "--port", "9000"],
        ["--config", "x.toml", "sources"],
    ]


def test_launcher_mode_starts_ui_on_free_port(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    opened: list[Any] = []
    monkeypatch.setattr("market_intel.cli.main", lambda argv: calls.append(argv) or 0)
    monkeypatch.setattr(desktop.webbrowser, "open", opened.append)
    with socket.socket() as busy:  # bevorzugter Port belegt → anderer freier Port
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        taken = busy.getsockname()[1]
        assert desktop.main(["--no-browser", "--port", str(taken)]) == 0
    argv = calls[0]
    assert argv[:3] == ["--config", str(home / "market_intel.toml"), "ui"]
    assert int(argv[argv.index("--port") + 1]) != taken and not opened


def test_cli_works_end_to_end_with_home(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert desktop.main(["products", "list"]) == 0
    assert "Produktmatrix" in capsys.readouterr().out
    assert (home / "market_intel.db").exists()  # Datenbank im Datenordner, nicht im Arbeitsordner
