# Karthik — DevOps Engineer (Bangalore, India)

Open to remote and onsite. Contact details intentionally omitted (this file is only used for job scoring).

Experience level: about 8 months full-time DevOps (Feb 2026 to present) plus 6 months HPC sysadmin internship
and a research internship. Graduated B.E. AI/ML in May 2026. Target: DevOps / SRE / Platform / Cloud / MLOps
roles asking for 0-3 years.

## Profile
DevOps Engineer with hands-on experience building observability, incident response, and automation systems for
cloud-native infrastructure on AWS, Azure, and Kubernetes. Background running GPU infrastructure (NVIDIA H200,
AMD MI300X) with Slurm scheduling and fine-tuning open-weight AI models (Nemotron Nano 8B, Magpie TTS).
IEEE Access (Q1) published author on scaling ML workflows with cloud and DevOps technologies.

## Skills
- Cloud & Kubernetes: AWS (EKS, SNS, CloudWatch, S3), Azure (AKS, VNet, Blob Storage), GCP (GKE), Docker, Podman, Kubernetes
- IaC & CI/CD: Terraform, Ansible, GitOps, Argo CD, Helm, GitHub Actions, Jenkins
- Observability & Security: Prometheus, Grafana, OpenTelemetry, Datadog, Loki, Tempo, Mimir, OpenObserve, SonarQube, Trivy
- Systems, Data & AI/ML: Python, Bash, Git, Linux, TCP/IP, DNS, SSL/TLS, SSH, PostgreSQL, MongoDB, Redis, ClickHouse,
  NVIDIA H200, AMD Instinct MI300X, Slurm, LLM fine-tuning and inference, Langfuse
- Compliance: SOC 2, ISO 27001

## Work experience

### DevOps Engineer, InCruiter, Bangalore (Feb 2026 – Present), AI-powered recruitment platform
- Deployed and fine-tuned NVIDIA Nemotron Nano 8B (LLM) and Magpie TTS on AWS EC2 GPU instances, using Langfuse for model observability.
- Production readiness owner for 6 company products on AKS using Datadog; reduced MTTD by 40% through dashboard and alert triage, generating RCA for outages across latency, error rate, and pod health.
- Architected a hub-and-spoke observability stack (Grafana Alloy, Tempo, Loki, Mimir/Prometheus) with Azure Blob Storage telemetry; achieved a 35% collection efficiency gain.
- Automated Kubernetes cost governance for non-production environments via Bash/YAML scheduling, cutting non-prod compute cost by 60% with zero manual intervention.
- Led a Kubernetes cluster migration across Azure subscriptions, cutting infrastructure cost by 50% through spot-instance selection and workload right-sizing with CAST AI and Kubecost.
- Owned SOC 2 and ISO 27001 compliance posture; remediated IAM, log retention, and encryption gaps across Azure and AWS with zero audit findings.
- Engineered session resilience for a high-concurrency interview platform via Redis-backed liveness checks and Argo CD canary deployments; optimized production MongoDB queries and migrated database region, cutting cross-region transfer costs by 15%.

### System Administrator Intern, Robert Bosch Centre (RBCCPS), IISc Bangalore (Aug 2025 – Jan 2026), HPC infrastructure
- Owned reliability of a production bare-metal GPU cluster (NVIDIA H200 and AMD Instinct MI300X, 8 nodes) for LLM, robotics, and RL workloads using Slurm; maintained 98% availability with zero data loss.
- Built GPU monitoring (Prometheus/Grafana) for VRAM, queue depth, and thermals, cutting queue wait times by 35% and improving training throughput by 40%.
- Automated Linux onboarding and Slurm provisioning (Bash) for 70 researchers, cutting onboarding time 60% and support escalations 50%.

### Research Intern, Confluence of Computing Lab, BITS Pilani, Goa (May 2025 – Jul 2025)
- Built an oral cancer detection pipeline using LIME and Grad-CAM for model interpretability; automated medical imaging and ML pipelines with MLflow and DVC, delivering structured JSON/PostgreSQL datasets and improving label quality by 18%.

## Projects
- Self-Healing DevSecOps Pipeline, Automated Rollback System (2026): event-driven microservice on AWS EKS with Splunk-based anomaly detection triggering automated rollback on failed deployments; security gates on every commit.
- Event-Driven Microservices on AWS EKS (2026): services deployed on AWS EKS via Terraform and CI/CD with full observability stack, 99% reliability under load.
- GitOps-Based Production Kubernetes Platform (2025): Argo CD GitOps pipeline with canary traffic shifting, automated rollback, and Prometheus/Grafana monitoring.
- Scalable API Health Monitoring on AWS EKS (2025): containerized monitoring on multi-AZ EKS with Terraform-managed auto-scaling, tracking 10,000 endpoints at 99.9% uptime.

## Awards & certifications
- IEEE Access (Q1) author: peer-reviewed publication on scaling ML workflows with cloud and DevOps technologies (2025)
- 1st place, Fusion Techathon 3.0 (national hackathon), AI-driven disaster management system, 1st of 200 teams
- Demonstrated MLOps-based real-time parking space recognition at Infosys Tech Event 2024, Bangalore
- AWS Cloud Practitioner Essentials; Ansible Automation (DO007); Red Hat OpenShift Foundations

## Education
- B.E. Artificial Intelligence and Machine Learning, Alva's Institute of Engineering and Technology, Mangalore (2022 – 2026), CGPA 8.0/10
