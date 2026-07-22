# provisioner.py
# Cloud Resource Manager — shared cluster monitoring
#
# IMPORTANT: This file does NOT create or delete Kubernetes objects per tenant.
# The shared PostgreSQL cluster is provisioned ONCE by deploy.sh at platform startup.
# This file only:
#   1. Checks if the shared cluster is healthy and has capacity
#   2. Returns current cluster status for monitoring/observability
#
# Per-tenant operations (schema creation, deletion) are handled by
# Yahavi's Database Resource Manager layer, which we call via gRPC.
import os
from kubernetes.stream import stream as k8s_stream
import logging
from kubernetes import client, config
from config import SHARED_NAMESPACE
import ast
logger = logging.getLogger(__name__)

# ── Kubernetes client setup (lazy — allows local/docker without kubeconfig) ───

_k8s_core: client.CoreV1Api | None = None
_k8s_checked = False


def _get_k8s_core() -> client.CoreV1Api | None:
    global _k8s_core, _k8s_checked
    if _k8s_checked:
        return _k8s_core
    _k8s_checked = True
    try:
        try:
            config.load_incluster_config()
            logger.info("Using in-cluster Kubernetes config")
        except config.ConfigException:
            config.load_kube_config()
            logger.info("Using local kubeconfig")
        _k8s_core = client.CoreV1Api()
    except (config.ConfigException, FileNotFoundError, IsADirectoryError) as e:
        logger.warning("Kubernetes not configured: %s", e)
        _k8s_core = None
    return _k8s_core
def _exec_patroni(k8s_core, pod_name):
    return k8s_stream(
        k8s_core.connect_get_namespaced_pod_exec,
        pod_name,
        SHARED_NAMESPACE,
        command=[
            "sh",
            "-c",
            "curl -s http://localhost:8008/patroni"
        ],
        stderr=False,
        stdin=False,
        stdout=True,
        tty=False,
    )
def _dev_without_k8s() -> bool:
    val = os.getenv("ALLOW_DEV_WITHOUT_K8S", "0")

    if val.lower() in ("1", "true", "yes"):
        logger.warning(
            "ALLOW_DEV_WITHOUT_K8S enabled - Kubernetes health checks are bypassed"
        )
        return True

    return False

# The namespace where your shared cluster lives
# This is fixed — it was created by deploy.sh

# Pod names in the shared cluster — also fixed
PRIMARY_POD = "postgres-0"
REPLICA_POD = "postgres-1"

# PVC names — created by StatefulSet, also fixed
PRIMARY_PVC = "postgres-data-postgres-0"
REPLICA_PVC = "postgres-data-postgres-1"


# ── Capacity check ────────────────────────────────────────────────────────────

