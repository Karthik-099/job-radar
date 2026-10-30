"""Common Job schema shared by every source."""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Pipeline. "shortlisted" = recruiter screen (key kept for old data); "ghosted" = no reply for weeks.
STATUSES = ("new", "shortlisted", "applied", "assessment", "interview", "final", "offer", "rejected", "ghosted", "ignored")
APPLIED_STATES = ("applied", "shortlisted", "assessment", "interview", "final", "offer", "rejected", "ghosted")

# Query params that only track clicks and never identify a posting.
_TRACKING_PARAMS = re.compile(r"^(utm_.*|ref|refid|src|source|trk|trackingid|gh_src|lever-source|from)$", re.I)

_LOCATION_ALIASES = {
    "bengaluru": "bangalore",
    "bangalore urban": "bangalore",
    "gurugram": "gurgaon",
    "bombay": "mumbai",
    "new delhi": "delhi",
}


def _norm_text(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9+#]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_company(company: str) -> str:
    value = _norm_text(company)
    value = re.sub(r"\b(inc|llc|ltd|limited|pvt|private|corp|corporation|gmbh|co|technologies|india)\b", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_location(location: str) -> str:
    # "Bengaluru, Karnataka, India" and "Bangalore" should collide: keep the first segment (city).
    first = re.split(r"[,/|;(]| - ", location or "", maxsplit=1)[0]
    value = _norm_text(first)
    for alias, canonical in _LOCATION_ALIASES.items():
        value = re.sub(rf"\b{alias}\b", canonical, value)
    return value


def normalize_url(url: str) -> str:
    if not url:
        return ""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False) if not _TRACKING_PARAMS.match(k)]
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower() or "https", parts.netloc.lower(), path, urlencode(sorted(query)), ""))


# Trailing title segments that only say where/how the job is, never which role it is.
_TITLE_NOISE = re.compile(
    r"^(remote|hybrid|on-?site|onsite|wfh|work from home|full[- ]?time|part[- ]?time|contract|permanent|"
    r"india|apac|emea|global|worldwide|anywhere|bangalore|bengaluru|pune|hyderabad|chennai|mumbai|delhi|ncr|"
    r"gurgaon|gurugram|noida|kolkata|ahmedabad|kochi|req\w*|job id.*|r?-?\d[\w-]*)$", re.I)


def role_title(title: str) -> str:
    """'DevOps Engineer (SRE) - Bengaluru | Remote' -> 'devops engineer'. Level markers (I, II) are kept."""
    value = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", title or "")
    parts = [p.strip() for p in re.split(r"\s+[-–|/]\s+|,\s+", value) if p.strip()]
    while len(parts) > 1 and _TITLE_NOISE.match(parts[-1]):
        parts.pop()
    return _norm_text(" ".join(parts))


def role_key(company: str, title: str) -> str:
    """Same role at the same company, whatever city, board or posting it appears under."""
    return hashlib.sha1(f"{normalize_company(company)}|{role_title(title)}".encode("utf-8")).hexdigest()[:12]


# Requisition ids companies use internally (what a referrer types into their referral portal).
_REQ_IN_TEXT = re.compile(
    r"\b(?:job|req(?:uisition)?|position|reference|vacancy|opening)\s*(?:id|no\.?|number|code|#)\s*[:#.]?\s*"
    r"([A-Z]{0,6}[-_]?\d[\w-]{2,18})", re.I)
_REQ_IN_URL = (
    re.compile(r"greenhouse\.io/.*/jobs/(\d{5,})"),
    re.compile(r"[?&]gh_jid=(\d{5,})"),
    re.compile(r"myworkdayjobs\.com/.*_([A-Za-z]{0,6}-?\d{3,}[\w-]*?)(?:-\d)?(?:[/?#]|$)"),
    re.compile(r"smartrecruiters\.com/[^/]+/(\d{9,})"),
)


def posting_ref(url: str, description: str = "") -> str:
    """The employer's own job/requisition id when the posting states one, else ''. Board ids (Indeed jk,
    Naukri, LinkedIn) are deliberately ignored: a referrer can't look those up."""
    m = _REQ_IN_TEXT.search((description or "")[:6000])
    if m and re.search(r"\d", m.group(1)):
        return m.group(1).strip("-_.")
    for rx in _REQ_IN_URL:
        m = rx.search(url or "")
        if m:
            return m.group(1)
    return ""


def dedupe_key(company: str, title: str, location: str) -> str:
    raw = "|".join((normalize_company(company), _norm_text(title), normalize_location(location)))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


@dataclass
class Job:
    title: str
    company: str
    location: str
    url: str
    source: str
    description: str = ""
    remote: bool = False
    posted_at: str | None = None  # ISO 8601 UTC
    expires_at: str | None = None  # ISO 8601 UTC, when the source or JD states a closing date
    id: str = ""
    # Enrichment filled in by the pipeline.
    region: str = "unknown"  # india | remote | abroad | unknown
    country: str = ""
    salary: str = ""
    experience_text: str = ""
    min_years: int | None = None
    visa_sponsorship: bool | None = None  # True offered, False explicitly not, None unknown
    tags: list[str] = field(default_factory=list)
    alt_urls: list[str] = field(default_factory=list)  # same posting seen elsewhere
    first_seen: str = ""
    status: str = "new"
    score: float | None = None
    apply_priority: float | None = None  # 0-100, "apply to this next" ranking
    reasons: list[str] = field(default_factory=list)  # why it ranks where it does
    flags: list[str] = field(default_factory=list)  # warnings: local_language, non_english, ...
    scoring: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)
    jd_terms: list[str] = field(default_factory=list)  # ATS keywords found in the full JD (see tailor.py)
    ref: str = ""  # the employer's job/requisition id, when the posting states one
    letter: dict[str, Any] = field(default_factory=dict)  # cover letter {"text", "rh": resume hash, "at"}

    def __post_init__(self) -> None:
        self.title = (self.title or "").strip()
        self.company = (self.company or "").strip()
        self.location = (self.location or "").strip()
        if not self.id:
            self.id = dedupe_key(self.company, self.title, self.location)[:16]
        if not self.first_seen:
            self.first_seen = datetime.now(timezone.utc).isoformat(timespec="seconds")

    @property
    def key(self) -> str:
        return dedupe_key(self.company, self.title, self.location)

    @property
    def norm_url(self) -> str:
        return normalize_url(self.url)

    def posted_datetime(self) -> datetime | None:
        if not self.posted_at:
            return None
        try:
            dt = datetime.fromisoformat(self.posted_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Job":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})
