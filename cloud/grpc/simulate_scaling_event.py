# simulate_scaling_event.py
# Simulates Saksham sending a scaling event to your consumer
# Run this while server.py is running to test scaling behavior
#
# Usage:
#   python simulate_scaling_event.py up    → simulate scale up
#   python simulate_scaling_event.py down  → simulate scale down

import sys
import grpc
import json

# We call a special test endpoint on your gRPC server
# OR we import the consumer directly and call handle_event()
# Direct import is simpler for local testing

from scaling_consumer import ScalingConsumer

# Make sure k8s config is loaded
from kubernetes import config as k8s_config
try:
    k8s_config.load_incluster_config()
except:
    k8s_config.load_kube_config()

consumer = ScalingConsumer()
consumer.start()

# Show current resources before scaling
print("Current resources before scaling:")
current = consumer.get_current_resources()
print(json.dumps(current, indent=2))
print()

# Determine which event to simulate
direction = sys.argv[1] if len(sys.argv) > 1 else "up"

if direction == "up":
    event = {
        "event": "autoscale.trigger",
        "reason": "cpu_high",
        "current_cpu_percent": 87,
        "action": "scale_up",
        "suggested_cpu_request": "1500m",
        "suggested_memory_request": "1Gi"
    }
    print("Simulating scale UP event (CPU high)...")

elif direction == "down":
    event = {
        "event": "autoscale.trigger",
        "reason": "cpu_low",
        "current_cpu_percent": 12,
        "action": "scale_down",
        "suggested_cpu_request": "500m",
        "suggested_memory_request": "512Mi"
    }
    print("Simulating scale DOWN event (CPU low)...")

else:
    print(f"Unknown direction: {direction}. Use 'up' or 'down'")
    sys.exit(1)

print(f"Event: {json.dumps(event, indent=2)}")
print()

# Send the event to the consumer
consumer.handle_event(event)

print()
print("Check kubectl to see StatefulSet rolling restart:")
print(f"  kubectl get pods -n intellidb -w")
print(f"  kubectl describe statefulset postgres -n intellidb | grep -A5 Resources")
