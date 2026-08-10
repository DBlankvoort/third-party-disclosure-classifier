"""Headquarters country"""

from __future__ import annotations

import json
from functools import lru_cache

from . import DATA_DIR

COUNTRIES_PATH = DATA_DIR / "entity_countries.json"

# Lowercase forms only. Anything unmatched resolves to "".
_ALIAS_TO_ISO2 = {
    "united states": "us", "united states of america": "us", "usa": "us",
    "u.s.": "us", "u.s.a.": "us", "america": "us",
    "united kingdom": "gb", "u.k.": "gb", "uk": "gb", "great britain": "gb",
    "britain": "gb", "england": "gb", "scotland": "gb", "wales": "gb",
    "northern ireland": "gb",
    "ireland": "ie", "republic of ireland": "ie",
    "germany": "de", "deutschland": "de",
    "france": "fr",
    "netherlands": "nl", "holland": "nl", "the netherlands": "nl",
    "spain": "es", "italy": "it",
    "switzerland": "ch", "schweiz": "ch",
    "austria": "at", "belgium": "be", "sweden": "se", "norway": "no",
    "finland": "fi", "denmark": "dk", "poland": "pl",
    "czech republic": "cz", "czechia": "cz",
    "russia": "ru", "russian federation": "ru",
    "china": "cn", "people's republic of china": "cn", "prc": "cn",
    "hong kong": "hk", "taiwan": "tw", "japan": "jp",
    "south korea": "kr", "korea": "kr", "republic of korea": "kr",
    "india": "in", "singapore": "sg", "israel": "il", "canada": "ca",
    "mexico": "mx", "brazil": "br", "brasil": "br", "argentina": "ar",
    "australia": "au", "new zealand": "nz", "south africa": "za",
    "turkey": "tr", "türkiye": "tr",
    "united arab emirates": "ae", "uae": "ae",
    "luxembourg": "lu", "estonia": "ee", "latvia": "lv", "lithuania": "lt",
    "portugal": "pt", "greece": "gr", "malta": "mt", "cyprus": "cy",
    "iceland": "is",
    # Not a country, and the bucket a transfer clause names most often.
    "european union": "eu",
}

_ALPHA3 = {
    "usa": "us", "gbr": "gb", "deu": "de", "fra": "fr", "jpn": "jp",
    "chn": "cn", "irl": "ie", "esp": "es", "ita": "it", "nld": "nl",
    "can": "ca", "aus": "au", "bra": "br", "ind": "in", "rus": "ru",
    "isr": "il", "sgp": "sg", "che": "ch", "swe": "se", "dnk": "dk",
}

# Countries and unions the European Commission has found adequate, plus the
# EEA itself.
ADEQUATE_COUNTRIES = frozenset({
    "eu", "at", "be", "bg", "hr", "cy", "cz", "dk", "ee", "fi", "fr", "de",
    "gr", "hu", "ie", "it", "lv", "lt", "lu", "mt", "nl", "pl", "pt", "ro",
    "sk", "si", "es", "se", "is", "li", "no",
    "ad", "ar", "ca", "fo", "gg", "il", "im", "je", "jp", "nz", "kr", "ch",
    "gb", "uy",
})


def normalise_country(value: str | None) -> str:
    """An ISO-3166 alpha-2 code in lower case, or ""."""
    v = str(value or "").strip().lower().rstrip(".")
    if not v:
        return ""
    if v in _ALIAS_TO_ISO2:
        return _ALIAS_TO_ISO2[v]
    if len(v) == 2 and v.isalpha():
        return v
    return _ALPHA3.get(v, "")


def is_adequate(country: str) -> bool:
    return normalise_country(country) in ADEQUATE_COUNTRIES


@lru_cache(maxsize=1)
def country_overrides() -> dict[str, str]:
    if not COUNTRIES_PATH.exists():
        return {}
    raw = json.loads(COUNTRIES_PATH.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for name, country in raw.items():
        code = normalise_country(country)
        if name and code:
            out[str(name)] = code
    return out
