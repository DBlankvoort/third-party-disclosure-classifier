"""Named-organisation detection."""

from __future__ import annotations

import re
import sys

from .. import gazetteer
from ..lexicons import CATEGORY_RE, GENERIC_RE

# Regexes for common NER noise.
NER_BLOCK_RE = re.compile(r"\b(information|data|personal|cardholder|cookies?)\b", re.I)
# Data types and audience attributes.
NER_DATA_TERM_RE = re.compile(
    r"^(?:ip[- ]?addresse?s?|ids?|identifiers?|device\s+ids?|"
    r"advertising\s+ids?|demographics?|geoloc(?:ation)?s?|locations?|"
    r"interest\s+reporting|interests?|preferences?|"
    r"e-?mail(?:\s+addresse?s?)?|phone\s+numbers?|browser\s+types?|"
    r"user\s+agents?|screen\s+resolutions?|time\s+zones?|"
    r"dsps?|isps?|ssps?|urls?)$",
    re.I,
)
# Labels identifying a part of the document being read.
NER_DOCUMENT_PART_RE = re.compile(
    r"^(?:annex(?:es|ure)?|appendix|appendices|schedule|exhibit|attachment|"
    r"addendum|addenda|section|article|clause|part|table|figure|paragraph)"
    r"(?:\s+(?:[0-9]+|[ivxlcIVXLC]+|[A-Z]))?$",
    re.I,
)
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
    "platforms", "account", "accounts", "user", "users", "customer",
    "customers", "device",
    "devices", "product", "products", "software", "internet", "web", "online",
    "page", "pages", "feature", "features", "detect", "protect", "cookie",
    "cookies", "session", "sessions", "profile", "profiles", "ad", "ads",
    "advertisement", "advertisements", "subscription", "subscriptions",
    "controller", "controllers", "processor", "processors", "subprocessor",
    "sub-processor", "addendum", "ecosystem", "transfer", "transfers",
    "disclosure", "disclosures", "request", "requests", "consent", "integration",
    "integrations", "order", "property", "intellectual", "group", "framework",
    "extension", "password", "recipient", "recipients", "purpose", "purposes",
    "party", "parties", "counterparty", "counterparties", "affiliate",
    "affiliates", "vendor", "vendors", "partner", "partners", "supplier",
    "suppliers", "importer", "importers", "exporter", "exporters",
    "keyword", "keywords",
}

# Quantifiers standing where a name would.
_QUANTIFIER_WORDS = {
    "most", "each", "every", "certain", "various", "several", "many", "both",
    "either", "multiple", "numerous", "no", "few",
}

# Jurisdictions named in international-transfer clauses.
NER_PLACE_TERMS = {
    "australia", "brazil", "canada", "china", "india", "japan", "singapore",
    "switzerland", "new zealand", "south africa", "south korea", "israel",
    "mexico", "argentina", "russia", "turkey", "philippines", "vietnam",
    "indonesia", "malaysia", "thailand", "ireland", "germany", "france",
    "spain", "italy", "netherlands", "belgium", "poland", "sweden", "norway",
    "denmark", "finland", "portugal", "austria", "greece", "romania",
    "united states", "united kingdom", "great britain", "england", "scotland",
    "wales", "northern ireland", "hong kong", "taiwan", "united arab emirates",
    "u.s", "u.s.", "us", "usa", "uk", "eu", "eea", "california", "texas",
    "new york", "virginia", "colorado", "connecticut", "utah",
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


def load_ner(enable: bool = True, nlp=None):
    """Return (ner_fn, backend_name), with ner_fn(text) returning a list[str] of ORG surfaces."""
    if not enable:
        return (lambda text: []), "disabled"
    cache_key = id(nlp) if nlp is not None else "own"
    if _NER_CACHE.get("key") == cache_key:
        return _NER_CACHE["fn"], _NER_CACHE["name"]

    if nlp is not None:
        def ner_fn(text):
            return [e.text for e in nlp(text).ents if e.label_ in ("ORG", "PRODUCT")]

        def ner_fn_batch(texts):
            return [
                [e.text for e in d.ents if e.label_ in ("ORG", "PRODUCT")]
                for d in nlp.pipe(texts)
            ]

        ner_fn.batch = ner_fn_batch
        name = f"shared spaCy pipeline ({nlp.meta.get('name', '?')})"
    else:
        try:
            import spacy

            # We only need the NER pipe, so disable the parser, tagger,
            # lemmatizer and attribute-ruler for ~2-3x faster inference.
            own_nlp = spacy.load(
                "en_core_web_sm",
                disable=["parser", "tagger", "lemmatizer", "attribute_ruler"],
            )

            def ner_fn(text):
                return [e.text for e in own_nlp(text).ents if e.label_ in ("ORG", "PRODUCT")]

            def ner_fn_batch(texts):
                # Batched function for rich document sets.
                return [
                    [e.text for e in d.ents if e.label_ in ("ORG", "PRODUCT")]
                    for d in own_nlp.pipe(texts)
                ]

            ner_fn.batch = ner_fn_batch
            name = f"spaCy {spacy.__version__}/en_core_web_sm (ner-only)"
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] spaCy unavailable ({exc}); gazetteer-only NER", file=sys.stderr)
            ner_fn, name = (lambda text: []), "gazetteer-only"
            ner_fn.batch = lambda texts: [[] for _ in texts]
    _NER_CACHE["key"], _NER_CACHE["fn"], _NER_CACHE["name"] = cache_key, ner_fn, name
    return ner_fn, name


