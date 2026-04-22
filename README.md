# AWS SRE Incident Automation

> Production-grade SRE toolkit for AWS — automated incident triage, self-healing scripts, CloudWatch alerting, and EKS observability using Terraform, Python, and Jenkins.

[![Terraform](https://img.shields.io/badge/Terraform-1.6+-7B42BC?logo=terraform)](https://www.terraform.io/)
[![AWS](https://img.shields.io/badge/AWS-EKS%20%7C%20CloudWatch-FF9900?logo=amazonaws)](https://aws.amazon.com/)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python)](https://www.python.org/)
[![Jenkins](https://img.shields.io/badge/Jenkins-CI%2FCD-D24939?logo=jenkins)](https://www.jenkins.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## Overview

This repository implements a battle-tested SRE automation framework for AWS environments. It combines infrastructure provisioning, real-time monitoring, and automated incident response to reduce Mean Time to Detect (MTTD) and Mean Time to Recovery (MTTR) for production workloads.

Key capabilities:

- **EKS cluster provisioning** via modular Terraform with VPC, IAM, and autoscaling
- **Automated incident triage** — Python scripts that query CloudWatch, correlate signals, and generate structured RCA reports
- **Self-healing automation** — Bash and Python handlers that respond to common alert patterns
- **CloudWatch alarm infrastructure** — Terraform-managed alarms with SNS routing
- **Grafana dashboards** — Pre-built JSON dashboards for EKS, RDS, and application metrics
- **Jenkins pipeline** — Multi-stage CI/CD with health gate checks and rollback capability

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          AWS Account                                  │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                      VPC: 10.0.0.0/16                         │   │
│  │                                                                │   │
│  │   ┌──────────────┐    ┌──────────────┐    ┌───────────────┐  │   │
│  │   │ Public Subnet │    │Private Subnet│    │Private Subnet │  │   │
│  │   │ 10.0.1.0/24  │    │ 10.0.10.0/24│    │ 10.0.11.0/24 │  │   │
│  │   │   (NAT GW)   │    │  EKS Nodes  │    │   RDS / DB   │  │   │
│  │   └──────┬───────┘    └──────┬───────┘    └───────────────┘  │   │
│  │          │                   │                                  │   │
│  │   ┌──────▼───────────────────▼──────────────────────────────┐ │   │
│  │   │                   EKS Cluster                            │ │   │
│  │   │   System Pool  │  Application Pool  │  Spot Pool        │ │   │
│  │   └─────────────────────────────────────────────────────────┘ │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                        │
│  ┌──────────────┐   ┌───────────────┐   ┌────────────────────────┐  │
│  │  CloudWatch  │   │  SNS Topics   │   │  HashiCorp Vault       │  │
│  │  Alarms +    │──►│  (PagerDuty / │   │  (Secrets Management)  │  │
│  │  Dashboards  │   │   Slack)      │   └────────────────────────┘  │
│  └──────────────┘   └───────────────┘                                 │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Repository Structure

```
aws-sre-incident-automation/
├── terraform/
│   ├── main.tf                        # Root module — VPC, EKS, monitoring
│   ├── variables.tf
│   ├── outputs.tf
│   └── modules/
│       ├── vpc/                       # VPC, subnets, NAT, route tables
│       ├── eks/                       # EKS cluster, node groups, IAM roles
│       └── monitoring/                # CloudWatch alarms, SNS, dashboards
├── scripts/
│   ├── incident_triage.py             # Automated incident triage and RCA
│   ├── health_check.sh                # Cluster and service health checks
│   └── alert_handler.py              # Self-healing alert response automation
├── jenkins/
│   └── Jenkinsfile                    # Multi-stage CI/CD pipeline
├── monitoring/
│   ├── cloudwatch-alarms.tf           # Alarm definitions (managed separately)
│   └── grafana-dashboard.json         # Pre-built EKS + app metrics dashboard
├── RUNBOOK.md                         # Incident response playbooks
└── README.md
```

---

## Prerequisites

| Tool | Minimum Version |
|------|----------------|
| Terraform | `>= 1.6` |
| AWS CLI | `>= 2.15` |
| Python | `>= 3.11` |
| kubectl | `>= 1.28` |
| Helm | `>= 3.13` |
| Jenkins | `>= 2.440` |

### Required AWS IAM Permissions

The deploying IAM role requires:
- `AmazonEKSClusterPolicy`
- `AmazonVPCFullAccess`
- `CloudWatchFullAccess`
- `AmazonSNSFullAccess`
- `IAMFullAccess` (for EKS role creation)

---

## Quick Start

### 1. Configure AWS Credentials

```bash
aws configure --profile platform-sre
export AWS_PROFILE=platform-sre
export AWS_REGION=us-east-1
```

### 2. Deploy Infrastructure

```bash
cd terraform
terraform init
terraform plan -var-file="environments/prod.tfvars" -out=tfplan
terraform apply tfplan
```

### 3. Configure kubectl for EKS

```bash
aws eks update-kubeconfig \
  --region us-east-1 \
  --name eks-sre-prod \
  --kubeconfig ~/.kube/eks-sre-prod

kubectl get nodes
```

### 4. Install Monitoring Stack

```bash
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update

helm upgrade --install grafana grafana/grafana \
  --namespace monitoring \
  --create-namespace \
  --set persistence.enabled=true \
  --set persistence.storageClassName=gp3

# Import dashboard
kubectl create configmap grafana-dashboard \
  --from-file=monitoring/grafana-dashboard.json \
  -n monitoring
```

### 5. Set Up Incident Triage Scripts

```bash
cd scripts
pip install -r requirements.txt

# Run a triage check
python incident_triage.py \
  --cluster eks-sre-prod \
  --region us-east-1 \
  --time-window 30m
```

---

## Incident Triage Automation

The `incident_triage.py` script automates the first 15 minutes of incident response:

```bash
# Full triage — queries CloudWatch, checks EKS health, generates RCA draft
python scripts/incident_triage.py \
  --cluster eks-sre-prod \
  --namespace production \
  --region us-east-1 \
  --output rca-$(date +%Y%m%d-%H%M%S).md

# Check specific service
python scripts/incident_triage.py \
  --service payments-api \
  --time-window 60m

# Alert handler — responds to SNS-triggered events
python scripts/alert_handler.py \
  --action restart-unhealthy-pods \
  --namespace production \
  --dry-run
```

### What It Checks

| Signal | Source | Action |
|--------|--------|--------|
| Pod crash loops | Kubernetes API | Captures logs, flags for restart |
| Node memory pressure | CloudWatch | Triggers cluster autoscaler scale-out |
| API error rate spike | CloudWatch Metrics | Correlates with recent deployments |
| RDS connection pool | CloudWatch | Checks slow query log, alerts DBA |
| Disk I/O saturation | CloudWatch | Identifies top consumers, pages on-call |

---

## CI/CD Pipeline (Jenkins)

The `Jenkinsfile` implements a multi-stage deployment pipeline with automatic rollback:

```
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│  Build & │   │  Test &  │   │  Deploy  │   │  Health  │   │  Notify  │
│   Scan   ├──►│  Lint    ├──►│  Staging ├──►│  Gate    ├──►│  Team    │
└──────────┘   └──────────┘   └──────────┘   └──────┬───┘   └──────────┘
                                                      │
                                              ┌───────▼──────┐
                                              │  Pass? Deploy │
                                              │  to Production│
                                              │  Fail? Auto   │
                                              │  Rollback     │
                                              └──────────────┘
```

**Pipeline stages:**

1. `Build & Scan` — Docker build, Trivy vulnerability scan
2. `Test & Lint` — Unit tests, Python lint, Terraform validate
3. `Deploy Staging` — Apply to staging EKS namespace
4. `Health Gate` — 5-minute stability check (error rate, pod restarts)
5. `Deploy Production` — Blue/green promotion or rolling update
6. `Post-Deploy Verify` — Synthetic health probe, Slack notification

---

## CloudWatch Alarms

Alarms are Terraform-managed and routed via SNS:

| Alarm | Metric | Threshold | Severity |
|-------|--------|-----------|----------|
| High CPU | `CPUUtilization` | > 85% for 5m | Warning |
| Pod OOM Kill | `pod_oom_kill_total` | > 0 | Critical |
| API 5xx Rate | `5XXError` | > 1% for 3m | Critical |
| RDS Connections | `DatabaseConnections` | > 80% max | Warning |
| Node Not Ready | Kubernetes Events | Any | Critical |
| Disk I/O Wait | `DiskReadOps` | > 1000 IOPS | Warning |

---

## Self-Healing Capabilities

The `alert_handler.py` responds automatically to known failure patterns:

| Trigger | Automated Response |
|---------|-------------------|
| Pod CrashLoopBackOff > 3 restarts | Cordon node, collect logs, restart pod |
| Node memory > 90% | Evict low-priority pods, trigger scale-out |
| Deployment rollout stalled | Auto-rollback to last stable revision |
| Certificate expiry < 7 days | Trigger cert-manager renewal |
| Kafka consumer lag > threshold | Scale consumer deployment replicas |

---

## Observability

### Key SLOs

| Service | Availability SLO | Latency SLO (P99) |
|---------|-----------------|-------------------|
| API Gateway | 99.9% | < 300ms |
| EKS Control Plane | 99.95% | N/A |
| RDS | 99.9% | < 50ms query |
| Kafka | 99.5% | < 100ms produce |

### Grafana Dashboard Panels

- **Cluster Overview** — Node count, pod count, resource utilization
- **Application Health** — Request rate, error rate, latency (RED method)
- **Infrastructure** — EC2 CPU, memory, disk, network per node
- **Deployment Tracker** — Active deployments, rollout status, restart count
- **Alert Timeline** — Historical alert frequency by severity

---

## Security

- **IAM Roles for Service Accounts (IRSA)** — No static AWS credentials in pods
- **EKS Pod Security Standards** — `baseline` profile enforced cluster-wide
- **Secrets via AWS Secrets Manager** — Injected using External Secrets Operator
- **VPC Flow Logs** — Enabled and shipped to CloudWatch Logs
- **CloudTrail** — All API calls logged with 1-year retention
- **Security Groups** — Principle of least privilege, no `0.0.0.0/0` ingress

---

## Contributing

```bash
# Clone and set up pre-commit hooks
git clone https://github.com/<your-org>/aws-sre-incident-automation.git
cd aws-sre-incident-automation
pip install pre-commit
pre-commit install

# Run linters locally
terraform fmt -recursive terraform/
python -m flake8 scripts/
shellcheck scripts/*.sh
```

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

*Built on SRE principles from production AWS environments supporting enterprise financial and data platform workloads.*
