#!/bin/bash

# test-failover.sh
# Demonstrates that PostgreSQL data survives a pod crash
# This tests Kubernetes-level self-healing via PVC persistence
# Not a Patroni failover test — that comes when Patroni is added
#
# What this proves:
# When a pod crashes, Kubernetes recreates it
# The new pod remounts the same PVC (same disk)
# Data written before the crash is still there after restart

echo "========================================"
echo " IntelliDB Failover / Persistence Test"
echo "========================================"

# ── Step 1: Check postgres-0 is running before we start ──────────────────

echo ""
echo "Step 1: Verifying postgres-0 is running..."

# -o jsonpath extracts just the Ready status from the pod's conditions
# We've seen this pattern in validate.sh already
STATUS=$(kubectl get pod postgres-0 -n intellidb \
  -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)

if [ "$STATUS" != "True" ]; then
  echo "❌ postgres-0 is not running. Deploy the cluster first."
  echo "   Run: ./scripts/deploy.sh"
  exit 1
fi

echo "✅ postgres-0 is running"

# ── Step 2: Write test data into the database ─────────────────────────────

echo ""
echo "Step 2: Writing test data into postgres-0..."

# kubectl exec -it pod-name -n namespace -- command
# runs a command inside a running container
# -- separates kubectl arguments from the command being run inside
# psql -U postgres -d intellidb -c "SQL" runs a single SQL command and exits

kubectl exec -it postgres-0 -n intellidb -- \
  psql -U postgres -d intellidb -c \
  "CREATE TABLE IF NOT EXISTS failover_test (id INT, written_at TEXT);"

# IF NOT EXISTS means: if this table already exists from a previous run, don't error
# just continue — this makes the script safe to run multiple times

kubectl exec -it postgres-0 -n intellidb -- \
  psql -U postgres -d intellidb -c \
  "INSERT INTO failover_test VALUES (1, '$(date)');"

# $(date) runs the date command and inserts the current timestamp
# This lets you see exactly when the data was written

echo ""
echo "Data written. Verifying it exists before crash..."

kubectl exec -it postgres-0 -n intellidb -- \
  psql -U postgres -d intellidb -c \
  "SELECT * FROM failover_test;"

# ── Step 3: Kill the pod ──────────────────────────────────────────────────

echo ""
echo "Step 3: Killing postgres-0 (simulating a crash)..."
echo "Watch what happens — Kubernetes will recreate it automatically"
echo ""

kubectl delete pod postgres-0 -n intellidb

# kubectl delete pod does NOT delete the StatefulSet or the PVC
# It only kills the running pod
# The StatefulSet controller immediately notices one of its pods is missing
# and creates a new postgres-0 pod, remounting the same PVC

# ── Step 4: Wait for it to come back ─────────────────────────────────────

echo ""
echo "Step 4: Waiting for postgres-0 to come back..."
echo "(This usually takes 20-40 seconds)"
echo ""

# kubectl wait pauses the script until the condition is met
# --for=condition=ready means wait until the pod passes its readiness probe
# --timeout=120s means give up after 2 minutes if it hasn't come back
kubectl wait --for=condition=ready pod/postgres-0 \
  -n intellidb --timeout=120s

echo ""
echo "✅ postgres-0 is back"

# ── Step 5: Verify data survived ─────────────────────────────────────────

echo ""
echo "Step 5: Checking if data survived the crash..."
echo ""

kubectl exec -it postgres-0 -n intellidb -- \
  psql -U postgres -d intellidb -c \
  "SELECT * FROM failover_test;"

echo ""
echo "========================================"
echo " If you can see your data above — "
echo " persistence is working correctly."
echo " The pod crashed. The disk survived."
echo "========================================"