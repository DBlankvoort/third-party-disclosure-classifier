"""Lexicons and compiled patterns for the disclosure classifier."""

from __future__ import annotations

import re

# --------------------------------------------------------------------------- #
# Category / generic descriptors
# --------------------------------------------------------------------------- #
CATEGORY_PATTERNS = [
    # "advertisers" is a category; "advertising" is not unless it heads a partner noun.
    r"advertisers?",
    r"advertising\s+(?:partner|network|compan(?:y|ies)|service|provider|vendor|agenc(?:y|ies)|platform)s?",
    r"ad networks?", r"ad servers?", r"ad(?:[- ]?tech)\s+(?:partner|vendor|compan(?:y|ies))s?",
    r"(?:third[- ]party\s+)?service providers?",
    r"sub[- ]?processors?",
    r"(?:business|marketing|trusted|strategic|distribution)\s+partners?",
    r"partners?",
    r"affiliates?", r"subsidiar(?:y|ies)",
    r"data brokers?", r"data partners?", r"data providers?", r"data aggregators?",
    r"analytics (?:provider|partner|compan(?:y|ies)|service)s?",
    r"social (?:network|media)(?:\s+(?:site|platform|network|service)s?)?",
    r"(?:payment|credit[- ]?card|card|transaction) (?:processor|gateway|provider)s?",
    r"vendors?", r"(?:sub[- ]?)?contractors?", r"suppliers?", r"agents?", r"merchants?",
    r"cloud (?:provider|hosting|storage)s?", r"hosting providers?",
    r"credit (?:bureaus?|reporting agenc(?:y|ies))",
    r"resellers?", r"licensees?", r"sponsors?", r"publishers?",
    r"law enforcement", r"government(?:\s+agenc(?:y|ies))?",
]
CATEGORY_RE = re.compile(r"\b(" + "|".join(CATEGORY_PATTERNS) + r")\b", re.I)

GENERIC_PATTERNS = [
    r"third[- ]part(?:y|ies)", r"3rd part(?:y|ies)",
    r"other compan(?:y|ies)", r"other organi[sz]ations?", r"other entit(?:y|ies)",
    r"other (?:business(?:es)?|part(?:y|ies))", r"outside part(?:y|ies)",
    r"external (?:part(?:y|ies)|agenc(?:y|ies)|organi[sz]ations?|compan(?:y|ies)|entit(?:y|ies))",
    r"unaffiliated (?:compan(?:y|ies)|part(?:y|ies))",
    r"selected (?:compan(?:y|ies)|part(?:y|ies))", r"others",
]
GENERIC_RE = re.compile(r"\b(" + "|".join(GENERIC_PATTERNS) + r")\b", re.I)

# --------------------------------------------------------------------------- #
# Document-class cues
# --------------------------------------------------------------------------- #

# Words indicating a title for a subprocessor table.
SUBPROCESSOR_TITLE_RE = re.compile(
    r"\b(sub[- ]?processors?|sub[- ]?processor list|list of sub[- ]?processors|"
    r"vendor list|service providers? list|third[- ]party (?:vendors?|providers?)|"
    r"data processors?)\b",
    re.I,
)

# Words indicating a help dox.
HELP_DOC_RE = re.compile(
    r"(?:^|[./])(help|support|faq|faqs|kb|knowledge[- ]?base|hc/|/articles?/)\b"
    r"|\b(help ?cent(?:er|re)|frequently asked questions|knowledge base)\b",
    re.I,
)

# Words indicating a 'partners' page
PARTNERS_PAGE_RE = re.compile(
    r"\b(our partners|partner directory|advertising partners|ad partners|"
    r"integrations?|app directory|marketplace)\b",
    re.I,
)

# Words indicating a title for a cookie-related page.
COOKIE_TITLE_RE = re.compile(
    r"\b(cookie (?:policy|notice|statement|preferences|settings|list)|"
    r"cookies?|consent (?:preferences|manager)|your privacy choices|"
    r"do not sell|vendor list|advertising partners)\b",
    re.I,
)

# --------------------------------------------------------------------------- #
# Machine-readable registries
# ---------------------------------------------------------------------------

# sellers.json: a JSON object with a "sellers" array of shape {seller_id, ...}.
SELLERS_JSON_RE = re.compile(r'"seller_id"\s*:|"sellers"\s*:\s*\[', re.I)

# IAB-TCF Global Vendor List: a JSON object keyed by version + "vendors".
TCF_GVL_RE = re.compile(
    r'"vendorListVersion"\s*:|"gvlSpecificationVersion"\s*:|'
    r'"tcfPolicyVersion"\s*:',
    re.I,
)

# vendors.json: any "vendors" array which does does match TCF_GVL_RE
VENDORS_JSON_RE = re.compile(r'"vendors"\s*:\s*[\[{]|"vendor_id"\s*:', re.I)

# An ads.txt / app-ads.txt data row: "<domain>, <publisher id>, DIRECT|RESELLER".
ADS_TXT_LINE_RE = re.compile(
    r"^[^\s,#][^,]*,[^,]+,\s*(?:DIRECT|RESELLER)\b", re.I | re.M
)

# Document roles for a machine-readable file.
MACHINE_READABLE_ROLES = frozenset(
    {"ads_txt", "app_ads_txt", "sellers_json", "vendors_json", "tcf_gvl"}
)


def machine_readable_kind(text: str) -> str:
    """Classify a fetched registry file by kind.

    Returns ``'sellers_json'`` / ``'tcf_gvl'`` / ``'vendors_json'`` / ``'ads_txt'``
    when ``text`` is a real, non-empty registry, else ``''``.
    
    JSON is checked before ads.txt because JSON can also contain
    domain-like substrings; GVL is checked before vendors.json
    because the GVL also has a ``"vendors"`` array.
    """
    if SELLERS_JSON_RE.search(text):
        return "sellers_json"
    if TCF_GVL_RE.search(text):
        return "tcf_gvl"
    if VENDORS_JSON_RE.search(text):
        return "vendors_json"
    if ADS_TXT_LINE_RE.search(text):
        return "ads_txt"
    return ""


# --------------------------------------------------------------------------- #
# CMP detection
# ---------------------------------------------------------------------------

# Common fingerprints for CMP
CMP_FINGERPRINTS = [
    r"onetrust", r"optanon", r"cookielaw\.org", r"cookiepro",
    r"cookiebot", r"consent\.cookiebot",
    r"trustarc", r"truste",
    r"quantcast", r"quantcast choice", r"__cmp", r"__tcfapi",
    r"didomi", r"sourcepoint", r"sp[_-]?cmp", r"usercentrics",
    r"osano", r"termly", r"iubenda", r"civic ?cookie ?control",
    r"cookieyes", r"cookie-law-info", r"cky[_-]consent",
    r"\biab\b.{0,20}(?:tcf|vendor)", r"tcf ?v?2", r"gdpr ?consent",
    r"vendor[_\- ]?list", r"vendors? we work with",
]
CMP_RE = re.compile("|".join(CMP_FINGERPRINTS), re.I)