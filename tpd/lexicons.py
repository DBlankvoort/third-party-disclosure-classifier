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
# Sharing/collecting cues
# --------------------------------------------------------------------------- #
SHARING_VERBS = [
    r"shar(?:e|es|ed|ing)", r"disclos(?:e|es|ed|ure|ing)",
    r"sell(?:s|ing)?", r"sold", r"rent(?:s|ed|ing)?",
    r"provid(?:e|es|ed|ing)\s+(?:to|with)", r"transfer(?:s|red|ring)?",
    r"mak(?:e|es|ing)\s+available", r"give\s+access", r"allow\s+access",
    r"(?:we|us|our|they)\s+(?:also\s+)?work\s+with", r"partner\s+with",
]
SHARING_RE = re.compile(r"\b(" + "|".join(SHARING_VERBS) + r")\b", re.I)

SALE_OF_DATA_RE = re.compile(
    r"\b(?:sell(?:s|ing)?|sold|rent(?:s|ing|ed)?)\b"
    r"[^.;:!?]{0,60}?\b(?:personal\s+(?:information|data)|information|data)\b",
    re.I,
)

# "stop/cease selling ..." is not an affirmative sale.
_SALE_STOP_RE = re.compile(r"\b(?:stop(?:s|ped)?|ceas(?:e|es|ed)|discontinu\w+)\s+$", re.I)

COLLECTION_VERBS = [
    r"collect(?:s|ed|ing)?", r"track(?:s|ed|ing)?", r"gather(?:s|ed|ing)?",
    r"obtain(?:s|ed|ing)?", r"receiv(?:e|es|ed|ing)",
    r"serv(?:e|es|ed|ing)\s+(?:ads|advertis\w+)",
    r"plac(?:e|es|ed|ing)\s+cookies", r"set(?:s|ting)?\s+cookies",
    r"us(?:e|es|ed|ing)\s+(?:cookies|pixels?|web\s+beacons?|sdks?|"
    r"(?:tracking\s+|similar\s+)?technolog\w+)",
]
COLLECTION_RE = re.compile(r"\b(" + "|".join(COLLECTION_VERBS) + r")\b", re.I)

# --------------------------------------------------------------------------- #
# Inline naming cues
# --------------------------------------------------------------------------- #
EXEMPLIFIER_RE = re.compile(
    r"\b(such as|includ(?:e|es|ing)(?: but not limited to)?|for example|"
    r"e\.?g\.?|like|for instance|namely|(?:most )?notably|specifically)\b",
    re.I,
)
# --------------------------------------------------------------------------- #
# External pointer cues
# --------------------------------------------------------------------------- #
_PARTNERISH = (r"(?:third[- ]part|partner|advertis|service provider|sub[- ]?processor|"
               r"compan|vendor|affiliate|provider|recipient|organi[sz]ation|entit)")
POINTER_PHRASES = [
    r"(?:click|tap|see) here",
    rf"(?:for|see) (?:a |the |our )?(?:current |complete |full |updated )?list of {_PARTNERISH}\w*",
    rf"(?:complete |full |current |updated )?list of (?:our |the |all |current )?{_PARTNERISH}\w*",
    rf"{_PARTNERISH}\w* (?:are |is )?listed (?:below|here|at|on)",
    r"available (?:upon|on) request",
    r"can be found (?:at|here|on)",
]
POINTER_RE = re.compile(r"\b(" + "|".join(POINTER_PHRASES) + r")\b", re.I)

# --------------------------------------------------------------------------- #
# Document-class cues
# --------------------------------------------------------------------------- #

