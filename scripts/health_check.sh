#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────
# SRE Cluster & Service Health Check Script
# Usage: ./health_check.sh [--cluster <name>] [--region <region>] [--namespace <ns>]
# ─────────────────────────────────────────────────────────────

CLUSTER=""
REGION="us-east-1"
NAMESPACE="production"
FAIL_FAST=false
EXIT_CODE=0

log()     { echo "[$(date '+%H:%M:%S')] INFO  $*"; }
warn()    { echo "[$(date '+%H:%M:%S')] WARN  $*" >&2; }
error()   { echo "[$(date '+%H:%M:%S')] ERROR $*" >&2; EXIT_CODE=1; }
section() { echo; echo "══════════════════════════════════════════"; echo "  $*"; echo "══════════════════════════════════════════"; }

usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  --cluster    EKS cluster name
  --region     AWS region (default: us-east-1)
  --namespace  Kubernetes namespace to check (default: production)
  --fail-fast  Exit on first failure
  -h, --help   Show help
EOF
  exit 0
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --cluster)   CLUSTER="$2";    shift 2 ;;
      --region)    REGION="$2";     shift 2 ;;
      --namespace) NAMESPACE="$2";  shift 2 ;;
      --fail-fast) FAIL_FAST=true;  shift ;;
      -h|--help)   usage ;;
      *) echo "Unknown arg: $1"; exit 1 ;;
    esac
  done
}

check_tools() {
  section "Prerequisite Check"
  for tool in kubectl aws curl jq; do
    if command -v "$tool" &>/dev/null; then
      log "  ✅  $tool found"
    else
      error "  ❌  $tool not found — install it and retry"
      [[ "$FAIL_FAST" == "true" ]] && exit 1
    fi
  done
}

check_aws_connectivity() {
  section "AWS Connectivity"
  if aws sts get-caller-identity --region "$REGION" &>/dev/null; then
    ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
    log "✅  AWS authenticated — Account: $ACCOUNT | Region: $REGION"
  else
    error "❌  AWS authentication failed"
    [[ "$FAIL_FAST" == "true" ]] && exit 1
  fi

  if [[ -n "$CLUSTER" ]]; then
    log "Refreshing kubeconfig for cluster: $CLUSTER"
    aws eks update-kubeconfig --region "$REGION" --name "$CLUSTER" --kubeconfig /tmp/health-check-kubeconfig 2>/dev/null
    export KUBECONFIG=/tmp/health-check-kubeconfig
  fi
}

check_nodes() {
  section "Node Health"
  if ! kubectl get nodes &>/dev/null; then
    error "❌  Cannot reach Kubernetes API"
    [[ "$FAIL_FAST" == "true" ]] && exit 1
    return
  fi

  TOTAL=0; READY=0; NOT_READY=0
  while IFS= read -r line; do
    TOTAL=$((TOTAL + 1))
    STATUS=$(echo "$line" | awk '{print $2}')
    NAME=$(echo "$line" | awk '{print $1}')
    if [[ "$STATUS" == "Ready" ]]; then
      READY=$((READY + 1))
      log "  ✅  $NAME — Ready"
    else
      NOT_READY=$((NOT_READY + 1))
      error "  ❌  $NAME — $STATUS"
    fi
  done < <(kubectl get nodes --no-headers 2>/dev/null)

  log "Nodes: $READY/$TOTAL ready"
  [[ "$NOT_READY" -gt 0 ]] && error "  $NOT_READY node(s) not ready"
}

