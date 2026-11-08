"""Phase E: Desktop-Hülle – Einzelinstanz, Backend, Fokus, Beenden, Protokolldatei.

Das Fenster selbst (pywebview/WebView2) läuft nur unter Windows; dort prüft der Rauchtest
``MarketIntel.exe --smoke-test`` im Windows-Build Fenster, Seiteninhalt und Einzelinstanz.
Hier wird alles darunter echt geprüft (Server, Worker, Sperre, Token, Rückfall ohne Fenster).
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from market_intel import desktop, shell
from market_intel.applog import log_files, setup_logging


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "home"
    monkeypatch.setenv("MI_HOME", str(target))
    desktop.prepare_home(target)
    return target


def test_instance_lock_is_exclusive_and_released(home: Path) -> None:
    first = shell.InstanceLock(home)
    second = shell.InstanceLock(home)
    assert first.acquire()
    assert not second.acquire()  # zweiter Start erkennt die laufende Instanz
    first.release()
    assert second.acquire()
    second.release()


def test_backend_serves_health_and_focus_requires_token(home: Path) -> None:
    backend = shell.Backend(home / desktop.CONFIG_NAME, 0)
    focused: list[bool] = []
    backend.focus_handler = lambda: focused.append(True)
    backend.start()
    try:
        with urllib.request.urlopen(backend.url + "health") as response:
            health = json.loads(response.read())
        assert health["ok"] is True and health["worker"] is True
        shell.write_info(home, backend.port, backend.token)
        assert shell.notify_running(home) is True and focused == [True]
        forged = urllib.request.Request(
            backend.url + "app/focus", data=b"", headers={"X-MI-Token": "falsch"}
        )
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(forged)
        assert err.value.code == 403 and focused == [True]
        page = urllib.request.urlopen(backend.url + "jobs").read().decode()
        assert "Anwendung beenden" in page  # nur in der Desktop-Hülle
    finally:
        backend.stop()
    assert not backend.worker.alive()


def test_quit_button_stops_the_application(home: Path) -> None:
    import re
    import urllib.parse

    backend = shell.Backend(home / desktop.CONFIG_NAME, 0)
    stopped = threading.Event()
    backend.quit_handler = stopped.set
    backend.start()
    try:
        page = urllib.request.urlopen(backend.url).read().decode()
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)  # type: ignore[union-attr]
        data = urllib.parse.urlencode({"csrf": token}).encode()
        body = urllib.request.urlopen(backend.url + "app/quit", data=data).read().decode()
        assert "wird beendet" in body
        assert stopped.wait(5)
    finally:
        backend.stop()


def test_run_without_window_second_start_and_cleanup(home: Path) -> None:
    """Vollständiger Lauf der Hülle ohne Fenster (Rauchtest-Modus) inklusive Zweitstart."""
    out = home / "smoke.json"
    assert shell.run(home / desktop.CONFIG_NAME, home, 0, "browser", out) == 0
    result = json.loads(out.read_text("utf-8"))
    assert result["second_instance_focus"] is True
    assert not (home / shell.INFO_NAME).exists()  # sauber aufgeräumt
    lock = shell.InstanceLock(home)
    assert lock.acquire()  # Sperre freigegeben
    lock.release()


def test_second_start_while_running_hands_over(home: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    def first() -> None:
        lock = shell.InstanceLock(home)
        assert lock.acquire()
        backend = shell.Backend(home / desktop.CONFIG_NAME, 0)
        backend.focus_handler = release.set
        backend.start()
        shell.write_info(home, backend.port, backend.token)
        started.set()
        release.wait(20)
        backend.stop()
        lock.release()

    thread = threading.Thread(target=first)
    thread.start()
    assert started.wait(30)
    begin = time.monotonic()
    assert shell.run(home / desktop.CONFIG_NAME, home, 0, "browser") == 0
    assert time.monotonic() - begin < 10 and release.is_set()  # erste Instanz geweckt
    thread.join(30)


def test_start_error_is_reported_not_swallowed(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (home / desktop.CONFIG_NAME).write_text('database_url = "postgresql://x"\n', "utf-8")
    assert shell.run(home / desktop.CONFIG_NAME, home, 0, "browser") == 2
    assert "konnte nicht gestartet werden" in capsys.readouterr().err


def test_mi_program_name_is_cli(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert desktop.is_cli_program("C:/Programme/Market Intelligence/mi.exe")
    assert not desktop.is_cli_program("MarketIntel.exe")
    assert desktop.main(["products", "list"], program="mi.exe") == 0
    assert "Produktmatrix" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        desktop.main([], program="mi")  # ohne Befehl: Hilfe statt Fenster


def test_log_file_rotates(home: Path) -> None:
    from market_intel import applog

    path = setup_logging(home)
    assert setup_logging(home) == path  # kein doppelter Handler
    handler = next(
        h
        for h in logging.getLogger("market_intel").handlers
        if getattr(h, "baseFilename", "") == str(path.resolve())
    )
    handler.maxBytes = 2000  # type: ignore[attr-defined]
    logger = logging.getLogger("market_intel.test")
    for i in range(200):
        logger.info("Zeile %s %s", i, "x" * 40)
    files = log_files(home)
    assert path in files and len(files) > 1 and len(files) <= applog.BACKUPS + 1
    handler.maxBytes = applog.MAX_BYTES  # type: ignore[attr-defined]
