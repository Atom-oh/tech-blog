"""Run on the lab EC2 instance via SSM to create/read one private EKS Fargate pod."""
import argparse
import base64
import json
import ssl
import subprocess
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("region", "cluster", "endpoint", "ca", "image"):
        parser.add_argument("--" + key, required=True)
    args = parser.parse_args()
    output = subprocess.check_output(["aws", "eks", "get-token", "--region", args.region,
                                     "--cluster-name", args.cluster, "--output", "json"])
    token = json.loads(output)["status"]["token"]
    context = ssl.create_default_context(cadata=base64.b64decode(args.ca).decode())
    pods = args.endpoint + "/api/v1/namespaces/default/pods"
    spec = {"apiVersion": "v1", "kind": "Pod", "metadata": {
        "name": "envoy-lab-echo", "labels": {"app": "envoy-lab-echo"}}, "spec": {
        "automountServiceAccountToken": False, "containers": [{
            "name": "echo", "image": args.image, "env": [{"name": "BACKEND", "value": "eks"}],
            "ports": [{"containerPort": 8080}], "readinessProbe": {"httpGet": {"path": "/ready", "port": 8080}},
            "resources": {"requests": {"cpu": "100m", "memory": "64Mi"}}}]}}

    def request(url, body=None):
        request = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                         headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with urllib.request.urlopen(request, context=context, timeout=10) as response:
            return json.load(response)

    try:
        pod = request(pods + "/envoy-lab-echo")
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        pod = request(pods, spec)
    status = pod.get("status", {})
    print(json.dumps({"phase": status.get("phase"), "ip": status.get("podIP"),
                      "ready": any(c["type"] == "Ready" and c["status"] == "True" for c in status.get("conditions", [])),
                      "conditions": status.get("conditions", [])}))


if __name__ == "__main__":
    main()
