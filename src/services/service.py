"""AgentCore Platform v1.0"""

# MFG-C2-063 -- research-material service.
#
# Owns the caller-data contract of this agent and everything that touches a
# caller-supplied value before a node renders it:
#
#   validate_input_context()  -- the `input_context` contract (channel,
#                                report_requirements, research_materials):
#                                every field bounded, every enum closed, every
#                                number finite and in range, every free-text
#                                field screened for prompt-injection forms and
#                                credential shapes, structured personal data
#                                masked. Fails CLOSED with the field name; the
#                                rejected value is never part of the error.
#   validate_materials()      -- the same material schema for operator-supplied
#                                documents in config/config.yaml.
#   rank_materials()          -- relevance ranking over caller / operator
#                                documents, falling back to the built-in
#                                reference corpus when neither is supplied.
#   single_line()             -- the one rendering primitive for caller text:
#                                whatever reaches a Markdown line cannot carry a
#                                newline, so a caller cannot manufacture a
#                                heading, a bullet or a section boundary.
#
# Identifier patterns are anchored with \A and \Z, never ^ and $: in Python `$`
# also matches just before a trailing newline, so "web\n" would satisfy a
# `^...$` channel pattern and the newline would travel into the audit record.
# \Z has no such exception.
#
# Nodes call this; it imports framework/ only (no mediator/, no api/).

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any, Dict, List, Optional

from framework.security.credential_detector import detect_credentials
from framework.security.injection_detector import detect_injection
from framework.security.pii_detector import detect_pii
from framework.security.pii_masking import mask_pii

# The platform's input filter replaces personal-data shapes in the request text
# with this sentinel before any node runs. A sentinel is not a value: it must
# never be rendered as an extracted title nor scored as a query term.
MASK_SENTINEL = "[MASKED]"

# ── Bounds (every caller-controlled field has one) ───────────────────────────
MAX_MATERIALS_PER_REQUEST = 50
MAX_MATERIAL_TITLE_CHARS = 200
MAX_MATERIAL_URL_CHARS = 512
MAX_MATERIAL_CONTENT_CHARS = 8000
MAX_REPORT_TITLE_CHARS = 120
DEFAULT_MAX_MATERIALS = 8
MAX_MATERIALS_CEILING = MAX_MATERIALS_PER_REQUEST

# ── Closed vocabularies ──────────────────────────────────────────────────────
MATERIAL_TYPES = frozenset({"paper", "patent", "standard", "spec", "report", "document"})
MATERIAL_LANGUAGES = frozenset({"ja", "en"})
REPORT_LANGUAGES = frozenset({"ja"})

# Keys of every normalised material record (the retrieval schema).
MATERIAL_KEYS = ("title", "url", "content", "type", "language", "relevance_score")

_REQUIREMENT_KEYS = frozenset({"title", "language"})
_MATERIAL_INPUT_KEYS = frozenset(MATERIAL_KEYS)

# ── Anchored identifier grammars (\A ... \Z, see module comment) ─────────────
_CHANNEL_RE = re.compile(r"\A[a-z0-9_-]{1,32}\Z")
_SAFE_FIELD_NAME_RE = re.compile(r"\A[A-Za-z0-9_]{1,32}\Z")
# http(s) only, no whitespace / quotes / angle brackets anywhere, bounded.
_URL_RE = re.compile(r"\Ahttps?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{1,504}\Z")

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_INVISIBLE_RE = re.compile(r"[​‌‍﻿­]")
_WS_RE = re.compile(r"\s+")

