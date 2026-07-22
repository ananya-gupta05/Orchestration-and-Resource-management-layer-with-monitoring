#!/bin/bash
set -e
echo "========================================"
echo " IntelliDB Infrastructure Deploy"
echo "========================================"

echo ""
echo "Step 1: Creating namespace..."
kubectl apply -f k8s/namespace.yaml

echo ""
echo "Step 1b: Creating RBAC permissions for Patroni..."
kubectl apply -f k8s/rbac.yaml

echo ""
echo "Step 2: Creating secrets..."
kubectl apply -f k8s/secrets.yaml

echo ""
echo "Step 3: Creating configmap..."
kubectl apply -f k8s/configmap.yaml

echo ""
echo "Step 4: Creating services..."
kubectl apply -f k8s/services.yaml

echo ""
echo "Step 5: Deploying etcd (required for Patroni)..."
kubectl apply -f k8s/etcd.yaml

echo ""
echo "Step 6: Waiting for etcd to be ready..."
kubectl wait --for=condition=ready pod/etcd-0 -n intellidb --timeout=60s

echo ""
echo "Step 7: Deploying PostgreSQL with Patroni..."
kubectl apply -f k8s/statefulset.yaml

echo ""
echo "Step 8: Waiting for PostgreSQL pods..."
echo "This takes longer with Patroni — up to 3 minutes..."
kubectl wait --for=condition=ready pod/postgres-0 -n intellidb --timeout=180s
kubectl wait --for=condition=ready pod/postgres-1 -n intellidb --timeout=180s

echo ""
echo "Step 9: Deploying PgBouncer..."
kubectl apply -f k8s/pgbouncer.yaml

echo ""
echo "Step 10: Waiting for PgBouncer..."
kubectl wait --for=condition=ready pod -l app=pgbouncer -n intellidb --timeout=60s

echo ""
echo "========================================"
echo " Deploy Complete"
echo "========================================"

echo ""
echo "PODS:"
kubectl get pods -n intellidb -o wide

echo ""
echo "STORAGE:"
kubectl get pvc -n intellidb

echo ""
echo "SERVICES:"
kubectl get services -n intellidb