"""Resume <-> JD skill matching and a deterministic 0-10 fit score.

This is the always-available scorer: it runs on every job, feeds the "Apply next" ranking, picks which
jobs are worth an LLM call (Phase 3), and is the fallback when every LLM quota is spent.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from .config import PROFILE_DIR

# canonical skill -> regex alternatives. Weight 2 = core DevOps skill, 1 = supporting.
SKILLS: dict[str, tuple[int, str]] = {
    "kubernetes": (2, r"kubernetes|k8s|\beks\b|\baks\b|\bgke\b|openshift"),
    "aks": (1, r"\baks\b|azure kubernetes"),
    "eks": (1, r"\beks\b|elastic kubernetes"),
    "docker": (2, r"docker|containers?\b|containeri[sz]ation|podman"),
    "helm": (1, r"\bhelm\b"),
    "terraform": (2, r"terraform|\bhcl\b|opentofu"),
    "ansible": (1, r"ansible"),
    "iac": (1, r"infrastructure[- ]as[- ]code|\biac\b|cloudformation|pulumi|bicep"),
    "argocd": (2, r"argo ?cd|argo\b|gitops|flux ?cd"),
    "ci/cd": (2, r"ci ?/ ?cd|continuous (integration|delivery|deployment)|pipelines?"),
    "github actions": (1, r"github actions"),
    "jenkins": (1, r"jenkins"),
    "gitlab ci": (1, r"gitlab"),
    "aws": (2, r"\baws\b|amazon web services|\bec2\b|\bs3\b|cloudwatch|\biam\b"),
    "azure": (2, r"azure"),
    "gcp": (1, r"\bgcp\b|google cloud"),
    "prometheus": (2, r"prometheus"),
    "grafana": (2, r"grafana|loki|tempo|mimir|alloy"),
    "opentelemetry": (1, r"opentelemetry|\botel\b|tracing"),
    "datadog": (1, r"datadog"),
    "observability": (2, r"observability|monitoring|alerting|\bslos?\b|\bslis?\b"),
    "incident response": (1, r"incident|on-?call|\brca\b|root cause|postmortem"),
    "linux": (2, r"linux|unix|\brhel\b|ubuntu|centos"),
    "bash": (1, r"\bbash\b|shell script"),
    "python": (2, r"python"),
    "networking": (1, r"tcp/?ip|\bdns\b|networking|load balanc|\bvpc\b|\bvnet\b|ssl|tls"),
    "security": (1, r"devsecops|sonarqube|trivy|snyk|vulnerab|sast|dast|secrets? management|vault"),
    "compliance": (1, r"soc ?2|iso ?27001|compliance|audit"),
    "postgresql": (1, r"postgres"),
    "mongodb": (1, r"mongo"),
    "redis": (1, r"redis"),
    "gpu": (1, r"\bgpu|nvidia|cuda|\bhpc\b|slurm"),
    "mlops": (1, r"mlops|mlflow|kubeflow|model serving|inference|\bllm"),
    "cost optimization": (1, r"finops|cost optimi[sz]|kubecost|right-?siz|spot instance"),
    "git": (1, r"\bgit\b"),
    # Skills Karthik doesn't list: they only ever appear as "missing".
    "go": (1, r"\bgolang\b|\bgo\b(?= (?:language|programming|developer|experience))"),
    "java": (1, r"\bjava\b"),
    "kafka": (1, r"kafka"),
    "service mesh": (1, r"istio|linkerd|service mesh|envoy"),
    "elk": (1, r"\belk\b|elasticsearch|logstash|kibana|opensearch"),
    "splunk": (1, r"splunk"),
    "nginx": (1, r"nginx|haproxy"),
    "windows": (1, r"windows server|powershell|active directory"),
    "networking hardware": (1, r"cisco ios|ccna|ccnp|routers?|switch(es|ing)\b|firewalls?"),
}
_COMPILED = {k: re.compile(p, re.I) for k, (_, p) in SKILLS.items()}


@lru_cache(maxsize=1)
def resume_skills(path: Path = PROFILE_DIR / "resume.md") -> frozenset[str]:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    return frozenset(k for k, rx in _COMPILED.items() if rx.search(text))


def jd_skills(text: str) -> set[str]:
    return {k for k, rx in _COMPILED.items() if rx.search(text or "")}


def keyword_fit(title: str, description: str, min_years: int | None) -> dict:
    """Return {score 0-10, matched, missing, level_fit} without any API call."""
    have = resume_skills()
    wanted = jd_skills(f"{title}\n{description}")
    matched = sorted(wanted & have, key=lambda k: -SKILLS[k][0])
    missing = sorted(wanted - have, key=lambda k: -SKILLS[k][0])
    w_match = sum(SKILLS[k][0] for k in matched)
    w_all = sum(SKILLS[k][0] for k in wanted) or 1
    coverage = w_match / w_all                 # how much of what they ask for you have
    depth = min(1.0, w_match / 14)             # thin JDs shouldn't score 10 from two matches
    score = 10 * (0.6 * coverage + 0.4 * depth)
    if not description.strip():
        score = min(score, 5.0)                # title-only listings: unknown fit
    if min_years is None:
        level = "ok"
    elif min_years <= 2:
        level = "ok"
    elif min_years == 3:
        level, score = "stretch", score - 0.5
    else:
        level, score = "too_senior", score - 3
    return {
        "method": "keywords",
        "score": round(max(0.0, min(10.0, score)), 1),
        "matched_skills": matched,
        "missing_skills": missing,
        "level_fit": level,
    }
