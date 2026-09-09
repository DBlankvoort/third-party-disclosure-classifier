from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from .collect.base import Target
from .entities import registrable_domain
from .typology import TargetType

# --------------------------------------------------------------------------- #
# Store listings
# --------------------------------------------------------------------------- #
_PLAY_HOSTS = {"play.google.com", "play.app.goo.gl"}
_APPLE_HOSTS = {"apps.apple.com", "itunes.apple.com", "geo.itunes.apple.com"}
_APPLE_ID_RE = re.compile(r"(?:^|/)id(\d{4,})(?:$|[/?#])")
_PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+$")


def store_app_ref(url: str) -> tuple[str, str] | None:
    """``(target type, app id)`` when ``url`` names one store listing."""
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host in _PLAY_HOSTS:
        if not parsed.path.startswith("/store/apps/details"):
            return None
        app_id = (parse_qs(parsed.query).get("id") or [""])[0].strip()
        if not _PACKAGE_RE.match(app_id):
            return None
        return TargetType.PLAY_STORE_APP.value, app_id
    if host in _APPLE_HOSTS:
        match = _APPLE_ID_RE.search(parsed.path)
        if not match:
            return None
        return TargetType.APP_STORE_APP.value, match.group(1)
    return None


def store_target(url: str) -> Target | None:
    """The app target one store-listing URL names, if it names one."""
    ref = store_app_ref(url)
    if ref is None:
        return None
    target_type, app_id = ref
    return Target(
        id=f"{target_type}__{Target.make_id(app_id)}",
        type=target_type,
        name=app_id,
        url=url.strip(),
        app_id=app_id,
    )


# --------------------------------------------------------------------------- #
# Vendor-side sites
# --------------------------------------------------------------------------- #
BROKER_CATEGORIES = frozenset({
    "Advertising", "Ad Motivated Tracking", "Audience Measurement",
    "Third-Party Analytics Marketing", "Action Pixels", "Session Replay",
    "Ad Fraud", "Obscure Ownership",
})
BROKER_ROLES = frozenset({"sellers_json", "vendors_json", "tcf_gvl"})


def _gvl_domains() -> frozenset[str]:
    from .kb import gvl

    return frozenset(
        d for d in (registrable_domain(v.domain) for v in gvl.vendors()) if d
    )


_GVL_CACHE: frozenset[str] | None = None


def gvl_domains() -> frozenset[str]:
    """Registrable domains the TCF vendor list registers."""
    global _GVL_CACHE
    if _GVL_CACHE is None:
        _GVL_CACHE = _gvl_domains()
    return _GVL_CACHE


def broker_signals(origin: str, docs=None) -> list[str]:
    from .kb import tracker_radar

    host = (urlparse(origin).hostname or "").lower().removeprefix("www.")
    reg = registrable_domain(host)
    signals: list[str] = []
    if reg and reg in gvl_domains():
        signals.append("tcf_gvl")
    index = tracker_radar.index()
    for candidate in (host, reg):
        record = index.lookup_domain(candidate) if candidate else None
        if record is None:
            continue
        hit = sorted(set(record.categories) & BROKER_CATEGORIES)
        if hit:
            signals.append(f"tracker_radar:{hit[0]}")
            break
    for doc in docs or ():
        role = getattr(doc, "role", "") or (doc.get("role") if isinstance(doc, dict) else "")
        ok = getattr(doc, "ok", True) if not isinstance(doc, dict) else doc.get("ok", True)
        if role in BROKER_ROLES and ok:
            signals.append(f"publishes:{role}")
            break
    return signals


def kind_for(origin: str, target: Target | None = None, docs=None) -> str:
    if target is not None and target.type in {
        TargetType.PLAY_STORE_APP.value, TargetType.APP_STORE_APP.value,
    }:
        return target.type
    if broker_signals(origin, docs):
        return TargetType.DATA_BROKER.value
    return TargetType.WEBSITE.value


# --------------------------------------------------------------------------- #
# View policy
# --------------------------------------------------------------------------- #
MAIN_VIEW = "main"
PROSE_VIEW = "discloses_relation_with"
VENDOR_VIEW = "lists_vendor"
ADTECH_VIEW = "authorises_inventory_sale"
TRAFFIC_VIEW = "contacts_domain"

ALL_VIEWS = (PROSE_VIEW, VENDOR_VIEW, ADTECH_VIEW, TRAFFIC_VIEW)

_VIEWS: dict[str, tuple[str, ...]] = {
    TargetType.WEBSITE.value: (PROSE_VIEW, ADTECH_VIEW, TRAFFIC_VIEW),
    TargetType.DATA_BROKER.value: (PROSE_VIEW, VENDOR_VIEW, ADTECH_VIEW, TRAFFIC_VIEW),
    TargetType.PLAY_STORE_APP.value: (PROSE_VIEW,),
    TargetType.APP_STORE_APP.value: (PROSE_VIEW,),
}

_APP_KINDS = frozenset({
    TargetType.PLAY_STORE_APP.value, TargetType.APP_STORE_APP.value,
})


def views_for(kind: str) -> list[str]:
    return list(_VIEWS.get(kind, _VIEWS[TargetType.WEBSITE.value]))


def graph_views_for(kind: str) -> list[str]:
    views = [view for view in views_for(kind) if view != VENDOR_VIEW]
    return views if len(views) < 2 else [MAIN_VIEW, *views]


def default_view_for(kind: str) -> str:
    return PROSE_VIEW if kind in _APP_KINDS else MAIN_VIEW


def supports(kind: str, view: str) -> bool:
    return view == MAIN_VIEW or view in _VIEWS.get(
        kind, _VIEWS[TargetType.WEBSITE.value])


def profile(origin: str, target: Target | None = None, docs=None) -> dict:
    kind = kind_for(origin, target, docs)
    return {
        "site_kind": kind,
        "site_kind_signals": broker_signals(origin, docs)
        if kind == TargetType.DATA_BROKER.value else [],
        "views": views_for(kind),
        "graph_views": graph_views_for(kind),
        "default_view": default_view_for(kind),
    }
