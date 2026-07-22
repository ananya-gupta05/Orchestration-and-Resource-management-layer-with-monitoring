# Cloud Resource Manager (gRPC Layer)

## Overview

This component is the Cloud Resource Manager for IntelliDB DBaaS.

Responsibilities:

- Manage shared PostgreSQL infrastructure
- Monitor cluster health
- Coordinate with the Database Resource Manager
- Publish infrastructure telemetry
- Apply scaling recommendations
- Expose gRPC APIs for provisioning and infrastructure status

This service does **not** create Kubernetes resources per tenant.

IntelliDB uses a **shared PostgreSQL cluster** with **schema-based multi-tenancy**.

---

## Architecture

### Communication

| Component | Protocol |
|------------|------------|
| Frontend ↔ Backend | REST |
| Backend ↔ Cloud Resource Manager | gRPC |
| Cloud Resource Manager ↔ Database Resource Manager | gRPC |
| Telemetry & Scaling Events | Pub/Sub (simulated) |

### Ports

| Service | Port |
|----------|------|
| Cloud Resource Manager | 50052 |
| Database Resource Manager | 50051 |

---

## gRPC Methods

### ProvisionTenant

Receives provisioning requests from the backend.

Flow:

1. Check shared cluster health
2. Verify infrastructure capacity
3. Call Database Resource Manager
4. Create tenant schema
5. Return connection information

### TerminateTenant

Receives tenant deletion requests.

Flow:

1. Call Database Resource Manager
2. Drop tenant schema
3. Return operation status

### GetInfraStatus

Returns infrastructure information including:

- PostgreSQL pod status
- PVC status
- Node placement
- Patroni leader/replica state
- Cluster health

---

## Shared PostgreSQL Infrastructure

The platform runs a shared PostgreSQL cluster consisting of:

- PostgreSQL 15 (Spilo)
- Patroni
- etcd
- PgBouncer
- Kubernetes StatefulSet

Current deployment:

- postgres-0
- postgres-1

Patroni automatically performs leader election and failover.

---

## Project Files

### server.py

Main gRPC server implementation.

### provisioner.py

Infrastructure monitoring and cluster health checks.

### db_client.py

gRPC client used to communicate with the Database Resource Manager.

### telemetry.py

Background telemetry publisher.

Publishes:

- Pod health
- PVC status
- Node placement
- Patroni state
- Cluster health

### scaling_consumer.py

Receives scaling recommendations and updates StatefulSet resources.

### simulate_scaling_event.py

Testing utility for simulating scaling events.

### mock_db_server.py

Mock Database Resource Manager used during local development.

### test_client.py

Client used for gRPC testing.

---

## Running Locally

### Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### Install Dependencies

```bash
pip install -r requirements.txt
```

### Start Mock Database Server

```bash
python mock_db_server.py
```

### Start Cloud Resource Manager

```bash
python server.py
```

### Run Test Client

```bash
python test_client.py
```

---

## Scaling Tests

Scale Up:

```bash
python simulate_scaling_event.py up
```

Scale Down:

```bash
python simulate_scaling_event.py down
```

Watch rolling restart:

```bash
kubectl get pods -n intellidb -w
```

---

## Infrastructure Validation

Run:

```bash
cd cloud
./scripts/validate.sh
```

Validation checks:

- Pod readiness
- Anti-affinity
- PVC status
- Service availability
- PgBouncer health
- Patroni API health
- Leader/replica state

---

## Current Status

Implemented:

- Shared PostgreSQL cluster
- Patroni HA
- Automated failover
- StatefulSet deployment
- Telemetry publishing
- Scaling consumer
- gRPC provisioning APIs
- Infrastructure validation

Pending integrations:

- Real pub/sub integration
- Real Database Resource Manager endpoint
- Production deployment configuration