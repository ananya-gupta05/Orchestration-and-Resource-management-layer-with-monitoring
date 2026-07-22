# server.py
# Cloud Resource Manager — gRPC server
#
# This server receives calls from Sanvi's backend and:
#   ProvisionTenant:  checks shared cluster health, calls Yahavi to create schema
#   TerminateTenant:  calls Yahavi to drop schema, leaves K8s infrastructure alone
#   GetInfraStatus:   returns current shared cluster health
#
# The shared PostgreSQL cluster is NOT created or modified by this server.
# It was provisioned once by deploy.sh and stays running permanently.
from telemetry import InfrastructureTelemetryPublisher
from scaling_consumer import ScalingConsumer
import grpc
import time
import logging
from concurrent import futures
import os
import cloud_pb2
import cloud_pb2_grpc

# Import our two gRPC client functions for calling Yahavi's DB layer
from db_client import create_tenant_schema, delete_tenant_schema

# Import our two monitoring functions for the shared cluster
from provisioner import check_shared_cluster_capacity, get_cluster_status, get_patroni_status

# Prometheus metrics HTTP server — exposes /metrics on a separate port
# so Prometheus can scrape without touching the gRPC port (50052)
from prometheus_client import start_http_server
from metrics import PROVISION_REQUESTS, PROVISION_LATENCY, TERMINATE_REQUESTS

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ── Shared cluster endpoint ───────────────────────────────────────────────────
# This is the same for every tenant — they all connect to the same PostgreSQL
# The endpoint is the Kubernetes Service DNS name
# Format: service-name.namespace.svc.cluster.local
# This resolves to the ClusterIP of postgres-primary Service
# which Patroni keeps pointed at whichever pod is currently the primary

SHARED_PRIMARY_ENDPOINT = os.environ.get(
    "SHARED_PRIMARY_ENDPOINT",
    "postgres-primary.intellidb.svc.cluster.local"
)

SHARED_PGBOUNCER_ENDPOINT = os.environ.get(
    "SHARED_PGBOUNCER_ENDPOINT",
    "pgbouncer-service.intellidb.svc.cluster.local"
)

# Clients must connect through PgBouncer.
SHARED_PORT = int(
    os.environ.get(
        "SHARED_PGBOUNCER_PORT",
        "6432"
    )
)