def clean_ner_org(ent: str, allow_lowercase: bool = False):
    """A usable organisation surface from one NER span, or None."""
    # Strip bullets / punctuation + determiners.
    s = re.sub(r"^[^0-9A-Za-z]+", "", ent.strip())
    s = _LEAD_DET_RE.sub("", s).strip(" .,:;-•")
    if not s or NER_BLOCK_RE.search(s) or s.upper() in NER_BLOCK_ACRONYMS:
        return None
    if NER_STOP_RE.search(s) or NER_DOCUMENT_PART_RE.match(s):
        return None
    # Vendor names capitalise internally as often as initially ("mParticle",
    # "i-Movo", "eBay"), so an uppercase letter anywhere carries the signal.
    if not allow_lowercase and not any(c.isupper() for c in s):
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
    if low in NER_PLACE_TERMS or NER_DATA_TERM_RE.match(low):
        return None
    parts = low.split()
    if len(parts) > 1 and parts[-1] in _LEGISLATION_TAILS:
        return None
    # Check for entities only consisting of generic terms (test 1)
    core = _CORP_SUFFIX_RE.sub("", low).strip()
    if core in NER_DEFINED_TERMS:
        return None
    # Check for entities only consisting of generic terms (test 2)
    word_toks = [t for t in re.split(r"[^a-z0-9]+", core) if t]
    if word_toks and all(
        t in NER_DEFINED_TERMS or t in _FUNCTION_WORDS or t in _QUANTIFIER_WORDS
        for t in word_toks
    ):
        return None
    # Remove category/generic descriptors
    if CATEGORY_RE.fullmatch(low) or GENERIC_RE.fullmatch(low):
        return None
    if len(s.split()) <= 3 and (CATEGORY_RE.search(low) or GENERIC_RE.search(low)):
        return None
    return s


# Cues that introduce a vendor by name.
_NAMING_CUE_RE = re.compile(
    r"\b(?:such as|includ(?:e|es|ing)(?: but not limited to)?|for example|"
    r"e\.?g\.?|for instance|namely|(?:most )?notably|specifically|"
    r"called|named|powered by|provided by|operated by|supplied by|hosted by|"
    r"licen[cs]ed from|in partnership with|partners?|vendors?|"
    r"providers?|processors?|suppliers?|recipients?)\b[\s:,-]*$",
    re.I,
)
# Material that may sit between the cue and the name without breaking the
# frame.
_FRAME_FILLER_RE = re.compile(
    r"^(?:(?:our|the|a|an|its|their|his|her)\s+|"
    r"[A-Z][\w&.'’-]*(?:\s+[A-Za-z][\w&.'’-]*){0,3}\s*(?:,|\band\b|\bor\b)\s*|"
    r"[a-z][\w-]*\s+)+$",
)
# "Skimlinks because they are an affiliate marketing service provider."
_NAME_THEN_ROLE_RE = re.compile(r"^\s*because\b", re.I)
# Capitalised sentence openers that a segment-initial name test would otherwise
# mistake for a vendor.
_SENTENCE_OPENERS = {
    "it", "they", "we", "you", "he", "she", "this", "that", "these", "those",
    "there", "here", "if", "when", "where", "while", "although", "however",
    "below", "above", "share", "client", "please", "note", "our", "your",
    "the", "a", "an", "all", "any", "some", "each", "every", "no", "not",
    "for", "in", "on", "at", "to", "as", "by", "with", "from", "about",
}
# Opt-out enumerations of the form "LiveRamp: https://…".
_NAME_THEN_LINK_RE = re.compile(r"^\s*[:–-]\s*(?:https?://|www\.)", re.I)

