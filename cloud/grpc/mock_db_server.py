# mock_db_server.py
# A fake version of Yahavi's DB server for testing
# Run this when you want to test your server without Yahavi's real server
# This pretends to be her server and returns success responses

import grpc
import time
from concurrent import futures
import dbmanager_pb2
import dbmanager_pb2_grpc


class MockDbManager(dbmanager_pb2_grpc.DbmanagerServicer):

    def CreateTenantSchema(self, request, context):
        print(f"[MOCK DB] CreateTenantSchema called for tenant {request.tenant_id}")
        print(f"[MOCK DB] Schema: {request.schema_name}, Node: {request.node}")
        # Pretend it worked
        role = f"{request.schema_name}_role"
        return dbmanager_pb2.CreateSchemaResponse(
            status="success",
            schema_name=request.schema_name,
            message=f"Schema {request.schema_name} created successfully",
            db_username=role,
            db_password="MOCK-DO-NOT-USE-IN-PRODUCTION",
        )

    def DeleteTenantSchema(self, request, context):
        print(f"[MOCK DB] DeleteTenantSchema called for tenant {request.tenant_id}")
        return dbmanager_pb2.DeleteSchemaResponse(
            status="TERMINATED",
            schema_name=request.schema_name,
            message=f"Schema {request.schema_name} deleted"
        )

    def GetTelemetry(self, request, context):
        return dbmanager_pb2.TelemetryResponse(status="success")

    def GetTenantStats(self, request, context):
        return dbmanager_pb2.TenantStatsResponse(
            status="success",
            schema_name=request.schema_name,
            schema_size="10 MB",
            active_connections=5
        )


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=5))
    dbmanager_pb2_grpc.add_DbmanagerServicer_to_server(MockDbManager(), server)
    server.add_insecure_port("[::]:50051")
    server.start()
    print("[MOCK DB] Yahavi's mock DB server running on port 50051")
    try:
        while True:
            time.sleep(86400)
    except KeyboardInterrupt:
        server.stop(0)


if __name__ == "__main__":
    serve()