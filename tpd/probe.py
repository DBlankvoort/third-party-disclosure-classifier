"""Traffic observed in a disposable browser profile."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

PRE_CONSENT = "pre_consent"
POST_CONSENT = "post_accept_all"
POST_REJECTION = "post_reject_all"

PROBE_TIMEOUT_MS = 20000
SETTLE_MS = 2500
ACCEPT_SETTLE_MS = 3000
CLICK_TIMEOUT_MS = 2500


def minimize_request_url(url: str) -> str:
    """Retain routing information without persisting user/query secrets."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))

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

_REJECT_ALL_TEXT = (
    "reject all", "decline all", "deny all", "refuse all", "necessary only",
    "essential only", "alle ablehnen", "alles ablehnen", "tout refuser",
    "rechazar todo", "rechazar todas", "rifiuta tutto", "alles weigeren",
    "odrzuć wszystko",
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


def _is_reject(text: str) -> bool:
    t = " ".join(text.split()).strip().lower().strip(".!→ ")
    return bool(t) and len(t) <= 60 and any(
        t == phrase or t.startswith(phrase) for phrase in _REJECT_ALL_TEXT
    )


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


def reject_consent(page) -> str:
    """Click a directly exposed reject-all/necessary-only control."""
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
                if not _is_reject(label):
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
    sequence = 0
    interactions: list[str] = []

    def recorder(state: list[str]):
        def record(req) -> None:
            nonlocal sequence
            sequence += 1
            requests.append({
                "url": minimize_request_url(req.url),
                "type": req.resource_type,
                "consent": state[0],
                "sequence": sequence,
                "observed_ms": int(time.time() * 1000),
            })

        return record

    pw = browser = None
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        for action, post_state in (
            (accept_consent, POST_CONSENT),
            (reject_consent, POST_REJECTION),
        ):
            context = browser.new_context()
            page = context.new_page()
            phase = [PRE_CONSENT]

            page.on("request", recorder(phase))
            page.goto(origin, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(settle_ms)
            clicked = action(page)
            if clicked:
                interactions.append(f"{post_state}:{clicked}")
                phase[0] = post_state
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
    return requests, "; ".join(interactions)


def merge_requests(*lists) -> list[dict]:
    """Combine captures while retaining every observed consent treatment."""
    best: dict[tuple[str, str], dict] = {}
    for reqs in lists:
        for req in reqs or ():
            if not isinstance(req, dict):
                continue
            item = dict(req)
            item["url"] = minimize_request_url(item.get("url") or "")
            key = (item["url"], item.get("type") or "")
            held = best.get(key)
            if held is None:
                state = item.get("consent") or ""
                item["consent_states"] = [state] if state else []
                item["observations"] = int(item.get("observations") or 1)
                best[key] = item
                continue
            states = set(held.get("consent_states") or ())
            state = item.get("consent") or ""
            if state:
                states.add(state)
            held["consent_states"] = sorted(states)
            held["observations"] = int(held.get("observations") or 1) + int(
                item.get("observations") or 1)
            held["consent"] = consent_summary(states)
    return list(best.values())


def consent_summary(states) -> str:
    values = {s for s in (states or ()) if s}
    post = values & {POST_CONSENT, POST_REJECTION}
    if PRE_CONSENT in values and post:
        return "pre_and_post_choice"
    if len(post) > 1:
        return "multiple_post_choice_states"
    if PRE_CONSENT in values:
        return PRE_CONSENT
    if post:
        return sorted(post)[0]
    return ""


# --------------------------------------------------------------------------- #
# Cached captures
# --------------------------------------------------------------------------- #
TRAFFIC_FILE = "traffic.json"

PROBE_MAX_AGE = 7 * 24 * 3600


def read_probe(target_dir: str | Path) -> dict | None:
    """A stored capture, or ``None`` where none was ever written."""
    path = Path(target_dir) / TRAFFIC_FILE
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or not isinstance(record.get("requests"), list):
        return None
    return record


def write_probe(target_dir: str | Path, record: dict) -> None:
    path = Path(target_dir)
    try:
        path.mkdir(parents=True, exist_ok=True)
        (path / TRAFFIC_FILE).write_text(
            json.dumps(record), encoding="utf-8")
    except OSError as exc:
        print(f"[probe] capture not stored: {exc}", file=sys.stderr)


def _fresh(record: dict | None, max_age: float) -> bool:
    if record is None:
        return False
    if max_age <= 0:
        return True
    return (time.time() - float(record.get("probed_at") or 0)) < max_age


def cached_probe(
    target_dir: str | Path,
    origin: str,
    force: bool = False,
    max_age: float = PROBE_MAX_AGE,
) -> dict:
    """Load ``origin`` in a clean profile"""
    stored = read_probe(target_dir)
    if not force and _fresh(stored, max_age):
        return {**stored, "cached": True}
    requests, accepted = probe_origin(origin)
    if not requests:
        if stored is not None:
            return {**stored, "cached": True}
        return {"origin": origin, "requests": [], "accepted": "",
                "probed_at": 0.0, "cached": False, "available": False}
    record = {"origin": origin, "requests": requests, "accepted": accepted,
              "probed_at": time.time(), "available": True}
    write_probe(target_dir, record)
    return {**record, "cached": False}