# ── Prompt-injection forms screened on caller text ───────────────────────────
# Chat-template control tokens are screened as a CLASS (any `<|...|>` marker,
# Llama/Mistral instruction and system tags, forged role tags), not as a list
# of phrases. Directive phrases are anchored at both ends and require a persona
# or an instruction object so ordinary engineering prose ("act as a reference
# standard", "prior tolerance context") does not trip them.
_CONTROL_TOKEN_RES: List[re.Pattern[str]] = [
    re.compile(r"<\|[^|<>]{1,32}\|>"),
    re.compile(r"\[/?(?:INST|SYS)\]", re.IGNORECASE),
    re.compile(r"<</?SYS>>", re.IGNORECASE),
    re.compile(r"<\s*/?\s*(?:system|user|assistant)\s*>", re.IGNORECASE),
    re.compile(r"###\s*(?:system|instruction)\b", re.IGNORECASE),
]
_DIRECTIVE_RES: List[re.Pattern[str]] = [
    re.compile(
        r"(?:ignore|disregard)\s+(?:all\s+|the\s+)?(?:previous|above|prior)\s+" r"(?:instructions?|prompts?|context)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bact\s+as\s+(?:a|an)\s+(?:different|new|unrestricted|unfiltered|evil|"
        r"jailbroken|dan|god|admin|root|superuser|hacker)\b",
        re.IGNORECASE,
    ),
]

# ── Credential shapes refused in caller text and blocked in the report ───────
# The framework detector is the FLOOR (Stripe / OpenAI keys, JWT, AWS key ids,
# Bearer tokens, connection strings). The local patterns below catch shapes the
# framework does not describe -- generic `pk-`/`ak-` keys, three-segment JWTs
# regardless of prefix, and plain credential assignments (`password=...`).
# Both sets are applied; neither replaces the other.
_LOCAL_CREDENTIAL_PATTERNS: List[tuple[str, re.Pattern[str]]] = [
    ("api_key", re.compile(r"\b(?:sk|pk|ak)-[A-Za-z0-9]{16,}", re.IGNORECASE)),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("bearer_token", re.compile(r"Bearer\s+[A-Za-z0-9._~+/]{20,}", re.IGNORECASE)),
    (
        "credential_assignment",
        re.compile(
            r"\b(?:password|passwd|secret|api_key|token|access_key|private_key)\s*[:=]\s*\S{8,}",
            re.IGNORECASE,
        ),
    ),
]

# ── Structured personal-data shapes masked in caller documents ───────────────
# Research literature is title-case by convention, and the platform's Latin
# name heuristic reads any two Title Case words as a person ("Statistical
# Process Control" -> masked). Applying it to document text would destroy the
# report, so only the structured shapes -- addresses, phone numbers, national
# identifiers, card numbers and labelled Japanese names -- are masked here.
_STRUCTURED_PII_TYPES = frozenset({"email", "phone_jp", "phone_us", "ssn_us", "my_number_jp", "credit_card", "name_jp"})
# Japanese is written without spaces, so the framework's word-boundary based
# My Number pattern misses `個人番号1234-5678-9012を確認`. Non-digit lookarounds
# close that gap for the separated 4-4-4 form only, so dates, thousands-grouped
# amounts and decimals stay untouched.
_MY_NUMBER_JP_UNSPACED_RE = re.compile(r"(?<!\d)\d{4}[-\s]\d{4}[-\s]\d{4}(?!\d)")


class ContractError(ValueError):
    """A caller field failed its contract. Carries the FIELD, never the value."""

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"{field} {reason}")


# ── Text primitives ──────────────────────────────────────────────────────────


def strip_control_chars(text: str) -> str:
    """Remove C0/C1 control characters and invisible code points; keep newlines."""
    return _INVISIBLE_RE.sub("", _CONTROL_RE.sub("", text))


def single_line(text: str, limit: int) -> str:
    """Render caller text onto ONE Markdown line, bounded to `limit` characters.

    Control characters and invisible code points are removed and every
    whitespace run -- newlines included -- collapses to a single space, so the
    returned string can never open a heading, a bullet or a section of its own.
    """
    collapsed = _WS_RE.sub(" ", strip_control_chars(text)).strip()
    return collapsed[:limit]


def _normalise_for_screen(text: str) -> str:
    """The post-sanitise view a screen must ALSO look at.

    A sanitiser can re-assemble a spliced directive (`ig\\x00nore`) or fold a
    fullwidth token onto its ASCII form, so every screen runs on both the raw
    text and this normalised form.
    """
    return _WS_RE.sub(" ", strip_control_chars(unicodedata.normalize("NFKC", text)))


