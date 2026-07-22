# scaling_consumer.py
# Listens for scaling recommendations from Saksham's monitoring layer
# and patches the shared PostgreSQL StatefulSet with new resource values.
#
# In production: connects to IntelliDB pub/sub and receives real events.
# Right now: exposes a method that can be called directly for simulation.

import logging
from kubernetes import client
from config import SHARED_NAMESPACE
from metrics import SCALING_EVENTS

logger = logging.getLogger(__name__)

STATEFULSET_NAME = "postgres"
CONTAINER_NAME = "postgres"

MIN_CPU = "250m"
MAX_CPU = "2000m"
MIN_MEMORY = "256Mi"
MAX_MEMORY = "2Gi"


class ScalingConsumer:
    def __init__(self):
        self._k8s_apps = None
        self._running = False
        self._thread = None
        self._scale_count = 0

    def _get_k8s_apps(self):
        if self._k8s_apps is None:
            self._k8s_apps = client.AppsV1Api()
        return self._k8s_apps

    def start(self):
        self._running = True
        logger.info("Scaling consumer started — ready to receive events")

    def stop(self):
        self._running = False
        logger.info("Scaling consumer stopped")

    def handle_event(self, event: dict):
        logger.info(
            f"Scaling event received: action={event.get('action')} "
            f"reason={event.get('reason')} "
            f"cpu={event.get('suggested_cpu_request')} "
            f"memory={event.get('suggested_memory_request')}"
        )

        required_fields = ["action", "suggested_cpu_request", "suggested_memory_request"]
        for field in required_fields:
            if field not in event:
                logger.error(f"Scaling event missing required field: {field}")
                return

        action = event["action"]
        raw_cpu = event["suggested_cpu_request"]
        raw_memory = event["suggested_memory_request"]

        # ── Enforce resource boundaries ───────────────────────────────────
        try:
            cpu_m = int(raw_cpu.replace("m", ""))
            min_cpu_m = int(MIN_CPU.replace("m", ""))
            max_cpu_m = int(MAX_CPU.replace("m", ""))

            if cpu_m < min_cpu_m:
                logger.warning(f"CPU {raw_cpu} below minimum, clamping to {MIN_CPU}")
                new_cpu = MIN_CPU
            elif cpu_m > max_cpu_m:
                logger.warning(f"CPU {raw_cpu} above maximum, clamping to {MAX_CPU}")
                new_cpu = MAX_CPU
            else:
                new_cpu = raw_cpu
        except ValueError:
            logger.error(f"Invalid CPU format: {raw_cpu}")
            SCALING_EVENTS.labels(action=action, outcome="rejected").inc()
            return

        try:
            requested_memory_mi = _memory_to_mi(raw_memory)
            min_memory_mi = _memory_to_mi(MIN_MEMORY)
            max_memory_mi = _memory_to_mi(MAX_MEMORY)

            if requested_memory_mi < min_memory_mi:
                logger.warning(f"Memory {raw_memory} below minimum, clamping to {MIN_MEMORY}")
                new_memory = MIN_MEMORY
            elif requested_memory_mi > max_memory_mi:
                logger.warning(f"Memory {raw_memory} above maximum, clamping to {MAX_MEMORY}")
                new_memory = MAX_MEMORY
            else:
                new_memory = raw_memory
        except ValueError:
            logger.error(f"Invalid memory format: {raw_memory}")
            SCALING_EVENTS.labels(action=action, outcome="rejected").inc()
            return

        if action not in ("scale_up", "scale_down"):
            logger.error(f"Unknown scaling action: {action}")
            SCALING_EVENTS.labels(action=action, outcome="rejected").inc()
            return

        # Determine if clamping occurred — tracked as its own outcome
        # so Grafana can alert when Saksham's engine sends out-of-bounds values.
        was_clamped = (new_cpu != raw_cpu or new_memory != raw_memory)

        # ── Skip duplicate events ─────────────────────────────────────────
        current = self.get_current_resources()
        if current.get("cpu_request") == new_cpu and current.get("memory_request") == new_memory:
            logger.info(
                f"Scaling skipped: cluster already has cpu={new_cpu} memory={new_memory}"
            )
            SCALING_EVENTS.labels(action=action, outcome="skipped").inc()
            return

        # ── Apply the scaling ─────────────────────────────────────────────
        result = self._patch_statefulset(new_cpu, new_memory, action)

        if result["success"]:
            self._scale_count += 1
            outcome = "clamped" if was_clamped else "applied"
            SCALING_EVENTS.labels(action=action, outcome=outcome).inc()
            logger.info(
                f"Scaling {outcome} successfully "
                f"(total scaling operations: {self._scale_count})"
            )
        else:
            logger.error(f"Scaling failed: {result['error']}")

    def _patch_statefulset(self, new_cpu: str, new_memory: str, action: str) -> dict:
        logger.info(f"Patching StatefulSet: cpu={new_cpu} memory={new_memory} ({action})")

        patch_body = {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": CONTAINER_NAME,
                                "resources": {
                                    "requests": {"cpu": new_cpu, "memory": new_memory},
                                    "limits": {
                                        "cpu": _double_cpu(new_cpu),
                                        "memory": _double_memory(new_memory),
                                    },
                                },
                            }
                        ]
                    }
                }
            }
        }

        try:
            self._get_k8s_apps().patch_namespaced_stateful_set(
                name=STATEFULSET_NAME,
                namespace=SHARED_NAMESPACE,
                body=patch_body,
            )
            logger.info(
                "StatefulSet patched. Kubernetes will perform rolling restart. "
                "Patroni manages HA during restart."
            )
            return {"success": True, "error": ""}
        except client.ApiException as e:
            error_msg = f"Kubernetes API error: {e.status} {e.reason}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
        except Exception as e:
            logger.error(f"Unexpected error patching StatefulSet: {e}")
            return {"success": False, "error": str(e)}

    def get_current_resources(self) -> dict:
        try:
            sts = self._get_k8s_apps().read_namespaced_stateful_set(
                name=STATEFULSET_NAME, namespace=SHARED_NAMESPACE
            )
            for container in sts.spec.template.spec.containers:
                if container.name == CONTAINER_NAME:
                    return {
                        "cpu_request": container.resources.requests.get("cpu", "unknown"),
                        "memory_request": container.resources.requests.get("memory", "unknown"),
                        "cpu_limit": container.resources.limits.get("cpu", "unknown"),
                        "memory_limit": container.resources.limits.get("memory", "unknown"),
                    }
        except Exception as e:
            logger.error(f"Could not read current resources: {e}")
        return {}


# ── Helper functions ──────────────────────────────────────────────────────────

def _memory_to_mi(memory_str: str) -> int:
    if memory_str.endswith("Mi"):
        return int(memory_str[:-2])
    if memory_str.endswith("Gi"):
        return int(memory_str[:-2]) * 1024
    raise ValueError(f"Unsupported memory format: {memory_str}. Expected Mi or Gi suffix.")


def _double_cpu(cpu_str: str) -> str:
    return f"{int(cpu_str[:-1]) * 2}m"


def _double_memory(memory_str: str) -> str:
    value_mi = _memory_to_mi(memory_str)
    return f"{value_mi * 2}Mi"
