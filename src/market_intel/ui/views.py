"""Seiten der Arbeitsabläufe: Rechercheaufträge (Phase C), Importe und Datenpflege (Phase D).

Alle Werte kommen aus der Datenbank (``jobs.progress``, Importberichte); Texte werden mit
``e()`` maskiert. Es gibt keine Platzhalterwerte: Unbekanntes wird als unbekannt angezeigt.
"""

from __future__ import annotations

from typing import Any

from ..jobs import FINAL, RESUMABLE, STATUS_LABELS
from .render import e

COUNTRY_CHOICES = (
    ("DE", "Deutschland"),
    ("AT", "Österreich"),
    ("CH", "Schweiz"),
    ("NL", "Niederlande"),
    ("BE", "Belgien"),
    ("FR", "Frankreich"),
    ("IT", "Italien"),
    ("PL", "Polen"),
    ("CZ", "Tschechien"),
    ("DK", "Dänemark"),
    ("GB", "Vereinigtes Königreich"),
    ("US", "USA"),
)
KIND_LABELS = {
    "research": "Recherche",
    "reassess": "Neubewertung",
    "websites": "Website-Ermittlung",
    "analyze": "Website-Analyse und Neubewertung",
}
STATUS_CLASS = {
    "queued": "st-wait",
    "running": "st-run",
    "paused": "st-wait",
    "completed": "st-ok",
    "completed_with_gaps": "st-warn",
    "budget_exhausted": "st-warn",
    "cancelled": "st-muted",
    "failed": "st-err",
}


def _ts(value: Any) -> str:
    return e(str(value)[:16].replace("T", " ")) if value else "–"


def status_badge(status: str) -> str:
    return (
        f'<span class="status {STATUS_CLASS.get(status, "")}">'
        f"{e(STATUS_LABELS.get(status, status))}</span>"
    )


def jobs_page(jobs: list[dict[str, Any]], progress: dict[int, dict[str, Any]], worker: bool) -> str:
    rows = "".join(
        f'<tr><td class="num">{j["id"]}</td>'
        f'<td><a href="/jobs/{j["id"]}">{e(j["title"] or "(ohne Titel)")}</a><br>'
        f'<span class="muted">{KIND_LABELS.get(j["kind"], j["kind"])}</span></td>'
        f'<td>{status_badge(j["status"])}<br><span class="muted">{e(progress[j["id"]]["phase"])}</span></td>'
        f'<td class="num">{progress[j["id"]]["counts"].get("companies_selected", "–")}</td>'
        f"<td>{_ts(j['created_at'])}</td><td>{_ts(j['finished_at'])}</td></tr>"
        for j in jobs
    )
    worker_note = (
        ""
        if worker
        else '<div class="warnbox">Kein Hintergrund-Worker aktiv: Aufträge werden gespeichert, '
        "aber erst verarbeitet, wenn die Anwendung (oder <code>mi worker</code>) läuft.</div>"
    )
    return (
        '<div class="pagehead"><h1>Rechercheaufträge</h1>'
        '<a class="button primary" href="/jobs/new">Neuer Rechercheauftrag</a></div>'
        f"{worker_note}"
        '<p class="muted">Aufträge laufen im Hintergrund weiter, auch wenn diese Seite geschlossen '
        "wird. Nach einem Neustart wird an der nächsten offenen Teilaufgabe fortgesetzt.</p>"
        '<div class="tablewrap"><table><thead><tr><th class="num">Nr.</th><th>Auftrag</th>'
        '<th>Status</th><th class="num">Unternehmen</th><th>angelegt</th><th>beendet</th>'
        f"</tr></thead><tbody>{rows or '<tr><td colspan=6>Noch keine Aufträge.</td></tr>'}"
        "</tbody></table></div>"
    )


def new_job_page(
    cases: dict[str, Any], csrf: str, llm_available: bool, error: str = "", values: Any = None
) -> str:
    values = values or {}

    def val(name: str, default: str = "") -> str:
        return e(values.get(name, [default])[0])

    chosen_cases = set(values.get("use_case", []))
    chosen_countries = set(values.get("country", ["DE"]))
    case_boxes = "".join(
        f'<label class="check"><input type="checkbox" name="use_case" value="{e(k)}"'
        f"{' checked' if k in chosen_cases else ''}> {e(c.name)}</label>"
        for k, c in sorted(cases.items(), key=lambda kv: kv[1].name)
    )
    country_boxes = "".join(
        f'<label class="check"><input type="checkbox" name="country" value="{code}"'
        f"{' checked' if code in chosen_countries else ''}> {e(name)}</label>"
        for code, name in COUNTRY_CHOICES
    )
    llm = (
        '<label class="check"><input type="checkbox" name="use_llm" value="1"> Lokale KI (Ollama) '
        "für die Suchstrategie nutzen</label>"
        if llm_available
        else '<p class="muted">Lokale KI ist nicht eingerichtet – die Recherche läuft '
        "regelbasiert.</p>"
    )
    return (
        '<p><a href="/jobs">← Rechercheaufträge</a></p><h1>Neuer Rechercheauftrag</h1>'
        + (f'<div class="errorbox" role="alert">{e(error)}</div>' if error else "")
        + '<form class="jobform" method="post" action="/jobs">'
        f'<input type="hidden" name="csrf" value="{e(csrf)}">'
        "<fieldset><legend>Auftrag</legend>"
        f'<label for="title">Titel</label><input id="title" name="title" maxlength="200" value="{val("title")}" placeholder="z. B. Fahrzeugfolierer Süddeutschland">'
        '<label for="task">Beschreibung (frei, optional)</label>'
        f'<textarea id="task" name="task" rows="3" maxlength="2000" placeholder="z. B. Finde Fahrzeugfolierer in Deutschland">{val("task")}</textarea>'
        "</fieldset>"
        f'<fieldset><legend>Zielgruppe / Anwendungsfall</legend><div class="checks">{case_boxes}</div></fieldset>'
        "<fieldset><legend>Branche und Suchbegriffe</legend>"
        f'<label for="industry">Branchen (durch Komma getrennt)</label><input id="industry" name="industry" maxlength="500" value="{val("industry")}">'
        f'<label for="keywords">Zusätzliche Suchbegriffe (durch Komma getrennt)</label><input id="keywords" name="keywords" maxlength="500" value="{val("keywords")}">'
        '<label for="companies">Bestimmte Unternehmen (eines je Zeile, optional)</label>'
        f'<textarea id="companies" name="companies" rows="3" maxlength="4000">{val("companies")}</textarea>'
        "</fieldset>"
        f'<fieldset><legend>Regionen</legend><div class="checks">{country_boxes}</div></fieldset>'
        '<fieldset><legend>Umfang</legend><div class="grid3">'
        f'<div><label for="max_candidates">Höchstens Unternehmen</label><input id="max_candidates" name="max_candidates" type="number" min="1" max="200" value="{val("max_candidates", "10")}"></div>'
        f'<div><label for="max_requests">Höchstens Abrufe</label><input id="max_requests" name="max_requests" type="number" min="10" max="5000" value="{val("max_requests", "300")}"></div>'
        f'<div><label for="max_minutes">Höchstens Minuten</label><input id="max_minutes" name="max_minutes" type="number" min="1" max="600" value="{val("max_minutes", "30")}"></div>'
        "</div>"
        f"{llm}</fieldset>"
        '<p class="muted">Abgerufen werden nur frei zugängliche Quellen unter Beachtung von robots.txt '
        "und Abrufpausen. Gesperrte Quellen werden protokolliert, nicht umgangen.</p>"
        '<button class="primary">Auftrag starten</button></form>'
    )