_FRAME_WINDOW = 80


def in_naming_frame(text: str, name: str) -> bool:
    """Whether ``name`` is introduced by an explicit vendor-naming construction."""
    for m in re.finditer(re.escape(name), text):
        pre = text[max(0, m.start() - _FRAME_WINDOW):m.start()]
        post = text[m.end():]
        if _NAMING_CUE_RE.search(pre):
            return True
        # Walk back over coordinated names to find the cue introducing the list.
        filler = _FRAME_FILLER_RE.search(pre)
        if filler and _NAMING_CUE_RE.search(pre[:filler.start()]):
            return True
        if not pre.strip() and (
            _NAME_THEN_ROLE_RE.match(post) or _NAME_THEN_LINK_RE.match(post)
        ):
            return True
    return False


_FRAME_CUE_RE = re.compile(
    r"\b(?:such as|includ(?:e|es|ing)(?: but not limited to)?|for example|"
    r"e\.?g\.?|for instance|namely|called|named|powered by|provided by|"
    r"operated by|supplied by|hosted by|licen[cs]ed from)\b[\s:,-]*",
    re.I,
)
_TOKEN_RE = re.compile(r"[A-Za-z][\w&'’-]*(?:\.[A-Za-z][\w&'’-]*)*")
_POSSESSIVE_RE = re.compile(r"['’]s\b\s*$")
_LEGISLATION_TAILS = {
    "act", "acts", "law", "laws", "code", "codes", "regulation", "regulations",
    "directive", "directives", "statute", "statutes", "rule", "rules",
    "amendment", "amendments", "treaty", "convention", "clauses",
}
# Whitespace and coordinating punctuation between names in an enumeration.
_FRAME_SEP_RE = re.compile(r"[ \t]*(?:,[ \t]*|&[ \t]*)?")
# Determiners and descriptor nouns that may precede the name after a cue.
_FRAME_SKIP_WORDS = {"our", "the", "a", "an", "its", "their"}
_FRAME_CONNECTORS = {"and", "or"}
# How many lowercase descriptor tokens may sit between cue and name.
_FRAME_MAX_SKIP = 4
_FRAME_MAX_NAMES = 6


def _name_token(tok: str) -> bool:
    """Whether a token carries the orthography of a proper name."""
    return any(c.isupper() for c in tok)


def frame_named_orgs(text: str) -> list[str]:
    """Names read directly off explicit naming constructions in ``text``."""
    out: list[str] = []

    def emit(name: str) -> None:
        name = _POSSESSIVE_RE.sub("", name).strip(" .,:;-•'’")
        if not name:
            return
        # Navigation runs and comma-free enumerations abut distinct brands with
        # nothing lowercase between them.
        toks = name.split()
        if len(toks) > 1:
            known = [t for t in toks if t.lower() in _GAZ_TYPE]
            if len(known) > 1:
                for t in known:
                    if t not in out:
                        out.append(t)
                return
        if name not in out:
            out.append(name)

    for cue in _FRAME_CUE_RE.finditer(text):
        pos, skipped, names = cue.end(), 0, 0
        current: list[str] = []
        while names < _FRAME_MAX_NAMES:
            sep = _FRAME_SEP_RE.match(text, pos)
            nxt = sep.end() if sep else pos
            m = _TOKEN_RE.match(text, nxt)
            if not m:
                break
            # A separator carrying sentence punctuation closes the frame.
            if sep and re.search(r"[.;:!?()]", sep.group(0)):
                break
            pos = nxt
            tok = m.group(0)
            low = tok.lower()
            if _name_token(tok):
                current.append(tok)
                pos = m.end()
                continue
            if current:
                # A lowercase word ends the name; a connector may resume it.
                emit(" ".join(current))
                names += 1
                current = []
                if low in _FRAME_CONNECTORS:
                    pos = m.end()
                    continue
                break
            if low in _FRAME_SKIP_WORDS or skipped < _FRAME_MAX_SKIP:
                skipped += 1
                pos = m.end()
                continue
            break
        if current:
            emit(" ".join(current))

    # "Skimlinks because they are an affiliate marketing service provider."
    lead = _TOKEN_RE.match(text.strip())
    if lead and _name_token(lead.group(0)):
        head: list[str] = []
        pos, stripped = 0, text.strip()
        while (m := _TOKEN_RE.match(stripped, pos)) and _name_token(m.group(0)):
            head.append(m.group(0))
            pos = m.end()
            while pos < len(stripped) and stripped[pos] == " ":
                pos += 1
        if (head and head[0].lower() not in _SENTENCE_OPENERS
                and (_NAME_THEN_ROLE_RE.match(stripped[pos:])
                     or _NAME_THEN_LINK_RE.match(stripped[pos:]))):
            emit(" ".join(head))
    return out

