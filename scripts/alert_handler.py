#!/usr/bin/env python3
"""
Self-healing alert handler for AWS EKS SRE automation.
Responds to common alert patterns with automated remediation actions.

Usage:
    python alert_handler.py --action restart-unhealthy-pods --namespace production --dry-run
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone


ACTIONS = [
    "restart-unhealthy-pods",
    "cordon-pressured-node",
    "rollback-deployment",
    "scale-deployment",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SRE Self-Healing Alert Handler")
    parser.add_argument("--action", required=True, choices=ACTIONS, help="Remediation action")
    parser.add_argument("--namespace", default=None, help="Target Kubernetes namespace")
    parser.add_argument("--deployment", default=None, help="Target deployment name (for rollback/scale)")
    parser.add_argument("--replicas", type=int, default=None, help="Replica count (for scale action)")
    parser.add_argument("--node", default=None, help="Target node name (for cordon action)")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without executing")
    return parser.parse_args()


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def kubectl(args_list: list[str], dry_run: bool = False) -> tuple[int, str, str]:
    cmd = ["kubectl"] + args_list
    log(f"{'[DRY-RUN] ' if dry_run else ''}kubectl {' '.join(args_list)}")
    if dry_run:
        return 0, "", ""
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return result.returncode, result.stdout, result.stderr


def restart_unhealthy_pods(namespace: str | None, dry_run: bool) -> int:
    log("Scanning for unhealthy pods...")
    ns_flags = ["-n", namespace] if namespace else ["-A"]
    rc, stdout, _ = kubectl(["get", "pods", "-o", "json"] + ns_flags, dry_run=False)
    if rc != 0:
        log("ERROR: Failed to query pods")
        return 1

    if dry_run:
        log("[DRY-RUN] Would delete crash-looping pods")
        return 0

    try:
        pods = json.loads(stdout).get("items", [])
    except json.JSONDecodeError:
        log("ERROR: Could not parse pod list")
        return 1

    restarted = 0
    for pod in pods:
        name = pod["metadata"]["name"]
        ns = pod["metadata"]["namespace"]
        for cs in pod["status"].get("containerStatuses", []):
            restarts = cs.get("restartCount", 0)
            waiting = cs.get("state", {}).get("waiting", {})
            reason = waiting.get("reason", "")
            if restarts > 5 or reason in ("CrashLoopBackOff", "OOMKilled", "Error"):
                log(f"Deleting pod {ns}/{name} (restarts={restarts}, reason={reason})")
                kubectl(["delete", "pod", name, "-n", ns, "--grace-period=10"], dry_run)
                restarted += 1

    log(f"Restarted {restarted} unhealthy pods")
    return 0


def cordon_pressured_node(node: str | None, dry_run: bool) -> int:
    if not node:
        log("ERROR: --node is required for cordon action")
        return 1

    log(f"Cordoning node: {node}")
    rc, _, err = kubectl(["cordon", node], dry_run)
    if rc != 0 and not dry_run:
        log(f"ERROR: {err}")
        return 1

    log(f"Draining node: {node}")
    rc, _, err = kubectl([
        "drain", node,
        "--ignore-daemonsets",
        "--delete-emptydir-data",
        "--grace-period=60",
        "--timeout=5m",
    ], dry_run)
    if rc != 0 and not dry_run:
        log(f"WARNING: Drain encountered issues — {err}")

    log(f"Node {node} cordoned and drained. Replacement will be handled by ASG.")
    return 0


def rollback_deployment(namespace: str | None, deployment: str | None, dry_run: bool) -> int:
    if not deployment:
        log("ERROR: --deployment is required for rollback action")
        return 1
    if not namespace:
        log("ERROR: --namespace is required for rollback action")
        return 1

    log(f"Rolling back deployment {namespace}/{deployment}...")
    rc, _, err = kubectl(["rollout", "undo", f"deployment/{deployment}", "-n", namespace], dry_run)
    if rc != 0 and not dry_run:
        log(f"ERROR: {err}")
        return 1

    log("Watching rollout status...")
    kubectl(["rollout", "status", f"deployment/{deployment}", "-n", namespace, "--timeout=5m"], dry_run)
    log(f"Rollback complete: {namespace}/{deployment}")
    return 0


def scale_deployment(namespace: str | None, deployment: str | None, replicas: int | None, dry_run: bool) -> int:
    if not all([deployment, namespace, replicas is not None]):
        log("ERROR: --deployment, --namespace, and --replicas are required for scale action")
        return 1

    log(f"Scaling {namespace}/{deployment} to {replicas} replicas...")
    rc, _, err = kubectl([
        "scale", f"deployment/{deployment}",
        "-n", namespace,
        f"--replicas={replicas}",
    ], dry_run)
    if rc != 0 and not dry_run:
        log(f"ERROR: {err}")
        return 1

    log(f"Scale action complete: {namespace}/{deployment} → {replicas} replicas")
    return 0


def main() -> None:
    args = parse_args()

    if args.dry_run:
        log("Running in DRY-RUN mode — no changes will be applied")

    action_map = {
        "restart-unhealthy-pods": lambda: restart_unhealthy_pods(args.namespace, args.dry_run),
        "cordon-pressured-node": lambda: cordon_pressured_node(args.node, args.dry_run),
        "rollback-deployment": lambda: rollback_deployment(args.namespace, args.deployment, args.dry_run),
        "scale-deployment": lambda: scale_deployment(args.namespace, args.deployment, args.replicas, args.dry_run),
    }

    exit_code = action_map[args.action]()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
