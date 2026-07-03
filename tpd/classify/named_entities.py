"""Named-organisation detection."""

from __future__ import annotations

import re
import sys

from .. import gazetteer
from ..lexicons import CATEGORY_RE, GENERIC_RE

# Regexes for common NER noise.
NER_BLOCK_RE = re.compile(r"\b(information|data|personal|cardholder|cookies?)\b", re.I)
NER_BLOCK_ACRONYMS = {
    "PIN", "CIN", "IP", "SSN", "ID", "DOB", "FAQ", "URL", "PII", "TOS",
    # legal regimes / standards / industry bodies which are never named disclosed third parties.
    "GDPR", "CCPA", "CPRA", "COPPA", "CALOPPA", "HIPAA", "FERPA", "VCDPA", "LGPD",
    "PIPEDA", "DPA", "SCC", "SCCS", "EU", "EEA", "US", "USA", "UK", "SSL", "TLS",
    "NAI", "DAA", "IAB", "FTC", "ICO", "API", "SDK", "PCI",
}
# Regulatory / legal / standards / industry-body phrases which NER mislabels as relevant orgs.
NER_STOP_RE = re.compile(
    r"\b("
    r"privacy|polic(?:y|ies)|terms|conditions|eula|agreement|"
    r"notice|statement|disclaimer|"
    r"tracking technolog(?:y|ies)|"
    r"gdpr|ccpa|cpra|coppa|caloppa|hipaa|ferpa|vcdpa|lgpd|pipeda|"
    r"civil code|penal code|standard contractual|contractual clauses?|"
    r"safe harbou?r|privacy shield|"
    r"network advertising initiative|digital advertising alliance|"
    r"interactive advertising bureau|"
    r"secure sockets? layer|transport layer security|"
    r"designated countr(?:y|ies)|european (?:union|economic area)|"
    r"social media account|"
    r"regulation|directive|amendment|"
    # government / regulatory / standards / dispute bodies.
    r"commission|department of commerce|arbitration|dispute resolution|"
    r"data protection authority|supervisory authority|"
    # framework / consent-tooling terms.
    r"consent management|data privacy framework|transparency (?:and|&|\+)? ?consent|"
    r"adtech ecosystem|insertion order"
    r")\b", re.I)

# Capitalised defined terms which NER tags as orgs.
NER_DEFINED_TERMS = {
    "service", "services", "site", "sites", "website", "websites", "content",
    "company", "app", "apps", "application", "applications", "platform",
    "account", "accounts", "user", "users", "customer", "customers", "device",
    "devices", "product", "products", "software", "internet", "web", "online",
    "page", "pages", "feature", "features", "detect", "protect", "cookie",
    "cookies", "session", "sessions", "profile", "profiles", "ad", "ads",
    "advertisement", "advertisements", "subscription", "subscriptions",
    # data-protection roles, document parts, and section-heading nouns.
    "controller", "controllers", "processor", "processors", "subprocessor",
    "sub-processor", "addendum", "ecosystem", "transfer", "transfers",
    "disclosure", "disclosures", "request", "requests", "consent", "integration",
    "integrations", "order", "property", "intellectual", "group", "framework",
    "extension", "password", "recipient", "recipients", "purpose", "purposes",
}

# Function words that may glue defined-term nouns into a glossary phrase.
_FUNCTION_WORDS = {
    "on", "of", "our", "your", "the", "a", "an", "and", "or", "for", "to",
    "in", "with", "by", "this", "that", "these", "those", "its", "we", "us",
    "you", "all", "any", "other", "such", "as", "about", "from", "their",
}

# Leading determiners / possessives included in NER entities.
_LEAD_DET_RE = re.compile(r"^(?:the|a|an|this|that|these|those|our|your|its)\s+", re.I)
_CORP_SUFFIX_RE = re.compile(
    r"\s+(?:inc|llc|l\.l\.c|ltd|limited|corp|corporation|co|gmbh|s\.?a|ag|bv|"
    r"b\.?v|plc|llp|pty|s\.?r\.?l|oy|ab|as|sas|sarl|kk|company)\.?$",
    re.I,
)

_NER_CACHE: dict[str, object] = {}


