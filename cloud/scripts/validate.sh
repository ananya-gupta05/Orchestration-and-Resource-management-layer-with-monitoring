#!/bin/bash

# validate.sh — checks that all IntelliDB infrastructure is healthy
# Run from the cloud/ folder: ./scripts/validate.sh
# Green checkmarks = good. Red X = something is wrong.

echo "========================================"
echo " IntelliDB Infrastructure Validation"
echo "========================================"

# We track pass and fail counts
# Each check adds 1 to PASS or FAIL
PASS=0
FAIL=0

echo ""
echo "--- Checking pods ---"

# kubectl get pod postgres-0 -n intellidb
#   gets info about pod postgres-0 in namespace intellidb
# -o jsonpath='{...}'
#   extracts a specific field from the output using a path expression
#   think of it like asking "give me just this one field"
# .status.conditions[?(@.type=="Ready")].status
#   finds the condition where type is "Ready" and gets its status value
#   the value will be "True" or "False"
# 2>/dev/null
#   hides error messages (like "pod not found") from showing on screen
#   we handle those cases ourselves below

PG0_STATUS=$(kubectl get pod postgres-0 -n intellidb \
  -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)

if [ "$PG0_STATUS" == "True" ]; then
  echo "✅ postgres-0 is Ready"
  PASS=$((PASS+1))
else
  echo "❌ postgres-0 is NOT Ready (status: $PG0_STATUS)"
  FAIL=$((FAIL+1))
fi

PG1_STATUS=$(kubectl get pod postgres-1 -n intellidb \
  -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)

if [ "$PG1_STATUS" == "True" ]; then
  echo "✅ postgres-1 is Ready"
  PASS=$((PASS+1))
else
  echo "❌ postgres-1 is NOT Ready (status: $PG1_STATUS)"
  FAIL=$((FAIL+1))
fi

echo ""
echo "--- Checking anti-affinity (pods on different nodes) ---"

# Get the node name each pod is running on
NODE0=$(kubectl get pod postgres-0 -n intellidb \
  -o jsonpath='{.spec.nodeName}' 2>/dev/null)
NODE1=$(kubectl get pod postgres-1 -n intellidb \
  -o jsonpath='{.spec.nodeName}' 2>/dev/null)

# Check three things:
# 1. NODE0 and NODE1 are not equal (different nodes)
# 2. NODE0 is not empty (pod exists)
# 3. NODE1 is not empty (pod exists)
if [ "$NODE0" != "$NODE1" ] && [ -n "$NODE0" ] && [ -n "$NODE1" ]; then
  echo "✅ Pods are on different nodes"
  echo "   postgres-0 → $NODE0"
  echo "   postgres-1 → $NODE1"
  PASS=$((PASS+1))
else
  echo "❌ Pods are on the SAME node or node info is missing"
  echo "   postgres-0 → $NODE0"
  echo "   postgres-1 → $NODE1"
  FAIL=$((FAIL+1))
fi

echo ""
echo "--- Checking storage (PVCs) ---"

PVC0=$(kubectl get pvc postgres-data-postgres-0 -n intellidb \
  -o jsonpath='{.status.phase}' 2>/dev/null)

if [ "$PVC0" == "Bound" ]; then
  echo "✅ postgres-data-postgres-0 is Bound"
  PASS=$((PASS+1))
else
  echo "❌ postgres-data-postgres-0 is NOT Bound (status: $PVC0)"
  FAIL=$((FAIL+1))
fi

PVC1=$(kubectl get pvc postgres-data-postgres-1 -n intellidb \
  -o jsonpath='{.status.phase}' 2>/dev/null)

if [ "$PVC1" == "Bound" ]; then
  echo "✅ postgres-data-postgres-1 is Bound"
  PASS=$((PASS+1))
else
  echo "❌ postgres-data-postgres-1 is NOT Bound (status: $PVC1)"
  FAIL=$((FAIL+1))
fi

echo ""
echo "--- Checking services ---"

PRIMARY_IP=$(kubectl get service postgres-primary -n intellidb \
  -o jsonpath='{.spec.clusterIP}' 2>/dev/null)

if [ -n "$PRIMARY_IP" ]; then
  echo "✅ postgres-primary service exists ($PRIMARY_IP)"
  PASS=$((PASS+1))
else
  echo "❌ postgres-primary service is missing"
  FAIL=$((FAIL+1))
fi

HEADLESS=$(kubectl get service postgres-headless -n intellidb \
  -o jsonpath='{.spec.clusterIP}' 2>/dev/null)

if [ "$HEADLESS" == "None" ]; then
  echo "✅ postgres-headless service exists (headless)"
  PASS=$((PASS+1))
else
  echo "❌ postgres-headless service is missing"
  FAIL=$((FAIL+1))
fi

echo ""
echo "--- Checking PgBouncer ---"

PGB_STATUS=$(kubectl get pod -l app=pgbouncer -n intellidb \
  -o jsonpath='{.items[0].status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)

if [ "$PGB_STATUS" == "True" ]; then
  echo "✅ PgBouncer pod is Ready"
  PASS=$((PASS+1))
else
  echo "❌ PgBouncer pod is NOT Ready (not deployed yet or still starting)"
  FAIL=$((FAIL+1))
fi
echo ""
echo "--- Checking Patroni ---"

PATRONI0=$(kubectl exec postgres-0 -n intellidb -- \
  curl -s http://localhost:8008/patroni 2>/dev/null)

PATRONI1=$(kubectl exec postgres-1 -n intellidb -- \
  curl -s http://localhost:8008/patroni 2>/dev/null)

if [ -n "$PATRONI0" ] && [ -n "$PATRONI1" ]; then
    echo "✅ Patroni API responding on both pods"
    PASS=$((PASS+1))
else
    echo "❌ Patroni API not responding"
    FAIL=$((FAIL+1))
fi

if echo "$PATRONI0" | grep -q '"role": "master"' \
   && echo "$PATRONI1" | grep -q '"role": "replica"'; then

    echo "✅ Patroni roles are healthy"
    echo "   postgres-0 → master"
    echo "   postgres-1 → replica"
    PASS=$((PASS+1))

elif echo "$PATRONI1" | grep -q '"role": "master"' \
     && echo "$PATRONI0" | grep -q '"role": "replica"'; then

    echo "✅ Patroni roles are healthy"
    echo "   postgres-1 → master"
    echo "   postgres-0 → replica"
    PASS=$((PASS+1))

else
    echo "❌ Patroni role configuration invalid"
    FAIL=$((FAIL+1))
fi
echo ""
echo "========================================"
if [ $FAIL -eq 0 ]; then
  echo " ✅ All $PASS checks passed"
else
  echo " Results: $PASS passed, $FAIL failed"
fi
echo "========================================"