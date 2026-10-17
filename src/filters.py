"""Cheap, deterministic filters that run before any LLM call."""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from .config import Preferences
from .geo import classify
from .models import Job, normalize_company

_YEARS_RE = re.compile(
    r"(?<![\d.])(?P<min>\d{1,2})(?:\.\d)?\s*(?:\+|plus)?\s*"
    r"(?:(?:-|–|—|to)\s*(?P<max>\d{1,2})\s*\+?\s*)?"
    r"(?:years?|yrs?)\b",
    re.I,
)
_EXP_CONTEXT = re.compile(r"experience|exp\b|hands[- ]on|background|industry|working|professional|relevant", re.I)
_PREFERRED = re.compile(r"prefer|nice to have|bonus|ideally|desirable|good to have", re.I)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;\n])\s+|\n+")

_VISA_YES = re.compile(
    r"visa sponsorship (is )?(available|provided|offered)|(we|will) (offer|provide|support) (visa|relocation)"
    r"|sponsor(ship)? (your |a )?(work )?visa|relocation (support|assistance|package)|relocate you"
    r"|blue card sponsorship|willing to sponsor",
    re.I,
)
_VISA_NO = re.compile(
    r"(no|not able to|unable to|cannot|can ?not|do not|don't|won't|will not) (offer |provide )?"
    r"(visa )?sponsor|without (the need for )?(visa )?sponsorship|sponsorship is not (available|provided)"
    r"|must (have|hold) (the )?(right|authori[sz]ation) to work|no relocation",
    re.I,
)


_LANGS = (r"german|deutsch|french|fran[cç]ais|dutch|nederlands|spanish|espa[nñ]ol|italian|polish|japanese|"
          r"swedish|danish|norwegian|finnish|portuguese|arabic|mandarin|chinese|korean|hebrew|czech|turkish|"
          r"hungarian|romanian|greek")
_LANG_REQUIRED = re.compile(
    rf"(fluent|fluency|native|business[- ]level|professional|excellent|very good|good|strong|proficien\w*|"
    rf"working|c1|c2|b2)[^.\n]{{0,40}}\b({_LANGS})\b"
    rf"|\b({_LANGS})\b[^.\n]{{0,25}}(is )?(required|mandatory|must|essential|a must|c1|c2|b2)"
    r"|deutschkenntnisse|sehr gute[n]? deutsch|flie(ß|ss)ende?s? deutsch|verhandlungssicher|"
    r"ma[iî]trise du fran[cç]ais|fran[cç]ais courant",
    re.I,
)
_LANG_OPTIONAL = re.compile(r"\bplus\b|advantage|nice to have|bonus|preferred|beneficial|desirable|not required|ideally", re.I)
_FOREIGN_WORDS = re.compile(
    r"\b(und|der|wir|mit|f[uü]r|sie|ihre|bei|auf|nous|vous|avec|pour|les|des|une|het|een|voor|wij|jij|"
    r"och|att|f[oö]r|med|del|los|las|para|con|una|dla|oraz|jest)\b",
    re.I,
)


def language_flags(text: str) -> list[str]:
    """Flag JDs that need a local language or are not written in English."""
    flags = []
    words = max(1, len(re.findall(r"\w+", text or "")))
    if words > 40 and len(_FOREIGN_WORDS.findall(text or "")) / words > 0.04:
        flags.append("non_english")
    for sentence in _SENTENCE_SPLIT.split(text or ""):
        if _LANG_REQUIRED.search(sentence) and not _LANG_OPTIONAL.search(sentence):
            flags.append("local_language")
            break
    return flags


_DEADLINE = re.compile(
    r"(?:apply by|apply before|closing date|close[sd]? on|last date(?: to apply| of application| for applications?)?|"
    r"application deadline|deadline(?: to apply| for applications?)?|applications close(?: on)?|"
    r"accepting applications until|open until)\s*[:\-–]?\s*(?P<date>[^\n.;]{4,40})",
    re.I,
)


def extract_deadline(text: str, now: datetime | None = None) -> str | None:
    """Closing date stated in the JD ("Apply by 30 Oct 2026", "Last date: 25/10/2026"), as ISO UTC."""
    from dateutil import parser as dateparser

    now = now or datetime.now(timezone.utc)
    for m in _DEADLINE.finditer(text or ""):
        chunk = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", m.group("date"))
        chunk = re.split(r"\s+(?:at|by|,? ?\d{1,2}[:.]\d{2}|\(|or|and)\b", chunk)[0]
        try:
            dt = dateparser.parse(chunk, dayfirst=True, fuzzy=True, default=now.replace(hour=23, minute=59, second=0, microsecond=0, tzinfo=None))
        except (ValueError, OverflowError):
            continue
        dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
        if now - timedelta(days=7) <= dt <= now + timedelta(days=365):  # sanity window
            return dt.isoformat(timespec="seconds")
    return None