class CloudResourceManagerServicer(cloud_pb2_grpc.CloudResourceManagerServicer):
    def __init__(self, telemetry_publisher):
        self.telemetry_publisher = telemetry_publisher

    def ProvisionTenant(self, request, context):
        """
        Called by Sanvi's backend when a new tenant signs up.

        What this does:
        1. Check shared cluster is healthy before doing anything
        2. Call Yahavi to create schema + role inside shared PostgreSQL
        3. Return the shared cluster endpoint

        What this does NOT do:
        - Create Kubernetes namespaces
        - Create StatefulSets
        - Create Services or PVCs
        - Spin up new Patroni clusters
        Those things only happen if a tenant needs isolation (future feature).
        """

        logger.info(
            f"ProvisionTenant: tenant_id={request.tenant_id} "
            f"schema={request.schema_name}"
        )

        # Track end-to-end latency of the entire provision operation.
        # histogram_quantile(0.99, ...) in Grafana shows P99 latency.
        with PROVISION_LATENCY.time():

            # ── Step 1: Check shared cluster health ───────────────────────────
            capacity = check_shared_cluster_capacity()

            if not capacity["has_capacity"]:
                logger.error(
                    f"Shared cluster not ready: {capacity['message']}"
                )
                # Label "failed_capacity" distinguishes cluster-level failures
                # from DB-layer failures — important for debugging which part broke.
                PROVISION_REQUESTS.labels(status="failed_capacity").inc()
                return cloud_pb2.ProvisionResponse(
                    status="FAILED",
                    error_message=f"Shared cluster not ready: {capacity['message']}"
                )

            logger.info("Shared cluster is healthy, proceeding with schema creation")

            # ── Step 2: Call Yahavi's DB layer to create the schema ───────────
            cluster_status = get_cluster_status()

            db_result = create_tenant_schema(
                tenant_id=request.tenant_id,
                schema_name=request.schema_name,
                user_name=request.user_name,
                company=request.company,
                node=cluster_status.get("primary_node", "unknown"),
                storage=f"{request.storage_gb} GB",
                ram="shared",
                conn_limit=request.conn_limit
            )

            if db_result["status"] != "success":
                logger.error(
                    f"Schema creation failed: {db_result['message']}"
                )
                # Label "failed_db" means the cluster was healthy but Yahavi's
                # schema creation failed — a different failure mode entirely.
                PROVISION_REQUESTS.labels(status="failed_db").inc()
                return cloud_pb2.ProvisionResponse(
                    status="FAILED",
                    error_message=db_result["message"]
                )

            PROVISION_REQUESTS.labels(status="success").inc()
            logger.info(
                f"Schema {request.schema_name} created successfully "
                f"for tenant {request.tenant_id}"
            )

            # ── Step 3: Return the shared cluster endpoint ────────────────────
            return cloud_pb2.ProvisionResponse(
                status="READY",
                endpoint=SHARED_PRIMARY_ENDPOINT,
                port=SHARED_PORT,
                pgbouncer_endpoint=SHARED_PGBOUNCER_ENDPOINT,
                node_primary=cluster_status.get("primary_node", "unknown"),
                node_replica=cluster_status.get("replica_node", "unknown"),
                error_message="",
                db_username=db_result.get("db_username", ""),
                db_password=db_result.get("db_password", ""),
            )

    def TerminateTenant(self, request, context):
        """
        Called by Sanvi's backend when a tenant deletes their account.
        """

        logger.info(
            f"TerminateTenant: tenant_id={request.tenant_id} "
            f"schema={request.schema_name}"
        )

        db_result = delete_tenant_schema(
            tenant_id=request.tenant_id,
            schema_name=request.schema_name
        )

        if db_result["status"] != "TERMINATED":
            logger.error(
                f"Schema deletion failed: {db_result['message']}"
            )
            TERMINATE_REQUESTS.labels(status="failed").inc()
            return cloud_pb2.TerminateResponse(
                status="FAILED",
                error_message=db_result["message"]
            )

        TERMINATE_REQUESTS.labels(status="success").inc()
        logger.info(f"Tenant {request.tenant_id} terminated successfully")
        return cloud_pb2.TerminateResponse(
            status="TERMINATED",
            error_message=""
        )

    def GetInfraStatus(self, request, context):
        """
        Called by Sanvi's backend to check infrastructure health.
        Used by the frontend dashboard to show instance status.
        """

        logger.info(f"GetInfraStatus: tenant_id={request.tenant_id}")

        status = get_cluster_status()
        patroni = get_patroni_status()

        if patroni["patroni_healthy"]:
            patroni_role_label = (
                f"Leader ({patroni['leader_pod']})"
            )
        else:
            patroni_role_label = "Unknown"

        return cloud_pb2.StatusResponse(
            primary_pod=patroni["leader_pod"],
            replica_pod=patroni["replica_pod"],
            primary_status=status["primary_status"],
            replica_status=status["replica_status"],
            pvcs_bound=status["pvcs_bound"],
            patroni_role=patroni_role_label,
            patroni_lag_mb=str(
                round(
                    patroni["lag_bytes"] / (1024 * 1024),
                    2
                )
            )
        )

    def GetClusterMetrics(self, request, context):
        metrics = self.telemetry_publisher.latest_metrics
        logger.info(f"RPC metrics snapshot: {metrics}")
        return cloud_pb2.GetClusterMetricsResponse(
            cluster_cpu_usage_percent=metrics.get(
                "cluster_cpu_usage_percent", 0.0
            ),
            cluster_ram_usage_percent=metrics.get(
                "cluster_ram_usage_percent", 0.0
            ),
            cluster_storage_used_gb=metrics.get(
                "cluster_storage_used_gb", 0.0
            ),
            cluster_bandwidth_mb=metrics.get(
                "cluster_bandwidth_mb", 0.0
            ),
            cluster_io_throughput=metrics.get(
                "cluster_io_throughput", 0.0
            )
        )


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    telemetry_publisher = InfrastructureTelemetryPublisher()
    try:
        telemetry_publisher.latest_metrics = (
            telemetry_publisher._collect_metrics()
        )
        logger.info("Initial telemetry snapshot collected successfully")
    except Exception as e:
        logger.warning(f"Initial telemetry collection failed: {e}")

    cloud_pb2_grpc.add_CloudResourceManagerServicer_to_server(
        CloudResourceManagerServicer(telemetry_publisher), server
    )

    port = "50052"
    server.add_insecure_port(f"[::]:{port}")

    # Start Prometheus HTTP metrics endpoint on a separate port.
    # Prometheus scrapes http://cloud-grpc:9090/metrics every 15s.
    # This is completely independent of the gRPC server on port 50052.
    metrics_port = int(os.environ.get("METRICS_PORT", "9090"))
    start_http_server(metrics_port)
    logger.info(f"Prometheus metrics endpoint started on :{metrics_port}/metrics")

    telemetry_publisher.start()
    scaling_consumer = ScalingConsumer()
    scaling_consumer.start()
    server.start()
    logger.info(f"Cloud Resource Manager gRPC server started on port {port}")
    logger.info("Waiting for requests...")

    try:
        while True:
            time.sleep(86400)
    except KeyboardInterrupt:
        telemetry_publisher.stop()
        scaling_consumer.stop()
        server.stop(0)


if __name__ == "__main__":
    serve()
