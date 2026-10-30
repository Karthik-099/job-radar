""""Learn" tab: recent videos in fixed sections (System Design, DevOps, DevSecOps, Architecture, AWS, Azure,
Kubernetes, Observability & SRE, AI & MLOps, Career).

Sources:
  1. Curated channels (profile/youtube_channels.yaml) through their public RSS feeds: no key, no account.
  2. Optional: with a free YouTube Data API key (secret YOUTUBE_API_KEY, no billing needed) each section also
     gets the most-watched videos of the last 30 days from all of YouTube. Cost: 101 of the 10,000 free daily
     quota units per section, twice a day = ~2,000 units/day.
A video is filed under the section its TITLE names (an AWS talk on a DevOps channel goes to AWS), else under the
channel's home section. Off-topic videos (vlogs, shorts) are dropped. Ranked per section by match with your
stack, this week's skill gaps, freshness and views. Refreshed at most every REFRESH_HOURS; failures keep the old list.
"""
from __future__ import annotations

import logging
import math
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

import yaml

from . import net
from .config import PROFILE_DIR
from .envsecrets import secret

log = logging.getLogger(__name__)

CHANNELS_FILE = PROFILE_DIR / "youtube_channels.yaml"
FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
YT_API = "https://www.googleapis.com/youtube/v3"
REFRESH_HOURS = 8
MAX_AGE_DAYS = 45
PER_SECTION = 12
PER_CHANNEL = 4  # per section, for variety
VERSION = 2  # bump when sections change: forces a refresh instead of waiting REFRESH_HOURS
NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
      "m": "http://search.yahoo.com/mrss/"}

# section -> (pattern, YouTube search query). Order matters: the first section a title matches wins.
SECTIONS: dict[str, tuple[str, str]] = {
    "DevSecOps": (r"devsecops|security|secure|vulnerab|supply chain|sbom|zero trust|\biam\b|secrets|owasp|cve|"
                  r"pentest|threat|sast|dast|trivy|snyk", "DevSecOps tutorial"),
    "System Design": (r"system design|design (a|an|the) |distributed system|load balanc|caching|"
                      r"consistent hashing|rate limit|database (design|shard)|\bcap theorem", "system design interview"),
    "Architecture": (r"architect|microservice|event[- ]driven|domain[- ]driven|\bddd\b|monolith|design pattern|"
                     r"cqrs|event sourcing|software design", "software architecture"),
    "AWS": (r"\baws\b|amazon web services|\bec2\b|\beks\b|\bs3\b|lambda|cloudformation|re:?invent", "AWS tutorial"),
    "Azure": (r"azure|\baks\b|entra|bicep|microsoft cloud", "Azure tutorial"),
    "Kubernetes": (r"kubernetes|k8s|helm|kubecon|cilium|ebpf|istio|service mesh|container|docker|gke|"
                   r"karpenter|crossplane|operator", "Kubernetes tutorial"),
    "Observability & SRE": (r"observability|monitor|opentelemetry|otel|prometheus|grafana|loki|tempo|mimir|"
                            r"\bsre\b|reliability|incident|on-?call|\bslo|datadog|logging|tracing", "SRE observability"),
    "AI & MLOps": (r"\bai\b|llm|gpt|claude|gemini|agent|mcp|\brag\b|inference|gpu|nvidia|mlops|machine learning|"
                   r"openai|anthropic|model", "MLOps LLM infrastructure"),
    "DevOps": (r"devops|ci ?/ ?cd|github actions|jenkins|gitlab|pipeline|terraform|opentofu|ansible|pulumi|gitops|"
               r"argo|platform engineering|backstage|infrastructure as code|\biac\b|linux|bash", "DevOps tutorial"),
    "Career": (r"interview|roadmap|career|resume|\bjob|hiring|beginner|learn|salary|portfolio", "DevOps career roadmap"),
}
_RX = {k: re.compile(p, re.I) for k, (p, _) in SECTIONS.items()}
# Your stack: matching videos rank higher inside a section.
_STACK = re.compile(r"kubernetes|terraform|argo|prometheus|grafana|azure|aws|aks|eks|helm|gitops|observability|"
                    r"sre|incident|gpu|slurm|llm|datadog|opentelemetry|linux|python", re.I)


def _channels() -> list[dict[str, str]]:
    if not CHANNELS_FILE.exists():
        return []
    data = yaml.safe_load(CHANNELS_FILE.read_text(encoding="utf-8")) or {}
    return [c for c in data.get("channels") or [] if c.get("id")]


def parse_feed(xml_text: str, channel: str, section: str = "") -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    out = []
    for e in root.findall("a:entry", NS):
        vid = e.findtext("yt:videoId", "", NS)
        link_el = e.find("a:link", NS)
        link = link_el.attrib.get("href", "") if link_el is not None else ""
        title = e.findtext("a:title", "", NS)
        if not vid or "/shorts/" in link or "#shorts" in title.lower():
            continue
        stats = e.find("m:group/m:community/m:statistics", NS)
        desc = e.findtext("m:group/m:description", "", NS) or ""
        out.append({"id": vid, "title": title, "channel": channel, "home": section,
                    "published": e.findtext("a:published", "", NS),
                    "views": int(stats.attrib.get("views", 0)) if stats is not None else 0, "desc": desc[:400]})
    return out