def _compile(patterns: list[str]) -> re.Pattern[str] | None:
    if not patterns:
        return None
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(f"(?:{p})" for p in patterns) + r")(?![a-z0-9])", re.I)


def extract_min_years(text: str, structured: str = "") -> int | None:
    """Return the largest *required* minimum years of experience mentioned, or None.

    `structured` is a board-provided range like Naukri's "2-5 Yrs" and is trusted without context.
    Free-text matches need an experience word nearby and are ignored in "preferred" sentences.
    """
    if structured:
        m = _YEARS_RE.search(structured) or re.search(r"(\d{1,2})\s*(?:-|to)\s*\d{1,2}", structured)
        if m:
            return int(m.group(1))
    found: list[int] = []
    for sentence in _SENTENCE_SPLIT.split(text or ""):
        if _PREFERRED.search(sentence):
            continue
        for m in _YEARS_RE.finditer(sentence):
            window = sentence[max(0, m.start() - 60): m.end() + 60]
            if not _EXP_CONTEXT.search(window):
                continue
            value = int(m.group("min"))
            if value <= 20:
                found.append(value)
    return max(found) if found else None


def detect_visa(text: str) -> bool | None:
    if _VISA_NO.search(text or ""):
        return False
    if _VISA_YES.search(text or ""):
        return True
    return None


def is_target_company(company: str, targets: list[str]) -> bool:
    norm = normalize_company(company)
    return any(normalize_company(t) and re.search(rf"\b{re.escape(normalize_company(t))}\b", norm) for t in targets)


def enrich(job: Job, prefs: Preferences) -> Job:
    geo = classify(job.location, job.remote)
    job.region, job.country = geo.region, geo.country or job.country
    job.remote = job.remote or geo.region == "remote"
    job.min_years = extract_min_years(job.description, job.experience_text)
    job.visa_sponsorship = detect_visa(job.description)
    job.expires_at = job.expires_at or extract_deadline(job.description)
    for flag in language_flags(job.description) if job.region in ("abroad", "remote", "unknown") else []:
        if flag not in job.flags:
            job.flags.append(flag)
    if is_target_company(job.company, prefs.target_companies) and "target_company" not in job.tags:
        job.tags.append("target_company")
    return job


class JobFilter:
    def __init__(self, prefs: Preferences, now: datetime | None = None) -> None:
        self.prefs = prefs
        self.now = now or datetime.now(timezone.utc)
        self.include = _compile(prefs.title_include)
        self.exclude = _compile(prefs.title_exclude)
        self.desc_exclude = _compile(prefs.description_exclude)
        self.company_exclude = _compile(prefs.company_exclude)
        self.rejections: Counter[str] = Counter()

    def title_ok(self, title: str) -> bool:
        """Title-only check, used by sources to skip fetching descriptions for irrelevant postings."""
        if self.include and not self.include.search(title or ""):
            return False
        return not (self.exclude and self.exclude.search(title or ""))

    def reason(self, job: Job) -> str | None:
        """Return why a job is rejected, or None if it passes."""
        p = self.prefs
        if not job.title or not job.url:
            return "missing_fields"
        if self.include and not self.include.search(job.title):
            return "title_not_target"
        if self.exclude and self.exclude.search(job.title):
            return "title_excluded"
        if self.company_exclude and self.company_exclude.search(job.company or ""):
            return "company_excluded"
        if self.desc_exclude and self.desc_exclude.search(job.description or ""):
            return "description_excluded"
        if job.min_years is not None and job.min_years > p.max_required_years:
            return "too_much_experience"
        if job.expires_at and job.expires_at < self.now.isoformat():
            return "expired"
        posted = job.posted_datetime()
        if posted is None:
            if not p.keep_unknown_date:
                return "unknown_date"
        elif posted < self.now - timedelta(days=p.max_age_days):
            return "too_old"
        if job.region == "unknown" and not p.keep_unknown_location:
            return "unknown_location"
        if p.drop_local_language and ({"local_language", "non_english"} & set(job.flags)):
            return "needs_local_language"
        if job.region == "abroad":
            if job.visa_sponsorship is False and p.abroad_drop_if_no_sponsorship:
                return "abroad_no_sponsorship"
            listed = job.country in p.abroad_countries
            if not listed and not (p.abroad_keep_if_visa and job.visa_sponsorship):
                return "abroad_country_not_listed"
        return None

    def apply(self, jobs: list[Job]) -> list[Job]:
        kept: list[Job] = []
        for job in jobs:
            why = self.reason(job)
            if why:
                self.rejections[why] += 1
            else:
                kept.append(job)
        return kept

