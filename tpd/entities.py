"""Canonicalisation of organisation names and request domains."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import tldextract

from . import gazetteer
from .kb import blocklist, gvl, jurisdiction, tracker_radar
from .lexicons import CATEGORY_RE

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
    if CATEGORY_RE.fullmatch(s.strip().lower()):
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

ENTITY_ALIASES = {
    "telaria": "magnite",
    "tremorvideo": "magnite",
    "tremorvideodsp": "magnite",
    "tremorhub": "magnite",
    "rubiconproject": "magnite",
    "spotx": "magnite",
    "spotxchange": "magnite",
    "appnexus": "microsoft",
    "xandr": "microsoft",
    "doubleclick": "google",
    "alphabet": "google",
    "facebook": "meta",
    "metaplatforms": "meta",
    "twitter": "x",
    "xcorp": "x",
    "verizonmedia": "yahoo",
    "oath": "yahoo",
    "aol": "yahoo",
    "amazoncom": "amazon",
}


def merged_key(key: str) -> str:
    """The key an organisation's several corporate identities share."""
    return ENTITY_ALIASES.get(key, key)


# --------------------------------------------------------------------------- #
# Services and organisations
# --------------------------------------------------------------------------- #
_SERVICE_TAILS = {
    "advertising": [
        "ads", "ad", "adwords", "adsense", "admob", "doubleclick",
        "ad manager", "ad server",
        "advertising", "advertising cloud", "ad exchange", "adx",
        "display network", "audience network", "audience studio",
        "demand", "exchange bidding", "dsp", "ssp", "pixel", "pixel tags",
        "tag manager", "tags", "marketing cloud", "marketing platform",
    ],
    "analytics": [
        "analytics", "measurement", "insights", "attribution", "clarity",
        "moat", "audience insights", "data cloud", "customer data platform",
    ],
    "services": [
        "cloud", "web services", "aws", "azure", "hosting", "storage",
        "sheets", "docs", "drive", "maps", "fonts", "sdk", "api", "platform",
        "workspace", "suite", "cdn", "bedrock", "openai service", "heroku",
        "firebase", "crashlytics", "bigquery",
    ],
    "security": ["recaptcha", "captcha"],
}
_TAIL_PURPOSE = {
    tail: purpose
    for purpose, tails in _SERVICE_TAILS.items()
    for tail in tails
}
_VERSION_TAIL = r"(?:\s*v?\d+(?:\.\d+)*)?[\s)\].]*$"
_SERVICE_TAIL_RE = re.compile(
    r"[\s,]*[(\-–—:]?\s*\b(" + "|".join(
        re.escape(t) for t in sorted(_TAIL_PURPOSE, key=len, reverse=True)
    ) + r")\b" + _VERSION_TAIL,
    re.IGNORECASE,
)
_PLAIN_TAILS = ("technologies", "labs", "studio", "express", "business",
                "commerce", "shopping", "pay", "wallet", "search", "news")
_PLAIN_TAIL_RE = re.compile(
    r"[\s,]*[(\-–—:]?\s*\b(" + "|".join(_PLAIN_TAILS) + r")\b[\s)\].]*$",
    re.IGNORECASE,
)
_MIN_SERVICE_HEAD = 3

_BRAND_SERVICES = {
    "admob": ("Google", "advertising"),
    "adsense": ("Google", "advertising"),
    "adwords": ("Google", "advertising"),
    "doubleclick": ("Google", "advertising"),
    "doubleclickformadvertisers": ("Google", "advertising"),
    "campaignmanager": ("Google", "advertising"),
    "firebase": ("Google", "services"),
    "crashlytics": ("Google", "services"),
    "bigquery": ("Google", "services"),
    "recaptcha": ("Google", "security"),
    "azure": ("Microsoft", "services"),
    "appnexus": ("Microsoft", "advertising"),
    "xandr": ("Microsoft", "advertising"),
    "aws": ("Amazon", "services"),
}


_LEGAL_QUALIFIER_RE = re.compile(
    r"\b(?:ireland|irish|uk|u\.k|britain|emea|apac|americas|europe|european|"
    r"france|french|germany|german|deutschland|netherlands|dutch|spain|italy|"
    r"sweden|denmark|norway|poland|portugal|switzerland|austria|belgium|"
    r"singapore|japan|korea|china|india|brazil|mexico|canada|australia|"
    r"international|global|worldwide|holdings?|group|subsidiar\w+|"
    r"unlimited|llc|inc|ltd|limited|gmbh|b\.?v|s\.?a|s\.?a\.?r\.?l|plc|pte|pty)\b",
    re.IGNORECASE,
)
_MAX_OPERATOR_TOKENS = 4


