"""Truthful resume tailoring for ATS keyword match.

What it does for one job, using profile/resume.yaml and the keywords found in the job description:
  1. Reorders: skills inside each line, bullets inside each job, and projects, most relevant to the JD first.
  2. Picks the profile paragraph variant for the job's family (SRE / platform / cloud / MLOps / DevSecOps).
  3. Adds a JD keyword to a skills line ONLY when the resume already proves it (EVIDENCE below): e.g. the JD asks
     for "Infrastructure as Code" and the resume has Terraform. Keywords with no evidence are reported as missing
     and never added. No bullet is rewritten and no number changes, so nothing can be invented.
  4. Scores keyword match before/after (share of the JD's keywords that appear in the resume text), which is
     what online ATS checkers measure.

The dashboard prints the result in the same layout as the PDF resume (Lato, A4, one column, real text).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .config import PROFILE_DIR

RESUME_FILE = PROFILE_DIR / "resume.yaml"
MAX_ADD_PER_LINE = 4
MAX_ADD_TOTAL = 9

# Skill line indexes in resume.yaml: 0 Cloud & Kubernetes, 1 IaC & CI/CD, 2 Observability & Security, 3 Systems...
CLOUD, CICD, OBS, SYS = 0, 1, 2, 3


@dataclass(frozen=True)
class Term:
    name: str            # what gets added to the skills line (the JD's usual wording)
    line: int            # which skills line it belongs to
    jd: re.Pattern[str]  # how the JD (and the resume text, for "already there") says it
    evidence: re.Pattern[str] | None  # resume text that proves you have it; None = can only be matched literally


def _t(name: str, line: int, jd: str, evidence: str | None = None) -> Term:
    return Term(name, line, re.compile(jd, re.I), re.compile(evidence, re.I) if evidence else None)


# Order matters only for display. Keep jd patterns specific: a false hit costs a wrong keyword in the PDF.
VOCAB: tuple[Term, ...] = (
    # ---- Cloud & Kubernetes
    _t("Kubernetes", CLOUD, r"kubernetes|\bk8s\b"),
    _t("Docker", CLOUD, r"\bdocker\b"),
    _t("Containerization", CLOUD, r"containeri[sz]ation|containeri[sz]ed", r"docker|podman"),
    _t("Container Orchestration", CLOUD, r"container orchestration", r"kubernetes"),
    _t("AWS", CLOUD, r"\baws\b"),
    _t("Amazon Web Services", CLOUD, r"amazon web services", r"\baws\b"),
    _t("Azure", CLOUD, r"\bazure\b"),
    _t("Microsoft Azure", CLOUD, r"microsoft azure", r"\bazure\b"),
    _t("GCP", CLOUD, r"\bgcp\b"),
    _t("Google Cloud", CLOUD, r"google cloud", r"\bgcp\b|\bgke\b"),
    _t("EKS", CLOUD, r"\beks\b"),
    _t("AKS", CLOUD, r"\baks\b"),
    _t("GKE", CLOUD, r"\bgke\b"),
    _t("EC2", CLOUD, r"\bec2\b", r"\bec2\b"),
    _t("S3", CLOUD, r"\bs3\b"),
    _t("CloudWatch", CLOUD, r"cloudwatch"),
    _t("IAM", CLOUD, r"\biam\b", r"\biam\b"),
    _t("Multi-Cloud", CLOUD, r"multi-?cloud", r"\baws\b.*\bazure\b|\bazure\b.*\baws\b"),
    _t("Microservices", CLOUD, r"micro-?services?", r"microservice"),
    _t("Auto Scaling", CLOUD, r"auto-?scal|autoscal|\bhpa\b", r"auto-?scaling"),
    _t("High Availability", CLOUD, r"high[- ]availability|highly available", r"high availability|multi-az"),
    _t("Spot Instances", CLOUD, r"spot instances?", r"spot-instance"),
    _t("Cost Optimization", CLOUD, r"cost optimi[sz]|finops|cloud cost", r"kubecost|cast ai|cost governance"),
    _t("Capacity Planning", CLOUD, r"capacity planning", r"right-sizing"),
    _t("OpenShift", CLOUD, r"openshift"),
    _t("Podman", CLOUD, r"podman"),
    # ---- IaC & CI/CD
    _t("Terraform", CICD, r"terraform"),
    _t("Ansible", CICD, r"ansible"),
    _t("Infrastructure as Code", CICD, r"infrastructure[- ]as[- ]code", r"terraform|ansible"),
    _t("IaC", CICD, r"\biac\b"),
    _t("Configuration Management", CICD, r"configuration management", r"ansible"),
    _t("CI/CD", CICD, r"\bci ?/ ?cd\b"),
    _t("CI/CD Pipelines", CICD, r"(ci ?/ ?cd|build|deployment|release) pipelines?", r"github actions|jenkins"),
    _t("Continuous Integration", CICD, r"continuous integration", r"github actions|jenkins"),
    _t("Continuous Delivery", CICD, r"continuous (delivery|deployment)", r"argo ?cd|github actions|jenkins"),
    _t("GitOps", CICD, r"gitops"),
    _t("Argo CD", CICD, r"argo ?cd"),
    _t("Helm", CICD, r"\bhelm\b"),
    _t("GitHub Actions", CICD, r"github actions"),
    _t("Jenkins", CICD, r"jenkins"),
    _t("Canary Deployments", CICD, r"canary", r"canary"),
    _t("Automated Rollback", CICD, r"roll-?backs?", r"rollback"),
    _t("YAML", CICD, r"\byaml\b"),
    _t("Git", SYS, r"\bgit\b"),
    # ---- Observability & Security
    _t("Observability", OBS, r"observability"),
    _t("Monitoring", OBS, r"monitoring"),
    _t("Alerting", OBS, r"alerting|alerts?\b"),
    _t("Logging", OBS, r"\blogging\b|log management|log aggregation|centrali[sz]ed logs?", r"\bloki\b|log retention"),
    _t("Distributed Tracing", OBS, r"tracing", r"\btempo\b|opentelemetry"),
    _t("Prometheus", OBS, r"prometheus"),
    _t("Grafana", OBS, r"grafana"),
    _t("Datadog", OBS, r"datadog"),
    _t("OpenTelemetry", OBS, r"opentelemetry|\botel\b"),
    _t("APM", OBS, r"\bapm\b|application performance monitoring", r"datadog"),
    _t("Dashboards", OBS, r"dashboards?"),
    _t("Incident Response", OBS, r"incident response"),
    _t("Incident Management", OBS, r"incident management|incident handling", r"incident response|\brca\b"),
    _t("Root Cause Analysis", OBS, r"root cause|\brca\b", r"\brca\b"),
    _t("Troubleshooting", SYS, r"troubleshoot", r"\brca\b|triage"),
    _t("Splunk", OBS, r"splunk"),
    _t("DevSecOps", OBS, r"devsecops"),
    _t("Vulnerability Scanning", OBS, r"vulnerabilit(y|ies) (scan|management|assessment)|image scanning|container scanning",
       r"trivy"),
    _t("SAST", OBS, r"\bsast\b|static (application )?security testing|static code analysis", r"sonarqube"),
    _t("SonarQube", OBS, r"sonarqube"),
    _t("Trivy", OBS, r"trivy"),
    _t("SOC 2", OBS, r"soc ?2"),
    _t("ISO 27001", OBS, r"iso ?27001"),
    _t("Compliance", OBS, r"compliance"),
    _t("Encryption", OBS, r"encryption"),
    # ---- Systems, Data & AI/ML
    _t("Linux", SYS, r"\blinux\b"),
    _t("Linux Administration", SYS, r"(linux|unix) (system )?admin|system administration|sysadmin",
       r"system administrator|linux onboarding"),
    _t("Python", SYS, r"python"),
    _t("Bash", SYS, r"\bbash\b"),
    _t("Shell Scripting", SYS, r"shell script|bash script|scripting", r"\bbash\b"),
    _t("Automation", SYS, r"automation"),
    _t("Networking", SYS, r"networking|network protocols", r"tcp/ip|\bdns\b|vnet"),
    _t("TCP/IP", SYS, r"tcp ?/ ?ip"),
    _t("DNS", SYS, r"\bdns\b"),
    _t("SSL/TLS", SYS, r"\bssl\b|\btls\b"),
    _t("SSH", SYS, r"\bssh\b"),
    _t("SQL", SYS, r"\bsql\b", r"postgresql"),
    _t("NoSQL", SYS, r"\bnosql\b", r"mongodb|redis"),
    _t("PostgreSQL", SYS, r"postgres"),
    _t("MongoDB", SYS, r"mongo"),
    _t("Redis", SYS, r"\bredis\b"),
    _t("ClickHouse", SYS, r"clickhouse"),
    _t("GPU", SYS, r"\bgpus?\b"),
    _t("NVIDIA", SYS, r"nvidia"),
    _t("HPC", SYS, r"\bhpc\b|high[- ]performance computing"),
    _t("Slurm", SYS, r"slurm"),
    _t("MLOps", SYS, r"mlops|ml ops"),
    _t("LLMOps", SYS, r"llmops|llm ops", r"langfuse"),
    _t("LLM", SYS, r"\bllms?\b|large language model"),
    _t("Model Inference", SYS, r"inference|model serving", r"inference"),
    _t("MLflow", SYS, r"mlflow"),
    _t("Machine Learning", SYS, r"machine learning", r"\bml\b|mlflow"),
    # ---- Never added (no evidence on the resume): reported as gaps
    _t("Go", SYS, r"\bgolang\b|\bgo\b(?=\s*(?:lang|programming|developer|,|/|\)|and\b|or\b))"),
    _t("Java", SYS, r"\bjava\b"),
    _t("Kafka", SYS, r"kafka"),
    _t("Elasticsearch / ELK", OBS, r"\belk\b|elasticsearch|logstash|kibana|opensearch"),
    _t("Istio / Service Mesh", CLOUD, r"istio|linkerd|service mesh"),
    _t("Nginx", SYS, r"nginx"),
    _t("Load Balancing", CLOUD, r"load[- ]balanc"),
    _t("VPC", CLOUD, r"\bvpc\b"),
    _t("ECS", CLOUD, r"\becs\b|fargate"),
    _t("AWS Lambda", CLOUD, r"aws lambda|lambda functions?|serverless"),
    _t("RDS", CLOUD, r"\brds\b"),
    _t("CloudFormation", CICD, r"cloudformation"),
    _t("Pulumi", CICD, r"pulumi"),
    _t("Puppet / Chef", CICD, r"\bpuppet\b|\bchef\b"),
    _t("GitLab CI", CICD, r"gitlab"),
    _t("Azure DevOps", CICD, r"azure devops|\bado\b"),
    _t("Packer", CICD, r"\bpacker\b"),
    _t("Vault / Secrets Management", OBS, r"\bvault\b|secrets? management"),
    _t("SLOs / SLIs", OBS, r"\bslos?\b|\bslis?\b|error budget"),
    _t("On-call", OBS, r"on-?call"),
    _t("New Relic / Dynatrace", OBS, r"new relic|dynatrace|appdynamics"),
    _t("Nagios / Zabbix", OBS, r"nagios|zabbix"),
    _t("PagerDuty", OBS, r"pagerduty|opsgenie"),
    _t("Windows / PowerShell", SYS, r"powershell|windows server"),
    _t("Kubeflow / Ray", SYS, r"kubeflow|\bray\b"),
    _t("CUDA", SYS, r"\bcuda\b"),
    _t("Airflow / Spark", SYS, r"airflow|\bspark\b"),
)

_FAMILIES = (
    ("mlops", r"mlops|ml ops|llmops|ml (infra|platform)|ai (infra|platform)|machine learning (platform|infra)|gpu|hpc"),
    ("devsecops", r"devsecops|secops|security"),
    ("sre", r"site reliability|\bsre\b|reliability|production engineer|observability"),
    ("platform", r"platform|kubernetes|k8s"),
    ("cloud", r"cloud|aws|azure|gcp"),
)


@lru_cache(maxsize=1)
def load_base(path: Path = RESUME_FILE) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _plain(text: str) -> str:
    return (text or "").replace("**", "")


def resume_text(base: dict[str, Any], profile_key: str = "default", extra: dict[int, list[str]] | None = None) -> str:
    """Everything an ATS reads, as one string (contact details excluded)."""
    parts = [(base.get("profile") or {}).get(profile_key) or (base.get("profile") or {}).get("default", "")]
    for i, line in enumerate(base.get("skills") or []):
        parts.append(line.get("label", ""))
        parts.extend(line.get("items") or [])
        parts.extend((extra or {}).get(i, []))
    for job in base.get("experience") or []:
        parts += [job.get("title", ""), job.get("company", ""), job.get("subtitle", ""), *job.get("bullets", [])]
    for p in base.get("projects") or []:
        parts += [p.get("name", ""), p.get("text", "")]
    parts += list(base.get("awards") or [])
    for e in base.get("education") or []:
        parts += [e.get("school", ""), e.get("degree", "")]
    return _plain("\n".join(str(p) for p in parts))


def jd_terms(text: str) -> list[str]:
    """Keywords this JD asks for (names from VOCAB), in VOCAB order."""
    return [t.name for t in VOCAB if t.jd.search(text or "")]


def family(title: str) -> str:
    for name, rx in _FAMILIES:
        if re.search(rx, title or "", re.I):
            return name
    return "default"


def _relevance(text: str, terms: list[Term]) -> int:
    plain = _plain(text)
    return sum(1 for t in terms if t.jd.search(plain) or (t.evidence and t.evidence.search(plain)))


def _order(items: list[str], terms: list[Term]) -> list[int] | None:
    """Indexes, most relevant first; ties keep your original order. None = original order (keeps payload small)."""
    scored = [(-_relevance(x, terms), i) for i, x in enumerate(items)]
    order = [i for _, i in sorted(scored)]
    return None if order == list(range(len(items))) else order


def _best_highlight(base: dict[str, Any], words: set[str]) -> int:
    best, best_i = 0, 0
    for i, h in enumerate(base.get("highlights") or []):
        n = len(words & {str(t).lower() for t in h.get("tags") or []})
        if n > best:
            best, best_i = n, i
    return best_i


def plan(title: str, terms_found: list[str], base: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compact tailoring plan for the dashboard (indexes into resume.yaml, plus additions and scores)."""
    base = base if base is not None else load_base()
    if not base:
        return {}
    by_name = {t.name: t for t in VOCAB}
    terms = [by_name[n] for n in dict.fromkeys(terms_found) if n in by_name]
    original = resume_text(base)
    present, add, missing = [], {}, []
    added = 0
    for t in terms:
        if t.jd.search(original):
            present.append(t.name)
        elif t.evidence and t.evidence.search(original) and added < MAX_ADD_TOTAL \
                and len(add.get(t.line, [])) < MAX_ADD_PER_LINE:
            add.setdefault(t.line, []).append(t.name)
            added += 1
        else:
            missing.append(t.name)
    fam = family(title)
    if fam not in (base.get("profile") or {}):
        fam = "default"
    tailored = resume_text(base, fam, add)
    after_hits = sum(1 for t in terms if t.jd.search(tailored))
    n = max(1, len(terms))
    skills = base.get("skills") or []
    words = {w.lower() for w in re.findall(r"[a-z0-9/+]+", f"{title} {' '.join(terms_found)}", re.I)}
    return {
        "fam": fam,
        "skills": [_order(line.get("items") or [], terms) for line in skills],
        "add": {str(k): v for k, v in add.items()},
        "exp": [_order(job.get("bullets") or [], terms) for job in base.get("experience") or []],
        "proj": _order([f"{p.get('name', '')} {p.get('text', '')}" for p in base.get("projects") or []], terms),
        "have": (present + [x for v in add.values() for x in v])[:10],
        "added": [x for v in add.values() for x in v],
        "missing": missing,
        "before": round(100 * len(present) / n) if terms else None,
        "after": round(100 * after_hits / n) if terms else None,
        "hl": _best_highlight(base, words),
    }


def public_base(base: dict[str, Any] | None = None) -> dict[str, Any]:
    """The resume structure the dashboard prints from (goes only into the encrypted payload)."""
    base = base if base is not None else load_base()
    keep = ("name", "contact", "intro", "profile", "skills", "experience", "projects", "awards", "education", "highlights")
    return {k: base[k] for k in keep if k in base}
