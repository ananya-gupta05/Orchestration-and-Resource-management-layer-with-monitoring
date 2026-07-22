# telemetry.py
# Infrastructure telemetry publisher
#
# Runs as a background thread alongside the gRPC server.
# Every 30 seconds, reads cluster state from Kubernetes
# and publishes infrastructure metrics to the pub/sub system.
#
# What this layer publishes (cluster-level):
#   - pod health (Running/NotReady)
#   - which node each pod is on
#   - PVC status
#   - replication lag (when Patroni API is queryable)
#
# What this layer does NOT publish (Yahavi's responsibility):
#   - per-tenant query counts
#   - per-tenant schema sizes
#   - per-tenant connection counts
from unittest import result

from kubernetes.stream import stream
import time
import threading
import logging
import json
import requests
from datetime import datetime, timezone
from kubernetes import client, config
from provisioner import get_patroni_status
from metrics import (
    CLUSTER_CPU, CLUSTER_RAM, CLUSTER_STORAGE_GB,
    CLUSTER_BANDWIDTH_MB, CLUSTER_IO_THROUGHPUT,
    PATRONI_HEALTHY, PATRONI_LAG_MB, CLUSTER_PODS_READY,
    TELEMETRY_SCRAPE_ERRORS, TELEMETRY_SCRAPE_DURATION,
)

logger = logging.getLogger(__name__)
from config import SHARED_NAMESPACE

SCRAPE_INTERVAL = 30


