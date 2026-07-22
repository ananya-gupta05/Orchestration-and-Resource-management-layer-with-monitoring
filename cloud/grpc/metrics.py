# metrics.py
# Prometheus metric definitions for the Cloud Resource Manager.
# Import from server.py, telemetry.py, and scaling_consumer.py.
# All metric names are prefixed with "intellidb_" to avoid collisions.

from prometheus_client import Counter, Histogram, Gauge

# ── Provisioning ──────────────────────────────────────────────────────────────

PROVISION_REQUESTS = Counter(
    "intellidb_provision_requests_total",
    "Total ProvisionTenant gRPC calls",
    ["status"],  # "success", "failed_capacity", "failed_db"
)

PROVISION_LATENCY = Histogram(
    "intellidb_provision_duration_seconds",
    "End-to-end ProvisionTenant latency in seconds",
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)

TERMINATE_REQUESTS = Counter(
    "intellidb_terminate_requests_total",
    "Total TerminateTenant gRPC calls",
    ["status"],  # "success", "failed"
)

# ── Cluster Health ─────────────────────────────────────────────────────────────

CLUSTER_CPU = Gauge(
    "intellidb_cluster_cpu_usage_percent",
    "PostgreSQL cluster CPU usage percent (from metrics-server; -1 if unavailable)",
)

CLUSTER_RAM = Gauge(
    "intellidb_cluster_ram_usage_percent",
    "PostgreSQL cluster RAM usage percent (from metrics-server; -1 if unavailable)",
)

CLUSTER_STORAGE_GB = Gauge(
    "intellidb_cluster_storage_used_gb",
    "PostgreSQL pgdata directory usage in GB",
)

CLUSTER_BANDWIDTH_MB = Gauge(
    "intellidb_cluster_bandwidth_mb_per_sec",
    "Network bandwidth across PostgreSQL + PgBouncer pods, MB/s (rolling delta)",
)

CLUSTER_IO_THROUGHPUT = Gauge(
    "intellidb_cluster_io_throughput_mb_per_sec",
    "IO throughput across PostgreSQL pods, MB/s (rolling delta)",
)

PATRONI_HEALTHY = Gauge(
    "intellidb_patroni_healthy",
    "1 if Patroni leader election is healthy, 0 otherwise",
)

PATRONI_LAG_MB = Gauge(
    "intellidb_patroni_replication_lag_mb",
    "Replication lag from primary to replica in MB",
)

CLUSTER_PODS_READY = Gauge(
    "intellidb_cluster_pods_ready",
    "Number of PostgreSQL pods in Running+Ready state (max 2)",
)

# ── Telemetry Collection ───────────────────────────────────────────────────────

TELEMETRY_SCRAPE_ERRORS = Counter(
    "intellidb_telemetry_scrape_errors_total",
    "Telemetry metric categories that failed to collect",
    ["category"],  # "cpu_ram", "storage", "bandwidth_io", "patroni"
)

TELEMETRY_SCRAPE_DURATION = Histogram(
    "intellidb_telemetry_scrape_duration_seconds",
    "Time taken for one full telemetry collection cycle",
    buckets=[1.0, 2.0, 5.0, 10.0, 30.0],
)

# ── Scaling ────────────────────────────────────────────────────────────────────

SCALING_EVENTS = Counter(
    "intellidb_scaling_events_total",
    "Scaling events processed by ScalingConsumer",
    ["action", "outcome"],
    # action:  "scale_up" | "scale_down"
    # outcome: "applied" | "skipped" | "clamped" | "rejected"
)
