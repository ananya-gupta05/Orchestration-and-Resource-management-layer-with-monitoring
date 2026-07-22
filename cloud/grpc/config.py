import os

SHARED_NAMESPACE = os.environ.get(
    "K8S_NAMESPACE",
    "intellidb"
)