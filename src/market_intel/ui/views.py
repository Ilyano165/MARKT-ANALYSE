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
        f'<span class="muted">{"Recherche" if j["kind"] == "research" else "Neubewertung"}</span></td>'
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
        f'<span class="muted">{e(r["website"] or "keine Website bekannt")} · {e(r["country"] or "")}</span></td>'
        f"<td>{e(r['reason'])}</td>"
        f'<td class="num">{e(r["fit"] if r["fit"] is not None else "–")}</td>'
        f"<td>{e(r['fit_status'] or '–')}</td><td>{e(r['segment'] or '–')}</td></tr>"
        for r in results
    )
    live = status in ("queued", "running")
    return (
        '<p><a href="/jobs">← Rechercheaufträge</a></p>'
        f'<div class="pagehead"><h1>{e(job["title"] or f"Auftrag {jid}")}</h1><div class="actions">{actions}</div></div>'
        f'<section id="job-progress" class="card" data-url="/jobs/{jid}.json" data-live="{1 if live else 0}">'
        f'<p class="phase">{status_badge(status)} <span data-field="phase">{e(prog["phase"])}</span></p>'
        f'<div class="tiles">{tiles}</div>'
        + (f'<p class="errorbox">{e(prog["error"])}</p>' if prog.get("error") else "")
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