def load_ner(enable: bool = True):
    """Return (ner_fn, backend_name), with ner_fn(text) returning a list[str] of ORG surfaces."""
    if not enable:
        return (lambda text: []), "disabled"
    if "fn" in _NER_CACHE:
        return _NER_CACHE["fn"], _NER_CACHE["name"]
    
    try:
        import spacy

        # We only need the NER pipe, so disable the parser, tagger,
        # lemmatizer and attribute-ruler for ~2-3x faster inference.
        nlp = spacy.load(
            "en_core_web_sm",
            disable=["parser", "tagger", "lemmatizer", "attribute_ruler"],
        )

        def ner_fn(text):
            return [e.text for e in nlp(text).ents if e.label_ in ("ORG", "PRODUCT")]

        def ner_fn_batch(texts):
            # Batched function for rich document sets.
            return [
                [e.text for e in d.ents if e.label_ in ("ORG", "PRODUCT")]
                for d in nlp.pipe(texts)
            ]

        ner_fn.batch = ner_fn_batch
        name = f"spaCy {spacy.__version__}/en_core_web_sm (ner-only)"
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] spaCy unavailable ({exc}); gazetteer-only NER", file=sys.stderr)
        ner_fn, name = (lambda text: []), "gazetteer-only"
        ner_fn.batch = lambda texts: [[] for _ in texts]
    _NER_CACHE["fn"], _NER_CACHE["name"] = ner_fn, name
    return ner_fn, name


def clean_ner_org(ent: str):
    # Strip bullets / punctuation + determiners.
    s = re.sub(r"^[^0-9A-Za-z]+", "", ent.strip())
    s = _LEAD_DET_RE.sub("", s).strip(" .,:;-•")
    if not s or NER_BLOCK_RE.search(s) or s.upper() in NER_BLOCK_ACRONYMS:
        return None
    if NER_STOP_RE.search(s):
        return None
    if not any(t[:1].isupper() for t in s.split()):
        return None
    # Keep all-caps acronyms only if they are known by the gazetteer.
    if (
        s.isupper()
        and 2 <= len(s) <= 5
        and s.isalpha()
        and s.lower() not in _GAZ_TYPE
    ):
        return None
    # Only accept long sequences of capitalized words
    # (>= 4 words) if recorded in the gazetteer.
    if len(s.split()) >= 4 and s.lower() not in _GAZ_TYPE:
        return None
    low = s.lower()
    # Check for entities only consisting of generic terms (test 1)
    core = _CORP_SUFFIX_RE.sub("", low).strip()
    if core in NER_DEFINED_TERMS:
        return None
    # Check for entities only consisting of generic terms (test 2)
    word_toks = [t for t in re.split(r"[^a-z0-9]+", core) if t]
    if word_toks and all(t in NER_DEFINED_TERMS or t in _FUNCTION_WORDS for t in word_toks):
        return None
    # Remove category/generic descriptors
    if CATEGORY_RE.fullmatch(low) or GENERIC_RE.fullmatch(low):
        return None
    if len(s.split()) <= 3 and (CATEGORY_RE.search(low) or GENERIC_RE.search(low)):
        return None
    return s


def _is_first_party(name: str, first_party: set[str] | None) -> bool:
    """Check an entity is first party."""
    if not first_party:
        return False
    toks = {t for t in re.split(r"[^a-z0-9]+", name.lower()) if len(t) >= 4}
    return bool(toks & first_party)


_GENERIC_DOMAIN_LABELS = {
    "www", "com", "org", "net", "co", "io", "app", "apps", "gov", "edu", "ac",
    "go", "or", "ne", "store", "online", "site", "web", "info", "biz", "me",
    "privacy", "policy", "policies", "legal", "support", "help", "static",
    "cdn", "assets", "page", "pages", "sites", "google", "play",
}


# Corporate-form suffixes that are not distinguishing for first-party analysis.
_CORP_SUFFIX_TOKENS = {
    "inc", "llc", "ltd", "limited", "corp", "corporation", "co", "company",
    "gmbh", "sa", "ag", "bv", "plc", "llp", "pty", "srl", "oy", "ab", "as",
    "sas", "sarl", "kk", "group", "holdings", "media", "labs", "digital",
}