def _website_tiles(job_id: int, counts: dict[str, int]) -> str:
    """Website-Stand der ausgewählten Unternehmen eines Auftrags (Phase G)."""
    if not counts or not sum(counts.values()):
        return ""
    open_count = counts.get("vorschlag", 0) + counts.get("pruefen", 0)
    labels = (
        ("bestaetigt", "Website bestätigt"),
        ("keine", "ohne Website"),
        ("vorschlag", "eindeutiger Vorschlag"),
        ("pruefen", "Vorschläge zu prüfen"),
        ("quelle", "aus Quelle, unbestätigt"),
    )
    return (
        '<h3>Websites der gefundenen Unternehmen</h3><div class="tiles">'
        + "".join(
            f'<div class="tile"><span class="v" data-website="{k}">{counts.get(k, 0)}</span><span class="l">{e(label)}</span></div>'
            for k, label in labels
        )
        + "</div>"
        + (
            f'<p><a class="button primary" href="/websites?job={job_id}">{open_count} Website-Vorschläge prüfen</a></p>'
            if open_count
            else ""
        )
    )


def job_page(
    job: dict[str, Any],
    prog: dict[str, Any],
    events: list[dict[str, Any]],
    results: list[dict[str, Any]],
    csrf: str,
    report_path: str | None,
) -> str:
    jid = job["id"]
    status = job["status"]

    def action(name: str, label: str, cls: str = "") -> str:
        return (
            f'<form method="post" action="/jobs/{jid}/{name}" class="inline">'
            f'<input type="hidden" name="csrf" value="{e(csrf)}">'
            f'<button class="{cls}">{e(label)}</button></form>'
        )

    actions = ""
    if status in ("queued", "running") and not job["control"]:
        actions += action("pause", "Anhalten") + action("cancel", "Abbrechen", "danger")
    if status in RESUMABLE:
        actions += action("resume", "Fortsetzen", "primary")
    if status in ("paused", "budget_exhausted"):
        actions += action("cancel", "Abbrechen", "danger")
    if job["control"]:
        actions += f'<span class="muted">{"Anhalten" if job["control"] == "pause" else "Abbruch"} angefordert …</span>'
    counts = prog["counts"]
    count_items = (
        ("Unternehmen gefunden", counts.get("companies_found")),
        ("ausgewählt", counts.get("companies_selected")),
        ("Quellen abgerufen", counts.get("sources_checked")),
        ("davon erfolgreich", counts.get("sources_ok")),
        ("davon gescheitert", counts.get("sources_failed")),
        ("Unternehmen bewertet", counts.get("companies_assessed")),
        ("Website-Prüfabrufe", counts.get("website_checks")),
    )
    tiles = "".join(
        f'<div class="tile"><span class="v" data-count="{k}">{e(v if v is not None else "–")}</span>'
        f'<span class="l">{e(label)}</span></div>'
        for (label, v), k in zip(
            count_items,
            (
                "companies_found",
                "companies_selected",
                "sources_checked",
                "sources_ok",
                "sources_failed",
                "companies_assessed",
                "website_checks",
            ),
            strict=True,
        )
    )
    steps = "".join(
        f'<tr><td class="num">{s["step"]}</td><td>{e(s["name"])}</td>'
        f'<td class="num">{s["ok"]}</td><td class="num">{s["failed"]}</td>'
        f'<td class="num">{s["skipped"]}</td><td class="num">{s["open"]}</td></tr>'
        for s in prog["steps"]
    )
    log_rows = "".join(
        f'<li class="lv-{e(ev["level"])}"><span class="muted">{_ts(ev["at"])}</span> {e(ev["message"])}</li>'
        for ev in events[-80:]
    )
    result_rows = "".join(
        f'<tr><td><a href="/company/{r["id"]}/opportunity">{e(r["name"])}</a><br>'
        + (
            f'<span class="muted">{e(r["website"])} · {e(r["country"] or "")}</span></td>'
            if r["website"]
            else f'<span class="muted">keine Website bekannt – <a href="/company/{r["id"]}#edit">eintragen</a> · {e(r["country"] or "")}</span></td>'
        )
        + f"<td>{e(r['reason'])}</td>"
        f'<td class="num">{e(r["fit"] if r["fit"] is not None else "–")}</td>'
        f"<td>{e(r['fit_status'] or '–')}</td><td>{e(r['segment'] or '–')}</td></tr>"
        for r in results
    )
    live = status in ("queued", "running")
    return (
        '<p><a href="/jobs">← Rechercheaufträge</a></p>'
        f'<div class="pagehead"><h1>{e(job["title"] or f"Auftrag {jid}")}</h1><div class="actions">{actions}</div></div>'
        f'<section id="job-progress" class="card" data-url="/jobs/{jid}.json" data-live="{1 if live else 0}">'
        f'<p class="phase">{status_badge(status)} <span data-field="phase">{e(prog["phase"]) if prog["phase"] != prog["status_label"] else ""}</span></p>'
        f'<div class="tiles">{tiles}</div>'
        + (f'<p class="errorbox">{e(prog["error"])}</p>' if prog.get("error") else "")
        + _website_tiles(jid, prog.get("websites") or {})
        + (
            '<div class="tablewrap"><table class="steps"><thead><tr><th class="num">Schritt</th><th>Name</th>'
            '<th class="num">ok</th><th class="num">Fehler</th><th class="num">übersprungen</th>'
            f'<th class="num">offen</th></tr></thead><tbody>{steps}</tbody></table></div>'
            if steps
            else ""
        )
        + f'<p class="muted">Angelegt {_ts(job["created_at"])} · gestartet {_ts(job["started_at"])} · '
        f"beendet {_ts(job['finished_at'])} · Versuche {job['attempts']}</p></section>"
        + (
            "<h2>Gefundene Unternehmen</h2>"
            '<p class="muted">Ausgewählte Kandidaten dieses Auftrags mit gespeicherter Bewertung. '
            "Jede Angabe ist auf der Opportunity-Seite mit Quelle belegt.</p>"
            '<div class="tablewrap"><table><thead><tr><th>Unternehmen</th><th>gefunden über</th>'
            '<th class="num">Fit</th><th>Belegstatus</th><th>Segment</th></tr></thead>'
            f"<tbody>{result_rows}</tbody></table></div>"
            if results
            else (
                "<h2>Gefundene Unternehmen</h2><p>Noch keine ausgewählten Unternehmen.</p>"
                if status not in FINAL
                else "<h2>Gefundene Unternehmen</h2><p>Dieser Auftrag hat kein Unternehmen ausgewählt.</p>"
            )
        )
        + (f"<p>Bericht: <code>{e(report_path)}</code></p>" if report_path else "")
        + f'<h2>Protokoll</h2><ul class="log">{log_rows or "<li>–</li>"}</ul>'
    )