check_pods() {
  section "Pod Health — Namespace: $NAMESPACE"
  CRASHING=0; PENDING=0

  while IFS= read -r line; do
    STATUS=$(echo "$line" | awk '{print $4}')
    NAME=$(echo "$line" | awk '{print $1}')
    RESTARTS=$(echo "$line" | awk '{print $5}')
    case "$STATUS" in
      Running|Completed)
        [[ "$RESTARTS" -gt 10 ]] && warn "  ⚠️  $NAME — Running but high restarts: $RESTARTS"
        ;;
      CrashLoopBackOff|Error|OOMKilled)
        error "  ❌  $NAME — $STATUS (restarts: $RESTARTS)"
        CRASHING=$((CRASHING + 1))
        ;;
      Pending)
        warn "  ⚠️  $NAME — Pending"
        PENDING=$((PENDING + 1))
        ;;
    esac
  done < <(kubectl get pods -n "$NAMESPACE" --no-headers 2>/dev/null)

  [[ "$CRASHING" -eq 0 && "$PENDING" -eq 0 ]] && log "✅  All pods healthy in $NAMESPACE"
  [[ "$CRASHING" -gt 0 ]] && error "  $CRASHING pod(s) crashing"
  [[ "$PENDING" -gt 0 ]] && warn "  $PENDING pod(s) pending"
}

check_deployments() {
  section "Deployment Rollout Status"
  UNAVAILABLE=0

  while IFS= read -r line; do
    NAME=$(echo "$line" | awk '{print $1}')
    READY=$(echo "$line" | awk '{print $2}')
    UP_TO_DATE=$(echo "$line" | awk '{print $3}')
    DESIRED=$(echo "$line" | cut -d'/' -f2 | awk '{print $1}' | head -c1)

    if [[ "$READY" == *"/"* ]]; then
      CURRENT=$(echo "$READY" | cut -d'/' -f1)
      TOTAL=$(echo "$READY" | cut -d'/' -f2)
      if [[ "$CURRENT" -lt "$TOTAL" ]]; then
        error "  ❌  $NAME — $READY replicas ready"
        UNAVAILABLE=$((UNAVAILABLE + 1))
      else
        log "  ✅  $NAME — $READY replicas ready"
      fi
    fi
  done < <(kubectl get deployments -n "$NAMESPACE" --no-headers 2>/dev/null)

  [[ "$UNAVAILABLE" -gt 0 ]] && error "  $UNAVAILABLE deployment(s) degraded"
}

check_pvc() {
  section "Persistent Volume Claims"
  UNBOUND=0

  while IFS= read -r line; do
    NAME=$(echo "$line"  | awk '{print $1}')
    STATUS=$(echo "$line" | awk '{print $2}')
    if [[ "$STATUS" != "Bound" ]]; then
      error "  ❌  PVC $NAME — $STATUS"
      UNBOUND=$((UNBOUND + 1))
    else
      log "  ✅  $NAME — Bound"
    fi
  done < <(kubectl get pvc -n "$NAMESPACE" --no-headers 2>/dev/null)

  [[ "$UNBOUND" -eq 0 ]] && log "✅  All PVCs bound"
}

check_cloudwatch_alarms() {
  section "CloudWatch Alarm Status"
  ALARM_COUNT=$(aws cloudwatch describe-alarms \
    --state-value ALARM \
    --region "$REGION" \
    --query 'length(MetricAlarms)' \
    --output text 2>/dev/null || echo "0")

  if [[ "$ALARM_COUNT" -eq 0 ]]; then
    log "✅  No active CloudWatch alarms"
  else
    warn "  ⚠️  $ALARM_COUNT active CloudWatch alarm(s)"
    aws cloudwatch describe-alarms \
      --state-value ALARM \
      --region "$REGION" \
      --query 'MetricAlarms[*].[AlarmName,StateReason]' \
      --output table 2>/dev/null | head -20
  fi
}

print_summary() {
  section "Health Check Summary"
  if [[ "$EXIT_CODE" -eq 0 ]]; then
    log "✅  All checks passed — cluster is healthy"
  else
    error "❌  One or more checks failed — review output above"
    log "    Refer to RUNBOOK.md for remediation steps"
  fi
  echo
}

main() {
  parse_args "$@"
  check_tools
  check_aws_connectivity
  check_nodes
  check_pods
  check_deployments
  check_pvc
  check_cloudwatch_alarms
  print_summary
  exit "$EXIT_CODE"
}

main "$@"