def section_of(video: dict[str, Any]) -> str | None:
    """The section the title names; else the channel's home section if the description is on-topic; else None."""
    for name, rx in _RX.items():
        if rx.search(video["title"]):
            return name
    text = video.get("desc", "")
    if any(rx.search(text) for rx in _RX.values()):
        return video.get("home") or next(n for n, rx in _RX.items() if rx.search(text))
    return None


def rank(videos: list[dict[str, Any]], gaps: list[str], now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    gap_rx = [(g, re.compile(rf"\b{re.escape(g)}\b", re.I)) for g in gaps if len(g) > 2]
    by_section: dict[str, list[dict[str, Any]]] = {}
    seen: set[str] = set()
    for v in videos:
        if v["id"] in seen:
            continue
        try:
            age = (now - datetime.fromisoformat(v["published"].replace("Z", "+00:00"))).total_seconds() / 86400
        except ValueError:
            continue
        section = section_of(v)
        if age > MAX_AGE_DAYS or not section:
            continue
        seen.add(v["id"])
        text = f"{v['title']} {v.get('desc', '')}"
        gap = next((g for g, rx in gap_rx if rx.search(text)), "")
        score = (2 if _STACK.search(v["title"]) else 0) + (3 if gap else 0) + max(0.0, 3 - age / 7) \
            + math.log10(v.get("views", 0) + 10)
        by_section.setdefault(section, []).append(
            {k: v[k] for k in ("id", "title", "channel", "published", "views")}
            | {"topic": section, "gap": gap, "score": round(score, 2)})
    picked: list[dict[str, Any]] = []
    for section in SECTIONS:
        per_channel: dict[str, int] = {}
        for v in sorted(by_section.get(section, []), key=lambda x: -x["score"]):
            if per_channel.get(v["channel"], 0) >= PER_CHANNEL:
                continue
            per_channel[v["channel"]] = per_channel.get(v["channel"], 0) + 1
            picked.append(v)
            if sum(per_channel.values()) >= PER_SECTION:
                break
    return picked


def _api_search(key: str) -> list[dict[str, Any]]:
    """Most-watched recent videos per section from all of YouTube (needs a free Data API key)."""
    after = (datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    found: list[dict[str, Any]] = []
    for section, (_, query) in SECTIONS.items():
        try:
            items = net.get_json(f"{YT_API}/search", params={
                "key": key, "part": "snippet", "q": query, "type": "video", "order": "viewCount", "maxResults": 8,
                "publishedAfter": after, "relevanceLanguage": "en", "videoDuration": "medium"}).get("items") or []
        except (net.requests.RequestException, ValueError):
            continue
        ids = [i["id"]["videoId"] for i in items if (i.get("id") or {}).get("videoId")]
        views: dict[str, int] = {}
        if ids:
            try:
                for s in net.get_json(f"{YT_API}/videos", params={"key": key, "part": "statistics",
                                                                  "id": ",".join(ids)}).get("items") or []:
                    views[s["id"]] = int((s.get("statistics") or {}).get("viewCount", 0))
            except (net.requests.RequestException, ValueError):
                pass
        for i in items:
            sn, vid = i.get("snippet") or {}, (i.get("id") or {}).get("videoId")
            if vid:
                found.append({"id": vid, "title": sn.get("title", ""), "channel": sn.get("channelTitle", ""),
                              "home": section, "published": sn.get("publishedAt", ""), "views": views.get(vid, 0),
                              "desc": sn.get("description", "")})
    return found


def refresh(meta: dict[str, Any], gaps: list[str], force: bool = False) -> int:
    """Update meta['videos'] when due. Returns how many videos are listed."""
    last = meta.get("videos_at") if meta.get("videos_v") == VERSION else None
    if not force and last and datetime.now(timezone.utc) - datetime.fromisoformat(last) < timedelta(hours=REFRESH_HOURS):
        return len(meta.get("videos") or [])

    def one(c: dict[str, str]) -> list[dict[str, Any]]:
        try:
            r = net.get(FEED.format(c["id"]), timeout=(5, 10))
            return parse_feed(r.text, c.get("name") or "", c.get("section") or "") if r.ok else []
        except (net.requests.RequestException, ET.ParseError):
            return []

    with ThreadPoolExecutor(max_workers=8) as pool:
        found = [v for batch in pool.map(one, _channels()) for v in batch]
    key = secret("YOUTUBE_API_KEY")
    if key:
        found += _api_search(key)
    if found:
        meta["videos"] = rank(found, gaps)
        meta["videos_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        meta["videos_v"] = VERSION
        log.info("videos: %d recent, kept %d in %d sections", len(found), len(meta["videos"]),
                 len({v["topic"] for v in meta["videos"]}))
    return len(meta.get("videos") or [])