# ---------------------------------------------------------------------- Importe (Phase D)

IMPORT_STATUS_CLASS = {
    "neu": "st-ok",
    "zugeordnet": "st-run",
    "doppelt": "st-warn",
    "übersprungen": "st-err",
}


def imports_page(
    lists: list[dict[str, Any]],
    customer_lists: list[str],
    cases: dict[str, Any],
    csrf: str,
    result: dict[str, Any] | None = None,
    error: str = "",
) -> str:
    """Upload, Ergebnis je Zeile, vorhandene Listen mit „Recherche starten“."""
    customer_options = "".join(f'<option value="{e(n)}">{e(n)}</option>' for n in customer_lists)
    report = ""
    if result:
        rows = "".join(
            f'<tr><td class="num">{r["line"]}</td><td>'
            + (
                f'<a href="/company/{r["company_id"]}">{e(r["name"])}</a>'
                if r["company_id"]
                else e(r["name"] or "–")
            )
            + f'</td><td><span class="status {IMPORT_STATUS_CLASS.get(r["status"], "")}">{e(r["status"])}</span></td>'
            f"<td>{e(r['detail'])}</td></tr>"
            for r in result["rows"]
        )
        counts = result["counts"]
        report = (
            f'<section class="card" id="import-result"><h2>Ergebnis: {e(result["list_name"])}</h2>'
            f'<div class="tiles">'
            + "".join(
                f'<div class="tile"><span class="v">{counts.get(k, 0)}</span><span class="l">{e(label)}</span></div>'
                for k, label in (
                    ("neu", "neu angelegt"),
                    ("zugeordnet", "vorhandenen Firmen zugeordnet"),
                    ("doppelt", "Dubletten in der Datei"),
                    ("übersprungen", "fehlerhafte Zeilen"),
                )
            )
            + "</div>"
            '<div class="tablewrap"><table><thead><tr><th class="num">Zeile</th><th>Firma</th><th>Ergebnis</th>'
            f"<th>Hinweis</th></tr></thead><tbody>{rows}</tbody></table></div></section>"
        )
    list_rows = "".join(
        f"<tr><td>{e(item['list_name'])}"
        + (
            ' <span class="badge">Bestandskunden</span>'
            if item["list_name"] in customer_lists
            else ""
        )
        + f'</td><td class="num">{item["members"]}</td><td>{e(str(item["imported_at"])[:16])}</td>'
        "<td>"
        + (
            ""
            if item["list_name"] in customer_lists
            else f'<form method="post" action="/imports/research" class="inline">'
            f'<input type="hidden" name="csrf" value="{e(csrf)}">'
            f'<input type="hidden" name="list_name" value="{e(item["list_name"])}">'
            f"<button>Recherche für diese Liste starten</button></form>"
        )
        + "</td></tr>"
        for item in lists
    )
    return (
        "<h1>Importe</h1>"
        + (f'<div class="errorbox" role="alert">{e(error)}</div>' if error else "")
        + report
        + '<section class="card"><h2>Firmenliste hochladen (CSV)</h2>'
        '<p class="muted">Spalten: <code>name</code> (Pflicht), optional <code>website</code>, '
        "<code>country</code> (ISO-Code), <code>lei</code>, <code>city</code>. Trennzeichen Komma, "
        "Semikolon oder Tab. Zuordnung zu vorhandenen Firmen nur über LEI, Website oder einen früheren "
        "Import derselben Liste – nie über einen bloßen Namensvergleich.</p>"
        '<form class="jobform" method="post" action="/imports" enctype="multipart/form-data">'
        f'<input type="hidden" name="csrf" value="{e(csrf)}">'
        '<label for="file">Datei</label><input id="file" name="file" type="file" accept=".csv,text/csv" required>'
        '<label for="kind">Art der Liste</label><select id="kind" name="kind">'
        '<option value="list">Firmenliste (z. B. Messe, Verband)</option>'
        f'<option value="customers">Bestandskunden ({e(", ".join(customer_lists) or "keine Liste konfiguriert")})</option></select>'
        '<label for="list_name">Listenname (bei Firmenliste)</label><input id="list_name" name="list_name" maxlength="120" placeholder="z. B. FESPA 2026 Aussteller">'
        + (
            f'<label for="customer_list">Bestandskundenliste</label><select id="customer_list" name="customer_list">{customer_options}</select>'
            if customer_lists
            else ""
        )
        + '<label for="source_url">Herkunft (URL, nur wenn bekannt)</label><input id="source_url" name="source_url" maxlength="500">'
        '<label for="publisher">Herausgeber (nur wenn bekannt)</label><input id="publisher" name="publisher" maxlength="200">'
        '<button class="primary">Importieren</button></form></section>'
        "<h2>Vorhandene Listen</h2>"
        '<div class="tablewrap"><table><thead><tr><th>Liste</th><th class="num">Firmen</th><th>zuletzt importiert</th><th></th></tr></thead>'
        f"<tbody>{list_rows or '<tr><td colspan=4>Noch keine Listen importiert.</td></tr>'}</tbody></table></div>"
    )


