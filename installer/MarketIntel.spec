# PyInstaller-Rezept für Yellotools Market Intelligence (Windows).
# Aufruf aus dem Projektordner:  pyinstaller installer/MarketIntel.spec
# Ergebnis: dist/MarketIntel/ mit
#   MarketIntel.exe – Desktop-Anwendung mit eigenem Fenster (kein Konsolenfenster)
#   mi.exe          – Kommandozeile (Konsolenprogramm, gleiche Bibliotheken und Daten)
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hidden = collect_submodules("market_intel") + collect_submodules("sqlalchemy.dialects.sqlite")
datas = collect_data_files("market_intel")  # Produktmatrix, Regeln, Karten, Oberfläche
try:  # Desktop-Fenster (pywebview + WebView2, nur Windows); ohne: Rückfall auf Edge-App-Modus
    hidden += collect_submodules("webview")
    datas += collect_data_files("webview")
except Exception:
    pass

a = Analysis(
    ["entry.py"],
    pathex=["../src"],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "pytest", "mypy", "ruff", "playwright"],
    noarchive=False,
)
pyz = PYZ(a.pure)
# UTF-8-Modus: Ausgaben und Dateien standardmäßig UTF-8 (sonst Windows-Codepage, Umlaute kaputt)
options = [("X utf8_mode=1", None, "OPTION")]
gui = EXE(
    pyz,
    a.scripts,
    options,
    exclude_binaries=True,
    name="MarketIntel",
    console=False,  # eigenes Anwendungsfenster, keine Konsole
    icon="MarketIntel.ico",
    upx=False,
)
cli = EXE(
    pyz,
    a.scripts,
    options,
    exclude_binaries=True,
    name="mi",
    console=True,  # Kommandozeile: mi research …, mi jobs …
    icon="MarketIntel.ico",
    upx=False,
)
coll = COLLECT(gui, cli, a.binaries, a.datas, name="MarketIntel", upx=False)
