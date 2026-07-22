import grpc
import cloud_pb2
import cloud_pb2_grpc
import time

channel = grpc.insecure_channel("localhost:50052")
stub = cloud_pb2_grpc.CloudResourceManagerStub(channel)

# -----------------------------
# PROVISION TEST
# -----------------------------
print("Testing ProvisionTenant...")
print("(This will take 1-2 minutes)")
print("")

response = stub.ProvisionTenant(
    cloud_pb2.ProvisionRequest(
        tenant_id=1001,
        schema_name="tenant_1001",
        user_name="alice",
        company="XXYY",
        tier="standard",
        storage_gb=1,
        conn_limit=50
    )
)

print("=== PROVISION RESPONSE ===")
print(f"Status:        {response.status}")
print(f"Endpoint:      {response.endpoint}")
print(f"Node primary:  {response.node_primary}")
print(f"Node replica:  {response.node_replica}")
print(f"Error:         {response.error_message}")
assert response.error_message == "", (
    f"Unexpected provision error: {response.error_message}"
)
print("")
assert response.status == "READY", (
    f"Expected READY, got {response.status}"
)

assert response.endpoint != "", (
    "Endpoint should not be empty"
)

assert response.node_primary != "", (
    "Primary node should not be empty"
)

assert response.node_replica != "", (
    "Replica node should not be empty"
)

print("✅ Provision assertions passed")
print("")
# -----------------------------
# TERMINATION TEST
# -----------------------------
print("Waiting 10 seconds before termination...")
time.sleep(10)

print("")
print("Testing TerminateTenant...")
print("")

terminate_response = stub.TerminateTenant(
    cloud_pb2.TerminateRequest(
        tenant_id=1001,
        schema_name="tenant_1001"
    )
)

print("=== TERMINATION RESPONSE ===")
print(f"Status: {terminate_response.status}")
print(f"Error:  {terminate_response.error_message}")
assert terminate_response.error_message == "", (
    f"Unexpected termination error: {terminate_response.error_message}"
)
assert terminate_response.status == "TERMINATED", (
    f"Expected TERMINATED, got {terminate_response.status}"
)

print("✅ Termination assertions passed")