_MIN_BRAND_TOKEN = 3


def _is_first_party(name: str, first_party: set[str] | None) -> bool:
    """Check an entity is first party."""
    if not first_party:
        return False
    toks = {t for t in re.split(r"[^a-z0-9]+", name.lower())
            if len(t) >= _MIN_BRAND_TOKEN}
    return bool(toks & first_party)


_URL_STRUCTURE_LABELS = {
    "www", "com", "org", "net", "co", "io", "app", "apps", "gov", "edu", "ac",
    "go", "or", "ne", "store", "online", "site", "web", "info", "biz", "me",
    "privacy", "policy", "policies", "legal", "support", "help", "static",
    "cdn", "assets", "page", "pages", "sites",
}
_SHARED_HOST_LABELS = {
    "google", "play", "github", "githubusercontent", "docs", "pastebin",
    "blogspot", "wordpress", "wixsite", "weebly", "webnode", "notion",
    "netlify", "vercel", "herokuapp", "firebaseapp", "appspot", "webflow",
    "squarespace", "tumblr", "medium",
}
_GENERIC_DOMAIN_LABELS = _URL_STRUCTURE_LABELS | _SHARED_HOST_LABELS


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
        labels = [l for l in host.split(".")
                  if len(l) >= _MIN_BRAND_TOKEN and l not in _GENERIC_DOMAIN_LABELS]
        if labels:
            toks.add(max(labels, key=len))
    for part in re.split(r"[^a-z0-9]+", (name or "").lower()):
        if (len(part) >= _MIN_BRAND_TOKEN and part not in _CORP_SUFFIX_TOKENS
                and part not in _URL_STRUCTURE_LABELS):
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

# Detect non-relevant "Meta" variants ("meta data", "meta tag", "meta description", "meta information").
_META_NONCOMPANY_RE = re.compile(r"\bmeta[ -](?:data|tag|tags|description|information|keyword|title|name)\b", re.I)

# Gazetteer names which are also common English words/verbs, 
# to grab only capitalised occurrences.
_AMBIGUOUS_GAZ = {"turn", "adjust", "branch", "segment", "heap", "moat", "snap",
                  "drift", "brave"}


def gazetteer_orgs(text: str) -> list[tuple[str, str]]:
    scan = text
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

def grounded_org(name: str) -> bool:
    """Whether a surface identifies an organisation without its context."""
    from ..entities import DISPLAY_FORMS, as_host, canonical_key, known_to_kb

    surface = (name or "").strip()
    if not surface:
        return False
    low = surface.lower()
    if low in _GAZ_TYPE or canonical_key(surface) in DISPLAY_FORMS or as_host(surface):
        return True
    if known_to_kb(surface):
        return True
    if CATEGORY_RE.fullmatch(low) or GENERIC_RE.fullmatch(low):
        return False
    m = _CORP_SUFFIX_RE.search(surface)
    if not m:
        return False
    stem = surface[:m.start()].strip(" .,")
    if not stem or (CATEGORY_RE.search(stem.lower()) or GENERIC_RE.search(stem.lower())
                    or stem.lower() in _FUNCTION_WORDS
                    or stem.lower() in NER_DEFINED_TERMS):
        return False
    if m.group(0).strip(" .").lower() in _WEAK_CORP_TAILS:
        return len(stem.split()) > 1
    return True


_WEAK_CORP_TAILS = {"co", "company"}


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
    framed = frame_named_orgs(text)
    framed_keys = {f.strip(" .,:;-•").lower() for f in framed}
    for ent in list(ents) + framed:
        if CATEGORY_RE.fullmatch(ent.strip()) or GENERIC_RE.search(ent):
            continue
        cleaned = clean_ner_org(ent)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen or _is_first_party(cleaned, first_party):
            continue
        if (prose_precision and key not in _GAZ_TYPE
                and key not in framed_keys
                and not _CORP_SUFFIX_RE.search(cleaned)
                and not in_naming_frame(text, cleaned)):
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
