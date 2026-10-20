"""Erzeugt src/market_intel/data/country_names.json aus i18n-iso-countries (MIT).

Aufruf: python tools/build_country_names.py i18n-iso-countries/langs \\
            src/market_intel/data/country_names.json
"""

import json
import sys

langs, out = sys.argv[1], sys.argv[2]
names: dict[str, list[str]] = {}
for lang in ("de", "en"):
    with open(f"{langs}/{lang}.json", encoding="utf-8") as handle:
        data = json.load(handle)["countries"]
    for code, value in data.items():
        for name in value if isinstance(value, list) else [value]:
            if name not in names.setdefault(code, []):
                names[code].append(name)
with open(out, "w", encoding="utf-8") as handle:
    json.dump(
        {"source": "i18n-iso-countries 7.14.0 (MIT), Sprachen de und en", "names": names},
        handle,
        ensure_ascii=False,
        separators=(",", ":"),
    )
