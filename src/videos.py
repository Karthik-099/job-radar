""""Learn" tab: recent videos on DevOps, cloud native and AI from a curated channel list (profile/youtube_channels.yaml).

Reads each channel's public RSS feed (youtube.com/feeds/videos.xml, no key, no account), keeps long-form videos
from the last few weeks, and ranks them by how well they match your stack and this week's skill gaps, freshness
and views. Refreshed at most every REFRESH_HOURS; a failure keeps the previous list.
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

log = logging.getLogger(__name__)

CHANNELS_FILE = PROFILE_DIR / "youtube_channels.yaml"
FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
REFRESH_HOURS = 12
MAX_AGE_DAYS = 30
KEEP = 36
NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
      "m": "http://search.yahoo.com/mrss/"}

# topic -> (pattern, weight). The first matching topic labels the video.
TOPICS: dict[str, tuple[str, int]] = {
    "Kubernetes & cloud native": (r"kubernetes|k8s|helm|argo|gitops|cilium|ebpf|istio|service mesh|cncf|kubecon|"
                                  r"container|docker|platform engineering|backstage|crossplane|karpenter", 3),
    "Observability & SRE": (r"observability|monitor|opentelemetry|otel|prometheus|grafana|loki|tempo|mimir|sre\b|"
                            r"reliability|incident|monitoring|datadog|on-?call|slo", 3),
    "IaC & CI/CD": (r"terraform|opentofu|ansible|pulumi|ci ?/ ?cd|github actions|jenkins|pipeline|devops|devsecops", 3),
    "Cloud": (r"\baws\b|azure|gcp|google cloud|cloud|serverless|lambda|finops|cost", 2),
    "AI & LLMs": (r"\bai\b|llm|gpt|claude|gemini|agent|mcp|rag|inference|gpu|nvidia|mlops|model|openai|anthropic", 2),
    "Security": (r"security|vulnerab|supply chain|sbom|zero trust|iam\b|secrets", 2),
    "System design & Linux": (r"system design|linux|networking|dns|tcp|database|postgres|redis|kafka|distributed", 2),
    "Career & interviews": (r"interview|roadmap|career|resume|job|hiring|beginner|learn", 1),
}
_COMPILED = {k: (re.compile(p, re.I), w) for k, (p, w) in TOPICS.items()}


def _channels() -> list[dict[str, str]]:
    if not CHANNELS_FILE.exists():
        return []
    return [c for c in (yaml.safe_load(CHANNELS_FILE.read_text(encoding="utf-8")) or {}).get("channels") or [] if c.get("id")]


def parse_feed(xml_text: str, channel: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    out = []
    for e in root.findall("a:entry", NS):
        vid = e.findtext("yt:videoId", "", NS)
        link = (e.find("a:link", NS).attrib.get("href", "") if e.find("a:link", NS) is not None else "")
        title = e.findtext("a:title", "", NS)
        if not vid or "/shorts/" in link or "#shorts" in title.lower():
            continue
        stats = e.find("m:group/m:community/m:statistics", NS)
        desc = e.findtext("m:group/m:description", "", NS) or ""
        out.append({"id": vid, "title": title, "channel": channel, "published": e.findtext("a:published", "", NS),
                    "views": int(stats.attrib.get("views", 0)) if stats is not None else 0, "desc": desc[:400]})
    return out


def rank(videos: list[dict[str, Any]], gaps: list[str], now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    gap_rx = [re.compile(rf"\b{re.escape(g)}\b", re.I) for g in gaps if len(g) > 2]
    ranked = []
    for v in videos:
        try:
            age = (now - datetime.fromisoformat(v["published"])).total_seconds() / 86400
        except ValueError:
            continue
        if age > MAX_AGE_DAYS:
            continue
        text = f"{v['title']} {v['desc']}"
        in_title = [(k, w) for k, (rx, w) in _COMPILED.items() if rx.search(v["title"])]
        hits = in_title + [(k, w) for k, (rx, w) in _COMPILED.items() if rx.search(text) and (k, w) not in in_title]
        if not hits:
            continue
        gap = next((g.pattern[2:-2].replace("\\", "") for g in gap_rx if g.search(text)), "")
        score = sum(w for _, w in hits) + (3 if gap else 0) + max(0.0, 3 - age / 7) + math.log10(v["views"] + 10)
        ranked.append({k: v[k] for k in ("id", "title", "channel", "published", "views")}
                      | {"topic": hits[0][0], "gap": gap, "score": round(score, 2)})
    ranked.sort(key=lambda x: -x["score"])
    per_channel: dict[str, int] = {}
    picked = []
    for v in ranked:  # variety: at most 4 per channel
        if per_channel.get(v["channel"], 0) >= 4:
            continue
        per_channel[v["channel"]] = per_channel.get(v["channel"], 0) + 1
        picked.append(v)
    return picked[:KEEP]


def refresh(meta: dict[str, Any], gaps: list[str], force: bool = False) -> int:
    """Update meta['videos'] when due. Returns how many videos are listed."""
    last = meta.get("videos_at")
    if not force and last and datetime.now(timezone.utc) - datetime.fromisoformat(last) < timedelta(hours=REFRESH_HOURS):
        return len(meta.get("videos") or [])

    def one(c: dict[str, str]) -> list[dict[str, Any]]:
        try:
            r = net.get(FEED.format(c["id"]), timeout=(5, 10))
            return parse_feed(r.text, c.get("name") or "") if r.ok else []
        except (net.requests.RequestException, ET.ParseError):
            return []

    with ThreadPoolExecutor(max_workers=8) as pool:
        found = [v for batch in pool.map(one, _channels()) for v in batch]
    if found:
        meta["videos"] = rank(found, gaps)
        meta["videos_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        log.info("videos: %d recent from %d feeds, kept %d", len(found), len(_channels()), len(meta["videos"]))
    return len(meta.get("videos") or [])