class InfrastructureTelemetryPublisher:
    def __init__(self):
        self._k8s_core = None
        self._k8s_custom = None
        self.latest_metrics = {}
        self.previous_network_bytes = {}
        self.previous_io_bytes = {}
        self.previous_timestamp = None
        self._running = False
        self._thread = None

    def _init_k8s(self):
        if self._k8s_core is not None:
            return True
        try:
            config.load_incluster_config()
        except config.ConfigException:
            try:
                config.load_kube_config()
            except config.ConfigException:
                logger.warning("No Kubernetes config found - telemetry disabled")
                return False
        self._k8s_core = client.CoreV1Api()
        self._k8s_custom = client.CustomObjectsApi()
        return True

    def _exec_in_pod(self, pod_name: str, command: list[str]) -> str:
        return stream(
            self._k8s_core.connect_get_namespaced_pod_exec,
            pod_name,
            SHARED_NAMESPACE,
            command=command,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
        )
    def _query_prometheus(self, query: str) -> float:
        response = requests.get(
                "http://host.docker.internal:9092/api/v1/query",

                params={"query": query},

                timeout=5,

        )

        data = response.json()

        result = data["data"]["result"]

        if not result:
            return 0.0

        return float(result[0]["value"][1])

    def start(self):
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="telemetry-publisher"
        )
        self._thread.start()
        logger.info(f"Telemetry publisher started (interval: {SCRAPE_INTERVAL}s)")

    def stop(self):
        self._running = False
        logger.info("Telemetry publisher stopped")

    def _run_loop(self):
        while self._running:
            # Time the full collection cycle so Grafana can show
            # how long each scrape takes — useful if K8s API is slow.
            with TELEMETRY_SCRAPE_DURATION.time():
                try:
                    metrics = self._collect_metrics()
                    self._publish(metrics)
                except Exception as e:
                    logger.error(f"Telemetry collection error: {e}")
            time.sleep(SCRAPE_INTERVAL)

    def _collect_metrics(self) -> dict:
        if not self._init_k8s():
            logger.warning("Skipping telemetry collection - K8s unavailable")
            return self.latest_metrics or {}

        metrics = {
            "event": "infra.telemetry",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "namespace": SHARED_NAMESPACE,
        }

        metrics["cluster_cpu_usage_percent"] = -1.0
        metrics["cluster_ram_usage_percent"] = -1.0
        metrics["cluster_storage_used_gb"] = 0.0
        metrics["cluster_bandwidth_mb"] = 0.0
        metrics["cluster_io_throughput"] = 0.0

        patroni = get_patroni_status()
        leader_pod = patroni["leader_pod"]
        replica_pod = patroni["replica_pod"]

        # ── Pod metrics ───────────────────────────────────────────────────
        try:
            leader = self._k8s_core.read_namespaced_pod(leader_pod, SHARED_NAMESPACE)
            metrics["primary_pod"] = leader_pod
            metrics["primary_phase"] = leader.status.phase
            containers_ready = all(
                cs.ready for cs in (leader.status.container_statuses or [])
            )
            metrics["primary_ready"] = containers_ready
            metrics["primary_node"] = leader.spec.node_name or "unknown"
            metrics["primary_restart_count"] = sum(
                cs.restart_count for cs in (leader.status.container_statuses or [])
            )
        except client.ApiException:
            metrics["primary_phase"] = "NotFound"
            metrics["primary_ready"] = False
            metrics["primary_node"] = "unknown"
            metrics["primary_restart_count"] = 0

        try:
            replica = self._k8s_core.read_namespaced_pod(replica_pod, SHARED_NAMESPACE)
            metrics["replica_pod"] = replica_pod
            metrics["replica_phase"] = replica.status.phase
            containers_ready = all(
                cs.ready for cs in (replica.status.container_statuses or [])
            )
            metrics["replica_ready"] = containers_ready
            metrics["replica_node"] = replica.spec.node_name or "unknown"
            metrics["replica_restart_count"] = sum(
                cs.restart_count for cs in (replica.status.container_statuses or [])
            )
        except client.ApiException:
            metrics["replica_phase"] = "NotFound"
            metrics["replica_ready"] = False
            metrics["replica_node"] = "unknown"
            metrics["replica_restart_count"] = 0

        # ── PVC metrics ───────────────────────────────────────────────────
        try:
            pvc0 = self._k8s_core.read_namespaced_persistent_volume_claim(
                f"postgres-data-{leader_pod}", SHARED_NAMESPACE
            )
            metrics["primary_pvc_phase"] = pvc0.status.phase
            metrics["primary_pvc_capacity"] = (
                pvc0.status.capacity.get("storage", "unknown")
                if pvc0.status.capacity else "unknown"
            )
        except client.ApiException:
            metrics["primary_pvc_phase"] = "NotFound"
            metrics["primary_pvc_capacity"] = "unknown"

        try:
            pvc1 = self._k8s_core.read_namespaced_persistent_volume_claim(
                f"postgres-data-{replica_pod}", SHARED_NAMESPACE
            )
            metrics["replica_pvc_phase"] = pvc1.status.phase
        except client.ApiException:
            metrics["replica_pvc_phase"] = "NotFound"

        # ── CPU / RAM (via metrics-server) ────────────────────────────────
        try:
            pod_metrics = self._k8s_custom.list_namespaced_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                namespace=SHARED_NAMESPACE,
                plural="pods"
            )
            total_cpu_m = 0
            total_memory_mi = 0
            for item in pod_metrics["items"]:
                pod_name = item["metadata"]["name"]
                if pod_name.startswith(("postgres", "pgbouncer", "etcd")):
                    for container in item["containers"]:
                        cpu = container["usage"]["cpu"]
                        memory = container["usage"]["memory"]
                        if cpu.endswith("n"):
                            total_cpu_m += int(cpu[:-1]) / 1000000
                        elif cpu.endswith("u"):
                            total_cpu_m += int(cpu[:-1]) / 1000
                        elif cpu.endswith("m"):
                            total_cpu_m += int(cpu[:-1])
                        if memory.endswith("Ki"):
                            total_memory_mi += int(memory[:-2]) / 1024
                        elif memory.endswith("Mi"):
                            total_memory_mi += int(memory[:-2])

            total_cpu_limit_m = 0
            total_memory_limit_mi = 0
            pods = self._k8s_core.list_namespaced_pod(namespace=SHARED_NAMESPACE)
            for pod in pods.items:
                if pod.metadata.name.startswith(("postgres", "pgbouncer", "etcd")):
                    for container in pod.spec.containers:
                        limits = container.resources.limits or {}
                        cpu_limit = limits.get("cpu")
                        memory_limit = limits.get("memory")
                        if cpu_limit:
                            if cpu_limit.endswith("m"):
                                total_cpu_limit_m += int(cpu_limit[:-1])
                            else:
                                total_cpu_limit_m += float(cpu_limit) * 1000
                        if memory_limit:
                            if memory_limit.endswith("Gi"):
                                total_memory_limit_mi += float(memory_limit[:-2]) * 1024
                            elif memory_limit.endswith("Mi"):
                                total_memory_limit_mi += float(memory_limit[:-2])

            if total_cpu_limit_m > 0:
                metrics["cluster_cpu_usage_percent"] = round(
                    (total_cpu_m / total_cpu_limit_m) * 100, 2
                )
            if total_memory_limit_mi > 0:
                metrics["cluster_ram_usage_percent"] = round(
                    (total_memory_mi / total_memory_limit_mi) * 100, 2
                )

        except Exception as e:
            logger.warning(
                f"CPU/RAM metrics unavailable: {e}. "
                f"If using minikube: minikube addons enable metrics-server"
            )
            # -1.0 means unavailable, not zero. Grafana dashboards
            # should filter negative values rather than display them.
            metrics["cluster_cpu_usage_percent"] = -1.0
            metrics["cluster_ram_usage_percent"] = -1.0
            TELEMETRY_SCRAPE_ERRORS.labels(category="cpu_ram").inc()

        # ── Storage ───────────────────────────────────────────────────────
        try:
            output = self._exec_in_pod(
                leader_pod, ["df", "-B1", "/home/postgres/pgdata"]
            )
            lines = output.strip().splitlines()
            if len(lines) >= 2:
                used_bytes = int(lines[1].split()[2])
                metrics["cluster_storage_used_gb"] = round(
                    used_bytes / (1024 ** 3), 2
                )
        except Exception as e:
            logger.warning(f"Storage metrics error: {e}")
            TELEMETRY_SCRAPE_ERRORS.labels(category="storage").inc()



        # ── Bandwidth / IO (Prometheus) ──────────────────────────────
        try:
            bandwidth_query = """
            sum(instance:node_network_receive_bytes:rate:sum)
            +
            sum(instance:node_network_transmit_bytes:rate:sum)
            """

            bandwidth_bytes = self._query_prometheus(
                bandwidth_query
            )

            metrics["cluster_bandwidth_mb"] = round(
                bandwidth_bytes / (1024 ** 2),
                4
            )

            io_query = """
            sum(rate(node_disk_read_bytes_total[1m]))
            +
            sum(rate(node_disk_written_bytes_total[1m]))
            """

            io_bytes = self._query_prometheus(
                io_query
            )

            metrics["cluster_io_throughput"] = round(
                io_bytes / (1024 ** 2),
                4
            )

        except Exception as e:
            logger.warning(f"Bandwidth/IO metrics error: {e}")
            TELEMETRY_SCRAPE_ERRORS.labels(category="bandwidth_io").inc()
        # ── Patroni ───────────────────────────────────────────────────────
        metrics["patroni_leader"] = patroni["leader_pod"]
        metrics["patroni_replica"] = patroni["replica_pod"]
        metrics["patroni_timeline"] = patroni["timeline"]
        metrics["patroni_healthy"] = patroni["patroni_healthy"]
        metrics["cluster_healthy"] = (
            metrics.get("primary_ready", False) and
            metrics.get("replica_ready", False) and
            metrics.get("primary_pvc_phase") == "Bound" and
            metrics.get("replica_pvc_phase") == "Bound" and
            patroni["patroni_healthy"]
        )

        # ── Push values into Prometheus Gauges ────────────────────────────
        # These are what Prometheus scrapes from /metrics every 15s.
        # -1.0 values (unavailable metrics) are set as-is so dashboards
        # can distinguish "genuinely 0" from "collection failed".
        CLUSTER_CPU.set(metrics["cluster_cpu_usage_percent"])
        CLUSTER_RAM.set(metrics["cluster_ram_usage_percent"])
        CLUSTER_STORAGE_GB.set(metrics["cluster_storage_used_gb"])
        CLUSTER_BANDWIDTH_MB.set(metrics["cluster_bandwidth_mb"])
        CLUSTER_IO_THROUGHPUT.set(metrics["cluster_io_throughput"])
        PATRONI_HEALTHY.set(1.0 if patroni["patroni_healthy"] else 0.0)
        PATRONI_LAG_MB.set(
            round(patroni.get("lag_bytes", 0) / (1024 * 1024), 2)
        )
        pods_ready = sum([
            1 if metrics.get("primary_ready") else 0,
            1 if metrics.get("replica_ready") else 0,
        ])
        CLUSTER_PODS_READY.set(pods_ready)

        self.latest_metrics = metrics
        return metrics

    def _publish(self, metrics: dict):
        metrics_json = json.dumps(metrics, indent=2)
        # TODO: replace with real pub/sub publish when available
        logger.info(f"[TELEMETRY] Publishing metrics:\n{metrics_json}")
