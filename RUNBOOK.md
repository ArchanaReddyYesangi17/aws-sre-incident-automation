# SRE Incident Response Runbook

> Operational playbooks for common production incidents on the AWS EKS platform.  
> Last reviewed: 2026-04  
> Owner: Platform / SRE Team

---

## Table of Contents

1. [Incident Severity Levels](#incident-severity-levels)
2. [First Response Checklist](#first-response-checklist)
3. [Playbook: High Pod Restart Rate](#playbook-high-pod-restart-rate)
4. [Playbook: Node Not Ready](#playbook-node-not-ready)
5. [Playbook: API Error Rate Spike](#playbook-api-error-rate-spike)
6. [Playbook: RDS Connection Exhaustion](#playbook-rds-connection-exhaustion)
7. [Playbook: Disk I/O Saturation](#playbook-disk-io-saturation)
8. [Playbook: Kafka Consumer Lag](#playbook-kafka-consumer-lag)
9. [Post-Incident Review Template](#post-incident-review-template)
10. [Escalation Contacts](#escalation-contacts)

---

## Incident Severity Levels

| Severity | Definition | Response SLA | Examples |
|----------|------------|-------------|---------|
| **SEV-1** | Complete service outage, revenue impact | 5 min | API down, all pods crashed, DB unreachable |
| **SEV-2** | Significant degradation, partial outage | 15 min | Error rate > 5%, latency > 2x SLO |
| **SEV-3** | Minor degradation, SLO impacted but service functional | 1 hour | Single node failure, non-critical alert |
| **SEV-4** | No user impact, potential risk | Next business day | Certificate approaching expiry, disk at 70% |

---

## First Response Checklist

Run this within the first 5 minutes of any SEV-1 or SEV-2:

```bash
# 1. Confirm cluster connectivity
kubectl get nodes

# 2. Check overall pod health
kubectl get pods -A --field-selector=status.phase!=Running

# 3. Run automated triage
python scripts/incident_triage.py \
  --cluster eks-sre-prod \
  --region us-east-1 \
  --time-window 30m \
  --output /tmp/triage-$(date +%H%M%S).md

# 4. Check recent deployments (common root cause)
kubectl rollout history deployment -A | tail -20

# 5. Check CloudWatch for anomalies
aws cloudwatch get-metric-statistics \
  --namespace AWS/EKS \
  --metric-name cluster_failed_node_count \
  --start-time $(date -u -d '30 minutes ago' +%FT%TZ) \
  --end-time $(date -u +%FT%TZ) \
  --period 60 \
  --statistics Maximum
```

---

## Playbook: High Pod Restart Rate

**Trigger:** `pod_oom_kill_total > 0` or CrashLoopBackOff alert  
**Severity:** SEV-2 (single namespace) / SEV-1 (cluster-wide)

### Diagnosis

```bash
# Identify crashing pods
kubectl get pods -A | grep -E "CrashLoop|Error|OOMKilled"

# Check restart count
kubectl get pods -n <namespace> --sort-by='.status.containerStatuses[0].restartCount'

# Get last crash logs
kubectl logs <pod-name> -n <namespace> --previous --tail=100

# Check resource limits vs actual usage
kubectl top pods -n <namespace>
kubectl describe pod <pod-name> -n <namespace> | grep -A5 Limits
```

### Resolution

```bash
# Option A: OOMKilled — increase memory limit temporarily
kubectl patch deployment <deployment-name> -n <namespace> \
  --patch '{"spec":{"template":{"spec":{"containers":[{"name":"<container>","resources":{"limits":{"memory":"1Gi"}}}]}}}}'

# Option B: Application crash — rollback to last stable revision
kubectl rollout undo deployment/<deployment-name> -n <namespace>
kubectl rollout status deployment/<deployment-name> -n <namespace>

# Option C: Automated restart (use alert_handler.py)
python scripts/alert_handler.py \
  --action restart-unhealthy-pods \
  --namespace <namespace>
```

### Verification

```bash
kubectl get pods -n <namespace> -w   # Watch pod status stabilize
kubectl top pods -n <namespace>      # Confirm resource usage normalizes
```

---

## Playbook: Node Not Ready

**Trigger:** `kube_node_status_condition{condition="Ready",status="true"} == 0`  
**Severity:** SEV-2

### Diagnosis

```bash
# Identify affected nodes
kubectl get nodes | grep -v Ready

# Describe node for events
kubectl describe node <node-name>

# Check node conditions
kubectl get node <node-name> -o jsonpath='{.status.conditions[*]}' | jq .

# Check system pods on affected node
kubectl get pods -A --field-selector spec.nodeName=<node-name>
```

### Resolution

```bash
# Cordon node — prevent new scheduling
kubectl cordon <node-name>

# Drain workloads safely
kubectl drain <node-name> \
  --ignore-daemonsets \
  --delete-emptydir-data \
  --grace-period=60 \
  --timeout=5m

# Terminate and replace via Auto Scaling Group
aws ec2 terminate-instances --instance-ids <instance-id>

# ASG will replace the node automatically
# Monitor replacement
watch kubectl get nodes
```

---

## Playbook: API Error Rate Spike

**Trigger:** `5XXError > 1%` sustained for 3 minutes  
**Severity:** SEV-1 (> 5%) / SEV-2 (1-5%)

### Diagnosis

```bash
# Check ingress error rate
kubectl logs -n ingress-nginx -l app.kubernetes.io/name=ingress-nginx --tail=200 \
  | grep " 5[0-9][0-9] "

# Identify failing upstream pods
kubectl get endpoints -n <namespace> <service-name> -o yaml

# Check deployment rollout — was there a recent change?
kubectl rollout history deployment/<name> -n <namespace>

# Query CloudWatch for correlated signals
aws cloudwatch get-metric-data --cli-input-json file://scripts/metric-query.json
```

### Resolution

```bash
# If caused by recent deployment — immediate rollback
kubectl rollout undo deployment/<deployment-name> -n <namespace>
kubectl rollout status deployment/<deployment-name> -n <namespace> --timeout=5m

# If pods are healthy but overloaded — scale up
kubectl scale deployment/<deployment-name> -n <namespace> --replicas=10

# Verify error rate drops (check CloudWatch or Grafana)
```

---

## Playbook: RDS Connection Exhaustion

**Trigger:** `DatabaseConnections > 80% of max_connections`  
**Severity:** SEV-2

### Diagnosis

```bash
# Check current connection count
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name DatabaseConnections \
  --dimensions Name=DBInstanceIdentifier,Value=<db-identifier> \
  --start-time $(date -u -d '15 minutes ago' +%FT%TZ) \
  --end-time $(date -u +%FT%TZ) \
  --period 60 \
  --statistics Maximum

# Connect to RDS and check active connections
psql -h <rds-endpoint> -U <admin-user> -d postgres \
  -c "SELECT count(*), state, wait_event_type, wait_event FROM pg_stat_activity GROUP BY state, wait_event_type, wait_event ORDER BY count DESC;"

# Identify top connection consumers
psql -h <rds-endpoint> -U <admin-user> -d postgres \
  -c "SELECT application_name, count(*) FROM pg_stat_activity GROUP BY application_name ORDER BY count DESC;"
```

### Resolution

```bash
# Terminate idle connections older than 5 minutes
psql -h <rds-endpoint> -U <admin-user> -d postgres \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state = 'idle' AND query_start < NOW() - INTERVAL '5 minutes';"

# Scale down connection-heavy deployments temporarily
kubectl scale deployment/<service> -n <namespace> --replicas=2

# Long-term: Enable PgBouncer connection pooling
# (see terraform/modules/monitoring for PgBouncer deployment)
```

---

## Playbook: Disk I/O Saturation

**Trigger:** `node_disk_io_time_seconds_total rate > 0.9`  
**Severity:** SEV-3 (single node) / SEV-2 (multiple nodes)

### Diagnosis

```bash
# Identify high I/O pods on node
kubectl top pods -A --sort-by=memory | head -20

# Check I/O per process via node shell (requires privileged access)
kubectl debug node/<node-name> -it --image=busybox -- sh
# Inside node shell:
# iostat -x 1 5

# Check PVC usage
kubectl get pvc -A
kubectl exec -n <namespace> <pod-name> -- df -h
```

### Resolution

```bash
# Evict high-I/O pods from saturated node
kubectl cordon <node-name>
kubectl drain <node-name> --ignore-daemonsets --delete-emptydir-data

# For PVC-heavy workloads — resize EBS volume
aws ec2 modify-volume \
  --volume-id <volume-id> \
  --size <new-size-gb>
```

---

## Playbook: Kafka Consumer Lag

**Trigger:** `kafka_consumer_group_lag > 10000` messages  
**Severity:** SEV-3 (non-critical topics) / SEV-2 (critical pipeline topics)

### Diagnosis

```bash
# Check lag across all consumer groups
kubectl exec -n kafka kafka-0 -- \
  kafka-consumer-groups.sh \
  --bootstrap-server localhost:9092 \
  --describe --all-groups

# Identify slowest consumers
python scripts/incident_triage.py \
  --check kafka-lag \
  --cluster eks-sre-prod
```

### Resolution

```bash
# Scale consumer deployment replicas
kubectl scale deployment/<consumer-deployment> \
  -n <namespace> \
  --replicas=<current+2>

# Monitor lag reduction
watch kubectl exec -n kafka kafka-0 -- \
  kafka-consumer-groups.sh \
  --bootstrap-server localhost:9092 \
  --describe \
  --group <consumer-group>
```

---

## Post-Incident Review Template

Use this template within 24 hours of resolving a SEV-1 or SEV-2:

```markdown
# Post-Incident Review — [Incident Title]

**Date:** YYYY-MM-DD  
**Severity:** SEV-X  
**Duration:** HH:MM  
**Services Affected:** [list]  
**Incident Commander:** [name]

## Timeline

| Time (UTC) | Event |
|------------|-------|
| HH:MM | Alert fired — [alert name] |
| HH:MM | On-call acknowledged |
| HH:MM | Root cause identified |
| HH:MM | Mitigation applied |
| HH:MM | Service restored |

## Root Cause

[1-3 sentence technical description of the root cause]

## Contributing Factors

- [factor 1]
- [factor 2]

## What Went Well

- [observation 1]

## What Needs Improvement

- [observation 1]

## Action Items

| Action | Owner | Due Date | Priority |
|--------|-------|----------|---------|
| [specific fix] | [team] | YYYY-MM-DD | High |
| [add alert/runbook] | [team] | YYYY-MM-DD | Medium |
```

---

## Escalation Contacts

| Role | Channel | When to Escalate |
|------|---------|-----------------|
| Platform On-Call | PagerDuty — `platform-sre` | Any SEV-1, SEV-2 unresolved > 15 min |
| Database Team | `#db-oncall` Slack | RDS/PostgreSQL incidents |
| Security Team | `#security-incidents` Slack | Suspected breach or IAM anomaly |
| Cloud Provider | AWS Support (Business) | AWS service incident confirmed |
| Engineering Lead | Direct message | Customer-facing impact > 30 min |

---

*This runbook is a living document. Update it after each incident with newly discovered resolution steps.*