def check_shared_cluster_capacity() -> dict:
    """
    Check whether the shared PostgreSQL cluster is healthy
    and able to accept new tenants.

    This is called by ProvisionTenant before creating a new schema.
    If the cluster is unhealthy, we return an error instead of
    calling Yahavi to create a schema on a broken database.

    What we check:
    1. Primary pod is Running — if postgres-0 is down, nobody can write
    2. Replica pod is Running — if postgres-1 is down, we have no HA
    3. Primary PVC is Bound — if storage is not attached, data is at risk

    Returns:
        has_capacity: bool — True if safe to provision a new tenant
        primary_running: bool
        replica_running: bool
        storage_ok: bool
        message: human-readable summary
    """

    result = {
        "has_capacity": False,
        "primary_running": False,
        "replica_running": False,
        "storage_ok": False,
        "message": ""
    }

    k8s_core = _get_k8s_core()
    if k8s_core is None:
        if _dev_without_k8s():
            result["has_capacity"] = True
            result["message"] = "K8s not configured (dev mode — schema provisioning allowed)"
            logger.info(result["message"])
            return result
        result["message"] = "Kubernetes not configured"
        return result

    try:
        # ── Check primary pod ─────────────────────────────────────────────
        # read_namespaced_pod fetches the current state of a specific pod
        # Arguments: pod name, namespace
        # Returns a V1Pod object with all pod details
        primary_pod = k8s_core.read_namespaced_pod(
            name=PRIMARY_POD,
            namespace=SHARED_NAMESPACE
        )

        # pod.status.phase is a string: "Running", "Pending", "Failed", "Unknown"
        # We check container_statuses too because a pod can be "Running"
        # but its containers might not be ready yet (probe not passed)
        primary_phase = primary_pod.status.phase
        primary_containers_ready = all(
            cs.ready
            for cs in (primary_pod.status.container_statuses or [])
        )

        result["primary_running"] = (
            primary_phase == "Running" and primary_containers_ready
        )

        # ── Check replica pod ─────────────────────────────────────────────
        replica_pod = k8s_core.read_namespaced_pod(
            name=REPLICA_POD,
            namespace=SHARED_NAMESPACE
        )

        replica_phase = replica_pod.status.phase
        replica_containers_ready = all(
            cs.ready
            for cs in (replica_pod.status.container_statuses or [])
        )

        result["replica_running"] = (
            replica_phase == "Running" and replica_containers_ready
        )

        # ── Check primary PVC ─────────────────────────────────────────────
        # read_namespaced_persistent_volume_claim fetches PVC state
        # pvc.status.phase is "Bound", "Pending", or "Lost"
        # "Bound" means the disk is successfully attached and usable
        # "Lost" means the disk was detached — data may be inaccessible
        primary_pvc = k8s_core.read_namespaced_persistent_volume_claim(
            name=PRIMARY_PVC,
            namespace=SHARED_NAMESPACE
        )

        result["storage_ok"] = primary_pvc.status.phase == "Bound"

        # ── Overall capacity decision ─────────────────────────────────────
        # All three must be healthy for us to accept a new tenant
        result["has_capacity"] = (
            result["primary_running"] and
            result["replica_running"] and
            result["storage_ok"]
        )

        if result["has_capacity"]:
            result["message"] = "Shared cluster is healthy"
        else:
            issues = []
            if not result["primary_running"]:
                issues.append("primary pod not ready")
            if not result["replica_running"]:
                issues.append("replica pod not ready")
            if not result["storage_ok"]:
                issues.append("primary PVC not bound")
            result["message"] = "Cluster issues: " + ", ".join(issues)

        logger.info(f"Capacity check: {result['message']}")
        return result

    except client.ApiException as e:
        # ApiException is raised when the Kubernetes API call fails
        # status 404 means the pod or PVC doesn't exist at all
        # This would happen if deploy.sh was never run
        logger.error(f"Kubernetes API error during capacity check: {e.status} {e.reason}")
        result["message"] = f"Kubernetes API error: {e.reason}"
        return result

    except Exception as e:
        logger.error(f"Unexpected error during capacity check: {e}")
        result["message"] = f"Unexpected error: {str(e)}"
        return result


# ── Cluster status ────────────────────────────────────────────────────────────

