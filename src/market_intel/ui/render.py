"""HTML der Unternehmensanalyse (Server und statischer Export).

Alle Werte werden mit ``html.escape`` ausgegeben. Diagramme und Karte zeichnet
``static/app.js`` aus dem eingebetteten JSON (``<script type="application/json">`` wird nicht
ausgeführt). Ohne JavaScript bleiben alle Tabellen vollständig lesbar.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from html import escape
from importlib import resources
from typing import Any

from ..domain import METRIC_LABELS
from ..intel.competitors import CLASS_LABELS
from ..repo import NOTE_SECTIONS

STATUS_LABELS = {
    "first": "erster Wert",
    "ok": "vergleichbar",
    "gap": "unbekannt",
    "after_gap": "nach Lücke",
    "break": "Bruch",
}
FLAG_LABELS = {
    "estimate": "Schätzung",
    "derived": "umgerechnet",
    "inorganic": "nicht organisch",
    "conflict": "Widerspruch",
    "low_confidence": "geringe Sicherheit",
}
COMPANY_STATUS = {"active": "aktiv", "dissolved": "erloschen", "unknown": "unbekannt"}
PRESENCE_LABELS = {
    "physical": "physischer Standort",
    "supplied": "beliefert",
    "export": "Exportmarkt",
    "mentioned": "nur erwähnt",
}
SECTION_NOTE_LABELS = {
    "overview": "Überblick",
    "finance": "Finanzen",
    "employees": "Mitarbeiter",
    "locations": "Standorte",
    "products": "Produkte",
    "markets": "Märkte",
    "history": "Historie",
    "sources": "Quellen",
    "notes": "Allgemein",
    "opportunity": "Vertrieb/Opportunity",
}


def e(value: Any) -> str:
    """HTML-sicher; None → leer."""
    return "" if value is None else escape(str(value), quote=True)


def num(value: Any, digits: int = 0) -> str:
    """Deutsche Zahlendarstellung; None → „unbekannt“ (nie 0)."""
    if value is None or value == "":
        return '<span class="unknown">unbekannt</span>'
    try:
        dec = Decimal(str(value))
    except InvalidOperation:
        return e(value)
    text = f"{dec:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return e(text)


def signed(value: Any, digits: int = 0, suffix: str = "") -> str:
    if value is None:
        return '<span class="muted">–</span>'
    dec = Decimal(str(value))
    sign = "+" if dec > 0 else ""
    return e(sign) + num(dec, digits) + e(suffix)


def ref(source_ref: str | None, base: str = "") -> str:
    if not source_ref:
        return ""
    return f'<a class="ref" href="{base}#src-{e(source_ref)}">[{e(source_ref)}]</a>'


def obs_link(point: dict[str, Any] | None, base: str, static: bool) -> str:
    if point is None or static:
        return ""
    return (
        f'<a class="obs" href="{base}/observation/{e(point["kind"])}/'
        f'{e(point["observation_id"])}" title="Herkunft dieser Zahl">Beleg</a>'
    )


def figure_text(point: dict[str, Any] | None, base: str, static: bool) -> str:
    """Zahl mit Jahr, Scope, Herkunft, Quelle – oder „unbekannt“."""
    if point is None:
        return '<span class="unknown">unbekannt</span>'
    unit = "Personen" if point["unit"] == "persons" else point["unit"]
    badges = [
        f'<span class="badge">{e(point.get("label") or point["period_end"])}</span>',
        f'<span class="badge">{e(point.get("scope_label", point["scope"]))}</span>',
        f'<span class="badge {"official" if point.get("official") else ""}">'
        f"{e(point.get('provenance_label', point['provenance']))}</span>",
    ]
    if point.get("estimate"):
        badges.append('<span class="badge warn">Schätzung</span>')
    return (
        f"<strong>{num(point['value'])} {e(unit)}</strong> {' '.join(badges)} "
        f"{ref(point.get('source_ref'))} {obs_link(point, base, static)}"
    )


# ---------------------------------------------------------------------- Seitengerüst


def static_asset(name: str) -> str:
    return resources.files("market_intel.ui").joinpath("static", name).read_text("utf-8")


def page(title: str, body: str, *, static: bool = False, data: Any = None) -> str:
    """Komplette Seite. ``static`` bettet CSS/JS/Karte ein (Datei ohne Server)."""
    data_json = ""
    if data is not None:
        payload = json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/")
        data_json = f'<script type="application/json" id="profile-data">{payload}</script>'
    if static:
        css = f"<style>{static_asset('app.css')}</style>"
        world = resources.files("market_intel.data").joinpath("world_map.json").read_text("utf-8")
        map_json = (
            '<script type="application/json" id="world-map">'
            + world.replace("</", "<\\/")
            + "</script>"
        )
        js = f"<script>{static_asset('app.js')}</script>"
    else:
        css = '<link rel="stylesheet" href="/static/app.css">'
        map_json = ""
        js = '<script src="/static/app.js" defer></script>'
    return (
        '<!doctype html><html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{e(title)}</title>{css}</head><body>"
        f'<header class="top"><a class="brand" href="{"#" if static else "/"}">'
        "Market Intelligence</a>"
        + ("" if static else '<a href="/">Unternehmen</a><a href="/leads">Leads</a>')
        + '<span class="muted">lokal · kostenlos · jede Zahl mit Quelle</span></header>'
        f"<main>{body}</main>{data_json}{map_json}{js}</body></html>"
    )


# ---------------------------------------------------------------------- Firmenliste


def company_list(rows: list[dict[str, Any]], query: str) -> str:
    items = "".join(
        f'<tr><td><a href="/company/{r["id"]}">{e(r["display_name"])}</a></td>'
        f"<td>{e(r['country_code'] or '')}</td><td>{e(r['website_domain'] or '')}</td>"
        f'<td class="num">{r["observations"]}</td><td class="num">{r["locations"]}</td>'
        f"<td>{e(r['updated_at'])[:10]}</td></tr>"
        for r in rows
    )
    return (
        "<h1>Unternehmen</h1>"
        '<form class="search" method="get" action="/"><label for="q">Suche</label>'
        f'<input id="q" name="q" value="{e(query)}" placeholder="Name oder Domain">'
        "<button>Suchen</button></form>"
        '<div class="tablewrap"><table><thead><tr><th>Name</th><th>Land</th><th>Webseite</th>'
        '<th class="num">Kennzahlen</th><th class="num">Standorte</th><th>aktualisiert</th>'
        f"</tr></thead><tbody>{items or '<tr><td colspan=6>Keine Treffer</td></tr>'}"
        "</tbody></table></div>"
        '<p class="muted">Neue Unternehmen kommen über <code>mi research</code> oder '
        "<code>mi import-companies</code> in die Datenbank.</p>"
    )


# ---------------------------------------------------------------------- Profil


def profile_page(
    profile: dict[str, Any], *, static: bool = False, csrf: str = "", base: str = ""
) -> str:
    o = profile["overview"]
    nav = "".join(
        f'<a href="#{s["key"]}" data-tab="{s["key"]}">{e(s["label"])}</a>'
        for s in profile["sections"]
    )
    controls = ""
    if not static:
        cid = profile["company_id"]
        controls = (
            f'<form class="controls" method="get" action="/company/{cid}">'
            '<label for="years">Zeitraum</label><select id="years" name="years">'
            + "".join(
                f'<option value="{y}"{" selected" if y == profile["years"] else ""}>'
                f"{y} Geschäftsjahre</option>"
                for y in (5, 6, 7, 8, 9, 10)
            )
            + '</select><label><input type="checkbox" name="est" value="1"'
            + (" checked" if profile["include_estimates"] else "")
            + "> Schätzungen einbeziehen</label><button>Anzeigen</button>"
            f'<a href="/company/{cid}/competitors">Wettbewerbsanalyse</a>'
            f'<a href="/company/{cid}/opportunity">Opportunity</a>'
            f'<a href="/company/{cid}.json">JSON</a></form>'
        )
    sections = [
        ("overview", _overview(profile, base, static)),
        ("finance", _finance(profile, base, static)),
        ("employees", _employees(profile, base, static)),
        ("locations", _locations(profile)),
        ("products", _products(profile)),
        ("markets", _markets(profile)),
        ("history", _history(profile, base, static)),
        ("sources", _sources(profile, base, static)),
        ("notes", _notes(profile, static, csrf)),
    ]
    labels = dict((s["key"], s["label"]) for s in profile["sections"])
    body = "".join(
        f'<section id="{key}" class="section" data-section="{key}"><h2>{e(labels[key])}</h2>'
        f"{html}</section>"
        for key, html in sections
    )
    return (
        f'<div class="title"><h1>{e(o["display_name"])}</h1>'
        f'<p class="muted">Stand {e(profile["as_of"])} · erzeugt {e(profile["generated_at"][:16])}'
        f" UTC · Betrachtung {profile['years']} Geschäftsjahre</p></div>"
        f'{controls}<nav class="tabs" aria-label="Abschnitte">{nav}</nav>{body}'
    )


def _list(items: list[str], empty: str = "unbekannt") -> str:
    if not items:
        return f'<span class="unknown">{e(empty)}</span>'
    return "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"


def _fact_items(facts: list[dict[str, Any]]) -> list[str]:
    return [
        f'{e(f["value"])} <span class="badge">{e(f["provenance_label"])}</span>'
        f'<span class="badge {"warn" if f["confidence"] == "low" else ""}">'
        f"Sicherheit {e(f['confidence'])}</span> {ref(f['source_ref'])}"
        for f in facts
    ]


def _overview(p: dict[str, Any], base: str, static: bool) -> str:
    o = p["overview"]
    hq = o["headquarters"]
    hq_text = (
        f"{e(hq['city'] or '')} {e(hq['country'] or '')} "
        f'<span class="badge">{e(hq["type_label"])}</span> {ref(hq.get("source_ref"))}'
        if hq
        else '<span class="unknown">unbekannt</span>'
    )
    ids = "".join(
        f"<tr><th>{e(k)}</th><td><code>{e(v)}</code></td></tr>" for k, v in o["identifiers"].items()
    )
    names = sorted({n for n, _ in o["names"]})

    def rel(items: list[dict[str, Any]]) -> list[str]:
        return [
            f'{_company_link(i["id"], i["name"], static)} <span class="badge">'
            f'{e(i["provenance_label"])}</span> <span class="muted">{e(i["basis"])}, erfasst '
            f"{e(i['captured_at'][:10])}</span> {ref(i['source_ref'])}"
            for i in items
        ]

    industries = [
        f'{e(i["label"])} <span class="muted">({e(i["scheme"])} {e(i["code"])})</span> '
        f"{ref(i['source_ref'])}"
        for i in o["industries"]
    ]
    growth = [f"{e(g['text'])} {ref(g.get('source_ref'))}" for g in o["growth_indicators"]]
    unknown = (
        '<p class="gapnote">Nicht belegt (unbekannt, nicht 0): '
        + e(", ".join(o["unknown"]))
        + "</p>"
        if o["unknown"]
        else ""
    )
    competitors = [
        f"{_company_link(c['competitor_id'], c['name'], static)} – {e(c['label'])} "
        f'<span class="muted">({e(c["rationale"])}; {e(c["assessed_at"][:10])})</span>'
        for c in o["competitors"][:12]
    ]
    return (
        f"{unknown}"
        '<div class="cards">'
        f'<div class="card"><h3>Umsatz (jüngster Wert)</h3>{figure_text(o["latest_revenue"], base, static)}</div>'
        f'<div class="card"><h3>Mitarbeiter (jüngster Wert)</h3>{figure_text(o["latest_headcount"], base, static)}</div>'
        f'<div class="card"><h3>Hauptsitz</h3>{hq_text}</div>'
        "</div>"
        '<div class="grid2"><div><h3>Stammdaten</h3><table class="kv">'
        f"<tr><th>Offizieller Name</th><td>{e(o['official_name']) or '<span class=unknown>unbekannt</span>'}</td></tr>"
        f"<tr><th>Rechtsform</th><td>{e(o['legal_form']) or '<span class=unknown>unbekannt</span>'}</td></tr>"
        f"<tr><th>Status</th><td>{e(COMPANY_STATUS.get(o['status'], o['status']))}</td></tr>"
        f"<tr><th>Land</th><td>{e(o['country']) or '<span class=unknown>unbekannt</span>'}</td></tr>"
        f"<tr><th>Webseite</th><td>{e(o['website']) or '<span class=unknown>unbekannt</span>'}</td></tr>"
        f"{ids}</table>"
        f"<h3>Namen und Bezeichnungen</h3>{_list([e(n) for n in names])}"
        f"<h3>Marken</h3>{_list(_fact_items(o['brands']))}</div>"
        f"<div><h3>Muttergesellschaften</h3>{_list(rel(o['parents']), 'keine belegt')}"
        f"<h3>Tochtergesellschaften</h3>{_list(rel(o['subsidiaries']), 'keine belegt')}"
        f"<h3>Branche</h3>{_list(industries)}"
        f"<h3>Teilbranchen</h3>{_list(_fact_items(o['sub_industries']))}"
        f"<h3>Weitere Standorte</h3><p>{len(o['other_locations'])} belegt – siehe "
        '<a href="#locations">Standorte</a></p></div></div>'
        f"<h3>Belegte Wachstumshinweise</h3>{_list(growth, 'keine belegt')}"
        f"<h3>Wettbewerb (letzte gespeicherte Einordnung)</h3>"
        f"{_list(competitors, 'noch keine Einordnung gespeichert')}"
    )


def _company_link(company_id: int, name: str, static: bool) -> str:
    if static:
        return e(name)
    return f'<a href="/company/{company_id}">{e(name)}</a>'


def _series_block(item: dict[str, Any], path: str, base: str, static: bool) -> str:
    unit = "Personen" if item["unit"] == "persons" else item["unit"]
    rows = []
    for row in item["rows"]:
        point = row["point"]
        flags = " ".join(
            f'<span class="badge {"warn" if f in ("estimate", "conflict", "inorganic") else ""}">'
            f"{e(FLAG_LABELS.get(f, f))}</span>"
            for f in row["flags"]
        )
        notes = "".join(f"<li>{e(n)}</li>" for n in row["notes"])
        if row["since_label"]:
            notes += (
                f"<li>seit {e(row['since_label'])}: {signed(row['since_abs'])} {e(unit)}"
                f" ({signed(row['since_pct'], 1, ' %')})</li>"
            )
        value = (
            '<span class="unknown">unbekannt</span>'
            if point is None
            else f"{num(point['value'])} {ref(point.get('source_ref'))} {obs_link(point, base, static)}"
        )
        origin = (
            "" if point is None else f"{e(point['provenance_label'])} · {e(point['scope_label'])}"
        )
        rows.append(
            f'<tr class="st-{e(row["status"])}"><th scope="row">{e(row["label"])}</th>'
            f'<td class="num">{value}</td><td class="num">{signed(row["change_abs"])}</td>'
            f'<td class="num">{signed(row["change_pct"], 1, " %")}</td>'
            f"<td>{e(STATUS_LABELS.get(row['status'], row['status']))} {flags}"
            f"{'<ul class=notes>' + notes + '</ul>' if notes else ''}</td><td>{origin}</td></tr>"
        )
    spans = []
    for i, span in enumerate(item["spans"]):
        is_total = i == item["total_index"]
        notes = "".join(f"<li>{e(n)}</li>" for n in span["notes"])
        cagr = (
            f"<strong>CAGR {num(span['cagr_pct'], 1)} %</strong> "
            f'<span class="formula">{e(span["formula"])}</span>'
            if span["cagr_pct"] is not None
            else '<span class="muted">CAGR nicht berechenbar</span>'
        )
        spans.append(
            f'<div class="span {"total" if is_total else ""}"><h4>'
            f"{'Gesamtentwicklung' if is_total else 'Vergleichbarer Abschnitt'} "
            f"{e(span['start_label'])}–{e(span['end_label'])} ({span['years']} Jahre)</h4>"
            f"<p>{num(span['start_value'])} → {num(span['end_value'])} {e(unit)}: "
            f"{signed(span['total_abs'])} {e(unit)} ({signed(span['total_pct'], 1, ' %')}) · "
            f"{cagr}</p>{'<ul class=notes>' + notes + '</ul>' if notes else ''}</div>"
        )
    excluded = "".join(
        f"<li>{e(x['point']['period_end'])}: {num(x['point']['value'])} – {e(x['reason'])} "
        f"{ref(x['point'].get('source_ref'))}</li>"
        for x in item["excluded"]
        if x["point"]
    )
    notes = "".join(f"<li>{e(n)}</li>" for n in item["notes"])
    return (
        f'<article class="series"><h3>{e(item["title"])}</h3>'
        f'<div class="chart" data-series="{e(path)}" role="img" '
        f'aria-label="Diagramm {e(item["title"])} – Werte in der Tabelle darunter"></div>'
        f"{'<ul class=notes>' + notes + '</ul>' if notes else ''}"
        f"{''.join(spans) or '<p class=muted>Keine Entwicklung berechenbar (weniger als zwei vergleichbare Werte).</p>'}"
        '<div class="tablewrap"><table><thead><tr><th>Jahr</th>'
        f'<th class="num">Wert ({e(unit)})</th><th class="num">Δ absolut</th>'
        '<th class="num">Δ %</th><th>Vergleich / Hinweise</th><th>Herkunft</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
        f"{'<details><summary>Nicht einbezogene Werte</summary><ul>' + excluded + '</ul></details>' if excluded else ''}"
        "</article>"
    )


def _finance(p: dict[str, Any], base: str, static: bool) -> str:
    revenue = p["finance"]["revenue"]
    if not revenue:
        gaps = [g for g in p["gaps"] if g["metric"] == "revenue"]
        detail = "".join(
            f"<li>{e(g['checked_at'][:10])}: {e(g['reason'])} – {e(g['detail'])} "
            f"(gesucht: {e(g['searched'])})</li>"
            for g in gaps
        )
        return (
            '<p class="gapnote">Umsatz unbekannt – kein belegter Wert. Das ist keine Null und '
            f"kein Stillstand.</p>{'<ul>' + detail + '</ul>' if detail else ''}"
        )
    blocks = [
        _series_block(item, f"finance.revenue.{i}", base, static) for i, item in enumerate(revenue)
    ]
    segments = [
        _series_block(item, f"finance.segments.{i}", base, static)
        for i, item in enumerate(p["finance"]["segments"])
    ]
    return (
        '<p class="muted">Jede Zahl: Geschäftsjahr, Konsolidierungskreis, Herkunft und Quelle. '
        "Lücken bleiben Lücken; Veränderungen nur zwischen vergleichbaren Werten.</p>"
        + "".join(blocks)
        + ("<h3>Segmente</h3>" + "".join(segments) if segments else "")
    )


def _employees(p: dict[str, Any], base: str, static: bool) -> str:
    series = p["employees"]["series"]
    principles = "".join(f"<li>{e(x)}</li>" for x in p["employees"]["principles"])
    out = [f'<div class="principles"><h3>Grundsätze</h3><ul>{principles}</ul></div>']
    if not series:
        out.append('<p class="gapnote">Mitarbeiterzahl unbekannt – kein belegter Wert.</p>')
    for i, item in enumerate(series):
        out.append(_series_block(item, f"employees.series.{i}", base, static))
        rows = "".join(
            f'<tr><th scope="row">{e(d["label"])}</th><td class="num">{signed(d["net_change"])}</td>'
            f'<td class="num">{num(d["documented_layoffs"]) if d["documented_layoffs"] else "–"}</td>'
            f'<td class="num">{num(d["documented_hiring"]) if d["documented_hiring"] else "–"}</td>'
            f"<td>{'; '.join(e(str(a['open_positions']) + ' am ' + a['date']) for a in d['job_ads']) or '–'}</td>"
            f"<td>{e(d['statement'])}</td></tr>"
            for d in item.get("dynamics", [])
        )
        out.append(
            '<h4>Personal-Dynamik</h4><div class="tablewrap"><table><thead><tr><th>Jahr</th>'
            '<th class="num">Netto</th><th class="num">belegter Abbau</th>'
            '<th class="num">belegte Einstellungen</th><th>Stellenanzeigen (Bedarf)</th>'
            f"<th>Aussage</th></tr></thead><tbody>{rows}</tbody></table></div>"
        )
    segments = p["employees"]["segments"]
    if segments:
        out.append("<h3>Nach Land / Standort</h3>")
        out.extend(
            _series_block(item, f"employees.segments.{i}", base, static)
            for i, item in enumerate(segments)
        )
    else:
        out.append(
            '<p class="muted">Aufteilung nach Land/Standort: nicht belegt '
            "(erfassbar mit <code>mi add-figure --segment country:DE</code>).</p>"
        )
    return "".join(out)


def _locations(p: dict[str, Any]) -> str:
    loc = p["locations"]
    rows = "".join(
        f"<tr><td>{e(item['type_label'])}</td><td>{e(item['city'] or '')}</td>"
        f"<td>{e(item['country'] or '')}</td><td>{e(item['street'] or '')}</td>"
        f"<td>{e(item['status'])}</td><td>{e(item['geo_precision'])}"
        f"{'<br><span class=muted>' + e(item['geo_note']) + '</span>' if item['geo_note'] else ''}</td>"
        f"<td>{e(item['provenance'])}</td><td>{e((item['captured_at'] or '')[:10])}</td>"
        f"<td>{ref(item['source_ref'])}</td></tr>"
        for item in loc["locations"]
    )
    hints = "".join(f"<li>{e(h)}</li>" for h in loc["hints"])
    jobs = ", ".join(f"{e(j['place'])} ({e(j['last_seen'])})" for j in loc["job_ad_locations"])
    return (
        '<div class="map" data-map="1" role="img" aria-label="Karte: Länder nach belegter '
        'Präsenz, Punkte nur bei Orts- oder Adressgenauigkeit"></div>'
        '<p class="legend"><span class="sw sw-physical"></span>physischer Standort '
        '<span class="sw sw-supplied"></span>beliefert/Export '
        '<span class="sw sw-mentioned"></span>nur erwähnt '
        '<span class="sw sw-point"></span>Standort mit Koordinaten</p>'
        f"{'<ul class=notes>' + hints + '</ul>' if hints else ''}"
        '<div class="tablewrap"><table><thead><tr><th>Art</th><th>Ort</th><th>Land</th>'
        "<th>Adresse</th><th>Status</th><th>Koordinaten</th><th>Herkunft</th><th>erfasst</th>"
        f"<th>Quelle</th></tr></thead><tbody>{rows or '<tr><td colspan=9><span class=unknown>keine Standorte belegt</span></td></tr>'}"
        "</tbody></table></div>"
        + (
            f"<p><strong>Arbeitsorte in Stellenanzeigen</strong> (Hinweis, kein belegter "
            f"Standort): {jobs}</p>"
            if jobs
            else ""
        )
    )


def _products(p: dict[str, Any]) -> str:
    prod = p["products"]
    fits = [
        f'{e(u["use_case"])}: <strong>{e(u["level"])}</strong> <span class="muted">'
        f"{e(u['rationale'])} ({e(u['assessed_at'][:10])})</span>"
        for u in prod["use_cases"]
    ]
    return (
        '<p class="muted">Produktgruppen aus strukturierten Daten, Überschriften von Produktseiten '
        "und Wikidata. Regel-Treffer haben geringe Sicherheit und sollten geprüft werden.</p>"
        f"<h3>Produktgruppen</h3>{_list(_fact_items(prod['product_groups']))}"
        f"<h3>Leistungen</h3>{_list(_fact_items(prod['services']), 'keine belegt')}"
        f"<h3>Marken</h3>{_list(_fact_items(prod['brands']), 'keine belegt')}"
        f"<h3>Bezug zu Anwendungsfällen (Yellotools-Hypothesen)</h3>{_list(fits, 'keiner belegt')}"
    )


def _markets(p: dict[str, Any]) -> str:
    m = p["markets"]
    rows = "".join(
        f"<tr><td>{e(c['country'])}</td><td>{e(c['presence_label'])}</td>"
        f"<td>{e(', '.join(c['site_types']))}</td><td>{e('; '.join(c['notes']))}</td>"
        f"<td>{' '.join(ref(r) for r in c['source_refs'])}</td></tr>"
        for c in m["countries"]
    )
    claims = [f"{e(c['value'])} – „{e(c['quote'])}“ {ref(c['source_ref'])}" for c in m["claims"]]
    return (
        '<p class="muted">Drei Stufen: physischer Standort, beliefert/Export (Quelle nennt '
        "Lieferung/Vertrieb), nur erwähnt. Eine Erwähnung ist kein Beleg für Geschäft.</p>"
        '<div class="tablewrap"><table><thead><tr><th>Land</th><th>Präsenz</th><th>Standortarten</th>'
        f"<th>Grundlage</th><th>Quellen</th></tr></thead><tbody>{rows or '<tr><td colspan=5><span class=unknown>keine Länder belegt</span></td></tr>'}"
        "</tbody></table></div>"
        f"<h3>Reichweitenangaben</h3>{_list(claims, 'keine')}"
        f"<h3>Vertriebswege</h3>{_list(_fact_items(m['sales_channels']))}"
        f"<h3>Zielgruppen</h3>{_list(_fact_items(m['target_groups']))}"
    )


def _history(p: dict[str, Any], base: str, static: bool) -> str:
    h = p["history"]
    rows = "".join(
        f"<tr><td>{e(item['detected_at'][:10])}</td><td>{e(item['event_date'] or 'unbekannt')}</td>"
        f"<td>{e(item['subject'])} / {e(item['kind'])}</td><td>{e(item['summary'])}"
        + (
            f" {obs_link({'kind': 'financial' if item.get('metric') == 'revenue' else 'employee', 'observation_id': item['observation_id']}, base, static)}"
            if item.get("observation_id")
            else ""
        )
        + f"</td><td>{ref(item.get('source_ref'))}</td></tr>"
        for item in h["timeline"]
    )
    events = "".join(
        f"<tr><td>{e(ev['event_date'] or 'unbekannt')}"
        f'<span class="muted"> ({e(ev["date_precision"])})</span></td>'
        f"<td>{e(ev['type'])}</td><td>{e(ev['summary'])}</td>"
        f"<td>{e(ev['verification'])} / {e(ev['confidence'])} ({e(ev['method'])})</td>"
        f"<td>{e(ev['learned_at'][:10])}</td><td>{ref(ev['source_ref'])}</td></tr>"
        for ev in h["events"]
    )
    return (
        '<p class="muted"><strong>Ereignisdatum</strong> = wann es laut Quelle geschah; '
        "<strong>erfasst</strong> = wann das System davon erfuhr. Ältere Werte bleiben erhalten; "
        "fehlt etwas bei einer erneuten Recherche, wird daraus nichts abgeleitet.</p>"
        "<h3>Änderungen zwischen Recherchen</h3>"
        '<div class="tablewrap"><table><thead><tr><th>erfasst</th><th>Ereignisdatum</th><th>Art</th>'
        f"<th>Beschreibung</th><th>Quelle</th></tr></thead><tbody>{rows or '<tr><td colspan=5>Noch keine Änderungen (Ersterfassung).</td></tr>'}"
        "</tbody></table></div>"
        "<h3>Unternehmensereignisse</h3>"
        '<div class="tablewrap"><table><thead><tr><th>Ereignisdatum</th><th>Art</th><th>Meldung</th>'
        f"<th>Prüfstatus</th><th>erfasst</th><th>Quelle</th></tr></thead><tbody>{events or '<tr><td colspan=6>keine</td></tr>'}"
        "</tbody></table></div>"
        '<p class="muted">Regel-Treffer aus Pressetexten sind „unverified“: Sie erscheinen hier, '
        "beeinflussen Wachstumsberechnungen aber nur als Hinweis. Bestätigte Ereignisse erfassen: "
        "<code>mi add-event</code>.</p>"
    )


def _sources(p: dict[str, Any], base: str, static: bool) -> str:
    rows = "".join(
        f'<tr id="src-{e(s["ref"])}"><th scope="row">{e(s["ref"])}</th>'
        f'<td>{e(s["source_name"])}<br><span class="muted">{e(s["publisher"] or "")}</span></td>'
        f'<td>{e(s["title"] or "")}<br><span class="url">{e(s["url"])}</span></td>'
        f"<td>{e(s['license'] or '')}</td><td>{e((s['retrieved_at'] or '')[:16])}</td>"
        f"<td>{e(s['published_at'] or 'unbekannt')}</td>"
        + (
            f'<td><a href="/document/{s["document_id"]}">Dokument</a></td>'
            if s["document_id"] and not static
            else "<td></td>"
        )
        + "</tr>"
        for s in p["sources"]
    )
    gaps = "".join(
        f"<li>{e(METRIC_LABELS.get(g['metric'], g['metric']))}: {e(g['reason'])} – "
        f"{e(g['detail'])} (gesucht in {e(g['searched'])}, {e(g['checked_at'][:10])})</li>"
        for g in p["gaps"]
    )
    return (
        '<div class="tablewrap"><table><thead><tr><th>Ref</th><th>Quelle</th><th>Dokument</th>'
        "<th>Lizenz</th><th>abgerufen</th><th>veröffentlicht</th><th></th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
        f"<h3>Dokumentierte Lücken</h3>{'<ul>' + gaps + '</ul>' if gaps else '<p>keine dokumentiert</p>'}"
    )


def _notes(p: dict[str, Any], static: bool, csrf: str) -> str:
    cid = p["company_id"]
    items = "".join(
        f'<li class="note"><div class="meta">{e(SECTION_NOTE_LABELS.get(n["section"], n["section"]))}'
        f" · {e(n['author'])} · {e(n['created_at'][:16])}"
        + ("" if not n["supersedes_id"] else " · bearbeitet")
        + "</div>"
        f'<div class="body">{e(n["body"])}</div>'
        + (
            ""
            if static
            else f'<form method="post" action="/company/{cid}/notes/{n["id"]}/delete">'
            f'<input type="hidden" name="csrf" value="{e(csrf)}"><button class="link">'
            "entfernen</button></form>"
        )
        + "</li>"
        for n in p["notes"]
    )
    form = ""
    if not static:
        options = "".join(
            f'<option value="{s}">{e(SECTION_NOTE_LABELS[s])}</option>' for s in NOTE_SECTIONS
        )
        form = (
            f'<form class="noteform" method="post" action="/company/{cid}/notes">'
            f'<input type="hidden" name="csrf" value="{e(csrf)}">'
            f'<label for="section">Abschnitt</label><select id="section" name="section">{options}</select>'
            '<label for="author">Autor</label><input id="author" name="author" maxlength="120" required>'
            '<label for="body">Notiz</label><textarea id="body" name="body" rows="4" maxlength="10000" required></textarea>'
            "<button>Notiz speichern</button></form>"
        )
    return (
        '<p class="muted">Interne Notizen bleiben lokal, werden nie an Quellen gesendet und '
        "versioniert (Entfernen legt eine neue Version an).</p>"
        f'<ul class="notes-list">{items or "<li class=muted>Keine Notizen.</li>"}</ul>{form}'
    )


# ---------------------------------------------------------------------- weitere Seiten


def competitors_page(result: dict[str, Any], csrf: str) -> str:
    cid = result["company_id"]
    focal = result["focal"]
    blocks = []
    for key, label in CLASS_LABELS.items():
        items = result["groups"][key]
        if not items:
            continue
        rows = "".join(
            f'<tr><th scope="row"><a href="/company/{r["competitor_id"]}">{e(r["name"])}</a></th>'
            f"<td>{e('; '.join(r['reasons']))}</td>"
            f"<td>{e(r['dimensions']['products']['text'])}"
            f"{'<br><span class=muted>' + e(', '.join(r['dimensions']['products']['shared'])) + '</span>' if r['dimensions']['products']['shared'] else ''}</td>"
            f"<td>{e(r['dimensions']['targets']['text'])}</td>"
            f"<td>{e(r['dimensions']['revenue']['text'])}</td>"
            f"<td>{e(r['dimensions']['headcount']['text'])}</td>"
            f"<td>{e(r['dimensions']['markets']['text'])}</td>"
            f"<td>{e(r['dimensions']['sites']['text'])}</td>"
            f"<td>{e(r['dimensions']['growth']['text'])}</td>"
            f'<td class="num">{r["known_dimensions"]}/9</td></tr>'
            for r in items
        )
        blocks.append(
            f'<h2>{e(label)} ({len(items)})</h2><div class="tablewrap"><table><thead><tr>'
            "<th>Unternehmen</th><th>Begründung</th><th>Produkte</th><th>Zielgruppen</th>"
            "<th>Umsatz (Verhältnis)</th><th>Mitarbeiter (Verhältnis)</th><th>Märkte</th>"
            '<th>Standorte</th><th>Wachstum</th><th class="num">bekannte Dimensionen</th>'
            f"</tr></thead><tbody>{rows}</tbody></table></div>"
        )
    return (
        f'<p><a href="/company/{cid}">← zurück zum Profil</a></p>'
        f"<h1>Wettbewerbsanalyse: {e(result['name'])}</h1>"
        f'<p class="gapnote">{e(result["note"])}</p>'
        f'<p class="muted">Regelwerk {e(result["method"])}. Grundlage {e(result["name"])}: '
        f"Produkte {e(', '.join(focal['products']) or 'unbekannt')}; Anwendungsfälle "
        f"{e(', '.join(focal['use_cases']) or 'unbekannt')}; Zielgruppen "
        f"{e(', '.join(focal['targets']) or 'unbekannt')}; Länder "
        f"{e(', '.join(focal['market_countries']) or 'unbekannt')}.</p>"
        f'<form method="post" action="/company/{cid}/competitors/store">'
        f'<input type="hidden" name="csrf" value="{e(csrf)}">'
        "<button>Einordnung speichern (Historie)</button></form>"
        + ("".join(blocks) or "<p>Keine Vergleichsunternehmen in der Datenbank.</p>")
    )


def observation_page(detail: dict[str, Any]) -> str:
    o = detail["observation"]
    alternatives = "".join(
        f'<tr><td><a href="/observation/{"financial" if o["unit"] != "persons" else "employee"}/'
        f'{a["id"]}">{a["id"]}</a></td><td class="num">{num(a["value"])} {e(a["unit"])}</td>'
        f"<td>{e(a['provenance_class'])}</td><td>{e(a['verification_status'])}</td>"
        f'<td>{e(str(a["captured_at"])[:16])}</td><td class="url">{e(a["source_url"])}</td></tr>'
        for a in detail["alternatives"]
    )
    fetches = "".join(
        f"<tr><td>{e(str(f['at'])[:19])}</td><td>{e(f['status'])}</td><td>{e(f['http_status'])}</td>"
        f"<td>{e(f['purpose'])}</td></tr>"
        for f in detail["fetches"]
    )
    rows = [
        ("Kennzahl", METRIC_LABELS.get(o["metric"], o["metric"])),
        ("Wert", f"{o['value']} {o['unit']}"),
        ("Zeitraum", f"{o['period_start'] or '?'} bis {o['period_end']} ({o['period_type']})"),
        ("Segment", o["segment"] or "Gesamt"),
        ("Konsolidierungskreis", detail["scope_label"]),
        ("Herkunft", detail["provenance_label"]),
        ("Prüfstatus", o["verification_status"]),
        ("Sicherheit", o["confidence"]),
        ("Methodik", o["methodology"] or "–"),
        ("Einschränkungen", o["limitations"] or "–"),
        ("Fundstelle", o["locator"]),
        ("Extraktion", f"{o['extraction_method']} ({o['extractor_version']})"),
        ("Zitat geprüft", "ja" if o["quote_verified"] else "nein"),
        ("Berechnung", o["calculation"] or "–"),
        ("Dokument", f"{o['document_title'] or ''} – {o['source_url']}"),
        ("Quelle", o["source_key"]),
        ("abgerufen", str(o["retrieved_at"])[:19]),
        ("SHA-256 Dokument", o["sha256"]),
        ("erfasst", str(o["captured_at"])[:19]),
        ("zuletzt bestätigt", str(o["last_seen_at"])[:19]),
        ("abgeleitet aus", ", ".join(str(i) for i in detail["derived_from"]) or "–"),
    ]
    table = "".join(f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in rows)
    return (
        f'<p><a href="/company/{o["company_id"]}">← zurück zum Profil</a> · '
        f'<a href="/document/{o["source_document_id"]}">Dokument</a></p>'
        f"<h1>Herkunft von Beobachtung {o['id']}</h1>"
        f'<blockquote class="quote">{e(o["quote"])}</blockquote>'
        f'<table class="kv">{table}</table>'
        "<h2>Weitere Angaben für denselben Zeitraum</h2>"
        f'<div class="tablewrap"><table><tbody>{alternatives or "<tr><td>keine</td></tr>"}</tbody></table></div>'
        f'<h2>Abrufprotokoll</h2><div class="tablewrap"><table><tbody>{fetches or "<tr><td>(Upload, kein Abruf)</td></tr>"}</tbody></table></div>'
    )


def document_page(doc: dict[str, Any], excerpt: str) -> str:
    rows = "".join(
        f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>"
        for k, v in (
            ("URL", doc["url"]),
            ("Quelle", doc["source_key"]),
            ("Titel", doc["title"]),
            ("abgerufen", str(doc["retrieved_at"])[:19]),
            ("veröffentlicht", doc["published_at"] or "unbekannt"),
            ("Inhaltstyp", doc["content_type"]),
            ("SHA-256", doc["sha256"]),
        )
    )
    return (
        f'<h1>Dokument {doc["id"]}</h1><table class="kv">{rows}</table>'
        "<h2>Textauszug (gespeicherte Fassung, ohne Ausführung von Inhalten)</h2>"
        f'<pre class="excerpt">{e(excerpt)}</pre>'
    )


# ---------------------------------------------------------------------- Phase 4: Opportunity

BASIS = {
    "confirmed": "bestätigt",
    "derived": "abgeleitet",
    "unconfirmed": "unbestätigt (Vermutung)",
    "none": "keine",
}
WATCH_LABELS = {
    "vorgemerkt": "vorgemerkt",
    "in_pruefung": "in Prüfung",
    "qualifiziert": "qualifiziert (Vertrieb)",
    "verworfen": "verworfen",
    "entfernt": "von Liste entfernt",
}


def _quotes(items: list[dict[str, Any]]) -> str:
    return _list(
        [
            f'„{e(i["quote"][:260])}“ <span class="muted">– {e(i["url"])}, abgerufen '
            f"{e(i['retrieved_at'][:10])}, "
            f"{'eigene Angabe' if i['own_source'] else 'Drittquelle'}</span>"
            for i in items
        ],
        "keine",
    )


def _parts(parts: dict[str, Any]) -> str:
    rows = "".join(
        f'<tr><th>{e(k)}</th><td class="num">{num(v["score"], 2)}</td>'
        f'<td class="num">{e(v.get("weight", ""))}</td>'
        f'<td class="num">{e(v.get("contribution", ""))}</td><td>{e(v["basis"])}</td></tr>'
        for k, v in parts.items()
    )
    return (
        '<div class="tablewrap"><table><thead><tr><th>Komponente</th><th class="num">Wert</th>'
        '<th class="num">Gewicht</th><th class="num">Beitrag</th><th>Begründung</th></tr></thead>'
        f"<tbody>{rows or '<tr><td colspan=5><span class=unknown>unbekannt</span></td></tr>'}"
        "</tbody></table></div>"
    )


def opportunity_page(
    report: dict[str, Any], notes: list[dict[str, Any]], csrf: str, statuses: tuple[str, ...]
) -> str:
    """Ergebnis je Unternehmen in elf Abschnitten, Watchlist-Formular, Kommentare."""
    best = report["best"]
    scores = best["scores"]
    hyp = best["hypothesis"]
    cid = report["company_id"]
    p = report["profile"]
    fit = bool(report["applications"])
    products = "".join(
        f'<li><strong>{e(g["name"])}</strong> <span class="badge">{e(g["status"])}</span>'
        f'<br><span class="muted">Prozesse: {e(", ".join(g["processes"]))}<br>'
        f"Grenzen: {e('; '.join(g['limitations']))}<br>Belegte Prozessbegriffe: "
        f"{e(', '.join(g['matched_process_terms']) or 'keine')}</span> "
        + " ".join(
            f'<a class="ref" href="{e(src.split("|")[0].strip())}" rel="noreferrer">Hersteller</a>'
            for src in g["sources"][:2]
        )
        + "</li>"
        for g in (hyp["products"] if fit else [])
    )
    apps = "".join(
        f"<tr><td>{e(a['use_case_name'])}</td><td>{'Kern' if a['core_application'] else 'neue Branche'}"
        f' · {e(a["use_case_status"])}</td><td class="num">{a["scores"]["product_relevance"]}</td>'
        f'<td class="num">{a["scores"]["priority"]}</td><td>{e(a["segment"]["name"])}</td></tr>'
        for a in report["applications"]
    )
    development = "".join(
        f"<li>{e(s['title'])}: "
        + ", ".join(
            f"{e(r['label'])} {num(r['value']) if r['value'] is not None else '<span class=unknown>unbekannt</span>'}"
            for r in s["rows"]
        )
        + (
            f' · <span class="formula">{e(s["total"]["formula"])}</span>'
            if s["total"] and s["total"]["formula"]
            else ""
        )
        + "</li>"
        for s in report["development"]
    )
    signals = "".join(
        f'<tr><td><span class="badge {"warn" if sig["basis"] == "unconfirmed" else ""}">'
        f"{e(BASIS[sig['basis']])}</span></td><td>{e(sig['label'])}</td><td>{e(sig['text'])}</td>"
        f'<td>{e(sig["date"] or "unbekannt")}</td><td class="url">{e(sig["source_url"] or "")}</td></tr>'
        for sig in best["growth"]["signals"]
    )
    countries = _list(
        [
            f"{e(c['country'])}: {e(c['presence_label'])} {e(', '.join(c['site_types']))}"
            for c in report["locations"]["countries"]
        ]
    )
    sources = "".join(
        f'<li>{e(s["url"])} <span class="muted">abgerufen {e(s["retrieved_at"][:10])}, {e(s["source_key"])}</span></li>'
        for s in report["sources"]["evidence"]
    ) + "".join(
        f'<li>[{e(s["ref"])}] {e(s["source_name"])}: {e(s["url"])} <span class="muted">abgerufen '
        f"{e((s['retrieved_at'] or '')[:10])}</span></li>"
        for s in report["sources"]["profile"]
    )
    history = _list(
        [
            f"{e(str(h['created_at'])[:10])} {e(h['watchlist'])}: {e(WATCH_LABELS[h['status']])} – "
            f'{e(h["reason"])} <span class="muted">({e(h["author"])})</span>'
            for h in report["watch_history"]
        ],
        "nicht auf einer Watchlist",
    )
    comments = _list(
        [
            f'{e(n["body"])} <span class="muted">– {e(n["author"])}, {e(str(n["created_at"])[:16])}</span>'
            for n in notes
        ],
        "keine Kommentare",
    )
    options = "".join(f'<option value="{s}">{e(WATCH_LABELS[s])}</option>' for s in statuses)
    mentions = hyp["tool_mentions"]
    potential = scores["business_potential"]
    return (
        f'<p><a href="/company/{cid}">← Profil</a> · <a href="/leads">Leads</a></p>'
        f"<h1>Opportunity: {e(report['name'])}</h1>"
        f'<div class="cards"><div class="card"><h3>Segment</h3><strong>{e(best["segment"]["name"])}</strong>'
        f'<br><span class="muted">{e(best["segment"]["description"])}</span></div>'
        f'<div class="card"><h3>Priorität</h3><strong>{scores["priority"]}</strong><br>'
        f'<span class="muted">{e(scores["priority_basis"])}</span></div>'
        f'<div class="card"><h3>Produktrelevanz · Potenzial · Datenqualität</h3>'
        f"<strong>{scores['product_relevance']} · {potential if potential is not None else 'unbekannt'} · "
        f"{scores['data_quality']}</strong></div></div>"
        f'<p class="gapnote">{e(scores["disclaimer"])}</p>'
        "<h2>1. Unternehmensprofil</h2>"
        f'<table class="kv"><tr><th>Offizieller Name</th><td>{e(p["official_name"] or "unbekannt")}</td></tr>'
        f"<tr><th>Website</th><td>{e(p['website'] or 'unbekannt')}</td></tr>"
        f"<tr><th>Land</th><td>{e(p['country'] or 'unbekannt')}</td></tr>"
        f"<tr><th>Branche</th><td>{e(', '.join(p['industries']) or 'unbekannt')}</td></tr>"
        f"<tr><th>Umsatz</th><td>{figure_text(p['latest_revenue'], '', False)}</td></tr>"
        f"<tr><th>Mitarbeiter</th><td>{figure_text(p['latest_headcount'], '', False)}</td></tr></table>"
        "<h2>2. Passende Yellotools-Produkte</h2>"
        + (
            f"<ul>{products}</ul>"
            if fit
            else '<p class="unknown">Kein Produkt-Fit belegt – keine Produktgruppe vorgeschlagen.</p>'
        )
        + "<h2>3. Mögliche Anwendungsfälle</h2>"
        + (
            '<div class="tablewrap"><table><thead><tr><th>Anwendungsfall</th><th>Art</th>'
            '<th class="num">Produktrelevanz</th><th class="num">Priorität</th><th>Segment</th></tr>'
            f"</thead><tbody>{apps}</tbody></table></div>"
            if apps
            else '<p class="unknown">keiner belegt</p>'
        )
        + f"<h2>4. Belege für den Produkt-Fit</h2><p>{e(hyp['statement'])}</p>"
        f"<h3>Tätigkeit</h3>{_quotes(hyp['activity_evidence'])}"
        f"<h3>Arbeitsprozesse</h3>{_quotes(hyp['process_evidence'])}"
        f"<h3>Gegenindizien</h3>{_quotes(hyp['counter_evidence'])}"
        f'<h3>Werkzeug-Erwähnungen</h3><p class="muted">{e(mentions["note"])}</p>'
        f"{_quotes(mentions['own_brand'] + mentions['other_brands'])}"
        f"<h2>5. Umsatz- und Beschäftigungsentwicklung</h2>{('<ul>' + development + '</ul>') if development else '<p class=unknown>keine Reihe belegt (unbekannt, kein Stillstand)</p>'}"
        f"<h2>6. Belegte Wachstumsindikatoren</h2><p>Status {e(best['growth']['status'])}, Grundlage "
        f"{e(BASIS[best['growth']['basis']])}, Expansion {e(BASIS.get(best['growth']['expansion'], best['growth']['expansion']))}</p>"
        '<div class="tablewrap"><table><thead><tr><th>Belegstufe</th><th>Art</th><th>Inhalt</th>'
        f"<th>Ereignisdatum</th><th>Quelle</th></tr></thead><tbody>{signals or '<tr><td colspan=5>keine</td></tr>'}</tbody></table></div>"
        f"<h2>7. Relevante Standorte und Länder</h2>{countries}"
        "<h2>8. Bewertung mit Einzelbegründungen</h2>"
        f"<h3>Produktrelevanz {scores['product_relevance']}</h3>{_parts(scores['product_relevance_parts'])}"
        f"<h3>Geschäftliches Potenzial {potential if potential is not None else 'unbekannt'}</h3>{_parts(scores['business_potential_parts'])}"
        + (
            f'<p class="muted">unbekannt (nicht negativ bewertet): {e(", ".join(scores["business_potential_unknown"]))}</p>'
            if scores["business_potential_unknown"]
            else ""
        )
        + f"<h3>Datenqualität {scores['data_quality']}</h3>{_parts(scores['data_quality_parts'])}"
        f"<p>Segmentregel: {e('; '.join(best['segment']['reasons']))}</p>"
        f"<h2>9. Datenlücken und Unsicherheiten</h2>{_list([e(g) for g in best['gaps']])}"
        f"<h2>10. Empfohlene nächste Recherche</h2>{_list([e(s) for s in best['next_steps']])}"
        f"<h2>11. Quellen und Datum</h2><ul>{sources}</ul>"
        f"<h2>Watchlist und Kommentare</h2>{history}<h3>Kommentare</h3>{comments}"
        f'<form class="noteform" method="post" action="/company/{cid}/watch">'
        f'<input type="hidden" name="csrf" value="{e(csrf)}">'
        f'<label for="status">Status</label><select id="status" name="status">{options}</select>'
        '<label for="list">Watchlist</label><input id="list" name="list" value="Leads" maxlength="120">'
        '<label for="reason">Begründung</label><input id="reason" name="reason" maxlength="2000">'
        '<label for="author">Autor</label><input id="author" name="author" maxlength="120" required>'
        '<label for="comment">Kommentar (intern)</label><textarea id="comment" name="comment" rows="3" maxlength="10000"></textarea>'
        "<button>Speichern</button></form>"
    )


def leads_page(rows: list[dict[str, Any]], segments: Any, selected: tuple[str, ...]) -> str:
    """Lead-Liste mit Segmentfilter und CSV-Download."""
    options = "".join(
        f'<option value="{e(s.key)}"{" selected" if s.key in selected else ""}>{e(s.name)}</option>'
        for s in segments
    )
    body = "".join(
        f'<tr><td class="num">{e(r["prioritaet"])}</td><td><a href="/company/{r["firma_id"]}/opportunity">'
        f'{e(r["firma"])}</a><br><span class="muted">{e(r["website"])} {e(r["land"])}</span></td>'
        f'<td>{e(r["segment"])}</td><td>{e(r["anwendungsfall"])}<br><span class="muted">{e(r["produktgruppen"])}</span></td>'
        f'<td class="num">{e(r["produktrelevanz"])}</td><td class="num">{e(r["potenzial"])}</td>'
        f'<td class="num">{e(r["datenqualitaet"])}</td><td>{e(r["mitarbeiter"])}</td>'
        f"<td>{e(r['bewertungsstatus'])}</td></tr>"
        for r in rows
    )
    query = "&".join(f"segment={e(s)}" for s in selected)
    return (
        "<h1>Leads</h1>"
        '<p class="gapnote">Ein hoher Score bedeutet nur: nach den gewählten Kriterien interessant für '
        "eine weitere Prüfung. Bedarf, Werkzeuge, Lieferanten und Kaufbereitschaft sind nicht belegt.</p>"
        '<form class="search" method="get" action="/leads"><label for="segment">Segment</label>'
        f'<select id="segment" name="segment"><option value="">alle</option>{options}</select>'
        "<button>Filtern</button>"
        f'<a href="/leads.csv{"?" + query if query else ""}">CSV herunterladen</a></form>'
        '<div class="tablewrap"><table><thead><tr><th class="num">Priorität</th><th>Unternehmen</th>'
        '<th>Segment</th><th>Anwendungsfall / Produkte</th><th class="num">Fit</th>'
        '<th class="num">Potenzial</th><th class="num">Datenqualität</th><th>Mitarbeiter</th>'
        f"<th>Status</th></tr></thead><tbody>{body or '<tr><td colspan=9>Noch keine bewerteten Unternehmen – mi opportunities ausführen.</td></tr>'}"
        "</tbody></table></div>"
    )
