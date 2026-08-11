"""Traffic observed in a disposable browser profile."""

from __future__ import annotations

import sys

PRE_CONSENT = "pre_consent"
POST_CONSENT = "post_consent"

PROBE_TIMEOUT_MS = 20000
SETTLE_MS = 2500
ACCEPT_SETTLE_MS = 3000
CLICK_TIMEOUT_MS = 2500

_ACCEPT_TEXT = (
    "accept all", "accept all cookies", "allow all", "allow all cookies",
    "accept cookies", "allow cookies", "i accept", "agree and continue",
    "accept and continue", "consent and continue", "got it", "ok, got it",
    "alle akzeptieren", "alles akzeptieren", "alle zulassen", "einverstanden",
    "tout accepter", "accepter tout", "accepter et continuer",
    "aceptar todo", "aceptar todas", "permitir todas",
    "accetta tutto", "accetta tutti", "consenti tutti",
    "alles accepteren", "alle cookies accepteren", "alles toestaan",
    "aceitar todos", "zaakceptuj wszystko", "acceptera alla",
    "godkänn alla", "hyväksy kaikki", "accepter alle", "godta alle",
)

_REJECT_TEXT = (
    "reject", "decline", "refuse", "deny", "necessary only", "essential only",
    "manage", "settings", "preferences", "customi", "options", "more info",
    "learn more", "purposes", "vendors", "partners", "ablehnen", "verwalten",
    "einstellungen", "refuser", "gérer", "paramètres", "rechazar",
    "configurar", "rifiuta", "gestisci", "impostazioni", "weigeren",
    "beheren", "instellingen", "odrzuć", "ustawienia",
)

_CLICKABLE = "button, [role=button], a[href='#'], a[role=button], input[type=button], input[type=submit]"


def _is_accept(text: str) -> bool:
    """Whether a control's label offers consent in full."""
    t = " ".join(text.split()).strip().lower().strip(".!→ ")
    if not t or len(t) > 60:
        return False
    if any(bad in t for bad in _REJECT_TEXT):
        return False
    return any(t == phrase or t.startswith(phrase) for phrase in _ACCEPT_TEXT)


def accept_consent(page) -> str:
    """Click the accept-all control of any consent dialog, in the main frame
    or a CMP's iframe. Returns the label clicked, empty when none was found.
    """
    for frame in page.frames:
        try:
            elements = frame.query_selector_all(_CLICKABLE)
        except Exception:  # noqa: BLE001
            continue
        for el in elements:
            try:
                if not el.is_visible():
                    continue
                label = el.inner_text() or el.get_attribute("value") or ""
                if not _is_accept(label):
                    continue
                el.click(timeout=CLICK_TIMEOUT_MS)
                return " ".join(label.split())
            except Exception:  # noqa: BLE001
                continue
    return ""


def probe_origin(
    origin: str,
    timeout_ms: int = PROBE_TIMEOUT_MS,
    settle_ms: int = SETTLE_MS,
    accept_settle_ms: int = ACCEPT_SETTLE_MS,
) -> tuple[list[dict], str]:
    """Load ``origin`` in a fresh profile and record what it contacts either
    side of the consent dialog."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[probe] Playwright unavailable; no probed traffic",
              file=sys.stderr)
        return [], ""

    requests: list[dict] = []
    phase = PRE_CONSENT
    accepted = ""

    def record(req) -> None:
        requests.append({
            "url": req.url, "type": req.resource_type, "consent": phase,
        })

    pw = browser = None
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()   # no storage state, discarded below
        page = context.new_page()
        page.on("request", record)
        page.goto(origin, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(settle_ms)
        accepted = accept_consent(page)
        if accepted:
            phase = POST_CONSENT
            page.wait_for_timeout(accept_settle_ms)
        context.close()
    except Exception as exc:  # noqa: BLE001
        print(f"[probe] {origin}: {type(exc).__name__}: {exc}", file=sys.stderr)
    finally:
        try:
            if browser is not None:
                browser.close()
        finally:
            if pw is not None:
                pw.stop()
    return requests, accepted


def merge_requests(*lists) -> list[dict]:
    """One request list from several, keeping the earliest consent state each
    URL was seen under."""
    order = {PRE_CONSENT: 0, "": 1, POST_CONSENT: 2}
    best: dict[tuple[str, str], dict] = {}
    for reqs in lists:
        for req in reqs or ():
            if not isinstance(req, dict):
                continue
            key = (req.get("url") or "", req.get("type") or "")
            held = best.get(key)
            if held is None:
                best[key] = dict(req)
                continue
            rank = order.get(req.get("consent") or "", 1)
            if rank < order.get(held.get("consent") or "", 1):
                held["consent"] = req.get("consent") or ""
    return list(best.values())