def get_cluster_status() -> dict:
    """
    Return detailed current status of the shared PostgreSQL cluster.

    Called by GetInfraStatus gRPC function.
    Returns pod states, which nodes they are on, and PVC status.

   Note on Patroni role:
    Pod identities (postgres-0, postgres-1) are fixed StatefulSet pod names.

    Actual leader/replica roles are determined dynamically via
    get_patroni_status(), which queries Patroni and detects the
    current leader after failovers.
    """

    status = {
        "primary_pod": PRIMARY_POD,
        "replica_pod": REPLICA_POD,
        "primary_status": "Unknown",
        "replica_status": "Unknown",
        "primary_node": "Unknown",
        "replica_node": "Unknown",
        "pvcs_bound": False,
        "error": ""
    }

    k8s_core = _get_k8s_core()
    if k8s_core is None:
        if _dev_without_k8s():
            status["error"] = "K8s not configured (dev mode)"
        else:
            status["error"] = "Kubernetes not configured"
        return status

    try:
        # ── Pod status ────────────────────────────────────────────────────
        primary_pod = k8s_core.read_namespaced_pod(
            name=PRIMARY_POD,
            namespace=SHARED_NAMESPACE
        )
        replica_pod = k8s_core.read_namespaced_pod(
            name=REPLICA_POD,
            namespace=SHARED_NAMESPACE
        )

        status["primary_status"] = primary_pod.status.phase
        status["replica_status"] = replica_pod.status.phase

        # spec.node_name is which physical node the pod is running on
        # This is set by the Kubernetes scheduler when the pod is placed
        # For your minikube setup: "minikube" or "minikube-m02"
        status["primary_node"] = primary_pod.spec.node_name or "Unknown"
        status["replica_node"] = replica_pod.spec.node_name or "Unknown"

        # ── PVC status ────────────────────────────────────────────────────
        pvc0 = k8s_core.read_namespaced_persistent_volume_claim(
            name=PRIMARY_PVC,
            namespace=SHARED_NAMESPACE
        )
        pvc1 = k8s_core.read_namespaced_persistent_volume_claim(
            name=REPLICA_PVC,
            namespace=SHARED_NAMESPACE
        )

        # Both PVCs must be Bound for full confidence
        status["pvcs_bound"] = (
            pvc0.status.phase == "Bound" and
            pvc1.status.phase == "Bound"
        )

        logger.info(
            f"Cluster status: primary={status['primary_status']} "
            f"on {status['primary_node']}, "
            f"replica={status['replica_status']} "
            f"on {status['replica_node']}"
        )

        return status

    except client.ApiException as e:
        logger.error(f"Kubernetes API error during status check: {e.status} {e.reason}")
        status["error"] = f"Kubernetes API error: {e.reason}"
        return status

    except Exception as e:
        logger.error(f"Unexpected error during status check: {e}")
        status["error"] = str(e)
        return status
    
def get_patroni_status():

    result = {
    "leader_pod": "unknown",
    "replica_pod": "unknown",
    "timeline": 0,
    "replication_state": "unknown",
    "lag_bytes": 0,
    "patroni_healthy": False
    }

    k8s_core = _get_k8s_core()
    if k8s_core is None:
        logger.warning(
            "K8s unavailable — cannot query Patroni status"
        )
        return result

    try:
        pod0_output = _exec_patroni(
            k8s_core,
            "postgres-0"
        )

        pod1_output = _exec_patroni(
            k8s_core,
            "postgres-1"
        )

        pod0_data = ast.literal_eval(pod0_output)

        pod1_data = ast.literal_eval(pod1_output)

        if pod0_data.get("role") == "master":
            result["leader_pod"] = "postgres-0"
            result["replica_pod"] = "postgres-1"
            result["timeline"] = pod0_data.get("timeline", 0)

            result["replication_state"] = (
            pod0_data.get("replication", [{}])[0]
            .get("state", "unknown")
             )

            leader_lsn = (
            pod0_data.get("xlog", {})
            .get("location", 0)
            )

            replica_lsn = (
            pod1_data.get("xlog", {})
            .get("replayed_location", 0)
            )

            result["lag_bytes"] = max(
             0,
            leader_lsn - replica_lsn
            )

        elif pod1_data.get("role") == "master":
            result["leader_pod"] = "postgres-1"
            result["replica_pod"] = "postgres-0"
            result["timeline"] = pod1_data.get("timeline", 0)

            result["replication_state"] = (
                pod1_data.get("replication", [{}])[0]
                .get("state", "unknown")
            )

            leader_lsn = (
                pod1_data.get("xlog", {})
                .get("location", 0)
            )

            replica_lsn = (
                pod0_data.get("xlog", {})
                .get("replayed_location", 0)
            )

            result["lag_bytes"] = max(
                0,
                leader_lsn - replica_lsn
            )
        result["patroni_healthy"] = (

        result["leader_pod"] != "unknown"

        and result["replica_pod"] != "unknown"

        )
        return result

    except Exception as e:

        logger.error(f"Patroni status error: {e}")

        return result