# ---------------------------------------------------------------------- Datenpflege (Phase D)


def maintenance_page(
    missing: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    csrf: str,
    query: str = "",
    message: str = "",
    error: str = "",
) -> str:
    def website_form(c: dict[str, Any]) -> str:
        return (
            f'<form method="post" action="/company/{c["id"]}/edit" class="rowform">'
            f'<input type="hidden" name="csrf" value="{e(csrf)}"><input type="hidden" name="back" value="/maintenance">'
            '<input type="hidden" name="field" value="website_domain">'
            f'<input name="value" aria-label="Website für {e(c["display_name"])}" placeholder="firma.de" maxlength="300" required>'
            '<input name="author" aria-label="Autor" placeholder="Autor" maxlength="120" required>'
            "<button>Speichern</button></form>"
        )

    missing_rows = "".join(
        f'<tr><td><a href="/company/{c["id"]}">{e(c["display_name"])}</a><br><span class="muted">{e(c["country_code"] or "Land unbekannt")}</span></td>'
        f"<td>{website_form(c)}</td></tr>"
        for c in missing
    )

    def group_block(g: dict[str, Any]) -> str:
        members = g["companies"]
        rows = "".join(
            f'<tr><td><a href="/company/{c["id"]}">{e(c["display_name"])}</a> <span class="muted">#{c["id"]}</span>'
            + (
                ' <span class="badge">öffentlicher Auftraggeber</span>'
                if c["entity_kind"] == "public_buyer"
                else ""
            )
            + f'</td><td>{e(c["website_domain"] or "–")}</td><td>{e(c["country_code"] or "–")}</td><td class="num">{c["signals"]}</td></tr>'
            for c in members
        )
        options = "".join(
            f'<option value="{c["id"]}">{e(c["display_name"])} (#{c["id"]})</option>'
            for c in members
        )
        ids = ",".join(str(c["id"]) for c in members)
        return (
            f'<div class="card"><p><strong>{e(g["reason"])}</strong>: {e(g["key"])}</p>'
            '<div class="tablewrap"><table><thead><tr><th>Datensatz</th><th>Website</th><th>Land</th><th class="num">Signale</th></tr></thead>'
            f"<tbody>{rows}</tbody></table></div>"
            '<form method="post" action="/maintenance/merge" class="rowform">'
            f'<input type="hidden" name="csrf" value="{e(csrf)}"><input type="hidden" name="ids" value="{ids}">'
            f'<label>Behalten <select name="keep">{options}</select></label>'
            '<input name="reason" placeholder="Begründung (Pflicht)" maxlength="500" required>'
            '<input name="author" placeholder="Autor" maxlength="120" required>'
            "<button>Zu einer Firma zusammenführen</button></form>"
            '<form method="post" action="/maintenance/distinct" class="rowform">'
            f'<input type="hidden" name="csrf" value="{e(csrf)}"><input type="hidden" name="ids" value="{ids}">'
            '<input name="author" placeholder="Autor" maxlength="120" required>'
            "<button>Sind verschiedene Firmen</button></form></div>"
        )

    return (
        "<h1>Datenpflege</h1>"
        + (f'<p class="okbox" role="status">{e(message)}</p>' if message else "")
        + (f'<div class="errorbox" role="alert">{e(error)}</div>' if error else "")
        + '<p class="muted">Fehlende Websites werden nie geraten. Eine eingetragene Website wird bei der '
        "nächsten Recherche oder Neubewertung genutzt; jede Änderung steht mit Autor und Grund im "
        "Änderungsprotokoll der Firma.</p>"
        f"<h2>Mögliche Dubletten ({len(groups)})</h2>"
        + ("".join(group_block(g) for g in groups) or "<p>Keine offenen Dubletten-Hinweise.</p>")
        + f"<h2>Firmen ohne Website ({len(missing)}{'+' if len(missing) >= 200 else ''})</h2>"
        '<form class="search" method="get" action="/maintenance"><label for="mq">Filter</label>'
        f'<input id="mq" name="q" value="{e(query)}" placeholder="Name"><button>Filtern</button></form>'
        '<div class="tablewrap"><table><thead><tr><th>Firma</th><th>Website eintragen</th></tr></thead>'
        f"<tbody>{missing_rows or '<tr><td colspan=2>Keine Firma ohne Website.</td></tr>'}</tbody></table></div>"
    )


