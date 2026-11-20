"""Local-first privacy guard: regex + Shannon-entropy secret scrubber and sensitive-path exclusion.

Everything here runs on the developer's machine. Nothing is sent anywhere.
"""

from __future__ import annotations

import fnmatch
import math
import re
from dataclasses import dataclass, field
from pathlib import PurePath

# Files that are never read, indexed, diffed into memory or sent to a model.
SENSITIVE_GLOBS = [
    ".env", ".env.*", "*.env", ".envrc",
    "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore", "*.kdbx",
    "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*",
    ".npmrc", ".pypirc", ".netrc", "_netrc", ".git-credentials", ".dockercfg",
    "credentials", "credentials.json", "credentials.*", "*credentials*.json",
    "service-account*.json", "*serviceaccount*.json", "client_secret*.json",
    "secrets.*", "*.secret", "*.secrets", "secrets",
    "terraform.tfstate", "terraform.tfstate.*", "*.tfvars",
]
SENSITIVE_DIRS = {".ssh", ".aws", ".gnupg", ".azure", ".kube", "secrets"}

REDACTED = "[REDACTED:{kind}]"

# (kind, pattern, group-to-redact)  group 0 = whole match.
PATTERNS: list[tuple[str, re.Pattern[str], int]] = [
    ("PRIVATE_KEY", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----"), 0),
    ("AWS_ACCESS_KEY", re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{16}\b"), 0),
    ("GOOGLE_API_KEY", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), 0),
    ("GOOGLE_OAUTH_SECRET", re.compile(r"\bGOCSPX-[0-9A-Za-z_\-]{28}\b"), 0),
    ("GITHUB_TOKEN", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{22,255})\b"), 0),
    ("GITLAB_TOKEN", re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b"), 0),
    ("SLACK_TOKEN", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"), 0),
    ("STRIPE_KEY", re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}\b"), 0),
    ("OPENAI_KEY", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_\-]{20,}\b"), 0),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"), 0),
    ("URL_CREDENTIALS", re.compile(r"(?i)\b[a-z][a-z0-9+.\-]*://[^\s:/@]+:([^\s@/]+)@"), 1),
    ("BEARER_TOKEN", re.compile(r"(?i)\bBearer\s+([A-Za-z0-9_\-.=+/]{16,})"), 1),
    (
        "SECRET_ASSIGNMENT",
        re.compile(
            r"""(?ix)
            \b[\w.\-]*(?:password|passwd|pwd|secret|api[_\-]?key|apikey|access[_\-]?key|auth[_\-]?token|
                         access[_\-]?token|refresh[_\-]?token|private[_\-]?key|client[_\-]?secret|
                         token|credential)s?[\w.\-]*
            \s*(?:=|:|:=|=>)\s*
            (["']?)([^\s"',;#]{6,})\1
            """
        ),
        2,
    ),
]

# Candidates for entropy scanning: quoted strings or assignment values.
_ENTROPY_CANDIDATE = re.compile(r"""["']([A-Za-z0-9+/=_\-]{20,})["']|(?<==)\s*([A-Za-z0-9+/=_\-]{24,})\b""")
_HEX = set("0123456789abcdefABCDEF")
_PLACEHOLDER = re.compile(r"(?i)^(x+|\*+|<.*>|\$\{.*\}|changeme|your[_\-].*|example.*|dummy.*|test.*|redacted.*)$")


def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def looks_like_secret(token: str) -> bool:
    if _PLACEHOLDER.match(token) or token.count("/") > 2:
        return False  # placeholders and paths
    if token.isalpha() or token.isdigit():
        return False
    ent = shannon_entropy(token)
    if set(token) <= _HEX:
        # 40/64-char hex are almost always git SHAs / content hashes, not credentials.
        return len(token) >= 32 and len(token) not in (40, 64) and ent >= 3.0
    has_upper = any(c.isupper() for c in token)
    has_lower = any(c.islower() for c in token)
    has_digit = any(c.isdigit() for c in token)
    return ent >= 4.0 and has_digit and (has_upper and has_lower)


@dataclass
class Finding:
    kind: str
    line: int
    preview: str


@dataclass
class ScrubResult:
    text: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def redacted(self) -> int:
        return len(self.findings)


def _line_of(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def _mask(s: str) -> str:
    return (s[:3] + "…" + s[-2:]) if len(s) > 8 else "…"


def scrub(text: str) -> ScrubResult:
    """Redact secrets from text. Returns the clean text and what was found (masked)."""
    findings: list[Finding] = []
    spans: list[tuple[int, int, str]] = []

    def taken(a: int, b: int) -> bool:
        return any(a < e and b > s for s, e, _ in spans)

    for kind, pat, grp in PATTERNS:
        for m in pat.finditer(text):
            a, b = m.span(grp)
            if a < 0 or taken(a, b):
                continue
            val = m.group(grp)
            if kind == "SECRET_ASSIGNMENT" and (_PLACEHOLDER.match(val) or _is_code_reference(val)):
                continue
            spans.append((a, b, kind))
            findings.append(Finding(kind, _line_of(text, a), _mask(val)))

    for m in _ENTROPY_CANDIDATE.finditer(text):
        g = 1 if m.group(1) else 2
        a, b = m.span(g)
        val = m.group(g)
        if taken(a, b) or not looks_like_secret(val):
            continue
        spans.append((a, b, "HIGH_ENTROPY"))
        findings.append(Finding("HIGH_ENTROPY", _line_of(text, a), _mask(val)))

    if not spans:
        return ScrubResult(text, [])
    out, pos = [], 0
    for a, b, kind in sorted(spans):
        out.append(text[pos:a])
        out.append(REDACTED.format(kind=kind))
        pos = b
    out.append(text[pos:])
    findings.sort(key=lambda f: f.line)
    return ScrubResult("".join(out), findings)


def _is_code_reference(val: str) -> bool:
    """`token = request.headers.get(...)`-style code is not a secret."""
    return bool(re.match(r"^[A-Za-z_][\w]*(\.[A-Za-z_]\w*)*(\(|\[|$)", val)) and not any(c.isdigit() for c in val)


def is_sensitive_path(path: str | PurePath) -> bool:
    p = PurePath(str(path).replace("\\", "/"))
    if any(part.lower() in SENSITIVE_DIRS for part in p.parts[:-1]):
        return True
    name = p.name.lower()
    if name in (".env.example", ".env.sample", ".env.template", ".env.dist"):
        return False
    return any(fnmatch.fnmatch(name, g) for g in SENSITIVE_GLOBS)
