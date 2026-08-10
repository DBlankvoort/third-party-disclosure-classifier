"""The IAB TCF Global Vendor List."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlparse

from . import DATA_DIR

GVL_PATH = DATA_DIR / "iab_gvl.json"


@dataclass(frozen=True)
class Vendor:
    """One registered TCF vendor."""

    id: int
    name: str
    policy_url: str = ""
    domain: str = ""
    purposes: tuple[int, ...] = ()
    legitimate_interest_purposes: tuple[int, ...] = ()
    special_purposes: tuple[int, ...] = ()
    uses_cookies: bool = False

    @property
    def all_purposes(self) -> tuple[int, ...]:
        return tuple(sorted(set(self.purposes) | set(self.legitimate_interest_purposes)))


def _policy_url(vendor: dict) -> str:
    for entry in vendor.get("urls") or ():
        if not isinstance(entry, dict):
            continue
        url = entry.get("privacy")
        if url and (entry.get("langId") or "en") == "en":
            return str(url)
    for entry in vendor.get("urls") or ():
        if isinstance(entry, dict) and entry.get("privacy"):
            return str(entry["privacy"])
    return ""


def _host(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return ""
    return host.lower().removeprefix("www.")


def _ints(values) -> tuple[int, ...]:
    return tuple(sorted(v for v in (values or ()) if isinstance(v, int)))


@lru_cache(maxsize=1)
def vendors() -> tuple[Vendor, ...]:
    """Every vendor the bundled list registers."""
    if not GVL_PATH.exists():
        return ()
    raw = json.loads(GVL_PATH.read_text(encoding="utf-8"))
    entries = raw.get("vendors")
    iterable = entries.values() if isinstance(entries, dict) else (entries or [])
    out: list[Vendor] = []
    for v in iterable:
        if not isinstance(v, dict) or not v.get("name"):
            continue
        policy = _policy_url(v)
        out.append(Vendor(
            id=int(v.get("id") or 0),
            name=str(v["name"]).strip(),
            policy_url=policy,
            domain=_host(policy),
            purposes=_ints(v.get("purposes")),
            legitimate_interest_purposes=_ints(v.get("legIntPurposes")),
            special_purposes=_ints(v.get("specialPurposes")),
            uses_cookies=bool(v.get("usesCookies")),
        ))
    return tuple(out)


@lru_cache(maxsize=1)
def by_id() -> dict[int, Vendor]:
    return {v.id: v for v in vendors() if v.id}


@lru_cache(maxsize=1)
def version() -> dict:
    """The list's own version stamp, for citing which edition produced a claim."""
    if not GVL_PATH.exists():
        return {}
    raw = json.loads(GVL_PATH.read_text(encoding="utf-8"))
    return {
        "vendor_list_version": raw.get("vendorListVersion"),
        "tcf_policy_version": raw.get("tcfPolicyVersion"),
        "gvl_specification_version": raw.get("gvlSpecificationVersion"),
        "last_updated": raw.get("lastUpdated"),
        "n_vendors": len(vendors()),
    }


def available() -> bool:
    return bool(vendors())
