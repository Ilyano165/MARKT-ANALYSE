# PyInstaller-Rezept für Yellotools Market Intelligence (Windows: MarketIntel.exe).
# Aufruf aus dem Projektordner:  pyinstaller installer/MarketIntel.spec
# Ergebnis: dist/MarketIntel/ (Ordner mit MarketIntel.exe und allen Bibliotheken; kein Python nötig)
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hidden = collect_submodules("market_intel") + collect_submodules("sqlalchemy.dialects.sqlite")

a = Analysis(
    ["entry.py"],
    pathex=["../src"],
    datas=collect_data_files("market_intel"),  # Produktmatrix, Regeln, Karten, Oberfläche
    hiddenimports=hidden,
    excludes=["tkinter", "pytest", "mypy", "ruff"],
    noarchive=False,
)
pyz = PYZ(a.pure)
# UTF-8-Modus: Ausgaben und Dateien standardmäßig UTF-8 (sonst Windows-Codepage, Umlaute kaputt)
options = [("X utf8_mode=1", None, "OPTION")]
exe = EXE(
    pyz,
    a.scripts,
    options,
    exclude_binaries=True,
    name="MarketIntel",
    console=True,  # Fenster zeigt Adresse und Datenordner; Schließen beendet die Oberfläche
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="MarketIntel", upx=False)
