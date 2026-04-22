#!/usr/bin/env python3
"""
Automated incident triage script for AWS EKS environments.
Queries CloudWatch metrics and Kubernetes API to generate structured RCA drafts.

Usage:
    python incident_triage.py --cluster eks-sre-prod --region us-east-1 --time-window 30m
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3

# Optional: pip install kubernetes
try:
    from kubernetes import client, config as k8s_config
    K8S_AVAILABLE = True
except ImportError:
    K8S_AVAILABLE = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SRE Incident Triage Tool")
    parser.add_argument("--cluster", required=True, help="EKS cluster name")
    parser.add_argument("--region", default="us-east-1", help="AWS region")
    parser.add_argument("--namespace", default=None, help="Kubernetes namespace (all if omitted)")
    parser.add_argument("--service", default=None, help="Specific service to check")
    parser.add_argument("--time-window", default="30m", help="Look-back window (e.g. 30m, 1h)")
    parser.add_argument("--check", default="all", choices=["all", "pods", "nodes", "metrics", "kafka-lag"],
                        help="Specific check to run")
    parser.add_argument("--output", default=None, help="Write RCA report to this file")
    return parser.parse_args()


def parse_time_window(window: str) -> datetime:
    now = datetime.now(timezone.utc)
    if window.endswith("m"):
        return now - timedelta(minutes=int(window[:-1]))
    if window.endswith("h"):
        return now - timedelta(hours=int(window[:-1]))
    raise ValueError(f"Unsupported time window format: {window}")


class CloudWatchChecker:
    def __init__(self, cluster_name: str, region: str, start_time: datetime):
        self.cluster_name = cluster_name
        self.region = region
        self.start_time = start_time
        self.cw = boto3.client("cloudwatch", region_name=region)

    def get_metric_stats(self, metric_name: str, namespace: str = "ContainerInsights") -> dict:
        response = self.cw.get_metric_statistics(
            Namespace=namespace,
            MetricName=metric_name,
            Dimensions=[{"Name": "ClusterName", "Value": self.cluster_name}],
            StartTime=self.start_time,
            EndTime=datetime.now(timezone.utc),
            Period=60,
            Statistics=["Average", "Maximum"],
        )
        datapoints = sorted(response.get("Datapoints", []), key=lambda x: x["Timestamp"])
        if not datapoints:
            return {"status": "no_data", "max": None, "avg": None}
        max_val = max(d["Maximum"] for d in datapoints)
        avg_val = sum(d["Average"] for d in datapoints) / len(datapoints)
        return {
            "status": "critical" if max_val > 85 else "warning" if max_val > 70 else "ok",
            "max": round(max_val, 2),
            "avg": round(avg_val, 2),
            "datapoints": len(datapoints),
        }

    def check_all(self) -> dict:
        print("[CloudWatch] Querying cluster metrics...")
        return {
            "cpu_utilization": self.get_metric_stats("node_cpu_utilization"),
            "memory_utilization": self.get_metric_stats("node_memory_utilization"),
            "pod_restarts": self.get_metric_stats("pod_number_of_container_restarts"),
            "network_rx": self.get_metric_stats("node_network_total_bytes"),
        }


class KubernetesChecker:
    def __init__(self, namespace: str | None):
        self.namespace = namespace

    def get_unhealthy_pods(self) -> list[dict]:
        cmd = ["kubectl", "get", "pods", "-o", "json"]
        if self.namespace:
            cmd += ["-n", self.namespace]
        else:
            cmd.append("-A")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            pods_data = json.loads(result.stdout)
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            print(f"[K8s] Warning: Could not query pods — {e}")
            return []

        unhealthy = []
        for pod in pods_data.get("items", []):
            phase = pod["status"].get("phase", "Unknown")
            name = pod["metadata"]["name"]
            ns = pod["metadata"]["namespace"]
            restarts = 0
            reason = None

            for cs in pod["status"].get("containerStatuses", []):
                restarts += cs.get("restartCount", 0)
                if cs.get("state", {}).get("waiting"):
                    reason = cs["state"]["waiting"].get("reason")

            if phase not in ("Running", "Succeeded") or restarts > 3 or reason:
                unhealthy.append({
                    "name": name,
                    "namespace": ns,
                    "phase": phase,
                    "restarts": restarts,
                    "reason": reason,
                })

        return unhealthy

    def get_node_status(self) -> list[dict]:
        try:
            result = subprocess.run(
                ["kubectl", "get", "nodes", "-o", "json"],
                capture_output=True, text=True, timeout=30
            )
            nodes_data = json.loads(result.stdout)
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            print(f"[K8s] Warning: Could not query nodes — {e}")
            return []

        nodes = []
        for node in nodes_data.get("items", []):
            name = node["metadata"]["name"]
            ready = any(
                c["type"] == "Ready" and c["status"] == "True"
                for c in node["status"].get("conditions", [])
            )
            nodes.append({"name": name, "ready": ready})

        return nodes

    def get_recent_deployments(self) -> list[str]:
        try:
            result = subprocess.run(
                ["kubectl", "get", "events", "-A", "--sort-by=.lastTimestamp",
                 "--field-selector=reason=ScalingReplicaSet", "-o", "json"],
                capture_output=True, text=True, timeout=30
            )
            events = json.loads(result.stdout).get("items", [])
            return [
                f"{e['metadata']['namespace']}/{e['involvedObject']['name']} — {e['message']}"
                for e in events[-5:]
            ]
        except Exception:
            return []


def generate_rca_report(args: argparse.Namespace, cw_results: dict, k8s_results: dict) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    unhealthy_pods = k8s_results.get("unhealthy_pods", [])
    nodes = k8s_results.get("nodes", [])
    not_ready_nodes = [n for n in nodes if not n["ready"]]

    severity = "SEV-4"
    if not_ready_nodes or any(p["restarts"] > 10 for p in unhealthy_pods):
        severity = "SEV-2"
    if len(not_ready_nodes) > 1 or len(unhealthy_pods) > 5:
        severity = "SEV-1"

    lines = [
        f"# Incident Triage Report",
        f"",
        f"**Generated:** {timestamp}  ",
        f"**Cluster:** {args.cluster}  ",
        f"**Region:** {args.region}  ",
        f"**Time Window:** {args.time_window}  ",
        f"**Suggested Severity:** {severity}",
        f"",
        f"---",
        f"",
        f"## CloudWatch Signal Summary",
        f"",
        f"| Metric | Status | Max | Avg |",
        f"|--------|--------|-----|-----|",
    ]

    for metric, data in cw_results.items():
        status_icon = {"ok": "✅", "warning": "⚠️", "critical": "🔴", "no_data": "❓"}.get(data["status"], "❓")
        lines.append(f"| {metric} | {status_icon} {data['status']} | {data['max']} | {data['avg']} |")

    lines += [
        f"",
        f"## Kubernetes Health",
        f"",
        f"### Nodes ({len(nodes)} total, {len(not_ready_nodes)} not ready)",
        f"",
    ]

    if not_ready_nodes:
        for node in not_ready_nodes:
            lines.append(f"- ❌ `{node['name']}` — NOT READY")
    else:
        lines.append("- ✅ All nodes ready")

    lines += [
        f"",
        f"### Unhealthy Pods ({len(unhealthy_pods)} found)",
        f"",
    ]

    if unhealthy_pods:
        lines.append("| Namespace | Pod | Phase | Restarts | Reason |")
        lines.append("|-----------|-----|-------|----------|--------|")
        for pod in unhealthy_pods[:20]:
            lines.append(
                f"| {pod['namespace']} | {pod['name']} | {pod['phase']} | {pod['restarts']} | {pod['reason'] or '-'} |"
            )
    else:
        lines.append("- ✅ No unhealthy pods detected")

    recent = k8s_results.get("recent_deployments", [])
    if recent:
        lines += ["", "### Recent Deployment Events", ""]
        for event in recent:
            lines.append(f"- {event}")

    lines += [
        f"",
        f"---",
        f"",
        f"## Recommended Actions",
        f"",
        f"1. Review unhealthy pod logs: `kubectl logs <pod> -n <ns> --previous`",
        f"2. Check recent rollouts: `kubectl rollout history deployment -A`",
        f"3. Run self-healing handler: `python scripts/alert_handler.py --action restart-unhealthy-pods`",
        f"4. Refer to [RUNBOOK.md](../RUNBOOK.md) for specific playbooks",
        f"",
        f"---",
        f"*Generated by aws-sre-incident-automation triage tool*",
    ]

    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    start_time = parse_time_window(args.time_window)

    print(f"Starting triage for cluster: {args.cluster} | window: {args.time_window}")
    print("-" * 60)

    cw = CloudWatchChecker(args.cluster, args.region, start_time)
    k8s = KubernetesChecker(args.namespace)

    cw_results = {}
    k8s_results = {}

    if args.check in ("all", "metrics"):
        cw_results = cw.check_all()

    if args.check in ("all", "pods"):
        print("[K8s] Checking pod health...")
        k8s_results["unhealthy_pods"] = k8s.get_unhealthy_pods()
        print(f"      Found {len(k8s_results['unhealthy_pods'])} unhealthy pods")

    if args.check in ("all", "nodes"):
        print("[K8s] Checking node status...")
        k8s_results["nodes"] = k8s.get_node_status()
        not_ready = [n for n in k8s_results["nodes"] if not n["ready"]]
        print(f"      {len(k8s_results['nodes'])} nodes, {len(not_ready)} not ready")

    k8s_results["recent_deployments"] = k8s.get_recent_deployments()

    report = generate_rca_report(args, cw_results, k8s_results)

    print("\n" + "=" * 60)
    print(report)
    print("=" * 60)

    if args.output:
        Path(args.output).write_text(report)
        print(f"\nReport written to: {args.output}")


if __name__ == "__main__":
    main()
