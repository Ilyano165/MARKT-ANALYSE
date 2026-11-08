"""Desktop-Hülle: eigenes Anwendungsfenster, Einzelinstanz, kontrollierter Start/Stopp (Phase E).

Die Hülle startet **dasselbe** Backend wie ``mi ui`` (lokaler HTTP-Server + Hintergrund-Worker)
im eigenen Prozess und zeigt die Oberfläche in einem Fenster:

1. **pywebview** (BSD-3) mit Microsoft Edge WebView2 – eigenes Fenster mit Programmsymbol, ohne
   Adressleiste. WebView2 ist auf Windows 10/11 vorinstalliert.
2. Rückfall: **Edge/Chrome im App-Modus** (``--app=…``) mit eigenem Profilordner – ebenfalls ein
   eigenes Fenster ohne Browserleisten; Schließen des Fensters beendet die Anwendung.
3. Letzter Rückfall: Standardbrowser; Beenden über „Anwendung beenden“ in der Navigation.

Einzelinstanz: Eine Sperrdatei im Datenordner wird vom Betriebssystem gesperrt (Windows
``msvcrt.locking``, sonst ``fcntl.flock``) – die Sperre fällt mit dem Prozess, auch bei
Absturz. Ein zweiter Start meldet sich beim laufenden Programm (``/app/focus`` mit Token aus
``instance.json``), das sein Fenster nach vorn holt, und beendet sich.

Beim Schließen: laufender Auftrag wird angehalten (fortsetzbar), Server und Worker stoppen.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any

from . import __version__

log = logging.getLogger("market_intel.shell")
TITLE = "Yellotools Market Intelligence"
LOCK_NAME = "instance.lock"
INFO_NAME = "instance.json"


# ---------------------------------------------------------------------- Einzelinstanz


class InstanceLock:
    """Betriebssystem-Sperre auf ``instance.lock`` (fällt automatisch mit dem Prozess)."""

    def __init__(self, home: Path) -> None:
        self.path = home / LOCK_NAME
        self._file: IO[bytes] | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")  # noqa: SIM115 – bleibt für die Laufzeit offen
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._file = handle
        return True

    def release(self) -> None:
        if self._file is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None


def write_info(home: Path, port: int, token: str) -> None:
    (home / INFO_NAME).write_text(
        json.dumps({"pid": os.getpid(), "port": port, "token": token, "version": __version__}),
        encoding="utf-8",
    )


def notify_running(home: Path, timeout: float = 5.0) -> bool:
    """Laufende Instanz bitten, ihr Fenster zu zeigen. True, wenn sie geantwortet hat."""
    try:
        info = json.loads((home / INFO_NAME).read_text(encoding="utf-8"))
        request = urllib.request.Request(
            f"http://127.0.0.1:{int(info['port'])}/app/focus",
            data=b"",
            method="POST",
            headers={"X-MI-Token": str(info["token"])},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status) == 204
    except (OSError, ValueError, KeyError, urllib.error.URLError):
        return False


# ---------------------------------------------------------------------- Fehleranzeige


def show_error(message: str) -> None:
    """Verständliche Fehlermeldung als Dialog (Windows) bzw. auf der Fehlerausgabe."""
    log.error("Fehlermeldung an den Nutzer: %s", message)
    # Modaler Dialog nur im installierten Programm – nie in Tests/Automatisierung, wo niemand
    # „OK“ klicken kann (Windows-CI hing an genau diesem Dialog). MI_NO_DIALOG=1 schaltet ab.
    interactive = getattr(sys, "frozen", False) and os.environ.get("MI_NO_DIALOG") != "1"
    if os.name == "nt" and interactive:
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, TITLE, 0x10)  # type: ignore[attr-defined]
            return
        except Exception:  # pragma: no cover - nur Windows
            pass
    print(f"{TITLE}: {message}", file=sys.stderr)


# ---------------------------------------------------------------------- Backend


class Backend:
    """HTTP-Server + Worker im eigenen Prozess (dieselbe Logik wie ``mi ui``)."""

    def __init__(self, config: Path, port: int) -> None:
        from .cli_intel import build_ui

        self.token = secrets.token_urlsafe(24)
        self.focus_handler: Callable[[], object] = lambda: None
        self.quit_handler: Callable[[], object] = lambda: None
        self.server, self.worker = build_ui(
            config, "127.0.0.1", port, shell=self, start_worker=True
        )
        self.port = int(self.server.server_address[1])
        self.url = f"http://127.0.0.1:{self.port}/"
        self._thread = threading.Thread(
            target=self.server.serve_forever, name="mi-http", daemon=True
        )

    # Aufrufe aus dem Server (/app/focus, /app/quit)
    def focus(self) -> None:
        self.focus_handler()

    def quit(self) -> None:
        self.quit_handler()

    def start(self) -> None:
        self._thread.start()
        if not wait_until_ready(self.url):
            raise RuntimeError("Die Oberfläche hat nicht innerhalb von 30 Sekunden geantwortet.")

    def stop(self) -> None:
        log.info("Anwendung wird beendet")
        if self.worker is not None:
            self.worker.stop(timeout=20)
        self.server.shutdown()
        self.server.server_close()


def wait_until_ready(url: str, seconds: float = 30.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url + "health", timeout=2) as response:
                if response.status == 200:
                    return True
        except OSError:
            time.sleep(0.2)
    return False


# ---------------------------------------------------------------------- Fenster


def _webview() -> Any:
    try:
        import webview  # type: ignore[import-not-found]  # pywebview (nur Desktop)
    except Exception as exc:  # nicht installiert oder WebView-Laufzeit fehlt
        log.info("pywebview nicht verfügbar: %s", exc)
        return None
    return webview


def browser_app_path() -> str | None:
    """Edge oder Chrome für den App-Modus (eigenes Fenster ohne Leisten)."""
    candidates: list[str] = []
    if os.name == "nt":
        names = ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")
        for base in (os.environ.get(n) for n in names):
            if base:
                candidates += [
                    os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                    os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"),
                ]
    for name in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    return next((c for c in candidates if os.path.isfile(c)), None)


def run_webview(webview: Any, backend: Backend, home: Path, on_loaded: Any = None) -> None:
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    window = webview.create_window(
        TITLE, backend.url, width=1360, height=880, min_size=(900, 600), text_select=True
    )

    def focus() -> None:
        window.restore()
        window.show()
        window.on_top = True
        window.on_top = False

    backend.focus_handler = focus
    backend.quit_handler = window.destroy
    if on_loaded is not None:
        window.events.loaded += lambda: on_loaded(window)
    webview.start(private_mode=False, storage_path=str(home / "fenster"))


def run_app_browser(path: str, backend: Backend, home: Path) -> None:
    profile = home / "fenster-browser"
    args = [
        path,
        f"--app={backend.url}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1360,880",
    ]
    process = subprocess.Popen(args)  # fester Programmpfad, eigene Argumente
    backend.focus_handler = lambda: subprocess.Popen(args)  # holt Fenster nach vorn
    backend.quit_handler = process.terminate
    process.wait()


def run_default_browser(backend: Backend) -> None:
    import webbrowser

    stop = threading.Event()
    backend.focus_handler = lambda: webbrowser.open(backend.url)
    backend.quit_handler = stop.set
    webbrowser.open(backend.url)
    stop.wait()


# ---------------------------------------------------------------------- Einstieg


def run(config: Path, home: Path, port: int, mode: str = "auto", smoke: Path | None = None) -> int:
    """Hülle starten. ``mode``: auto | webview | app | browser. ``smoke``: Rauchtest-Datei."""
    lock = InstanceLock(home)
    if not lock.acquire():
        if notify_running(home):
            log.info("Zweiter Start: laufende Instanz nach vorn geholt")
            return 0
        show_error(
            "Market Intelligence läuft bereits, antwortet aber nicht. Bitte das andere Fenster "
            "schließen oder den Computer neu anmelden."
        )
        return 3
    try:
        try:
            backend = Backend(config, port)
            backend.start()
        except Exception as exc:
            log.exception("Start fehlgeschlagen")
            show_error(f"Die Anwendung konnte nicht gestartet werden:\n{exc}")
            return 2
        write_info(home, backend.port, backend.token)
        log.info("Gestartet: %s (Version %s)", backend.url, __version__)
        try:
            webview = _webview() if mode in ("auto", "webview") else None
            app_path = browser_app_path() if mode in ("auto", "app") else None
            if smoke is not None:
                return _smoke(webview, backend, home, smoke)
            if webview is not None:
                run_webview(webview, backend, home)
            elif app_path is not None:
                run_app_browser(app_path, backend, home)
            else:
                run_default_browser(backend)
        except Exception as exc:
            log.exception("Fenster fehlgeschlagen")
            show_error(f"Das Anwendungsfenster konnte nicht geöffnet werden:\n{exc}")
            return 2
        finally:
            backend.stop()
        return 0
    finally:
        (home / INFO_NAME).unlink(missing_ok=True)
        lock.release()


def _smoke(webview: Any, backend: Backend, home: Path, out: Path) -> int:
    """Rauchtest: Fenster öffnen, Seite im Fenster prüfen, Einzelinstanz prüfen, schließen."""
    result: dict[str, Any] = {"mode": "webview" if webview else "none", "url": backend.url}

    def check(window: Any) -> None:
        try:
            result["title"] = window.evaluate_js("document.title")
            result["nav"] = window.evaluate_js("document.querySelectorAll('header a').length")
            result["second_instance_focus"] = notify_running(home)
        except Exception as exc:  # pragma: no cover - nur im Rauchtest
            result["error"] = repr(exc)
        finally:
            window.destroy()

    if webview is not None:
        run_webview(webview, backend, home, on_loaded=check)
    else:
        result["second_instance_focus"] = notify_running(home)
    out.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    ok = result.get("second_instance_focus") and (webview is None or result.get("title"))
    return 0 if ok and "error" not in result else 1