def _tail_purpose_of(remainder: str) -> str:
    """The purpose a product's name attests, where its words carry one."""
    m = _SERVICE_TAIL_RE.search(remainder)
    return _TAIL_PURPOSE[m.group(1).lower()] if m is not None else ""


def _curated_operator(key: str, surface: str) -> bool:
    return key in DISPLAY_FORMS or surface.lower() in _GAZ_DISPLAY


def _operator_prefix(surface: str) -> tuple[str, str]:
    """The curated operator a surface opens with, and what follows it."""
    tokens = surface.split()
    for cut in range(min(_MAX_OPERATOR_TOKENS, len(tokens) - 1), 0, -1):
        head = " ".join(tokens[:cut]).strip(" ,.-([")
        if len(head) < _MIN_SERVICE_HEAD:
            continue
        if _curated_operator(merged_key(canonical_key(head)), head):
            return head, " ".join(tokens[cut:])
    return "", ""


def split_service(name: str) -> tuple[str, str]:
    """Split a product name into ``(operator, purpose)``.

    Returns ``("", "")`` where the surface names an organisation in its own
    right, or where the head left by stripping the product falls outside the
    operators this project curates.
    """
    surface = clean_company_name(str(name or "").strip())
    if not surface or as_host(surface):
        return "", ""
    branded = _BRAND_SERVICES.get(canonical_key(surface))
    if branded is not None:
        return branded
    head, purpose = surface, ""
    for _ in range(3):
        m = _SERVICE_TAIL_RE.search(head)
        tail_purpose = _TAIL_PURPOSE[m.group(1).lower()] if m is not None else ""
        if m is None:
            m = _PLAIN_TAIL_RE.search(head)
        if m is None:
            break
        stripped = head[:m.start()].strip(" ,.-([")
        if len(stripped) < _MIN_SERVICE_HEAD:
            break
        purpose = purpose or tail_purpose
        head = stripped
    head = clean_company_name(head)
    if head.lower() == surface.lower():
        head, remainder = _operator_prefix(surface)
        if not head or "," in remainder or _LEGAL_QUALIFIER_RE.search(remainder):
            return "", ""
        purpose = _tail_purpose_of(remainder)
    if len(head) < _MIN_SERVICE_HEAD:
        return "", ""
    branded = _BRAND_SERVICES.get(canonical_key(head))
    if branded is not None:
        return branded[0], purpose or branded[1]
    key = merged_key(canonical_key(head))
    if not key:
        return "", ""
    if not (key in DISPLAY_FORMS or head.lower() in _GAZ_DISPLAY):
        return "", ""
    return head, purpose


def service_purpose(name: str) -> str:
    """The purpose a product name attests, or "" where it names no product."""
    return split_service(name)[1]


@lru_cache(maxsize=1)
def _tracker_radar_names() -> dict[str, tracker_radar.EntityRecord]:
    """Canonical key -> the Tracker Radar record for that organisation."""
    out: dict[str, tracker_radar.EntityRecord] = {}
    for rec in tracker_radar.index().entities.values():
        key = merged_key(canonical_key(rec.display_name or rec.name))
        if not key:
            continue
        held = out.get(key)
        if held is None or rec.prevalence > held.prevalence:
            out[key] = rec
    return out


@lru_cache(maxsize=1)
def _gvl_names() -> dict[str, gvl.Vendor]:
    """Canonical key -> the TCF Global Vendor List entry for that organisation."""
    out: dict[str, gvl.Vendor] = {}
    for vendor in gvl.vendors():
        key = merged_key(canonical_key(vendor.name))
        if key:
            out.setdefault(key, vendor)
    return out


@lru_cache(maxsize=1)
def _investor_parent_keys() -> frozenset[str]:
    return frozenset(
        merged_key(canonical_key(n)) for n in blocklist.investor_parents()
        if canonical_key(n)
    )


@lru_cache(maxsize=1)
def _country_keys() -> dict[str, str]:
    return {
        merged_key(canonical_key(name)): code
        for name, code in jurisdiction.country_overrides().items()
        if canonical_key(name)
    }


def is_investor_parent(name: str) -> bool:
    """Whether a name identifies a holding company rather than a recipient."""
    return merged_key(canonical_key(name)) in _investor_parent_keys()


def country_for(name: str) -> str:
    """The ISO-3166 alpha-2 headquarters country recorded for ``name``, or ""."""
    return _country_keys().get(merged_key(canonical_key(name)), "")


def kb_categories(name: str) -> list[str]:
    """The purposes Tracker Radar attributes to an organisation's domains."""
    rec = _tracker_radar_names().get(merged_key(canonical_key(name)))
    return list(rec.categories) if rec else []


