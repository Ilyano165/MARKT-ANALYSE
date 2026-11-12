"""Sichere Ablage von API-Keys – niemals in Datenbank, Konfigurationsdatei oder Log.

- Windows: **Windows-Anmeldeinformationsverwaltung** (Credential Manager, „Generische
  Anmeldeinformationen“) über die Win32-Funktionen ``CredWriteW``/``CredReadW``/``CredDeleteW``
  aus ``advapi32.dll`` – ohne Zusatzpaket. Ziel: ``MarketIntel/ai/<provider>``.
- Andere Systeme: Python-Paket ``keyring`` (Secret Service, macOS-Schlüsselbund), falls
  installiert. Ohne sicheren Speicher wird das Speichern **abgelehnt** statt im Klartext abgelegt.
- Tests: ``MemorySecretStore``.

Es werden nie Schlüsselwerte zurückgegeben, außer an den Provider, der sie für die
autorisierte Anfrage braucht. Anzeige nur maskiert (``mask``).
"""

from __future__ import annotations

import os
import sys
from typing import Protocol

TARGET_PREFIX = "MarketIntel/ai/"


class SecretStoreError(RuntimeError):
    """Sicherer Speicher fehlt oder lehnt ab (Meldung ohne Schlüsselwert)."""


class SecretStore(Protocol):
    label: str

    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> bool: ...


def mask(value: str | None) -> str:
    """Nur Anfang und die letzten 4 Zeichen, Rest Punkte – reicht zum Wiedererkennen."""
    if not value:
        return ""
    if len(value) <= 10:
        return "•" * len(value)
    head = value[:7] if value.startswith(("sk-ant-", "nova_")) else value[:3]
    return f"{head}…{value[-4:]}"


def _check(name: str, value: str | None = None) -> None:
    if not name or not name.replace("_", "").replace("-", "").isalnum():
        raise SecretStoreError("ungültiger Name für den Schlüssel")
    if value is not None and (
        not value.strip() or len(value) > 4096 or any(c in value for c in "\r\n\0")
    ):
        raise SecretStoreError("Der Schlüssel ist leer, zu lang oder enthält Zeilenumbrüche")


class MemorySecretStore:
    """Nur für Tests und Vorschau."""

    label = "Arbeitsspeicher (Test)"

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self._data.get(name)

    def set(self, name: str, value: str) -> None:
        _check(name, value)
        self._data[name] = value

    def delete(self, name: str) -> bool:
        return self._data.pop(name, None) is not None


class UnavailableSecretStore:
    """Kein sicherer Speicher: Lesen liefert nichts, Speichern wird abgelehnt."""

    label = "kein sicherer Speicher verfügbar"

    def get(self, name: str) -> str | None:
        return None

    def set(self, name: str, value: str) -> None:
        raise SecretStoreError(
            "Auf diesem System ist kein sicherer Schlüsselspeicher verfügbar. Unter Windows wird "
            "die Anmeldeinformationsverwaltung genutzt; der Schlüssel wird nicht im Klartext "
            "gespeichert."
        )

    def delete(self, name: str) -> bool:
        return False


