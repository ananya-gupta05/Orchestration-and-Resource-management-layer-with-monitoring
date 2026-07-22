#!/bin/bash

# teardown.sh — removes all IntelliDB infrastructure from Kubernetes
# Run from the cloud/ folder: ./scripts/teardown.sh
#
# IMPORTANT: This script intentionally keeps PVCs (your data disks)
# even after deleting everything else.
# This means if you redeploy, your PostgreSQL data will still be there.
# To also delete data, run: kubectl delete pvc --all -n intellidb

echo "========================================"
echo " IntelliDB Infrastructure Teardown"
echo "========================================"
echo ""
echo "WARNING: This will delete all pods and services."
echo "Your data (PVCs) will be kept safe."
echo ""

# Ask for confirmation
# read -p means: print this message and wait for the user to type something
# The typed value gets stored in the variable called "confirm"
read -p "Type 'yes' to continue: " confirm

# Check if they typed exactly "yes"
# [ "$confirm" != "yes" ] means: if confirm is NOT equal to yes
if [ "$confirm" != "yes" ]; then
  echo "Cancelled. Nothing was deleted."
  exit 0
fi

echo ""
echo "Step 1: Removing PgBouncer..."
# --ignore-not-found means: don't error if it doesn't exist
# useful if PgBouncer was never deployed
kubectl delete -f k8s/pgbouncer.yaml --ignore-not-found

echo ""
echo "Step 2: Removing PostgreSQL StatefulSet..."
kubectl delete -f k8s/statefulset.yaml --ignore-not-found

echo ""
echo "Step 3: Removing Services..."
kubectl delete -f k8s/services.yaml --ignore-not-found

echo ""
echo "Step 4: Removing ConfigMap and Secrets..."
kubectl delete -f k8s/configmap.yaml --ignore-not-found
kubectl delete -f k8s/secrets.yaml --ignore-not-found

echo ""
echo "Step 5: Removing Namespace..."
# This is last because deleting namespace would delete everything inside it
# But we want the other steps to be explicit
kubectl delete -f k8s/namespace.yaml --ignore-not-found

echo ""
echo "========================================"
echo " Teardown complete"
echo "========================================"
echo ""
echo "Your data (PVCs) are still on disk."
echo "To delete them too (PERMANENT DATA LOSS):"
echo "  kubectl delete pvc --all -n intellidb"
echo ""
echo "Current cluster state:"
kubectl get all -n intellidb 2>/dev/null || echo "Namespace is gone."