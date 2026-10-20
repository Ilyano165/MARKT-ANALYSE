"""Erzeugt src/market_intel/data/world_map.json aus world-atlas (Natural Earth, gemeinfrei).

Aufruf (Pakete vorher von registry.npmjs.org laden und entpacken):
    python tools/build_world_map.py world-atlas/countries-50m.json \\
        i18n-iso-countries/codes.json src/market_intel/data/world_map.json

TopoJSON wird dekodiert (quantisierte, deltakodierte Bögen), je Land in SVG-Pfade
(x = Länge, y = −Breite) umgerechnet und gerundet: Europa 0,1°, übrige Welt 0,5°.
"""

import json
import sys
from typing import Any


def decode_arcs(topo: dict[str, Any]) -> list[list[tuple[float, float]]]:
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0
        points = []
        for dx, dy in arc:
            x += dx
            y += dy
            points.append((x * sx + tx, y * sy + ty))
        arcs.append(points)
    return arcs


def ring(arcs: list[list[tuple[float, float]]], indices: list[int]) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for i in indices:
        arc = arcs[i] if i >= 0 else arcs[~i][::-1]
        points.extend(arc if not points else arc[1:])
    return points


def path(points: list[tuple[float, float]], step: float) -> str:
    out: list[tuple[float, float]] = []
    last = None
    for lon, lat in points:
        point = (round(lon / step) * step, round(-lat / step) * step)
        if point != last:
            out.append(point)
            last = point
    if len(out) < 3:
        return ""
    coords = (f"{x:.1f},{y:.1f}".replace(".0,", ",") for x, y in out)
    return "M" + "L".join(c.removesuffix(".0") for c in coords) + "Z"


def main(topo_path: str, codes_path: str, out_path: str) -> None:
    with open(topo_path, encoding="utf-8") as handle:
        topo = json.load(handle)
    with open(codes_path, encoding="utf-8") as handle:
        num_to_alpha2 = {c[2]: c[0] for c in json.load(handle)}
    arcs = decode_arcs(topo)
    countries = {}
    for geometry in topo["objects"]["countries"]["geometries"]:
        alpha2 = num_to_alpha2.get(str(geometry.get("id", "")))
        if not alpha2 or "arcs" not in geometry:
            continue
        polygons = geometry["arcs"] if geometry["type"] == "MultiPolygon" else [geometry["arcs"]]
        parts = [ring(arcs, r) for polygon in polygons for r in polygon]
        europe = any(-25 <= lon <= 45 and 34 <= lat <= 72 for p in parts for lon, lat in p)
        d = "".join(path(p, 0.1 if europe else 0.5) for p in parts)
        if d:
            countries[alpha2] = {"name": geometry["properties"]["name"], "d": d}
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "source": "Natural Earth 1:50m Admin 0 (gemeinfrei) über world-atlas 2.0.2 (ISC)",
                "projection": "equirectangular: x = Länge, y = -Breite (Grad)",
                "countries": countries,
            },
            handle,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    print(len(countries))


if __name__ == "__main__":
    main(*sys.argv[1:4])
