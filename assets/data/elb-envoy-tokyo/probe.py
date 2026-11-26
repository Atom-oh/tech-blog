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


def measure_three_backends(probe, phase):
    backends = ("ec2", "ecs", "eks")
    stamp = str(time.time_ns())
    result = {"phase": phase, "time_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "revision": probe("/revision")}
    if result["revision"]["body"] != phase:
        return result, False
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        def batch(paths):
            return list(pool.map(probe, paths))

        ok = True
        if phase == "matrix-a":
            result["headers"] = {}
            result["local"] = {}
            for backend in backends:
                pairs = [probe(f"/matrix/{mode}/{backend}/{stamp}") for mode in ("control", "rewrite")]
                result["headers"][backend] = pairs
                for item, hop, source in zip(pairs, (["seed"], ["seed", "ingress"]), ("original", "public-api")):
                    body = item["body"]
                    ok &= (item["status"] == 200 and isinstance(body, dict) and body.get("backend") == backend
                           and body.get("path") == "/capture/" + stamp
                           and [v.strip() for v in body.get("hop", "").split(",")] == hop
                           and body.get("source") == source)
                hits = batch([f"/matrix/local/{backend}/{stamp}-{i}" for i in range(16)])
                result["local"][backend] = hits
                ok &= {item["status"] for item in hits} == {200, 429}
                ok &= all((i["status"] == 429 and i["limit"] == "local") or
                          (i["status"] == 200 and i["body"].get("backend") == backend) for i in hits)

            # Send one allowed request to each backend, then revisit all three with the same RLS key.
            allowed = [probe(f"/matrix/global/{name}/{stamp}-allow") for name in backends]
            denied = batch([f"/matrix/global/{name}/{stamp}-{i}" for i in range(4) for name in backends])
            result["global"] = {"first_three": allowed, "after_quota": denied}
            ok &= all(i["status"] == 200 and i["decision"] == "OK" and i["body"].get("backend") == b
                      for b, i in zip(backends, allowed))
            ok &= all(i["status"] == 429 and i["decision"] == "OVER_LIMIT" for i in denied)
            result["maintenance"] = probe("/matrix/maintenance")
            ok &= result["maintenance"]["status"] == 503 and result["maintenance"]["body"] == "orders under maintenance"
        hits = batch([f"/matrix/split/{stamp}-{i}" for i in range(120)])
        counts = collections.Counter(i["body"]["backend"] for i in hits if i["status"] == 200)
        result.update(split=hits, counts=dict(counts))
        ok &= set(counts) == set(backends) and sum(counts.values()) == len(hits)
        return result, ok


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("hostname", "key-id", "private-key"):
        parser.add_argument("--" + key, required=True)
    parser.add_argument("--revision-only", action="store_true",
                        help="Read /revision once without consuming rate-limit tokens.")
    parser.add_argument("--expect-revision", help="Check the value returned by /revision.")
    parser.add_argument("--matrix-phase", choices=("matrix-a", "matrix-b"),
                        help="Measure the three-backend experiment while its matching revision is active.")
    parser.add_argument("--hybrid-pool", type=int, choices=(1, 3), metavar="EKS_PODS",
                        help="Measure the DNS-discovered pool and verify the number of distinct EKS pods.")
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
                    **({"decision": response.headers.get("x-lab-rls")} if args.matrix_phase else {}),
                    "limit": response.headers.get("x-lab-limit"), "cache": response.headers.get("x-cache")}

    if args.hybrid_pool:
        revision = probe("/revision")
        if revision["body"] != "hybrid-dns":
            print(json.dumps(revision))
            return 1
        stamp = str(time.time_ns())
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            hits = list(pool.map(probe, [f"/hybrid/{stamp}-{i}" for i in range(120)]))
        counts = collections.Counter(i["body"]["backend"] for i in hits if i["status"] == 200)
        pods = {i["body"]["instance"] for i in hits if i["status"] == 200 and i["body"]["backend"] == "eks"}
        print(json.dumps({"time_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                          "revision": revision["body"], "eks_pods": sorted(pods), "counts": dict(counts),
                          "requests": hits}, indent=2))
        return 0 if set(counts) == {"ecs", "eks"} and sum(counts.values()) == 120 and len(pods) == args.hybrid_pool else 1

    if args.matrix_phase:
        result, ok = measure_three_backends(probe, args.matrix_phase)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if ok else 1

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