# Words indicating a prose policy.
POLICY_CUES_RE = re.compile(
    r"privacy (?:policy|notice|statement)|personal (?:data|information)|"
    r"data (?:protection|processing|controller|subject)|"
    # "we [may/will/also/do] collect/share/use/disclose/process/sell ..."
    r"we(?:\s+\w+){0,2}\s+(?:collect|shar\w+|use|disclos\w+|process|sell)\b|"
    r"third part(?:y|ies)|your (?:rights|choices|personal)|opt[- ]?out|gdpr|ccpa|"
    r"cookies?|consent|service providers?|how we (?:use|share|collect)|"
    r"information (?:we|you) (?:collect|share|provide)",
    re.I,
)

# Words indicating ToS/ToU/EULA/etc..
TOS_TITLE_RE = re.compile(
    r"\b(terms (?:of (?:service|use)|and conditions)|terms ?& ?conditions|"
    r"end[- ]user license|eula|acceptable use|conditions of use)\b",
    re.I,
)

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

# Heading that frames the page as a list of third parties / vendors
# / sub-processors / data controllers.
VENDOR_LIST_TITLE_RE = re.compile(
    r"\b(?:list of|the (?:major |following )?)"
    r"[\w ,&/-]{0,40}?"
    r"(third[- ]?part(?:y|ies)|sub[- ]?processors?|vendors?|"
    r"data (?:controllers?|processors?|recipients?)|recipients?)\b",
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

# An ads.txt / app-ads.txt data row, capturing the domain (group 1) and the
# DIRECT/RESELLER relationship (group 2). Shared with
# classify.structured_relations, which needs the relationship too.
ADS_TXT_ROW_RE = re.compile(
    r"^\s*([A-Za-z0-9.-]+\.[A-Za-z]{2,})\s*,\s*[^,]+,\s*(DIRECT|RESELLER)\b",
    re.I | re.M,
)
_JSON_DOMAIN_RE = re.compile(r'"domain"\s*:\s*"([^"]+)"', re.I)
_JSON_NAME_RE = re.compile(r'"name"\s*:\s*"([^"]+)"', re.I)

# Registry scaffolding.
_REGISTRY_NAME_STOP = {"duns", "tag-id", "tagid", "confidential", "ssp", "dsp"}


def registry_named_orgs(text: str, kind: str, cap: int = 200) -> list[str]:
    """Enumerate the named third parties of a machine-readable registry list."""
    text = text or ""
    out: list[str] = []
    seen: set[str] = set()

    def add(v: str) -> None:
        v = v.strip()
        key = v.lower()
        if v and key not in seen and key not in _REGISTRY_NAME_STOP and len(seen) < cap:
            seen.add(key)
            out.append(v)

    if kind == "ads_txt":
        for m in ADS_TXT_ROW_RE.finditer(text):
            add(m.group(1).lower())
            if len(seen) >= cap:
                break
    elif kind == "sellers_json":
        for m in _JSON_DOMAIN_RE.finditer(text):
            add(m.group(1).lower())
            if len(seen) >= cap:
                break
        for m in _JSON_NAME_RE.finditer(text):
            add(m.group(1))
            if len(seen) >= cap:
                break
    elif kind in ("tcf_gvl", "vendors_json"):
        # Try to parse as JSON
        import json

        try:
            data = json.loads(text)
            vendors = data.get("vendors") if isinstance(data, dict) else None
            iterable = vendors.values() if isinstance(vendors, dict) else (vendors or [])
            for v in iterable:
                if isinstance(v, dict) and v.get("name"):
                    add(v["name"])
                if len(seen) >= cap:
                    break
        except Exception:  # noqa: BLE001
            for m in _JSON_NAME_RE.finditer(text):
                add(m.group(1))
                if len(seen) >= cap:
                    break
    return out

def machine_readable_kind(text: str) -> str:
    """Classify a fetched registry file by kind."""
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

# --------------------------------------------------------------------------- #
# Discover companion docs.
# --------------------------------------------------------------------------- #
LINK_DISCOVERY = [
    ("privacy_policy", re.compile(r"privacy(?:[-_ ]?(?:policy|notice|statement|center))?", re.I)),
    ("subprocessor_list", re.compile(r"sub[-_ ]?processor", re.I)),
    ("dpa", re.compile(r"\b(dpa|data processing (?:agreement|addendum))\b", re.I)),
    ("cookie_policy", re.compile(r"cookie[-_ ]?(?:policy|notice|statement|preferences|settings)", re.I)),
    ("vendor_list", re.compile(
        r"\bvendors?[-_ ]?lists?\b|advertising partners|ad partners|"
        r"third[-_ ]?part(?:y|ies)[-_ ]?data|"  # privacy-centre "third-party-data" pages
        r"categories of (?:third[- ]?part(?:y|ies)|recipients)|"
        r"(?:who|companies|parties) we share", re.I)),
    ("do_not_sell", re.compile(r"do[-_ ]?not[-_ ]?sell|your privacy choices|opt[-_ ]?out", re.I)),
    ("partners_page", re.compile(r"\b(our partners|partner directory|integrations?|app directory)\b", re.I)),
    ("help_doc", re.compile(r"\b(help|support|faq|knowledge ?base)\b", re.I)),
]

# --------------------------------------------------------------------------- #
# Clause detection
# --------------------------------------------------------------------------- #

# First-party hints
_FP_ANCHOR_RE = re.compile(
    r"\b(?:we|our|us)\b|\b(?:this|the)\s+(?:sites?|websites?|apps?|applications?|services?|pages?)\b",
    re.I,
)

# Data-related nouns
_DATA_NOUN_RE = re.compile(
    r"\b(?:data|information|cookies?|identifiers?|personal|browsing|usage|"
    r"analytics|pixels?|beacons?|trackers?|tracking)\b",
    re.I,
)

# Sentence boundaries
_SENT_BOUND_RE = re.compile(r"(?<=[a-z]{2})[.!?]\s")

# Negation cues
NEGATION_RE = re.compile(
    r"\b(not|never|n't|without|neither|nor|no longer|refrain from|"
    r"decline to|do not|does not|will not|cannot|won't|don't|doesn't)\b",
    re.I,
)

# How far back to look for a negator
_NEG_WINDOW = 45

# Contrastive conjunctions that close off a preceding negated clause
_CLAUSE_BOUND_RE = re.compile(
    r",\s*(?:but|however|yet|although|though|whereas)\b\s*", re.IGNORECASE
)

# Exception cues
EXCEPTION_RE = re.compile(
    r"\b(unless|except|other than|save (?:as|where|for|that|to the extent)|"
    r"with the exception of|aside from|apart from)\b",
    re.I,
)

# How far forward to look for an exception
_EXC_WINDOW = 140

# How far forward to look for a third-party collection clause
_TP_COLLECT_WINDOW = 90

# How far forward to look for a party
_PARTY_WINDOW = 70

# Tracker detection
_TRACKER_NOUN = (
    r"(?:cookies?|pixel(?:\s+tags?)?|tags?|trackers?|scripts?|sdks?|"
    r"web\s+beacons?|(?:tracking|similar)\s+technolog\w+|technolog\w+)"
)
TRACKER_PASSIVE_RE = re.compile(
    _TRACKER_NOUN + r"[^.;:!?]{0,40}?\b(?:placed|set|used|operated|served|"
    r"provided|integrated|deployed|collected)\s+by\b",
    re.I,
)
TRACKER_FROM_RE = re.compile(_TRACKER_NOUN + r"[^.;:!?]{0,60}?\bfrom\b", re.I)

# First party group cues
_FP_GROUP_TERMS = {"affiliate", "affiliates", "subsidiary", "subsidiaries"}
_FP_GROUP_POSSESSIVE_RE = re.compile(r"\b(?:its|our|their)\s+$", re.I)

# First-party collection happening on a third-party surface
ON_THIRD_PARTY_SURFACE_RE = re.compile(
    r"\b(?:on|via|through|across)\s+third[- ]part(?:y|ies)\s+"
    r"(?:web\s*)?(?:sites?|websites?|platforms?|apps?|applications?|services?)\b",
    re.I,
)

_SELF_RECIPIENT_RE = re.compile(r"^\s*(?:(?:to|with)\s+)?(?:you|us)\b", re.I)


# --------------------------------------------------------------------------- #
# Affirmations/negations
# --------------------------------------------------------------------------- #
def is_negated(text: str, pos: int) -> bool:
    """True iff a non-excepted negation cue precedes ``pos`` in ``text``."""
    pre = text[max(0, pos - _NEG_WINDOW):pos]
    cut = max(pre.rfind(". "), pre.rfind("; "), pre.rfind("! "), pre.rfind("? "),
              pre.rfind(": "))
    clause_cut = max((cb.end() for cb in _CLAUSE_BOUND_RE.finditer(pre)), default=-1)
    cut = max(cut, clause_cut - 1)
    if cut != -1:
        pre = pre[cut + 1:]
    if not NEGATION_RE.search(pre):
        return False
    post = text[pos:pos + _EXC_WINDOW]
    return not EXCEPTION_RE.search(post)


def _affirmative(segment: str, verb_re: re.Pattern) -> bool:
    """True iff a segment has a non-negated verb."""
    for m in verb_re.finditer(segment):
        if _SELF_RECIPIENT_RE.match(segment[m.end():]):
            continue
        if not is_negated(segment, m.start()):
            return True
    return False


def positive_sharing(segment: str) -> bool:
    """True iff a segment makes at least one affirmative sharing claim."""
    return _affirmative(segment, SHARING_RE)


def positive_collection(segment: str) -> bool:
    """True iff a segment makes at least one affirmative collection claim."""
    return _affirmative(segment, COLLECTION_RE)


def implicit_sale(segment: str) -> bool:
    """True iff a segment affirms selling/renting user data."""
    for m in SALE_OF_DATA_RE.finditer(segment):
        if is_negated(segment, m.start()):
            continue
        if _SALE_STOP_RE.search(segment[:m.start()]):
            continue
        return True
    return False



# --------------------------------------------------------------------------- #
# Discover clause windows
# --------------------------------------------------------------------------- #
def clause_window(segment: str, start: int, width: int) -> str:
    """``width`` chars of ``segment`` from ``start``, cut at a sentence boundary."""
    w = segment[start:start + width]
    cut = _SENT_BOUND_RE.search(w)
    return w[:cut.start()] if cut else w


def _party_follows(segment: str, end: int) -> bool:
    w = clause_window(segment, end, _PARTY_WINDOW)
    return bool(GENERIC_RE.search(w) or CATEGORY_RE.search(w))


def third_party_collects(segment: str) -> bool:
    """True iff a third party is described as the collector in the given segment."""
    anchored = bool(_FP_ANCHOR_RE.search(segment))
    for pm in GENERIC_RE.finditer(segment):
        window = clause_window(segment, pm.end(), _TP_COLLECT_WINDOW)
        if _affirmative(window, COLLECTION_RE):
            return True
    # Category-party and tracker-attribution shapes additionally require a first-party anchor.
    if anchored and _DATA_NOUN_RE.search(segment):
        for pm in CATEGORY_RE.finditer(segment):
            window = clause_window(segment, pm.end(), _TP_COLLECT_WINDOW)
            if _affirmative(window, COLLECTION_RE):
                return True
        for tracker_re in (TRACKER_PASSIVE_RE, TRACKER_FROM_RE):
            for m in tracker_re.finditer(segment):
                pre = segment[max(0, m.start() - _NEG_WINDOW):m.start()]
                # Handle negation
                negated = NEGATION_RE.search(pre) or re.search(r"\bno\s+\w*\s*$", pre, re.I)
                if not negated and _party_follows(segment, m.end()):
                    return True
        # Handle first-party collection on a third-party surface.
        if ON_THIRD_PARTY_SURFACE_RE.search(segment) and positive_collection(segment):
            return True
    return False