def company_edit_block(company: dict[str, Any], edits: list[dict[str, Any]], csrf: str) -> str:
    """Stammdaten bearbeiten (auf der Profilseite) mit Änderungsprotokoll."""
    cid = company["id"]

    def form(field: str, label: str, value: str, placeholder: str) -> str:
        return (
            f'<form method="post" action="/company/{cid}/edit" class="rowform">'
            f'<input type="hidden" name="csrf" value="{e(csrf)}"><input type="hidden" name="field" value="{field}">'
            f'<input type="hidden" name="back" value="/company/{cid}#edit">'
            f'<label>{e(label)} <input name="value" value="{e(value)}" placeholder="{e(placeholder)}" maxlength="300"></label>'
            '<input name="reason" placeholder="Grund" maxlength="500">'
            '<input name="author" placeholder="Autor" maxlength="120" required>'
            "<button>Speichern</button></form>"
        )

    log = "".join(
        f"<li>{_ts(ed['created_at'])} · {e(ed['author'])}: {e(ed['field'])} "
        f"„{e(ed['old_value'] or '–')}“ → „{e(ed['new_value'] or '–')}“"
        + (f" ({e(ed['reason'])})" if ed["reason"] else "")
        + "</li>"
        for ed in edits
    )
    return (
        '<p class="muted">Leere Website = Website entfernen (z. B. falsche Zuordnung). '
        "Es wird nichts geraten.</p>"
        + form("website_domain", "Website", company.get("website_domain") or "", "firma.de")
        + form("display_name", "Name", company.get("display_name") or "", "")
        + form("country_code", "Land (ISO)", company.get("country_code") or "", "DE")
        + f'<h3>Änderungsprotokoll</h3><ul class="log">{log or "<li>Keine manuellen Änderungen.</li>"}</ul>'
    )


# ---------------------------------------------------------------------- Übersicht und Stammseiten


def _tile(value: Any, label: str, href: str = "") -> str:
    inner = f'<span class="v">{e(value)}</span><span class="l">{e(label)}</span>'
    return (
        f'<a class="tile link" href="{e(href)}">{inner}</a>'
        if href
        else f'<div class="tile">{inner}</div>'
    )


def dashboard_page(
    stats: dict[str, Any], recent_jobs: list[dict[str, Any]], top_leads: list[dict[str, Any]]
) -> str:
    """Startseite: echter Stand aus der Datenbank, schnelle Wege zu den Arbeitsabläufen."""
    jobs_rows = "".join(
        f'<tr><td><a href="/jobs/{j["id"]}">{e(j["title"] or f"Auftrag {j['id']}")}</a></td>'
        f"<td>{status_badge(j['status'])}</td><td>{_ts(j['created_at'])}</td></tr>"
        for j in recent_jobs
    )
    lead_rows = "".join(
        f'<tr><td><a href="/company/{r["firma_id"]}/opportunity">{e(r["firma"])}</a></td>'
        f'<td class="num">{e(r["prioritaet"])}</td><td>{e(r["belegstatus"].split(":")[0])}</td>'
        f"<td>{e(r['anwendungsfall'])}</td></tr>"
        for r in top_leads
    )
    hints = []
    if stats["stale"]:
        hints.append(
            f'<li><a href="/leads">{stats["stale"]} veraltete Bewertungen</a> – neu bewerten</li>'
        )
    if stats.get("website_pending"):
        hints.insert(
            0,
            f'<li><a href="/websites">{stats["website_pending"]} Unternehmen mit Website-Vorschlägen</a> – prüfen und bestätigen</li>',
        )
    if stats.get("unassessed"):
        hints.append(
            f'<li><a href="/leads">{stats["unassessed"]} Unternehmen noch nicht bewertet</a></li>'
        )
    if stats["missing_websites"]:
        hints.append(
            f'<li><a href="/maintenance">{stats["missing_websites"]} Firmen ohne Website</a> – ohne Website keine eigenen Belege</li>'
        )
    if stats["duplicates"]:
        hints.append(
            f'<li><a href="/maintenance">{stats["duplicates"]} mögliche Dubletten</a> prüfen</li>'
        )
    if not stats["backup"]:
        hints.append('<li><a href="/settings">Noch keine Datensicherung</a></li>')
    if not stats["contact_email"]:
        hints.append(
            '<li><a href="/settings">Kontaktadresse fehlt</a> – SEC und Wikimedia verlangen sie im User-Agent</li>'
        )
    return (
        '<div class="pagehead"><h1>Übersicht</h1>'
        '<a class="button primary" href="/jobs/new">Neuer Rechercheauftrag</a></div>'
        '<div class="tiles">'
        + _tile(stats["companies"], "Unternehmen", "/companies")
        + _tile(stats["leads"], "Leads mit Produkt-Fit", "/leads")
        + _tile(stats["running"], "Aufträge laufen/warten", "/jobs")
        + _tile(stats["customers"], "Bestandskunden", "/customers")
        + _tile(stats["sources_ok"], "erfolgreiche Abrufe (30 Tage)", "/sources")
        + "</div>"
        + (
            f'<section class="card"><h2>Zu erledigen</h2><ul>{"".join(hints)}</ul></section>'
            if hints
            else ""
        )
        + '<div class="cols"><section class="card"><h2>Letzte Aufträge</h2>'
        + (
            f"<table><tbody>{jobs_rows}</tbody></table>"
            if jobs_rows
            else '<p>Noch keine Aufträge. <a href="/jobs/new">Ersten Rechercheauftrag anlegen</a> oder <a href="/imports">Firmenliste importieren</a>.</p>'
        )
        + '</section><section class="card"><h2>Beste aktuelle Leads</h2>'
        + (
            f'<table><thead><tr><th>Firma</th><th class="num">Priorität</th><th>Belegstatus</th><th>Anwendungsfall</th></tr></thead><tbody>{lead_rows}</tbody></table>'
            if lead_rows
            else "<p>Noch keine bewerteten Leads.</p>"
        )
        + '<p class="muted">Priorität = interessant für weitere Prüfung, keine Verkaufsprognose.</p></section></div>'
    )


