"""CSV-Pflege der Produktmatrix: verlustfreier Roundtrip (auch Einträge mit „;“) und Altformat."""

from __future__ import annotations

import csv
import io

from market_intel.products import export_csv, import_csv, load_products


def test_full_roundtrip_keeps_entries_with_semicolons() -> None:
    """Fehler (gefunden in Phase 6): „auf Fahrzeuggeometrien ausgelegt; Nutzen in anderen
    Branchen nicht belegt“ zerfiel beim Re-Import in zwei Einträge."""
    catalog = load_products()
    assert any(";" in item for g in catalog.products.values() for item in g.limitations)
    again, _ = import_csv(export_csv(catalog), catalog)
    assert again.products == catalog.products


def test_list_cells_use_line_breaks_like_excel() -> None:
    rows = list(csv.DictReader(io.StringIO(export_csv(load_products()))))
    cell = next(r["materials"] for r in rows if r["key"] == "rakel_kunststoff")
    assert "\n" in cell and "; " not in cell  # je Wert eine Zeile in der Zelle (Alt+Enter)


def test_excel_line_breaks_and_legacy_semicolon_files_are_read() -> None:
    base = load_products()
    excel = 'key,name,materials\r\nx,Gruppe X,"PVC-Folie\r\nPET; dünn"\r\n'
    group = import_csv(excel, base)[0].products["x"]
    assert group.materials == ("PVC-Folie", "PET; dünn")  # CRLF aus Excel, „;“ bleibt erhalten
    legacy = 'key,name,materials\nx,Gruppe X,"PVC-Folie; PET"\n'  # Format vor Phase 7
    assert import_csv(legacy, base)[0].products["x"].materials == ("PVC-Folie", "PET")


def test_csv_files_are_opened_without_newline_translation(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Windows übersetzt beim Schreiben im Textmodus „\\n“ → „\\r\\n“; ohne ``newline=""`` würde
    aus dem CSV-Zeilenende „\\r\\r\\n“. Unter Linux nicht direkt reproduzierbar – daher wird
    geprüft, dass die Datei so geöffnet wird, wie es für CSV üblich ist."""
    from pathlib import Path

    from market_intel.products import read_csv_file, write_csv_file

    calls: list[dict[str, object]] = []
    original = Path.open
    catalog = load_products()
    path = tmp_path / "produkte.csv"

    def spy(self: Path, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        if self == path:
            calls.append(kwargs)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", spy)
    write_csv_file(path, catalog)
    again, _ = import_csv(read_csv_file(path), catalog)
    assert again.products == catalog.products
    assert len(calls) == 2 and all(c.get("newline") == "" for c in calls)
    assert b"\r\r\n" not in path.read_bytes()