def safe_field_name(name: object, index: int, parent: str) -> str:
    """A caller-supplied field name, repeated back only when short and inert."""
    if isinstance(name, str) and _SAFE_FIELD_NAME_RE.fullmatch(name):
        return f"{parent}.{name}"
    return f"{parent} field #{index}"


# ── Screens ──────────────────────────────────────────────────────────────────


def screen_injection(text: str) -> Optional[str]:
    """Return the class of the first prompt-injection form found, else None.

    Runs on the raw text AND on its normalised form. The framework detector's
    high-confidence findings are unioned in as the floor.
    """
    for candidate in (text, _normalise_for_screen(text)):
        if any(rx.search(candidate) for rx in _CONTROL_TOKEN_RES):
            return "control_token"
        if any(rx.search(candidate) for rx in _DIRECTIVE_RES):
            return "directive"
        if any(f["confidence"] == "high" for f in detect_injection(candidate)):
            return "injection_pattern"
    return None


def credential_violation(text: str) -> Optional[str]:
    """Return the name of the first credential shape found, else None.

    The union of the local patterns and the framework detector -- the local set
    catches assignments the framework does not describe, the framework set
    catches key formats the local set does not.
    """
    for name, pattern in _LOCAL_CREDENTIAL_PATTERNS:
        if pattern.search(text):
            return name
    findings = detect_credentials(text)
    if findings:
        return str(findings[0]["type"])
    return None


def mask_structured_pii(text: str) -> str:
    """Mask structured personal-data shapes in document text (see module comment)."""
    findings = [f for f in detect_pii(text) if f["type"] in _STRUCTURED_PII_TYPES]
    for match in _MY_NUMBER_JP_UNSPACED_RE.finditer(text):
        findings.append({"type": "my_number_jp", "value": match.group(), "start": match.start(), "end": match.end()})
    if not findings:
        return text
    return str(mask_pii(text, findings))


# ── Numbers ──────────────────────────────────────────────────────────────────


def finite_in_range(value: object, low: float, high: float) -> Optional[float]:
    """Parse a caller number that must be finite and within [low, high].

    Booleans, non-numeric strings, NaN and +/-Infinity (which `float()` parses
    happily and which compare False against every bound -- a silent fail-open)
    and out-of-range magnitudes all return None. The caller fails CLOSED.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(number) or number < low or number > high:
        return None
    return number


# ── Contract validation ──────────────────────────────────────────────────────


def _validated_text(value: object, field: str, limit: int, required: bool) -> str:
    if value is None:
        if required:
            raise ContractError(field, "is required")
        return ""
    if not isinstance(value, str):
        raise ContractError(field, "must be a string")
    rendered = single_line(value, limit + 1)
    if not rendered:
        if required:
            raise ContractError(field, "must not be empty")
        return ""
    if len(rendered) > limit:
        raise ContractError(field, f"exceeds {limit} characters")
    injection = screen_injection(value)
    if injection is not None:
        raise ContractError(field, f"contains a prompt-injection pattern ({injection})")
    credential = credential_violation(value)
    if credential is not None:
        raise ContractError(field, f"contains a credential-shaped value ({credential})")
    return rendered


def _validate_material(entry: object, field: str) -> Dict[str, Any]:
    if not isinstance(entry, dict):
        raise ContractError(field, "must be an object")
    for index, key in enumerate(entry, start=1):
        if key not in _MATERIAL_INPUT_KEYS:
            raise ContractError(safe_field_name(key, index, field), "is not part of the material schema")
    title = mask_structured_pii(_validated_text(entry.get("title"), f"{field}.title", MAX_MATERIAL_TITLE_CHARS, True))
    content = mask_structured_pii(
        _validated_text(entry.get("content"), f"{field}.content", MAX_MATERIAL_CONTENT_CHARS, True)
    )
    url = _validated_text(entry.get("url"), f"{field}.url", MAX_MATERIAL_URL_CHARS, False)
    if url and not _URL_RE.fullmatch(url):
        raise ContractError(f"{field}.url", "must be an http(s) URL without whitespace or quotes")
    mtype = entry.get("type", "document")
    if not isinstance(mtype, str) or mtype not in MATERIAL_TYPES:
        raise ContractError(f"{field}.type", f"must be one of {sorted(MATERIAL_TYPES)}")
    language = entry.get("language", "en")
    if not isinstance(language, str) or language not in MATERIAL_LANGUAGES:
        raise ContractError(f"{field}.language", f"must be one of {sorted(MATERIAL_LANGUAGES)}")
    score: Optional[float] = None
    if "relevance_score" in entry and entry.get("relevance_score") is not None:
        score = finite_in_range(entry.get("relevance_score"), 0.0, 1.0)
        if score is None:
            raise ContractError(f"{field}.relevance_score", "must be a finite number between 0 and 1")
    material: Dict[str, Any] = {
        "title": title,
        "url": url,
        "content": content,
        "type": mtype,
        "language": language,
    }
    if score is not None:
        material["relevance_score"] = round(score, 4)
    return material


def validate_materials(value: object, field: str) -> List[Dict[str, Any]]:
    """Validate a list of research materials (caller or operator supplied)."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ContractError(field, "must be a list")
    if len(value) > MAX_MATERIALS_PER_REQUEST:
        raise ContractError(field, f"exceeds {MAX_MATERIALS_PER_REQUEST} entries")
    return [_validate_material(entry, f"{field}[{i}]") for i, entry in enumerate(value)]


