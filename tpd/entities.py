"""Canonicalisation of organisation names and request domains."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

import tldextract

from . import gazetteer

# The public suffix list is resolved offline.
_TLD_EXTRACTOR = tldextract.TLDExtract(suffix_list_urls=())

# Corporate-form tokens, matched at the end of a name.
_SUFFIX_TOKENS = [
    "incorporated", "inc", "llc", r"l\.l\.c", "llp", r"l\.l\.p",
    "ltd", "limited", "co", "company", "corp", "corporation",
    "plc", "lp", r"l\.p",
    "gmbh", "ag", "kg", "mbh", "kgaa", "se",
    "sa", r"s\.a", "sarl", r"s\.a\.r\.l", "srl", r"s\.r\.l",
    "spa", r"s\.p\.a", "sl", r"s\.l",
    "bv", r"b\.v", "nv", r"n\.v",
    "oy", "ab", "as", "a/s", "asa",
    "pty", "pte", "pvt",
    "holdings", "group",
]
_SUFFIX_RE = re.compile(
    r"(?:(?<=[\s,.)])|^)(?:" + "|".join(_SUFFIX_TOKENS) + r")\.?\)?\s*$",
    re.IGNORECASE,
)
# Trailing region markers: "(US)", "- EMEA", ", International".
_REGION_TAIL_RE = re.compile(
    r"(?:\s*[(\-,/]\s*(?:us|usa|uk|gb|eu|emea|apac|global|international|"
    r"worldwide|intl)\s*\)?)\s*$",
    re.IGNORECASE,
)


def clean_company_name(name: str) -> str:
    """Strip corporate-form suffixes and trailing region markers."""
    if not name:
        return name
    s = str(name).strip()
    if not s:
        return s
    prev = None
    # Repeat: names such as "Example Holdings Ltd" carry two strippable tails.
    for _ in range(4):
        if s == prev:
            break
        prev = s
        s = _REGION_TAIL_RE.sub("", s).rstrip(" ,.-")
        s = _SUFFIX_RE.sub("", s).rstrip(" ,.-")
    return s or str(name).strip()


def canonical_key(name: str) -> str:
    """A merge key for one organisation, insensitive to punctuation and form."""
    return "".join(ch for ch in clean_company_name(name).lower() if ch.isalnum())


def registrable_domain(host: str) -> str:
    """The eTLD+1 of ``host``, or "" when it has none."""
    host = (host or "").strip().lower().rstrip(".")
    if not host or _IP_RE.match(host):
        return host
    ext = _TLD_EXTRACTOR(host)
    if not ext.domain or not ext.suffix:
        return host
    return f"{ext.domain}.{ext.suffix}"


_IP_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$|^\[?[0-9a-f:]+\]?$", re.I)


# Tracker and service domains whose registrable name does not carry the name of
# the organisation operating them.
DOMAIN_OWNERS = {
    "doubleclick.net": "Google",
    "googlesyndication.com": "Google",
    "googletagmanager.com": "Google",
    "googletagservices.com": "Google",
    "google-analytics.com": "Google",
    "googleadservices.com": "Google",
    "googleapis.com": "Google",
    "gstatic.com": "Google",
    "youtube.com": "Google",
    "ytimg.com": "Google",
    "app-measurement.com": "Google",
    "crashlytics.com": "Google",
    "firebaseio.com": "Google",
    "recaptcha.net": "Google",
    "2mdn.net": "Google",
    "fbcdn.net": "Meta",
    "facebook.net": "Meta",
    "facebook.com": "Meta",
    "instagram.com": "Meta",
    "atdmt.com": "Meta",
    "scorecardresearch.com": "Comscore",
    "quantserve.com": "Quantcast",
    "adsrvr.org": "The Trade Desk",
    "casalemedia.com": "Index Exchange",
    "rubiconproject.com": "Magnite",
    "3lift.com": "TripleLift",
    "adnxs.com": "Microsoft",
    "bing.com": "Microsoft",
    "msn.com": "Microsoft",
    "clarity.ms": "Microsoft",
    "licdn.com": "Microsoft",
    "linkedin.com": "Microsoft",
    "bat.bing.com": "Microsoft",
    "amazon-adsystem.com": "Amazon",
    "media-amazon.com": "Amazon",
    "cloudfront.net": "Amazon",
    "ttdns2.com": "The Trade Desk",
    "krxd.net": "Salesforce",
    "demdex.net": "Adobe",
    "omtrdc.net": "Adobe",
    "adobedtm.com": "Adobe",
    "everesttech.net": "Adobe",
    "typekit.net": "Adobe",
    "tiktokcdn.com": "TikTok",
    "tiktokv.com": "TikTok",
    "byteoversea.com": "TikTok",
    "onetrust.com": "OneTrust",
    "cookielaw.org": "OneTrust",
    "trustarc.com": "TrustArc",
    "cookiebot.com": "Cookiebot",
    "usercentrics.eu": "Usercentrics",
    "sc-static.net": "Snap",
    "snapchat.com": "Snap",
    "twimg.com": "X",
    "t.co": "X",
    "twitter.com": "X",
    "x.com": "X",
    "ads-twitter.com": "X",
    "hotjar.com": "Hotjar",
    "segment.io": "Twilio",
    "segment.com": "Twilio",
    "sentry.io": "Sentry",
    "newrelic.com": "New Relic",
    "nr-data.net": "New Relic",
    "branch.io": "Branch",
    "adjust.com": "Adjust",
    "appsflyer.com": "AppsFlyer",
    "braze.com": "Braze",
    "amplitude.com": "Amplitude",
    "mixpanel.com": "Mixpanel",
    "optimizely.com": "Optimizely",
    "cloudflare.com": "Cloudflare",
    "cloudflareinsights.com": "Cloudflare",
    "akamaized.net": "Akamai",
    "akamai.net": "Akamai",
    "fastly.net": "Fastly",
    "stripe.com": "Stripe",
    "stripe.network": "Stripe",
    "paypal.com": "PayPal",
    "paypalobjects.com": "PayPal",
    "criteo.com": "Criteo",
    "criteo.net": "Criteo",
    "taboola.com": "Taboola",
    "outbrain.com": "Outbrain",
    "pubmatic.com": "PubMatic",
    "openx.net": "OpenX",
    "smartadserver.com": "Equativ",
    "id5-sync.com": "ID5",
    "rlcdn.com": "LiveRamp",
    "liveramp.com": "LiveRamp",
    "permutive.com": "Permutive",
    "skimresources.com": "Skimlinks",
    "skimlinks.com": "Skimlinks",
    "nielsen.com": "Nielsen",
    "imrworldwide.com": "Nielsen",
    "moatads.com": "Oracle",
    "bluekai.com": "Oracle",
    "oracle.com": "Oracle",
    "adsafeprotected.com": "Integral Ad Science",
    "doubleverify.com": "DoubleVerify",
    "zendesk.com": "Zendesk",
    "intercom.io": "Intercom",
    "hubspot.com": "HubSpot",
    "hs-scripts.com": "HubSpot",
    "jsdelivr.net": "jsDelivr",
    "unpkg.com": "unpkg",
    "bootstrapcdn.com": "BootstrapCDN",
    "jquery.com": "jQuery",
    "vimeo.com": "Vimeo",
    "vimeocdn.com": "Vimeo",
    "spotify.com": "Spotify",
    "acast.com": "Acast",
    "mparticle.com": "mParticle",
    "formstack.com": "Formstack",
    "onesignal.com": "OneSignal",
    "unity3d.com": "Unity",
    "applovin.com": "AppLovin",
    "inmobi.com": "InMobi",
    "ironsrc.com": "ironSource",
    "vungle.com": "Vungle",
    "chartboost.com": "Chartboost",
    "yandex.ru": "Yandex",
    "vk.com": "VK",
    "baidu.com": "Baidu",
    "sharethis.com": "ShareThis",
    "addthis.com": "Oracle",
    "disqus.com": "Disqus",
    "wp.com": "Automattic",
    "gravatar.com": "Automattic",
}

# Domain labels that carry no brand information.
_GENERIC_LABELS = {
    "www", "cdn", "static", "assets", "img", "images", "media", "api", "app",
    "edge", "content", "files", "s3", "storage", "cache", "js", "css",
    "ad", "ads", "adserver", "advertising", "analytics", "cloud", "customer",
    "data", "digital", "marketing", "mobile", "network", "online", "platform",
    "tech", "video", "web", "news", "tv", "go", "my", "the",
}


def _brand_label(domain: str) -> str:
    """The brand-bearing label of a registrable domain."""
    ext = _TLD_EXTRACTOR(domain)
    return ext.domain or domain.split(".")[0]


def entity_for_domain(host: str) -> tuple[str, str]:
    """Return ``(display_name, basis)`` for the organisation behind ``host``.    """
    reg = registrable_domain(host)
    if not reg:
        return "", "unknown"
    if reg in DOMAIN_OWNERS:
        return DOMAIN_OWNERS[reg], "domain_map"
    label = _brand_label(reg)
    if label in _GENERIC_LABELS:
        return reg, "domain"
    # The gazetteer already carries the display form of known organisations.
    if label in _GAZ_DISPLAY:
        return _GAZ_DISPLAY[label], "gazetteer"
    return _capitalise(label), "domain"


# Single-word gazetteer names, indexed for domain-label lookup.
_GAZ_DISPLAY: dict[str, str] = {}
for _name in list(gazetteer.COMPANIES) + list(gazetteer.SERVICES):
    if " " not in _name and _name.isalnum():
        _GAZ_DISPLAY.setdefault(_name, _name[:1].upper() + _name[1:])


# --------------------------------------------------------------------------- #
# Raw name -> merge key and display form
# --------------------------------------------------------------------------- #
_DISPLAY_NAMES = [
    "AppNexus", "Xandr", "DoubleClick", "YouTube", "AdMob", "AdSense",
    "AdWords", "Google Analytics", "Google AdSense", "Google AdWords",
    "Google Ads", "Google Tag Manager", "Google Ad Manager", "Google Maps",
    "Google Cloud", "BigQuery", "reCAPTCHA", "Firebase", "Crashlytics",
    "LinkedIn", "Bing", "Microsoft Clarity", "Azure", "MSN",
    "Facebook", "Instagram", "WhatsApp", "Meta Platforms", "Meta Pixel",
    "AOL", "Yahoo", "eBay", "PayPal", "AWS", "Amazon Web Services",
    "MediaMath", "LiveIntent", "AdColony", "MoPub", "AppLovin", "InMobi",
    "ironSource", "OneSignal", "mParticle", "OpenX", "PubMatic", "TripleLift",
    "DoubleVerify", "ShareThis", "AddThis", "ScoreCardResearch", "Comscore",
    "Quantcast", "TrustArc", "OneTrust", "Cookiebot", "Usercentrics", "Didomi",
    "Sourcepoint", "LexisNexis", "TransUnion", "CoreLogic", "BeenVerified",
    "PeopleFinders", "WhitePages", "LiveRamp", "GumGum", "SpotX", "SpotXchange",
    "Sovrn", "Teads", "Sharethrough", "Smaato", "SmartyAds", "LoopMe",
    "SmartAdServer", "MGID", "InfoLinks", "RhythmOne", "OpenWeb", "AdForm",
    "Zeta Global", "The Trade Desk", "Index Exchange", "Rubicon Project",
    "Media.net", "HubSpot", "SendGrid", "MailChimp", "SurveyMonkey",
    "VWO", "FullStory", "LogRocket", "Auth0", "PagerDuty", "AT&T", "VK",
    "TikTok", "ByteDance", "OpenAI", "GitHub", "WordPress", "jQuery",
    "iubenda", "unpkg", "jsDelivr",
]
DISPLAY_FORMS: dict[str, str] = {}
for _display in list(DOMAIN_OWNERS.values()) + _DISPLAY_NAMES:
    DISPLAY_FORMS.setdefault(canonical_key(_display), _display)

# Words a name keeps in lower case away from its first position.
_LOWER_WORDS = {
    "a", "an", "and", "as", "at", "by", "de", "for", "from", "in", "of", "on",
    "or", "the", "to", "van", "von", "with",
}

_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)


def as_host(name: str) -> str:
    """The registrable domain a name is written as, or ""."""
    s = _SCHEME_RE.sub("", (name or "").strip().strip("<>\"'`()[]")).strip()
    s = s.split("/")[0].split("?")[0].strip().rstrip(".").lower()
    if not s or " " in s or "." not in s:
        return ""
    ext = _TLD_EXTRACTOR(s)
    if not ext.domain or not ext.suffix:
        return ""
    return f"{ext.domain}.{ext.suffix}"


def is_shared_platform(domain: str) -> bool:
    """Whether a domain hosts many organisations rather than one."""
    return domain in _NON_HOME_DOMAINS


def _capitalise(word: str) -> str:
    """Capitalise one word, treating a hyphen as a word boundary."""
    return "-".join(
        p[:1].upper() + p[1:] if p[:1].isalpha() else p for p in word.split("-")
    )


def _title_case(name: str) -> str:
    """Capitalise a lowercased name without disturbing interior capitals."""
    words = name.split()
    return " ".join(
        w.lower() if i and w.lower() in _LOWER_WORDS else _capitalise(w)
        for i, w in enumerate(words)
    )


@dataclass(frozen=True)
class ResolvedName:
    """One organisation name reduced to a merge key and a display form."""

    key: str
    display: str
    basis: str
    domain: str = ""


@lru_cache(maxsize=100_000)
def resolve_name(name: str) -> ResolvedName:
    """Resolve one raw organisation name as written by a disclosure source."""
    raw = (name or "").strip()
    if not raw:
        return ResolvedName("", "", "unknown")

    host = as_host(raw)
    if host:
        display, basis = entity_for_domain(host)
        display = display or host
        key = canonical_key(display)
        return ResolvedName(key, DISPLAY_FORMS.get(key, display), basis, host)

    cleaned = clean_company_name(raw)
    key = canonical_key(cleaned)
    if not key:
        return ResolvedName(canonical_key(raw), raw, "name")
    curated = DISPLAY_FORMS.get(key)
    if curated:
        return ResolvedName(key, curated, "display_map")
    gaz = _GAZ_DISPLAY.get(cleaned.lower())
    if gaz:
        return ResolvedName(key, gaz, "gazetteer")
    # A surface carrying its own capitalisation states the organisation's
    # preferred form, which no table can improve on.
    if any(c.isupper() for c in cleaned) and not cleaned.isupper():
        return ResolvedName(key, cleaned, "surface")
    return ResolvedName(key, _title_case(cleaned), "name")


# --------------------------------------------------------------------------- #
# Organisation -> crawlable origin
# --------------------------------------------------------------------------- #
ENTITY_HOME_DOMAINS = {
    "google": "google.com",
    "meta": "meta.com",
    "microsoft": "microsoft.com",
    "amazon": "amazon.com",
    "adobe": "adobe.com",
    "oracle": "oracle.com",
    "salesforce": "salesforce.com",
    "x": "x.com",
    "snap": "snap.com",
    "tiktok": "tiktok.com",
    "twilio": "twilio.com",
    "comscore": "comscore.com",
    "quantcast": "quantcast.com",
    "thetradedesk": "thetradedesk.com",
    "indexexchange": "indexexchange.com",
    "magnite": "magnite.com",
    "triplelift": "triplelift.com",
    "equativ": "equativ.com",
    "id5": "id5.io",
    "liveramp": "liveramp.com",
    "integraladscience": "integralads.com",
    "newrelic": "newrelic.com",
    "automattic": "automattic.com",
    "unity": "unity.com",
    "ironsource": "is.com",
    "nielsen": "nielsen.com",
    "akamai": "akamai.com",
    "fastly": "fastly.com",
}

# Registrable domains that host a shared platform.
_NON_HOME_DOMAINS = {
    "cloudfront.net", "akamaized.net", "akamai.net", "fastly.net",
    "cloudflareinsights.com", "jsdelivr.net", "unpkg.com", "bootstrapcdn.com",
    "app-measurement.com", "2mdn.net", "ttdns2.com", "bat.bing.com",
}


def _domain_sort_key(entity_key: str, domain: str) -> tuple:
    """Order candidate domains: brand-matching first, then .com, then shortest."""
    label = _brand_label(domain)
    return (
        label != entity_key,
        not domain.endswith(".com"),
        len(domain),
        domain,
    )


def _build_owner_index() -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for domain, owner in DOMAIN_OWNERS.items():
        if domain in _NON_HOME_DOMAINS:
            continue
        index.setdefault(canonical_key(owner), []).append(domain)
    for key, domains in index.items():
        domains.sort(key=lambda d: _domain_sort_key(key, d))
    return index


_OWNER_INDEX = _build_owner_index()


def domains_for_entity(name: str) -> list[str]:
    """Curated domains attributed to ``name``, best candidate first."""
    key = canonical_key(name)
    if not key:
        return []
    out = []
    home = ENTITY_HOME_DOMAINS.get(key)
    if home:
        out.append(home)
    out.extend(d for d in _OWNER_INDEX.get(key, ()) if d != home)
    return out


_PARENTHETICAL_RE = re.compile(r"\s*\([^)]*\)")


def name_prefixes(name: str) -> list[str]:
    """Leading forms of an organisation name, longest first."""
    base = _PARENTHETICAL_RE.sub("", clean_company_name(name)).strip()
    tokens = [t for t in re.split(r"[\s,]+", base) if t]
    return [
        " ".join(tokens[:n]) for n in range(len(tokens), 0, -1)
    ]


def resolve_entity_domain(
    name: str,
    hints: dict[str, str] | None = None,
    overrides: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Return ``(domain, basis)`` for the site an organisation can be crawled at."""
    resolved = resolve_name(name)
    key = resolved.key
    if not key:
        return "", "unresolved"
    if overrides and key in overrides:
        return overrides[key], "override"
    curated = domains_for_entity(resolved.display)
    if curated:
        return curated[0], "entity_map" if key in ENTITY_HOME_DOMAINS else "domain_map"
    if resolved.domain and not is_shared_platform(resolved.domain):
        return resolved.domain, "name_domain"
    if hints and key in hints:
        return hints[key], "observed"
    for prefix in name_prefixes(resolved.display):
        pkey = canonical_key(prefix)
        if pkey == key:
            continue
        curated = domains_for_entity(prefix)
        if curated:
            return curated[0], "name_prefix"
        if hints and pkey in hints:
            return hints[pkey], "name_prefix"
    return "", "unresolved"


def observed_domain_hints(observed) -> dict[str, str]:
    """Canonical key -> domain, from the parties an analysis observed."""
    hints: dict[str, str] = {}
    for rec in observed or ():
        key = resolve_name(rec.get("entity") or "").key
        domains = [d for d in (rec.get("domains") or ()) if d not in _NON_HOME_DOMAINS]
        if not key or not domains:
            continue
        domains.sort(key=lambda d: _domain_sort_key(key, d))
        hints.setdefault(key, domains[0])
    return hints