def first_party_tokens(urls, name: str = "") -> set[str]:
    """Derive first-party brand tokens from a target's URLs + name."""
    from urllib.parse import urlparse

    toks: set[str] = set()
    for u in urls or ():
        host = (urlparse(u).hostname or "").lower()
        labels = [l for l in host.split(".") if len(l) >= 3 and l not in _GENERIC_DOMAIN_LABELS]
        if labels:
            toks.add(max(labels, key=len))
    for part in re.split(r"[^a-z0-9]+", (name or "").lower()):
        if len(part) >= 4 and part not in _CORP_SUFFIX_TOKENS:
            toks.add(part)
    return toks


# Precompile the gazetteer.
_GAZ_TYPE: dict[str, str] = {}
for _n in gazetteer.COMPANIES:
    _GAZ_TYPE[_n] = "company"
for _n in gazetteer.SERVICES:
    _GAZ_TYPE[_n] = "service"
_GAZ_NAMES = sorted(_GAZ_TYPE, key=len, reverse=True)
_GAZ_RE = re.compile(r"\b(?:" + "|".join(re.escape(n) for n in _GAZ_NAMES) + r")\b", re.I)

# Cap the amount of table/cell text scanned. 
_MAX_SCAN = 300_000


# Detect non-relevant "Meta" variants ("meta data", "meta tag", "meta description", "meta information").
_META_NONCOMPANY_RE = re.compile(r"\bmeta[ -](?:data|tag|tags|description|information|keyword|title|name)\b", re.I)

# Gazetteer names which are also common English words/verbs, 
# to grab only capitalised occurrences.
_AMBIGUOUS_GAZ = {"turn", "adjust", "branch", "segment", "heap", "moat", "snap",
                  "drift", "brave"}


def gazetteer_orgs(text: str) -> list[tuple[str, str]]:
    scan = text[:_MAX_SCAN]
    low = scan.lower()
    hits: dict[str, str] = {}
    for m in _GAZ_RE.finditer(low):
        name = m.group(0)
        if name == "meta":
            # Keep only if there is a "meta" which does not match regex.
            n_total = low.count("meta")
            n_noise = len(_META_NONCOMPANY_RE.findall(scan))
            if n_total <= n_noise:
                continue
        elif name in _AMBIGUOUS_GAZ:
            # Require a capitalised occurrence.
            if not re.search(r"\b" + re.escape(name.capitalize()) + r"\b", scan):
                continue
        if name not in hits:
            hits[name] = _GAZ_TYPE.get(name, "company")
    return list(hits.items())


def classify_org(text: str) -> str:
    low = text.lower().strip()
    if low in gazetteer.SERVICES:
        return "service"
    if low in gazetteer.COMPANIES:
        return "company"
    toks = low.split()
    if len(toks) >= 2 and toks[-1] in gazetteer.SERVICE_TAIL_WORDS:
        return "service"
    return "unknown"


def detect_orgs(
    text: str,
    ner_fn=None,
    first_party: set[str] | None = None,
    prose_precision: bool = False,
    ner_ents: list[str] | None = None,
) -> tuple[list[str], str]:
    """Return (sorted_org_surfaces, specificity) for ``text``.

    specificity is the summary over detected orgs:
    'service' | 'company' | 'mixed' | 'unknown' | '' (none found).

    ``first_party`` is a set of brand tokens identifying the document's own
    publisher.

    ``prose_precision`` raises the classification bar for NER-only names.

    ``ner_ents``, when given, is used as pre-computed NER output for ``text``.
    """
    found: dict[str, str] = {}
    seen: set[str] = set()
    for name, typ in gazetteer_orgs(text):
        if name in seen or _is_first_party(name, first_party):
            continue
        seen.add(name)
        found[name] = typ
    ents = ner_ents if ner_ents is not None else (ner_fn(text) if ner_fn else [])
    for ent in ents:
        if CATEGORY_RE.fullmatch(ent.strip()) or GENERIC_RE.search(ent):
            continue
        cleaned = clean_ner_org(ent)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen or _is_first_party(cleaned, first_party):
            continue
        if prose_precision and key not in _GAZ_TYPE and not _CORP_SUFFIX_RE.search(cleaned):
            continue
        seen.add(key)
        found[cleaned] = classify_org(cleaned)

    if not found:
        return [], ""
    types = set(found.values())
    concrete = types - {"unknown"}
    if len(concrete) > 1:
        spec = "mixed"
    elif concrete:
        spec = concrete.pop()
    else:
        spec = "unknown"
    return sorted(found.keys()), spec