def empty_contract() -> Dict[str, Any]:
    return {"channel": "unknown", "report_requirements": {}, "research_materials": []}


def validate_input_context(input_context: object) -> Dict[str, Any]:
    """Validate the caller's `input_context` and return the VALIDATED contract.

    Accepted top-level fields: `channel` (inert identifier), `report_requirements`
    (`title`, `language`) and `research_materials` (list of material objects).
    Other top-level keys are ignored -- the platform may add its own -- but the
    objects underneath the accepted keys are closed: an unknown key there is a
    contract violation, so a misspelt field never degrades silently.
    """
    if input_context is None:
        return empty_contract()
    if not isinstance(input_context, dict):
        raise ContractError("input_context", "must be an object")
    contract = empty_contract()

    channel = input_context.get("channel")
    if channel is not None:
        if not isinstance(channel, str) or not _CHANNEL_RE.fullmatch(channel):
            raise ContractError("input_context.channel", "must match [a-z0-9_-]{1,32}")
        contract["channel"] = channel

    requirements = input_context.get("report_requirements")
    if requirements is not None:
        if not isinstance(requirements, dict):
            raise ContractError("input_context.report_requirements", "must be an object")
        parent = "input_context.report_requirements"
        for index, key in enumerate(requirements, start=1):
            if key not in _REQUIREMENT_KEYS:
                raise ContractError(safe_field_name(key, index, parent), "is not part of the report requirements")
        validated_requirements: Dict[str, Any] = {}
        title = _validated_text(requirements.get("title"), f"{parent}.title", MAX_REPORT_TITLE_CHARS, False)
        if title:
            validated_requirements["title"] = title
        language = requirements.get("language")
        if language is not None:
            if not isinstance(language, str) or language not in REPORT_LANGUAGES:
                raise ContractError(f"{parent}.language", f"must be one of {sorted(REPORT_LANGUAGES)}")
            validated_requirements["language"] = language
        contract["report_requirements"] = validated_requirements

    contract["research_materials"] = validate_materials(
        input_context.get("research_materials"), "input_context.research_materials"
    )
    return contract


# ── Reference corpus + ranking ───────────────────────────────────────────────

