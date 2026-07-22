# Cloud Resource Manager — IntelliDB DBaaS

This folder contains the Kubernetes infrastructure layer for the IntelliDB DBaaS platform.

## What this layer owns

- PostgreSQL StatefulSet (primary + replica pods on separate nodes)
- PersistentVolumeClaims for data storage that survives pod crashes
- Kubernetes Services for write and read routing
- PgBouncer connection pooler
- Anti-affinity rules ensuring primary and replica never run on the same node
- Deploy, teardown, validation, and failover test scripts

## How it fits in the overall architecture

Tenant connections arrive at PgBouncer on port 6432. PgBouncer maintains a fixed
pool of real PostgreSQL connections and multiplexes tenant connections through them.
Write queries route through postgres-primary-service to whichever pod holds the
primary role. Read queries can optionally route through postgres-replica-service
to the replica pod. Primary and replica pods are always on separate physical nodes,
enforced by Kubernetes anti-affinity rules.

## How to run locally

Requirements: minikube, kubectl

Start a 2-node cluster:
    minikube start --nodes 2 --memory 4096 --cpus 2

Deploy everything:
    cd cloud
    ./scripts/deploy.sh

Validate all components are healthy:
    ./scripts/validate.sh

Run the failover/persistence test:
    ./scripts/test-failover.sh

Tear everything down (keeps data):
    ./scripts/teardown.sh

## Folder structure

    cloud/
    ├── k8s/
    │   ├── namespace.yaml       Namespace isolating all IntelliDB objects
    │   ├── secrets.yaml         PostgreSQL credentials (template values in repo)
    │   ├── configmap.yaml       Non-sensitive PostgreSQL configuration
    │   ├── services.yaml        Headless, primary, and replica services
    │   ├── statefulset.yaml     PostgreSQL primary + replica StatefulSet
    │   └── pgbouncer.yaml       PgBouncer connection pooler
    ├── scripts/
    │   ├── deploy.sh            Deploy all infrastructure in correct order
    │   ├── teardown.sh          Remove all infrastructure cleanly
    │   ├── validate.sh          Health check all components
    │   └── test-failover.sh     Prove persistence survives pod crashes
    └── grpc/                    gRPC provisioning server (next milestone)

## Key design decisions

**StatefulSet not Deployment for PostgreSQL**
PostgreSQL needs stable pod names and stable storage. StatefulSet gives each pod
a permanent name (postgres-0, postgres-1) and automatically creates a separate
PVC per pod. If postgres-0 crashes and restarts, it gets the same PVC remounted
with all data intact.

**Anti-affinity**
If primary and replica ran on the same node, a single node failure would kill both
simultaneously with no way to recover without a full restore. Anti-affinity forces
them onto separate nodes so one node failure only affects one pod.

**PgBouncer as separate Deployment**
PgBouncer is stateless — it has no data to persist. Running it as a separate
Deployment means PostgreSQL pods can restart independently without dropping
all client connections simultaneously.

**Asynchronous replication (Phase 1)**
The primary does not wait for the replica to confirm WAL records before
acknowledging writes to clients. This keeps write latency low at the cost of
a small risk of data loss if the primary crashes between writing and the replica
receiving the WAL. Acceptable for Phase 1.

## Current phase status

- Phase 1 complete: PostgreSQL StatefulSet with persistence, anti-affinity, PgBouncer
- Phase 2 planned: Patroni for automated HA failover and leader election
- Phase 3 planned: gRPC provisioning server for backend integration