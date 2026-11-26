"""Exercise the signed CloudFront distribution; output only paths, never signed URLs."""
import argparse
import base64
import collections
import concurrent.futures
import datetime
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("hostname", "key-id", "private-key"):
        parser.add_argument("--" + key, required=True)
    parser.add_argument("--revision-only", action="store_true",
                        help="Read /revision once without consuming rate-limit tokens.")
    parser.add_argument("--expect-revision", help="Check the value returned by /revision.")
    args = parser.parse_args()
    key = serialization.load_pem_private_key(Path(args.private_key).read_bytes(), password=None)
    expires = int(time.time()) + 600

    def probe(path, signed=True):
        url = "https://" + args.hostname + path
        if signed:
            statement = {"Statement": [{"Resource": url, "Condition": {"DateLessThan": {"AWS:EpochTime": expires}}}]}
            policy = json.dumps(statement, separators=(",", ":")).encode()
            signature = key.sign(policy, padding.PKCS1v15(), hashes.SHA1())
            encoded = base64.b64encode(signature).decode().translate(str.maketrans("+=/", "-_~"))
            url += f"?Expires={expires}&Signature={encoded}&Key-Pair-Id={args.key_id}"
        try:
            response = urllib.request.urlopen(url, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            body = response.read(2048).decode(errors="replace")
            try:
                body = json.loads(body)
            except ValueError:
                body = body[:120]
            return {"path": path, "status": response.status, "body": body,
                    "limit": response.headers.get("x-lab-limit"), "cache": response.headers.get("x-cache")}

    def revision_matches(item):
        return item["status"] == 200 and (args.expect_revision is None or item["body"] == args.expect_revision)

    if args.revision_only:
        result = probe("/revision")
        print(json.dumps({"time_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), **result}))
        return 0 if revision_matches(result) else 1

    paths = ["/eks/probe", "/ec2/probe", "/ecs/probe", "/revision", "/unmatched"]
    result = {"time_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "unsigned": probe("/readyz", False),
              "routes": [probe(path) for path in paths]}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        result["limited"] = list(executor.map(probe, [f"/limited/{i}" for i in range(16)]))
        result["split"] = list(executor.map(probe, [f"/split/{i}" for i in range(50)]))
    counters = collections.Counter(item["body"].get("backend") for item in result["split"]
                                   if item["status"] == 200 and isinstance(item["body"], dict))
    result["split_counts"] = dict(counters)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    ok = result["unsigned"]["status"] == 403
    for backend, item in zip(("eks", "ec2", "ecs"), result["routes"][:3]):
        ok &= item["status"] == 200 and item["body"] == {"backend": backend, "path": "/probe"}
    ok &= revision_matches(result["routes"][3]) and result["routes"][4]["status"] == 404
    ok &= any(x["status"] == 200 for x in result["limited"])
    ok &= any(x["status"] == 429 and x["limit"] == "local" for x in result["limited"])
    ok &= all(x["status"] == 200 or (x["status"] == 429 and x["limit"] == "local")
              for x in result["limited"])
    ok &= set(counters) == {"ec2", "ecs"} and sum(counters.values()) == len(result["split"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