def tcf_vendor(name: str):
    """The party's entry in the TCF Global Vendor List, or None."""
    return _gvl_names().get(merged_key(canonical_key(name)))


def known_to_kb(name: str) -> bool:
    """Whether a reference table recognises a surface as an organisation."""
    key = merged_key(canonical_key(name))
    return bool(key) and (key in _tracker_radar_names() or key in _gvl_names())


def entity_for_domain(host: str) -> tuple[str, str]:
    """Return ``(display_name, basis)`` for the organisation behind ``host``.    """
    reg = registrable_domain(host)
    if not reg:
        return "", "unknown"
    if reg in DOMAIN_OWNERS:
        return DOMAIN_OWNERS[reg], "domain_map"
    hit = tracker_radar.index().lookup_domain(host) or \
        tracker_radar.index().lookup_domain(reg)
    if hit is not None and hit.entity_name:
        rec = tracker_radar.index().lookup_entity(hit.entity_name)
        return (rec.display_name if rec else hit.entity_name), "tracker_radar"
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
    country: str = ""

    @property
    def grounded(self) -> bool:
        """Whether a record independent of the reading recognises the name."""
        return self.basis in _GROUNDED_BASES


_GROUNDED_BASES = frozenset({
    "domain_map", "display_map", "gazetteer", "tracker_radar", "tcf_gvl",
})


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
        key = merged_key(canonical_key(display))
        display = DISPLAY_FORMS.get(key, display)
        return ResolvedName(key, display, basis, host, country_for(display))

    cleaned = clean_company_name(raw)
    key = merged_key(canonical_key(cleaned))
    if not key:
        return ResolvedName(canonical_key(raw), raw, "name")

    operator, _ = split_service(cleaned)
    if operator:
        return resolve_name(operator)

    def resolved(display: str, basis: str, domain: str = "") -> ResolvedName:
        return ResolvedName(key, display, basis, domain, country_for(display))

    curated = DISPLAY_FORMS.get(key)
    if curated:
        return resolved(curated, "display_map")
    gaz = _GAZ_DISPLAY.get(cleaned.lower())
    if gaz:
        return resolved(gaz, "gazetteer")
    tr = _tracker_radar_names().get(key)
    if tr is not None:
        candidates = _kb_domain_order(key, tr.domains)
        return resolved(tr.display_name or tr.name, "tracker_radar",
                        candidates[0] if candidates else "")
    vendor = _gvl_names().get(key)
    if vendor is not None:
        domain = vendor.domain if not is_shared_platform(vendor.domain) else ""
        return resolved(clean_company_name(vendor.name) or vendor.name,
                        "tcf_gvl", domain)
    # A surface carrying its own capitalisation states the organisation's
    # preferred form, which no table can improve on.
    if any(c.isupper() for c in cleaned) and not cleaned.isupper():
        return resolved(cleaned, "surface")
    return resolved(_title_case(cleaned), "name")


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
    key = merged_key(canonical_key(name))
    if not key:
        return []
    out = []
    home = ENTITY_HOME_DOMAINS.get(key)
    if home:
        out.append(home)
    out.extend(d for d in _OWNER_INDEX.get(key, ()) if d != home)
    return out


def _kb_domain_order(key: str, domains) -> list[str]:
    """Candidate domains for an organisation, best first."""
    usable = [d for d in domains if not is_shared_platform(d)]
    return sorted(usable, key=lambda d: _domain_sort_key(key, d))


def kb_domains(name: str) -> list[str]:
    """Domains a reference table attributes to ``name``, best candidate first."""
    key = merged_key(canonical_key(name))
    if not key:
        return []
    out: list[str] = []
    rec = _tracker_radar_names().get(key)
    if rec is not None:
        out.extend(_kb_domain_order(key, rec.domains))
    vendor = _gvl_names().get(key)
    if vendor is not None and vendor.domain and vendor.domain not in out:
        if not is_shared_platform(vendor.domain):
            out.append(vendor.domain)
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
        basis = {"tracker_radar": "tracker_radar", "tcf_gvl": "tcf_gvl"}.get(
            resolved.basis, "name_domain")
        return resolved.domain, basis
    for domain in kb_domains(resolved.display):
        return domain, "tracker_radar"
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


def load_entity_domains(path: str | Path) -> dict[str, str]:
    """Organisation-to-domain mappings"""
    out: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (row.get("canonical_key") or "").strip()
            domain = (row.get("gold_domain") or "").strip().lower()
            if key and domain:
                out[key] = domain
    return out


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
