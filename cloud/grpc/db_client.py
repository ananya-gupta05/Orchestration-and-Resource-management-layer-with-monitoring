# db_client.py
# This is YOUR layer calling YAHAVI'S database layer
# You call this after infrastructure is provisioned
# and during termination workflows

import os
import grpc
import dbmanager_pb2
import dbmanager_pb2_grpc
import logging

logger = logging.getLogger(__name__)

# Yahavi's gRPC server address
# Local: localhost:50051
# Kubernetes: set DB_MANAGER_ADDRESS environment variable
DB_MANAGER_ADDRESS = os.environ.get(
    "DB_MANAGER_ADDRESS",
    "localhost:50051"
)
# Shared gRPC channel reused for the lifetime of the process.
_channel = None
_stub = None


def _get_stub():
    global _channel, _stub

    if _stub is None:
        _channel = grpc.insecure_channel(DB_MANAGER_ADDRESS)
        _stub = dbmanager_pb2_grpc.DbmanagerStub(_channel)

    return _stub

def create_tenant_schema(
    tenant_id: int,
    schema_name: str,
    user_name: str,
    company: str,
    node: str,
    storage: str,
    ram: str,
    conn_limit: int
) -> dict:
    """
    Call Yahavi's DB layer to create a schema after
    infrastructure is ready.

    Returns dict with status and message.
    """
    try:
        stub = _get_stub()

        logger.info(f"Calling CreateTenantSchema for tenant {tenant_id}")

        request = dbmanager_pb2.CreateSchemaRequest(
            tenant_id=tenant_id,
            schema_name=schema_name,
            user_name=user_name,
            company=company,
            node=node,
            storage=storage,
            ram=ram,
            conn_limit=conn_limit
        )

        response = stub.CreateTenantSchema(request, timeout=10)

        logger.info(
            f"CreateTenantSchema response: "
            f"{response.status} — {response.message}"
        )

        return {
            "status": response.status,
            "schema_name": response.schema_name,
            "message": response.message,
            "db_username": getattr(response, "db_username", ""),
            "db_password": getattr(response, "db_password", "")
        }

    except grpc.RpcError as e:
        logger.error(
            f"gRPC call to DB layer failed: "
            f"{e.code()} — {e.details()}"
        )

        return {
            "status": "error",
            "schema_name": schema_name,
            "message": f"DB layer unreachable: {e.details()}",
            "db_username": "",
            "db_password": ""
        }



def delete_tenant_schema(
    tenant_id: int,
    schema_name: str
) -> dict:
    """
    Call Yahavi's DB layer to delete a schema during
    tenant termination.

    Returns dict with status and message.
    """
    try:
        stub = _get_stub()

        logger.info(
            f"Calling DeleteTenantSchema for tenant {tenant_id}"
        )

        request = dbmanager_pb2.DeleteSchemaRequest(
            tenant_id=tenant_id,
            schema_name=schema_name
        )

        response = stub.DeleteTenantSchema(request,timeout=10)

        logger.info(
            f"DeleteTenantSchema response: "
            f"{response.status} — {response.message}"
        )

        return {
            "status": response.status,
            "schema_name": response.schema_name,
            "message": response.message
        }

    except grpc.RpcError as e:
        logger.error(
            f"gRPC call to DB layer failed: "
            f"{e.code()} — {e.details()}"
        )

        return {
            "status": "error",
            "schema_name": schema_name,
            "message": f"DB layer unreachable: {e.details()}"
        }