# Built-in manufacturing reference corpus: the baseline used when a request
# carries no research materials and the operator configured none. Relevance is
# scored against the query at runtime.
REFERENCE_CORPUS: List[Dict[str, Any]] = [
    {
        "title": "JIS B 0405 -- General tolerances for linear and angular dimensions",
        "url": "https://standards.example.org/jis/b0405",
        "content": (
            "Defines general tolerance classes (fine / medium / coarse) for "
            "machined linear and angular dimensions where individual tolerances "
            "are not specified on the drawing."
        ),
        "type": "standard",
        "language": "ja",
        "keywords": ["tolerance", "machining", "dimension", "kousa", "kakou"],
    },
    {
        "title": "Statistical process control for semiconductor yield improvement",
        "url": "https://papers.example.org/spc-semiconductor-yield",
        "content": (
            "Reviews SPC control-chart methods (X-bar/R, EWMA) applied to "
            "semiconductor wafer fabrication, with case studies on defect-density "
            "reduction and yield ramp."
        ),
        "type": "paper",
        "language": "en",
        "keywords": ["semiconductor", "yield", "spc", "defect", "buaisubmit"],
    },
    {
        "title": "Predictive maintenance of CNC machine tools (patent JP-XXXXXXX)",
        "url": "https://patents.example.org/jp/cnc-predictive-maintenance",
        "content": (
            "Describes a vibration- and spindle-load-based predictive maintenance "
            "method for CNC machining centers, triggering tool-change before "
            "out-of-tolerance production."
        ),
        "type": "patent",
        "language": "ja",
        "keywords": ["cnc", "maintenance", "predictive", "hozen", "vibration"],
    },
    {
        "title": "Lean manufacturing line-balancing techniques",
        "url": "https://papers.example.org/lean-line-balancing",
        "content": (
            "Surveys takt-time line-balancing and bottleneck-elimination methods "
            "for high-mix assembly, including changeover (SMED) reduction."
        ),
        "type": "paper",
        "language": "en",
        "keywords": ["lean", "line balancing", "takt", "smed", "assembly"],
    },
]


def query_tokens(query: str) -> List[str]:
    """Tokenise the research query for relevance scoring.

    Tokens that are (or contain) the platform's masking sentinel are dropped:
    a masked span is not a search term, and a query the filter masked entirely
    must fall back to the unranked baseline rather than match nothing.
    """
    sentinel = MASK_SENTINEL.lower()
    return [t for t in query.lower().replace("/", " ").split() if t and sentinel not in t]


def score_relevance(tokens: List[str], entry: Dict[str, Any]) -> float:
    """Deterministic keyword-overlap relevance score in [0.0, 1.0]."""
    haystack = " ".join(
        [str(entry.get("title", "")), str(entry.get("content", ""))] + [str(k) for k in entry.get("keywords", [])]
    ).lower()
    if not tokens:
        return 0.5
    hits = sum(1 for tok in tokens if tok in haystack)
    return round(min(1.0, hits / len(tokens)), 4)


def normalise_material(entry: Dict[str, Any], score: float) -> Dict[str, Any]:
    """Project a validated (or built-in) source record onto the retrieval schema."""
    supplied = entry.get("relevance_score")
    relevance = finite_in_range(supplied, 0.0, 1.0) if supplied is not None else None
    return {
        "title": str(entry.get("title", "Untitled source")),
        "url": str(entry.get("url", "")),
        "content": str(entry.get("content", "")),
        "type": str(entry.get("type", "document")),
        "language": str(entry.get("language", "en")),
        "relevance_score": float(relevance if relevance is not None else score),
    }


def rank_materials(
    query: str,
    caller_materials: List[Dict[str, Any]],
    operator_documents: List[Dict[str, Any]],
    limit: int,
) -> List[Dict[str, Any]]:
    """Rank the working set of research materials for a query.

    Caller-supplied materials and operator-configured documents are the
    request's own sources: every one of them is kept and ranked. Only when
    neither is present does the built-in reference corpus apply, and there a
    corpus entry with no overlap at all with a non-empty query is left out.
    Returns at most `limit` records, most relevant first.
    """
    tokens = query_tokens(query)
    supplied = [m for m in caller_materials if isinstance(m, dict)] + [
        d for d in operator_documents if isinstance(d, dict)
    ]
    ranked: List[Dict[str, Any]] = []
    if supplied:
        for entry in supplied:
            ranked.append(normalise_material(entry, score_relevance(tokens, entry)))
    else:
        for entry in REFERENCE_CORPUS:
            score = score_relevance(tokens, entry)
            if score <= 0.0 and tokens:
                continue
            ranked.append(normalise_material(entry, score))
    ranked.sort(key=lambda m: float(m["relevance_score"]), reverse=True)
    return ranked[: max(1, int(limit))]
