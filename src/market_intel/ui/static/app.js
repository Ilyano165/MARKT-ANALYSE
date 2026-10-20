/* Market Intelligence – Abschnitte, Diagramme und Karte (ohne externe Bibliotheken).
 * Daten kommen aus <script type="application/json" id="profile-data">. Alle Texte werden
 * über textContent gesetzt (kein innerHTML mit Daten). Lücken werden nie als 0 gezeichnet. */
(function () {
  "use strict";
  var SVG = "http://www.w3.org/2000/svg";
  var nf0 = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 0 });
  var nf1 = new Intl.NumberFormat("de-DE", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  var STATUS = { first: "erster Wert", ok: "vergleichbar", gap: "unbekannt", after_gap: "nach Lücke", "break": "Bruch – nicht vergleichbar" };
  var FLAGS = { estimate: "Schätzung", derived: "umgerechnet", inorganic: "nicht organisch", conflict: "Widerspruch", low_confidence: "geringe Sicherheit" };
  var PRESENCE = { physical: "physischer Standort", supplied: "beliefert", "export": "Exportmarkt", mentioned: "nur erwähnt" };

  document.documentElement.classList.add("js");

  function el(name, attrs, parent) {
    var node = document.createElementNS(SVG, name);
    Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    if (parent) parent.appendChild(node);
    return node;
  }
  function html(tag, cls, text, parent) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    if (parent) parent.appendChild(node);
    return node;
  }
  function compact(v) {
    var a = Math.abs(v);
    if (a >= 1e9) return nf1.format(v / 1e9) + " Mrd.";
    if (a >= 1e6) return nf1.format(v / 1e6) + " Mio.";
    if (a >= 1e4) return nf0.format(v / 1e3) + " Tsd.";
    return nf0.format(v);
  }
  function readJson(id) {
    var node = document.getElementById(id);
    if (!node) return null;
    try { return JSON.parse(node.textContent); } catch (e) { return null; }
  }
  var data = readJson("profile-data");

  /* ------------------------------------------------------------ Abschnitte */
  function showSection(key) {
    var sections = document.querySelectorAll(".section");
    if (!sections.length) return;
    var found = false;
    sections.forEach(function (s) {
      var on = s.id === key;
      found = found || on;
      s.classList.toggle("active", on);
    });
    if (!found) { sections[0].classList.add("active"); key = sections[0].id; }
    document.querySelectorAll(".tabs a").forEach(function (a) {
      a.classList.toggle("active", a.getAttribute("data-tab") === key);
      if (a.getAttribute("data-tab") === key) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    drawVisible();
  }
  window.addEventListener("hashchange", function () {
    var key = location.hash.replace("#", "");
    if (key.indexOf("src-") === 0) { showSection("sources"); return; }
    showSection(key);
  });

  /* ------------------------------------------------------------ Tooltip */
  function tooltip(container) {
    var tip = html("div", "tip", null, container);
    tip.hidden = true;
    tip.setAttribute("role", "status");
    return {
      show: function (lines, x, y) {
        tip.textContent = "";
        lines.forEach(function (line, i) {
          if (line) html(i === 0 ? "strong" : "div", null, line, tip);
        });
        tip.hidden = false;
        var w = container.clientWidth;
        var left = Math.min(Math.max(x + 12, 0), Math.max(w - tip.offsetWidth - 4, 0));
        tip.style.left = left + "px";
        tip.style.top = Math.max(y - tip.offsetHeight - 8, 0) + "px";
      },
      hide: function () { tip.hidden = true; }
    };
  }

  /* ------------------------------------------------------------ Diagramme */
  function resolve(path) {
    var node = data;
    path.split(".").forEach(function (part) { node = node == null ? null : node[/^\d+$/.test(part) ? +part : part]; });
    return node;
  }

  function drawChart(container) {
    var series = resolve(container.getAttribute("data-series"));
    if (!series || !series.rows || !series.rows.length) { container.textContent = ""; return; }
    var mode = container.getAttribute("data-mode") || "value";
    container.textContent = "";
    var unit = series.unit === "persons" ? "Personen" : series.unit;
    var bar = html("div", "chart-toolbar", null, container);
    [["value", "Werte"], ["pct", "Veränderung %"], ["abs", "Veränderung absolut"]].forEach(function (m) {
      var b = html("button", null, m[1], bar);
      b.type = "button";
      b.setAttribute("aria-pressed", String(mode === m[0]));
      b.addEventListener("click", function () { container.setAttribute("data-mode", m[0]); drawChart(container); });
    });
    var width = Math.max(container.clientWidth || 600, 300);
    var height = 240, m = { l: 70, r: 12, t: 12, b: 30 };
    var rows = series.rows;
    function valueOf(r) {
      if (mode === "value") return r.point ? +r.point.value : null;
      if (mode === "pct") return r.change_pct === null ? null : +r.change_pct;
      return r.change_abs === null ? null : +r.change_abs;
    }
    var values = rows.map(valueOf).filter(function (v) { return v !== null; });
    var lo = Math.min(0, Math.min.apply(null, values.concat([0])));
    var hi = Math.max(0, Math.max.apply(null, values.concat([0])));
    if (hi === lo) hi = lo + 1;
    var pad = (hi - lo) * 0.1;
    if (hi > 0) hi += pad;
    if (lo < 0) lo -= pad;
    var svg = el("svg", { viewBox: "0 0 " + width + " " + height, "aria-hidden": "true" }, container);
    var defs = el("defs", {}, svg);
    var pat = el("pattern", { id: "hatch", width: 6, height: 6, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" }, defs);
    el("rect", { width: 6, height: 6, fill: "var(--series-soft)" }, pat);
    el("line", { x1: 0, y1: 0, x2: 0, y2: 6, stroke: "var(--series)", "stroke-width": 3 }, pat);
    var pw = width - m.l - m.r, ph = height - m.t - m.b;
    function y(v) { return m.t + ph - (v - lo) / (hi - lo) * ph; }
    var grid = el("g", { "class": "grid axis" }, svg);
    for (var i = 0; i <= 4; i++) {
      var v = lo + (hi - lo) * i / 4;
      el("line", { x1: m.l, x2: width - m.r, y1: y(v), y2: y(v) }, grid);
      var t = el("text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end" }, grid);
      t.textContent = mode === "pct" ? nf1.format(v) + " %" : compact(v);
    }
    var band = pw / rows.length, bw = Math.max(Math.min(band * 0.6, 48), 4);
    var tip = tooltip(container);
    rows.forEach(function (r, idx) {
      var cx = m.l + band * idx + band / 2;
      var lbl = el("text", { x: cx, y: height - 10, "text-anchor": "middle", "class": "lbl" }, svg);
      lbl.textContent = r.label;
      if (r.status === "break" && idx > 0) {
        el("line", { x1: m.l + band * idx, x2: m.l + band * idx, y1: m.t, y2: m.t + ph, "class": "breakline" }, svg);
      }
      var v = valueOf(r);
      if (r.point === null) {
        el("rect", { x: cx - bw / 2, y: m.t + ph * 0.55, width: bw, height: ph * 0.45 - 1, rx: 4, "class": "gapbox" }, svg);
        var g = el("text", { x: cx, y: m.t + ph * 0.5, "text-anchor": "middle", "class": "gaptext" }, svg);
        g.textContent = "unbekannt";
      } else if (v === null) {
        var n = el("text", { x: cx, y: y(0) - 4, "text-anchor": "middle", "class": "gaptext" }, svg);
        n.textContent = "n. b.";
      } else {
        var top = y(Math.max(v, 0)), bottom = y(Math.min(v, 0));
        var cls = "bar";
        if (r.flags.indexOf("estimate") >= 0) cls += " est";
        else if (r.flags.indexOf("derived") >= 0) cls += " derived";
        if (mode !== "value" && v < 0) cls = "bar neg";
        el("rect", { x: cx - bw / 2, y: top, width: bw, height: Math.max(bottom - top, 1), rx: 4, "class": cls }, svg);
      }
      var hit = el("rect", { x: m.l + band * idx, y: m.t, width: band, height: ph, "class": "hit", tabindex: 0 }, svg);
      hit.setAttribute("aria-label", r.label);
      function lines() {
        var out = [r.label + " – " + (STATUS[r.status] || r.status)];
        if (r.point) {
          out.push(nf0.format(+r.point.value) + " " + unit + (r.point.source_ref ? " [" + r.point.source_ref + "]" : ""));
          out.push((r.point.provenance_label || r.point.provenance) + " · " + (r.point.scope_label || r.point.scope));
        } else {
          out.push("Wert unbekannt (nicht 0)");
        }
        if (r.change_pct !== null) out.push("ggü. " + r.compared_with + ": " + (+r.change_pct > 0 ? "+" : "") + nf1.format(+r.change_pct) + " % (" + (+r.change_abs > 0 ? "+" : "") + nf0.format(+r.change_abs) + ")");
        if (r.since_label) out.push("seit " + r.since_label + ": " + (r.since_pct === null ? "–" : nf1.format(+r.since_pct) + " %"));
        r.flags.forEach(function (f) { out.push("• " + (FLAGS[f] || f)); });
        r.notes.slice(0, 3).forEach(function (n) { out.push(n); });
        return out;
      }
      function show(evt) {
        var box = container.getBoundingClientRect();
        var x = evt && evt.clientX ? evt.clientX - box.left : (cx / width) * box.width;
        var yy = evt && evt.clientY ? evt.clientY - box.top : 40;
        tip.show(lines(), x, yy);
      }
      hit.addEventListener("mousemove", show);
      hit.addEventListener("focus", function () { show(null); });
      hit.addEventListener("mouseleave", tip.hide);
      hit.addEventListener("blur", tip.hide);
    });
    var zero = el("line", { x1: m.l, x2: width - m.r, y1: y(0), y2: y(0), stroke: "var(--text-2)", "stroke-width": 1 }, svg);
    zero.setAttribute("shape-rendering", "crispEdges");
  }

  /* ------------------------------------------------------------ Karte */
  var worldPromise = null;
  function world() {
    if (!worldPromise) {
      var inline = readJson("world-map");
      worldPromise = inline ? Promise.resolve(inline) : fetch("/static/world_map.json").then(function (r) { return r.json(); });
    }
    return worldPromise;
  }
  function bboxOf(d, box) {
    var nums = d.match(/-?\d+(?:\.\d+)?/g) || [];
    for (var i = 0; i + 1 < nums.length; i += 2) {
      var x = +nums[i], yy = +nums[i + 1];
      if (x < box[0]) box[0] = x; if (yy < box[1]) box[1] = yy;
      if (x > box[2]) box[2] = x; if (yy > box[3]) box[3] = yy;
    }
  }
  function drawMap(container) {
    if (!data || !data.locations) return;
    var countries = {};
    data.locations.countries.forEach(function (c) { countries[c.country] = c; });
    var points = data.locations.map_points;
    world().then(function (w) {
      container.textContent = "";
      var box = [Infinity, Infinity, -Infinity, -Infinity];
      Object.keys(countries).forEach(function (code) {
        if (!w.countries[code]) return;
        // nur das größte Teilgebiet (Festland), sonst ziehen Überseegebiete den Ausschnitt auf
        var parts = w.countries[code].d.split("M").filter(Boolean);
        parts.sort(function (a, b) { return b.length - a.length; });
        bboxOf(parts[0], box);
      });
      points.forEach(function (p) { bboxOf(p.lon + "," + (-p.lat), box); });
      if (!isFinite(box[0])) box = [-12, -72, 45, -34];
      if (box[2] - box[0] > 200) box = [-170, -75, 190, 58];
      var padX = Math.max((box[2] - box[0]) * 0.12, 2), padY = Math.max((box[3] - box[1]) * 0.12, 2);
      var vb = [box[0] - padX, box[1] - padY, box[2] - box[0] + 2 * padX, box[3] - box[1] + 2 * padY];
      if (vb[2] / vb[3] < 1.4) { var extra = vb[3] * 1.4 - vb[2]; vb[0] -= extra / 2; vb[2] += extra; }
      if (vb[2] / vb[3] > 2.4) { var extraY = vb[2] / 2.4 - vb[3]; vb[1] -= extraY / 2; vb[3] += extraY; }
      var svg = el("svg", { viewBox: vb.join(" "), "aria-hidden": "true", preserveAspectRatio: "xMidYMid meet" }, container);
      var tip = tooltip(container);
      Object.keys(w.countries).forEach(function (code) {
        var info = countries[code];
        var path = el("path", { d: w.countries[code].d, "class": "country" + (info ? " " + info.presence : "") }, svg);
        if (info) {
          path.addEventListener("mousemove", function (evt) {
            var r = container.getBoundingClientRect();
            tip.show([w.countries[code].name + " (" + code + ")", PRESENCE[info.presence] || info.presence, info.site_types.join(", ")].concat(info.notes.slice(0, 2)), evt.clientX - r.left, evt.clientY - r.top);
          });
          path.addEventListener("mouseleave", tip.hide);
        }
      });
      var radius = vb[2] / 140;
      points.forEach(function (p) {
        var c = el("circle", { cx: p.lon, cy: -p.lat, r: radius, "class": "pt", tabindex: 0 }, svg);
        c.setAttribute("aria-label", p.label + " " + (p.city || ""));
        function show(evt) {
          var r = container.getBoundingClientRect();
          tip.show([p.label + ": " + (p.city || "") + " (" + (p.country || "?") + ")", "Genauigkeit: " + (p.precision === "site" ? "Adresse/Standort" : "Ort"), p.note, p.source_ref ? "Quelle [" + p.source_ref + "]" : ""], evt ? evt.clientX - r.left : 20, evt ? evt.clientY - r.top : 40);
        }
        c.addEventListener("mousemove", show);
        c.addEventListener("focus", function () { show(null); });
        c.addEventListener("mouseleave", tip.hide);
        c.addEventListener("blur", tip.hide);
      });
    }).catch(function () { container.textContent = "Karte nicht verfügbar – siehe Tabelle."; });
  }

  function drawVisible() {
    document.querySelectorAll(".section.active .chart, .section.active .map, body:not(.js) .chart").forEach(function (c) {
      if (c.getAttribute("data-drawn") === String(c.clientWidth)) return;
      c.setAttribute("data-drawn", String(c.clientWidth));
      if (c.classList.contains("map")) drawMap(c); else drawChart(c);
    });
  }
  var resizeTimer = null;
  window.addEventListener("resize", function () { clearTimeout(resizeTimer); resizeTimer = setTimeout(drawVisible, 150); });

  function init() {
    if (!data) return;
    document.querySelectorAll(".tabs a").forEach(function (a) {
      a.addEventListener("click", function (evt) {
        evt.preventDefault();
        history.replaceState(null, "", "#" + a.getAttribute("data-tab"));
        showSection(a.getAttribute("data-tab"));
      });
    });
    var key = location.hash.replace("#", "");
    showSection(key.indexOf("src-") === 0 ? "sources" : key);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