def companies_page(
    rows: list[dict[str, Any]], query: str, page: int, total: int, per_page: int
) -> str:
    pages = max(1, -(-total // per_page))
    items = "".join(
        f'<tr><td><a href="/company/{r["id"]}">{e(r["display_name"])}</a>'
        + (
            ' <span class="badge">öffentl. Auftraggeber</span>'
            if r["entity_kind"] == "public_buyer"
            else ""
        )
        + f"</td><td>{e(r['country_code'] or '–')}</td><td>{e(r['website_domain'] or '–')}</td>"
        f'<td class="num">{r["observations"]}</td><td class="num">{r["locations"]}</td>'
        f"<td>{e(str(r['updated_at'])[:10])}</td></tr>"
        for r in rows
    )
    q = f"&q={e(query)}" if query else ""
    nav = (
        '<nav class="pager" aria-label="Seiten">'
        + (f'<a href="/companies?page={page - 1}{q}">← zurück</a>' if page > 1 else "")
        + f"<span>Seite {page} von {pages} · {total} Unternehmen</span>"
        + (f'<a href="/companies?page={page + 1}{q}">weiter →</a>' if page < pages else "")
        + "</nav>"
    )
    return (
        '<div class="pagehead"><h1>Unternehmen</h1><a class="button" href="/imports">Liste importieren</a></div>'
        '<form class="search" method="get" action="/companies"><label for="q">Suche</label>'
        f'<input id="q" name="q" value="{e(query)}" placeholder="Name oder Domain">'
        "<button>Suchen</button></form>"
        '<div class="tablewrap"><table><thead><tr><th>Name</th><th>Land</th><th>Webseite</th>'
        '<th class="num">Kennzahlen</th><th class="num">Standorte</th><th>aktualisiert</th>'
        f"</tr></thead><tbody>{items or '<tr><td colspan=6>Keine Treffer</td></tr>'}</tbody></table></div>"
        f"{nav}"
    )


def customers_page(rows: list[dict[str, Any]], lists: list[str]) -> str:
    items = "".join(
        f'<tr><td><a href="/company/{r["id"]}">{e(r["name"])}</a></td><td>{e(r["website"] or "–")}</td>'
        f"<td>{e(r['basis'])}</td></tr>"
        for r in rows
    )
    return (
        '<div class="pagehead"><h1>Bestandskunden</h1><a class="button" href="/imports">Bestandskunden importieren</a></div>'
        f'<p class="muted">Quelle: Listen {e(", ".join(lists) or "(keine konfiguriert)")} und als „gewonnen“ '
        "erfasste Vertriebsergebnisse. Bestandskunden erscheinen nicht als neue Leads und werden in "
        "Recherchen nicht erneut ausgewählt.</p>"
        '<div class="tablewrap"><table><thead><tr><th>Unternehmen</th><th>Website</th><th>Grundlage</th></tr></thead>'
        f"<tbody>{items or '<tr><td colspan=3>Noch keine Bestandskunden erfasst.</td></tr>'}</tbody></table></div>"
    )


def products_page(catalog: Any, matrix: dict[str, Any], cases: dict[str, Any]) -> str:
    rows = "".join(
        f'<tr><td><strong>{e(g.name)}</strong><br><span class="muted">{e(g.description)}</span></td>'
        f"<td>{e(', '.join(g.processes) or '–')}</td>"
        f"<td>{e(', '.join(cases[a].name for a in g.applications if a in cases) or '–')}</td>"
        f"<td>{e('; '.join(g.limitations) or '–')}</td>"
        f'<td><span class="badge warn">{e(g.status)}</span></td></tr>'
        for g in catalog.products.values()
    )
    return (
        "<h1>Produkt-Fit: Yellotools-Produktmatrix</h1>"
        f'<p class="gapnote">{e(matrix["label"])}</p>'
        '<p class="muted">Grundlage jeder Bewertung. Pflege über <code>mi products export-csv</code> / '
        "<code>import-csv</code>; eine Änderung macht alle Bewertungen „veraltet“ (Neubewertung über die Lead-Liste).</p>"
        '<div class="tablewrap"><table><thead><tr><th>Produktgruppe</th><th>Prozesse</th><th>Anwendungsfälle</th>'
        f"<th>Grenzen</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table></div>"
    )


def sources_page(sources: list[Any], stats: dict[str, dict[str, int]]) -> str:
    rows = "".join(
        f'<tr><td><strong>{e(s.name)}</strong><br><span class="muted">{e(s.publisher)}</span></td>'
        f"<td>{e(s.license)}</td><td>{e(s.free_scope)}</td>"
        f"<td>{'abrufbar' if s.fetchable else e(s.legal_status)}</td>"
        f'<td class="num">{stats.get(s.key, {}).get("ok", 0)}</td>'
        f'<td class="num">{stats.get(s.key, {}).get("failed", 0)}</td></tr>'
        for s in sources
    )
    return (
        "<h1>Quellen und Belege</h1>"
        '<p class="muted">Nur frei zugängliche Quellen; robots.txt, Abrufpausen und Sperren werden '
        "beachtet, nicht umgangen. Jede Zahl im Profil verweist auf Dokument, Zitat und Abrufzeit.</p>"
        '<div class="tablewrap"><table><thead><tr><th>Quelle</th><th>Lizenz</th><th>Kostenloser Umfang</th>'
        '<th>Status</th><th class="num">Abrufe ok (30 Tage)</th><th class="num">gescheitert</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div>"
    )


def settings_page(
    info: dict[str, Any], backups: list[Any], csrf: str, message: str = "", error: str = ""
) -> str:
    rows = "".join(f'<tr><th scope="row">{e(k)}</th><td>{e(v)}</td></tr>' for k, v in info.items())
    backup_rows = "".join(
        f'<tr><td>{e(b.created.strftime("%Y-%m-%d %H:%M"))} UTC</td><td class="num">{b.size / 1e6:.1f} MB</td>'
        f"<td><code>{e(str(b.path))}</code></td></tr>"
        for b in backups
    )
    return (
        "<h1>Einstellungen</h1>"
        + (f'<p class="okbox" role="status">{e(message)}</p>' if message else "")
        + (f'<div class="errorbox" role="alert">{e(error)}</div>' if error else "")
        + f'<section class="card"><h2>Anwendung</h2><table><tbody>{rows}</tbody></table>'
        '<p class="muted">Einstellungen ändern: Datei <code>market_intel.toml</code> im Datenordner '
        "(z. B. <code>contact_email</code>); wirksam nach Neustart. Zugangsdaten gehören nicht in die Datei.</p></section>"
        '<section class="card"><h2>Datensicherung</h2>'
        '<p class="muted">Geprüfte Sicherung der Datenbank (auch während Aufträge laufen); die letzten 10 '
        "bleiben erhalten, beim Start wird täglich automatisch gesichert.</p>"
        '<form method="post" action="/settings/backup">'
        f'<input type="hidden" name="csrf" value="{e(csrf)}"><button class="primary">Sicherung jetzt erstellen</button></form>'
        '<div class="tablewrap"><table><thead><tr><th>Zeitpunkt</th><th class="num">Größe</th><th>Datei</th></tr></thead>'
        f"<tbody>{backup_rows or '<tr><td colspan=3>Noch keine Sicherung.</td></tr>'}</tbody></table></div>"
        "<p><strong>Wiederherstellen:</strong> Anwendung beenden, dann in der Eingabeaufforderung "
        "<code>mi backup restore &lt;Datei&gt;</code>. Der aktuelle Stand wird vorher automatisch gesichert.</p></section>"
    )


# ---------------------------------------------------------------------- Website-Ermittlung (Phase G)

VERDICT_CLASS = {
    "eindeutig": "st-ok",
    "wahrscheinlich": "st-run",
    "unklar": "st-wait",
    "widerspricht": "st-err",
    "nicht_erreichbar": "st-muted",
}
SOURCE_STATUS = {
    "ok": "Treffer",
    "keine": "kein Treffer",
    "fehler": "nicht erreichbar",
    "deaktiviert": "deaktiviert",
    "nicht_prüfbar": "nicht prüfbar",
}


def _decision_forms(c: dict[str, Any], csrf: str, back: str, compact: bool = False) -> str:
    """Ein Formular, zwei eindeutige Aktionen (Bestätigen / Ablehnen)."""
    if c["status"] not in ("vorgeschlagen", "nicht_gewaehlt"):
        return ""
    reachable = c["verdict"] != "nicht_erreichbar"
    label = (
        "Diese Website bestätigen"
        if c["status"] == "vorgeschlagen"
        else "Stattdessen diese bestätigen"
    )
    if not reachable:
        label = "Trotzdem bestätigen"
    reject = (
        f'<button class="danger" formaction="/websites/{c["id"]}/reject">Ablehnen</button>'
        if c["status"] == "vorgeschlagen"
        else ""
    )
    return (
        f'<form method="post" action="/websites/{c["id"]}/confirm" class="rowform decision">'
        f'<input type="hidden" name="csrf" value="{e(csrf)}"><input type="hidden" name="back" value="{e(back)}">'
        '<input name="author" placeholder="Ihr Name" maxlength="120" required aria-label="Autor">'
        + (
            ""
            if compact
            else '<input name="reason" placeholder="Grund (optional)" maxlength="500" aria-label="Grund">'
        )
        + f'<button class="{"primary" if reachable else ""}">{e(label)}</button>{reject}</form>'
    )


def _evidence_list(c: dict[str, Any]) -> str:
    d = c["details"]

    def items(key: str, cls: str, sign: str) -> str:
        return "".join(
            f'<li class="{cls}"><span aria-hidden="true">{sign}</span> {e(x)}</li>'
            for x in d.get(key, [])
        )

    sources = "".join(
        f"<li>{e(s.get('detail', ''))}"
        + (f' – <a href="/document/{s["document_id"]}">Beleg</a>' if s.get("document_id") else "")
        + "</li>"
        for s in c["sources"]
    )
    pages = " ".join(
        f'<a href="/document/{d_id}">Seite {i}</a>' for i, d_id in enumerate(c["document_ids"], 1)
    )
    return (
        '<div class="evidence">'
        f'<ul class="checks-list">{items("pro", "pro", "✓")}{items("contra", "contra", "✗")}{items("unchecked", "open", "?")}</ul>'
        + "".join(f'<p class="muted">Hinweis: {e(n)}</p>' for n in d.get("notes", []))
        + f"<details><summary>Woher der Vorschlag stammt und geprüfte Seiten</summary><ul>{sources}</ul>"
        f"<p>{pages or 'keine Seite abrufbar'}</p></details></div>"
    )


def candidate_card(c: dict[str, Any], csrf: str, back: str, *, company: str = "") -> str:
    return (
        f'<article class="card candidate" id="kandidat-{c["id"]}">'
        '<div class="candhead">'
        + (f'<p class="muted">{e(company)}</p>' if company else "")
        + f'<h3><a href="{e(c["url"])}">{e(c["domain"])}</a></h3>'
        f'<span class="scorebadge" title="Matching-Score aus belegten Kriterien">{c["score"]}</span>'
        f'<span class="status {VERDICT_CLASS.get(c["verdict"], "")}">{e(c["verdict_label"])}</span>'
        f'<span class="status">{e(c["status_label"])}</span></div>'
        f'<p class="muted">{e(c["url"])} · zuletzt geprüft {_ts(c["checked_at"])}'
        + (
            f" · entschieden von {e(c['decided_by'])} {_ts(c['decided_at'])}"
            if c.get("decided_by")
            else ""
        )
        + "</p>"
        + _evidence_list(c)
        + _decision_forms(c, csrf, back)
        + "</article>"
    )


def website_section(
    company: dict[str, Any],
    state: str,
    state_label: str,
    candidates: list[dict[str, Any]],
    search: dict[str, Any] | None,
    csrf: str,
) -> str:
    cid = company["id"]
    back = f"/company/{cid}#websites"
    source = company.get("website_source")
    current = (
        f'<p><strong>Aktuelle Website:</strong> <a href="https://{e(company["website_domain"])}/">{e(company["website_domain"])}</a> '
        f'<span class="status {"st-ok" if state == "bestaetigt" else "st-warn"}">{e(state_label)}</span>'
        + (f' <span class="muted">({e(source)})</span>' if source and state != "bestaetigt" else "")
        + "</p>"
        if company.get("website_domain")
        else f'<p><strong>Keine Website bekannt.</strong> <span class="muted">{e(state_label)}</span></p>'
    )
    open_items = [c for c in candidates if c["status"] == "vorgeschlagen"]
    other = [c for c in candidates if c["status"] != "vorgeschlagen"]
    sources = ""
    if search:
        rows = "".join(
            f"<tr><td>{e(k)}</td><td>{e(SOURCE_STATUS.get(v.get('status'), v.get('status')))}</td>"
            f'<td class="num">{v.get("hits", 0)}</td><td>{e(v.get("error") or "")}</td></tr>'
            for k, v in search["outcome"].get("sources", {}).items()
        )
        sources = (
            f"<details><summary>Letzte Suche {_ts(search['searched_at'])}: Quellen</summary>"
            '<div class="tablewrap"><table><thead><tr><th>Quelle</th><th>Ergebnis</th><th class="num">Treffer</th><th>Hinweis</th></tr></thead>'
            f"<tbody>{rows}</tbody></table></div>"
            + (
                f'<p class="muted">Nicht erneut vorgeschlagen (abgelehnt, Suchgrundlage unverändert): {e(", ".join(search["outcome"].get("skipped_rejected", [])))}</p>'
                if search["outcome"].get("skipped_rejected")
                else ""
            )
            + "</details>"
        )
    return (
        current
        + '<p class="muted">Vorschläge werden nur mit Belegen gezeigt (Impressum, Adresse, Registernummer, Kontaktdaten). '
        "Fehlende Angaben zählen nie als Übereinstimmung. Erst eine bestätigte Website wird für die Unternehmensanalyse genutzt.</p>"
        + f'<form method="post" action="/company/{cid}/websites/search" class="inline"><input type="hidden" name="csrf" value="{e(csrf)}">'
        "<button>Erneut suchen</button></form>"
        + sources
        + (
            f"<h3>Offene Vorschläge ({len(open_items)})</h3>"
            + "".join(candidate_card(c, csrf, back) for c in open_items)
            if open_items
            else "<p>Keine offenen Vorschläge.</p>"
        )
        + (
            f"<details><summary>Entschiedene Vorschläge ({len(other)})</summary>"
            + "".join(candidate_card(c, csrf, back) for c in other)
            + "</details>"
            if other
            else ""
        )
        + "<h3>Website manuell eintragen</h3>"
        f'<form method="post" action="/company/{cid}/edit" class="rowform"><input type="hidden" name="csrf" value="{e(csrf)}">'
        f'<input type="hidden" name="field" value="website_domain"><input type="hidden" name="back" value="{e(back)}">'
        '<input name="value" placeholder="firma.de" maxlength="300" required aria-label="Website"><input name="reason" placeholder="Grund" maxlength="500" aria-label="Grund">'
        '<input name="author" placeholder="Ihr Name" maxlength="120" required aria-label="Autor"><button>Speichern</button></form>'
    )


def websites_review_page(
    items: list[dict[str, Any]],
    counts: dict[str, int],
    csrf: str,
    *,
    job: dict[str, Any] | None = None,
    message: str = "",
    error: str = "",
) -> str:
    from ..websites import STATE_LABELS

    back = f"/websites?job={job['id']}" if job else "/websites"
    clear = [c for c in items if c["verdict"] == "eindeutig"]
    per_company: dict[int, list[dict[str, Any]]] = {}
    for c in items:
        per_company.setdefault(int(c["company_id"]), []).append(c)
    blocks = "".join(
        f'<section class="reviewgroup"><h2><a href="/company/{cid}#websites">{e(cands[0]["company_name"])}</a> '
        f'<span class="muted">{len(cands)} {"Vorschlag" if len(cands) == 1 else "Vorschläge"}</span></h2>'
        + "".join(candidate_card(c, csrf, back) for c in cands)
        + "</section>"
        for cid, cands in per_company.items()
    )
    tiles = (
        "".join(
            f'<div class="tile"><span class="v">{counts.get(k, 0)}</span><span class="l">{e(label)}</span></div>'
            for k, label in STATE_LABELS.items()
        )
        if counts
        else ""
    )
    bulk = (
        '<form method="post" action="/websites/confirm-clear" class="rowform bulk">'
        f'<input type="hidden" name="csrf" value="{e(csrf)}"><input type="hidden" name="back" value="{e(back)}">'
        f'<input type="hidden" name="ids" value="{",".join(str(c["id"]) for c in clear)}">'
        '<input name="author" placeholder="Ihr Name" maxlength="120" required aria-label="Autor">'
        f'<button class="primary">Alle {len(clear)} eindeutigen Vorschläge bestätigen</button>'
        '<span class="muted">nur „eindeutig“ (Name mit Rechtsform und Adresse/Register/Kontakt belegt, kein Widerspruch); '
        "alle anderen bleiben zur Einzelprüfung</span></form>"
        if clear
        else ""
    )
    return (
        (f'<p><a href="/jobs/{job["id"]}">← {e(job["title"] or "Auftrag")}</a></p>' if job else "")
        + "<h1>Website-Prüfung</h1>"
        + (f'<p class="okbox" role="status">{e(message)}</p>' if message else "")
        + (f'<div class="errorbox" role="alert">{e(error)}</div>' if error else "")
        + (f'<div class="tiles">{tiles}</div>' if tiles else "")
        + bulk
        + (blocks or "<p>Keine offenen Website-Vorschläge.</p>")
    )
