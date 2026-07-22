FROM python:3.12-slim

# Install kubectl + curl
# Install kubectl + curl
RUN apt-get update && \
    apt-get install -y curl && \
    ARCH=$(dpkg --print-architecture) && \
    if [ "$ARCH" = "arm64" ]; then K8S_ARCH="arm64"; else K8S_ARCH="amd64"; fi && \
    curl -LO "https://dl.k8s.io/release/v1.35.1/bin/linux/${K8S_ARCH}/kubectl" && \
    install -m 0755 kubectl /usr/local/bin/kubectl && \
    rm kubectl && \
    apt-get clean

WORKDIR /app/cloud/grpc

COPY cloud/grpc/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY proto /app/proto
COPY cloud/grpc /app/cloud/grpc

WORKDIR /app/cloud/grpc

RUN python -m grpc_tools.protoc \
    -I. \
    --python_out=. \
    --grpc_python_out=. \
    cloud.proto \
    dbmanager.proto

ENV DB_MANAGER_ADDRESS=db-manager:50051

EXPOSE 50052

CMD ["python", "server.py"]