class WindowsCredentialStore:  # pragma: no cover - läuft nur unter Windows (dort getestet)
    """Windows-Anmeldeinformationsverwaltung über ``advapi32``."""

    label = "Windows-Anmeldeinformationsverwaltung"
    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2
    ERROR_NOT_FOUND = 1168

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

        class CREDENTIALW(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        self._ct = ctypes
        self._cred = CREDENTIALW
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
        self._write = advapi.CredWriteW
        self._write.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
        self._write.restype = wintypes.BOOL
        self._read = advapi.CredReadW
        self._read.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(CREDENTIALW)),
        ]
        self._read.restype = wintypes.BOOL
        self._delete = advapi.CredDeleteW
        self._delete.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._delete.restype = wintypes.BOOL
        self._free = advapi.CredFree
        self._free.argtypes = [ctypes.c_void_p]

    def get(self, name: str) -> str | None:
        _check(name)
        ct = self._ct
        pointer = ct.POINTER(self._cred)()
        if not self._read(TARGET_PREFIX + name, self.CRED_TYPE_GENERIC, 0, ct.byref(pointer)):
            if ct.get_last_error() == self.ERROR_NOT_FOUND:
                return None
            raise SecretStoreError("Anmeldeinformationsverwaltung: Lesen fehlgeschlagen")
        try:
            cred = pointer.contents
            blob: bytes = ct.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
            return blob.decode("utf-16-le")
        finally:
            self._free(pointer)

    def set(self, name: str, value: str) -> None:
        _check(name, value)
        ct = self._ct
        blob = value.encode("utf-16-le")
        buffer = (ct.c_ubyte * len(blob)).from_buffer_copy(blob)
        cred = self._cred()
        cred.Type = self.CRED_TYPE_GENERIC
        cred.TargetName = TARGET_PREFIX + name
        cred.Comment = "Yellotools Market Intelligence – KI-Zugang"
        cred.CredentialBlobSize = len(blob)
        cred.CredentialBlob = ct.cast(buffer, ct.POINTER(ct.c_ubyte))
        cred.Persist = self.CRED_PERSIST_LOCAL_MACHINE
        cred.UserName = "market-intel"
        if not self._write(ct.byref(cred), 0):
            raise SecretStoreError("Anmeldeinformationsverwaltung: Speichern fehlgeschlagen")

    def delete(self, name: str) -> bool:
        _check(name)
        ct = self._ct
        if self._delete(TARGET_PREFIX + name, self.CRED_TYPE_GENERIC, 0):
            return True
        if ct.get_last_error() == self.ERROR_NOT_FOUND:
            return False
        raise SecretStoreError("Anmeldeinformationsverwaltung: Löschen fehlgeschlagen")


class KeyringStore:  # pragma: no cover - abhängig vom Systemschlüsselbund
    """Systemschlüsselbund über das Paket ``keyring`` (Linux/macOS)."""

    def __init__(self) -> None:
        import keyring  # type: ignore[import-not-found,unused-ignore]
        from keyring.backends import fail  # type: ignore[import-not-found,unused-ignore]

        backend = keyring.get_keyring()
        if isinstance(backend, fail.Keyring):
            raise SecretStoreError("kein Systemschlüsselbund")
        self._keyring = keyring
        self.label = f"Systemschlüsselbund ({type(backend).__name__})"

    def get(self, name: str) -> str | None:
        _check(name)
        value = self._keyring.get_password("MarketIntel", TARGET_PREFIX + name)
        return str(value) if value else None

    def set(self, name: str, value: str) -> None:
        _check(name, value)
        self._keyring.set_password("MarketIntel", TARGET_PREFIX + name, value)

    def delete(self, name: str) -> bool:
        _check(name)
        try:
            self._keyring.delete_password("MarketIntel", TARGET_PREFIX + name)
        except Exception:
            return False
        return True


class EnvNovaStore:
    """Wie NOVAs eigener Client: ``NOVA_API_KEY`` aus der Umgebung als zusätzliche, nur lesbare
    Quelle für den NOVA-Schlüssel (z. B. Server ohne Schlüsselbund). Gilt **nur** für NOVA –
    externe Anbieter werden nie über Umgebungsvariablen aktiv."""

    def __init__(self, inner: SecretStore) -> None:
        self.inner = inner
        self.label = inner.label

    def get(self, name: str) -> str | None:
        value = self.inner.get(name)
        if value is None and name == "nova":
            env = os.environ.get("NOVA_API_KEY", "").strip()
            return env or None
        return value

    def set(self, name: str, value: str) -> None:
        self.inner.set(name, value)

    def delete(self, name: str) -> bool:
        return self.inner.delete(name)


def default_store() -> SecretStore:
    """Bester verfügbarer sicherer Speicher dieses Systems (plus ``NOVA_API_KEY``, nur lesend)."""
    inner: SecretStore
    if sys.platform == "win32":
        inner = WindowsCredentialStore()
    else:
        try:
            inner = KeyringStore()
        except Exception:
            inner = UnavailableSecretStore()
    return EnvNovaStore(inner)
