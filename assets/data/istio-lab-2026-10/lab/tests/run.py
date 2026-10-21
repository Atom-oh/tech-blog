#!/usr/bin/env python3
"""Istio data-plane measurements on the istio-lab cluster. Each subcommand writes results/<name>-<UTC time>.json.

  latency   steady-state latency per mode (base, sidecar, ambient-l4, ambient-l7), 200 and 1000 req/s, 3 rounds,
            plus proxy CPU (sidecar containers, ztunnel, waypoint) from kubelet stats
  rollout   errors while `kubectl rollout restart deploy/echo` runs 3 times under load, default pod spec vs preStop sleep
  obs       which telemetry each mode produces for the same 200/500/404-path mix (metrics and access logs)
  policy    AuthorizationPolicy with an L7 rule: sidecar selector, ambient-l4 selector (ztunnel), ambient-l7 targetRefs
            (waypoint), and the waypoint bypass via pod IP
  mtls      plaintext from a non-mesh pod under PERMISSIVE and STRICT, and the security policy seen in metrics
  outlier   one bad endpoint out of four: error rate with Istio defaults vs DestinationRule outlierDetection

Needs KUBECONFIG from env.sh. Nothing here deletes the cluster; each test restores what it changed.
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

MODES = ["base", "sidecar", "ambient-l4", "ambient-l7"]
MESH = ["sidecar", "ambient-l4", "ambient-l7"]
RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")
URL = "http://echo:8080/echo"
PCTS = "50,75,90,99,99.9"
RES = "0.0001"  # histogram resolution 100 us (fortio default 1 ms is too coarse for sub-ms gaps)


def sh(cmd, check=True, timeout=900, input=None):
    r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout, input=input)
    if check and r.returncode != 0:
        raise RuntimeError(f"{cmd}\n{r.stderr[-2000:]}")
    return r


def kubectl(*args, check=True, timeout=900, input=None):
    # "Unable to connect" means the request never reached the API server, so a retry cannot run anything twice.
    for attempt in range(3):
        r = sh(["kubectl", *args], check=False, timeout=timeout, input=input)
        if r.returncode == 0 or "Unable to connect" not in r.stderr:
            break
        log(f"kubectl could not reach the API (attempt {attempt + 1}), retrying")
        time.sleep(10)
    if check and r.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)}\n{r.stderr[-2000:]}")
    return r


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", file=sys.stderr, flush=True)


def apply(yaml_text):
    kubectl("apply", "-f", "-", input=yaml_text)


def delete(yaml_text):
    kubectl("delete", "--ignore-not-found", "-f", "-", input=yaml_text)


def save(name, data):
    os.makedirs(RESULTS, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    path = os.path.join(RESULTS, f"{name}-{ts}.json")
    data = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), **environment(), **data}
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    log(f"saved {os.path.relpath(path)}")
    return path


def environment():
    nodes = json.loads(kubectl("get", "nodes", "-o", "json").stdout)["items"]
    ver = json.loads(kubectl("version", "-o", "json").stdout)["serverVersion"]["gitVersion"]
    istio = kubectl("-n", "istio-system", "get", "deploy", "istiod", "-o",
                    "jsonpath={.spec.template.spec.containers[0].image}").stdout
    return {"env": {"kubernetes": ver, "istiod_image": istio, "nodes": [
        {"name": n["metadata"]["name"], "role": n["metadata"]["labels"].get("lab-role"),
         "type": n["metadata"]["labels"].get("node.kubernetes.io/instance-type"),
         "zone": n["metadata"]["labels"].get("topology.kubernetes.io/zone"),
         "kernel": n["status"]["nodeInfo"]["kernelVersion"]} for n in nodes]}}


def fortio(ns, args, timeout=900):
    """Run fortio load in the client pod of ns and return the parsed JSON report."""
    r = kubectl("-n", ns, "exec", "deploy/client", "-c", "fortio", "--", "fortio", "load", "-quiet", "-json", "-",
                *args, timeout=timeout)
    return json.loads(r.stdout)


def summarize(rep):
    h = rep["DurationHistogram"]
    pct = {f"p{p['Percentile']:g}": round(p["Value"] * 1000, 3) for p in h["Percentiles"]}
    codes = {str(k): v for k, v in rep["RetCodes"].items()}
    total = sum(codes.values())
    return {"requested_qps": rep.get("RequestedQPS"), "actual_qps": round(rep["ActualQPS"], 1),
            "connections": rep.get("NumThreads"), "count": total, "codes": codes,
            "avg_ms": round(h["Avg"] * 1000, 3), "min_ms": round(h["Min"] * 1000, 3),
            "max_ms": round(h["Max"] * 1000, 3), **pct,
            "errors": total - codes.get("200", 0), "sockets": rep.get("SocketCount")}


def curl_code(ns, url, deploy="client", extra=()):
    """HTTP status from inside a pod (fortio curl), or the error class if no HTTP response came back."""
    r = kubectl("-n", ns, "exec", f"deploy/{deploy}", "-c", "fortio", "--", "fortio", "curl", "-quiet",
                "-timeout", "4s", *extra, url, check=False, timeout=60)
    out = r.stdout + r.stderr
    m = re.search(r"HTTP/[0-9.]+ (\d{3})", out)
    if m:
        return m.group(1)
    if re.search(r"reset by peer|EOF|broken pipe", out, re.I):
        return "reset"
    if re.search(r"refused", out, re.I):
        return "refused"
    if re.search(r"timeout|deadline", out, re.I):
        return "timeout"
    return f"rc={r.returncode}"


# ---------------------------------------------------------------- CPU accounting (kubelet /stats/summary)

def cpu_snapshot():
    """Cumulative CPU seconds per (namespace, pod, container) for the proxies we care about."""
    snap = {}
    for n in kubectl("get", "nodes", "-o", "name").stdout.split():
        s = json.loads(kubectl("get", "--raw", f"/api/v1/nodes/{n.split('/')[1]}/proxy/stats/summary").stdout)
        for p in s.get("pods", []):
            ns, name = p["podRef"]["namespace"], p["podRef"]["name"]
            for c in p.get("containers", []):
                ns_ok = ns in MODES or ns == "istio-system"
                if ns_ok and c.get("cpu", {}).get("usageCoreNanoSeconds") is not None:
                    snap[(ns, name, c["name"])] = c["cpu"]["usageCoreNanoSeconds"] / 1e9
    return snap, time.time()


def cpu_delta(before, after, ns):
    (b, t0), (a, t1) = before, after
    secs = t1 - t0
    out = {"sidecars_cores": 0.0, "ztunnel_cores": 0.0, "waypoint_cores": 0.0, "app_cores": 0.0}
    for key, v in a.items():
        if key not in b:
            continue
        kns, pod, cont = key
        cores = (v - b[key]) / secs
        if kns == ns and cont == "istio-proxy" and not pod.startswith("waypoint"):
            out["sidecars_cores"] += cores
        elif kns == ns and pod.startswith("waypoint"):
            out["waypoint_cores"] += cores
        elif kns == "istio-system" and pod.startswith("ztunnel"):
            out["ztunnel_cores"] += cores
        elif kns == ns and cont in ("echo", "fortio"):
            out["app_cores"] += cores
    return {k: round(v, 3) for k, v in out.items()}


# ---------------------------------------------------------------- tests

def t_latency(rounds=3, levels=(200, 1000), conns=16, secs=60):
    runs = []
    for rnd in range(rounds):
        order = MODES[rnd % 4:] + MODES[:rnd % 4]  # rotate so no mode always runs first
        for qps in levels:
            for ns in order:
                warm = summarize(fortio(ns, ["-allow-initial-errors", "-qps", str(qps), "-c", str(conns), "-t", "10s",
                                             URL]))  # warm-up; only its error count is kept
                before = cpu_snapshot()
                rep = fortio(ns, ["-qps", str(qps), "-c", str(conns), "-t", f"{secs}s", "-r", RES, "-p", PCTS, URL])
                after = cpu_snapshot()
                # CPU is averaged over the snapshot window; drop it if slow kubectl calls stretched that window.
                window = after[1] - before[1]
                cpu = cpu_delta(before, after, ns) if window < secs + 5 else None
                row = {"round": rnd + 1, "mode": ns, "qps": qps, **summarize(rep), "cpu": cpu,
                       "cpu_window_s": round(window, 1), "warmup_errors": warm["errors"]}
                log(f"latency r{rnd + 1} {ns} {qps}qps p50={row['p50']} p99={row['p99']} err={row['errors']}")
                runs.append(row)
    return save("latency", {"test": "latency", "params": {"connections": conns, "seconds": secs, "url": URL,
                                                         "percentiles": PCTS, "histogram_resolution_s": RES}, "runs": runs})


PRESTOP = {"spec": {"template": {"spec": {"terminationGracePeriodSeconds": 30, "containers": [
    {"name": "echo", "lifecycle": {"preStop": {"sleep": {"seconds": 10}}}}]}}}}
NO_PRESTOP = [{"op": "remove", "path": "/spec/template/spec/containers/0/lifecycle"}]


def retry_stats(ns):
    """Envoy retry counters on the client-side proxy that would hide failures (sidecar client or waypoint)."""
    if ns == "sidecar":
        pod = kubectl("-n", ns, "get", "pod", "-l", "app=client", "-o", "jsonpath={.items[0].metadata.name}").stdout
    elif ns == "ambient-l7":
        pod = kubectl("-n", ns, "get", "pod", "-l", "gateway.networking.k8s.io/gateway-name=waypoint", "-o",
                      "jsonpath={.items[0].metadata.name}").stdout
    else:
        return None
    # The Envoy admin port listens on localhost only, so read it from inside the proxy container.
    raw = kubectl("-n", ns, "exec", pod, "-c", "istio-proxy", "--", "pilot-agent", "request", "GET", "stats",
                  check=False).stdout
    tot = {}
    for line in raw.splitlines():
        # outbound|8080||echo.<ns>.svc.cluster.local (sidecar) or inbound-vip|8080|http|echo.<ns>... (waypoint)
        m = re.match(r"cluster\.[^ ]*\|echo\.[a-z0-9-]+\.svc\.cluster\.local;?\.(upstream_rq_retry|upstream_rq_retry_success"
                     r"|upstream_rq_retry_limit_exceeded|upstream_rq_5xx|upstream_rq_503|upstream_cx_connect_fail): (\d+)", line)
        if m:
            tot[m.group(1)] = tot.get(m.group(1), 0) + int(m.group(2))
    return tot


def t_rollout(*modes, restarts=3, qps=200, conns=16):
    runs = []
    for variant in ("default", "prestop-sleep-10s"):
        for ns in (modes or MODES):
            if variant == "default":
                kubectl("-n", ns, "patch", "deploy", "echo", "--type", "json", "-p", json.dumps(NO_PRESTOP), check=False)
            else:
                kubectl("-n", ns, "patch", "deploy", "echo", "--type", "strategic", "-p", json.dumps(PRESTOP))
            kubectl("-n", ns, "rollout", "status", "deploy/echo", "--timeout=300s")
            time.sleep(10)
            r0 = retry_stats(ns)
            duration = 40 * restarts + 30
            p = subprocess.Popen(["kubectl", "-n", ns, "exec", "deploy/client", "-c", "fortio", "--", "fortio", "load",
                                  "-quiet", "-json", "-", "-allow-initial-errors", "-qps", str(qps), "-c", str(conns), "-t", f"{duration}s",
                                  "-p", PCTS, URL], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            t_start = time.time()
            time.sleep(15)
            done_at = []
            for _ in range(restarts):
                kubectl("-n", ns, "rollout", "restart", "deploy/echo")
                kubectl("-n", ns, "rollout", "status", "deploy/echo", "--timeout=300s")
                done_at.append(round(time.time() - t_start, 1))
                time.sleep(5)
            out, err = p.communicate(timeout=duration + 600)
            # The load runs for `duration` seconds inside the pod; restarts are driven from here. If kubectl was slow
            # and the last restart finished outside the load window, the sample does not cover the rollouts.
            in_window = done_at[-1] + 3 < duration
            r1 = retry_stats(ns)
            retries = {k: r1.get(k, 0) - (r0 or {}).get(k, 0) for k in r1} if r1 is not None else None
            row = {"variant": variant, "mode": ns, "restarts": restarts, "load_seconds": duration,
                   "restart_done_at_s": done_at, "restarts_inside_load_window": in_window, **summarize(json.loads(out)),
                   "client_side_proxy_counters": retries,
                   "fortio_error_lines": sorted({re.sub(r"\d+", "N", l)[:160] for l in err.splitlines() if "rror" in l})[:8]}
            log(f"rollout {variant} {ns}: codes={row['codes']} retries={retries} done_at={done_at} valid={in_window}")
            runs.append(row)
    for ns in MODES:  # leave the default spec behind
        kubectl("-n", ns, "patch", "deploy", "echo", "--type", "json", "-p", json.dumps(NO_PRESTOP), check=False)
    return save("rollout", {"test": "rollout", "params": {"qps": qps, "connections": conns, "restarts": restarts,
                                                         "strategy": "RollingUpdate 25%/25% (default)", "replicas": 3},
                            "runs": runs})


def metrics_text(ns, pod, port, path):
    return kubectl("get", "--raw", f"/api/v1/namespaces/{ns}/pods/{pod}:{port}/proxy/{path}", check=False).stdout


def pods(ns, selector):
    return kubectl("-n", ns, "get", "pod", "-l", selector, "-o", "jsonpath={.items[*].metadata.name}").stdout.split()


def t_obs():
    mix = [("/echo", 100), ("/echo?status=500", 50), ("/admin/report", 30)]
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for ns in MODES:
        for path, n in mix:
            fortio(ns, ["-qps", "50", "-c", "2", "-n", str(n), f"http://echo:8080{path}"])
    time.sleep(8)
    matrix = {}
    zt = pods("istio-system", "app=ztunnel")
    zt_metrics = "".join(metrics_text("istio-system", p, 15020, "metrics") for p in zt)
    zt_logs = "".join(kubectl("-n", "istio-system", "logs", p, f"--since-time={since}", check=False).stdout for p in zt)
    for ns in MODES:
        row = {}
        srv = pods(ns, "app=echo")
        if ns == "sidecar":
            text = "".join(metrics_text(ns, p, 15020, "stats/prometheus") for p in srv)
            logs = "".join(kubectl("-n", ns, "logs", p, "-c", "istio-proxy", f"--since-time={since}",
                                   check=False).stdout for p in srv)
            source = "server sidecar"
        elif ns == "ambient-l7":
            wp = pods(ns, "gateway.networking.k8s.io/gateway-name=waypoint")
            text = "".join(metrics_text(ns, p, 15020, "stats/prometheus") for p in wp)
            logs = "".join(kubectl("-n", ns, "logs", p, f"--since-time={since}", check=False).stdout for p in wp)
            source = "waypoint"
        else:
            text, logs, source = "", "", "none"
        mine = [l for l in zt_metrics.splitlines() if f'destination_workload_namespace="{ns}"' in l]
        zlog = [l for l in zt_logs.splitlines() if f"{ns}/echo" in l or f'dst.namespace="{ns}"' in l
                or f"dst.workload=\"echo" in l and ns in l]
        req = [l for l in text.splitlines() if l.startswith("istio_requests_total") and f'destination_workload_namespace="{ns}"' in l]
        row["l7_proxy"] = source
        row["istio_requests_total_series"] = len(req)
        row["response_codes_in_metrics"] = sorted(set(re.findall(r'response_code="(\d+)"', "\n".join(req))))
        row["request_duration_histogram"] = any(l.startswith("istio_request_duration_milliseconds_bucket")
                                                and f'destination_workload_namespace="{ns}"' in l for l in text.splitlines())
        row["ztunnel_tcp_series"] = sorted({l.split("{")[0] for l in mine if l.startswith("istio_tcp")})
        row["ztunnel_has_http_status"] = any("response_code" in l for l in mine)
        row["access_log_lines"] = len([l for l in logs.splitlines() if "/echo" in l or "/admin" in l])
        row["access_log_has_path_and_status"] = bool(re.search(r'"GET /admin/report[^"]*" 200', logs))
        row["ztunnel_log_lines"] = len(zlog)
        row["ztunnel_log_sample"] = zlog[-1][:400] if zlog else None
        row["access_log_sample"] = next((l[:400] for l in logs.splitlines() if "/echo?status=500" in l), None)
        matrix[ns] = row
        log(f"obs {ns}: {row['istio_requests_total_series']} req series, codes={row['response_codes_in_metrics']}, "
            f"zt={row['ztunnel_tcp_series']}, logs={row['access_log_lines']}")
    return save("obs", {"test": "observability", "traffic": [{"path": p, "requests": n} for p, n in mix],
                        "matrix": matrix})


def authz(ns, action, name, target):
    return f"""apiVersion: security.istio.io/v1
kind: AuthorizationPolicy
metadata: {{name: {name}, namespace: {ns}}}
spec:
  {target}
  action: {action}
  rules:
  - to:
    - operation:
        paths: ["/admin*"]
"""


SELECTOR = "selector: {matchLabels: {app: echo}}"
TARGETREF = 'targetRefs: [{kind: Service, group: "", name: echo}]'


def probe(ns):
    ip = kubectl("-n", ns, "get", "pod", "-l", "app=echo", "-o", "jsonpath={.items[0].status.podIP}").stdout
    return {"echo": curl_code(ns, f"{URL}"), "admin": curl_code(ns, "http://echo:8080/admin/report"),
            "admin_via_pod_ip": curl_code(ns, f"http://{ip}:8080/admin/report")}


def watch(ns, secs=60, step=2):
    """Probe /echo and /admin/report every `step` seconds; returns the timeline from policy creation."""
    t0, out = time.time(), []
    while time.time() - t0 < secs:
        out.append({"t": round(time.time() - t0, 1), "echo": curl_code(ns, URL),
                    "admin": curl_code(ns, "http://echo:8080/admin/report")})
        time.sleep(step)
    return out


def first_stable(timeline, key, want, n=5):
    """Seconds until `key` returned a value in `want` n times in a row (None if it never did)."""
    run = 0
    for i, row in enumerate(timeline):
        run = run + 1 if row[key] in want else 0
        if run == n:
            return timeline[i - n + 1]["t"]
    return None


def t_policy():
    cases = [("sidecar", "DENY", SELECTOR), ("ambient-l4", "DENY", SELECTOR), ("ambient-l4", "ALLOW", SELECTOR),
             ("ambient-l7", "DENY", TARGETREF), ("ambient-l7", "DENY", SELECTOR)]
    rows = []
    for ns, action, target in cases:
        doc = authz(ns, action, "lab-admin", target)
        before = probe(ns)
        warn = kubectl("apply", "-f", "-", input=doc).stderr.strip()
        timeline = watch(ns)
        steady = probe(ns)
        zt = None
        if ns.startswith("ambient"):
            raw = sh(f"{os.environ['ISTIOCTL']} ztunnel-config policies -o json", check=False).stdout
            zt = next((x for x in json.loads(raw or "[]") if x.get("name") == "lab-admin"), None)
        delete(doc)
        time.sleep(20)
        restored = probe(ns)
        denied = {"403", "reset", "refused", "timeout"}
        rows.append({"mode": ns, "action": action, "attach": "selector" if target == SELECTOR else "targetRefs Service",
                     "rule": "paths /admin*", "apply_warning": warn or None, "before": before, "steady_after_60s": steady,
                     "after_delete": restored,
                     "admin_denied_stable_after_s": first_stable(timeline, "admin", denied),
                     "echo_denied_stable_after_s": first_stable(timeline, "echo", denied),
                     "admin_codes_seen": sorted({r["admin"] for r in timeline}),
                     "echo_codes_seen": sorted({r["echo"] for r in timeline}),
                     "ztunnel_view": zt, "timeline": timeline})
        log(f"policy {ns} {action} {rows[-1]['attach']}: steady={steady} "
            f"admin_stable={rows[-1]['admin_denied_stable_after_s']}s")
    return save("policy", {"test": "authorization-policy-l7", "probe_interval_s": 2, "cases": rows})


def t_mtls():
    rows = []
    for mode in ("PERMISSIVE", "STRICT"):
        for ns in MESH:
            apply(f"""apiVersion: security.istio.io/v1
kind: PeerAuthentication
metadata: {{name: default, namespace: {ns}}}
spec: {{mtls: {{mode: {mode}}}}}
""")
        time.sleep(30)  # policy propagation took up to ~25 s per replica in the policy test
        for ns in MESH:
            plain = curl_code("base", f"http://echo.{ns}:8080/echo")  # client in the non-mesh namespace
            meshed = curl_code(ns, URL)
            rows.append({"peer_authentication": mode, "target": ns, "from_non_mesh_pod": plain, "from_mesh_client": meshed})
            log(f"mtls {mode} {ns}: plaintext={plain} meshed={meshed}")
    # Security policy as recorded by the proxies after STRICT.
    sec = {}
    for ns in MESH:
        fortio(ns, ["-qps", "20", "-c", "1", "-n", "40", URL])
    time.sleep(6)
    zt = "".join(metrics_text("istio-system", p, 15020, "metrics") for p in pods("istio-system", "app=ztunnel"))
    for ns in MESH:
        if ns == "sidecar":
            text = "".join(metrics_text(ns, p, 15020, "stats/prometheus") for p in pods(ns, "app=echo"))
            pol = re.findall(r'connection_security_policy="([a-z_]+)"', "\n".join(
                l for l in text.splitlines() if l.startswith("istio_requests_total") and 'reporter="destination"' in l))
        else:
            pol = re.findall(r'connection_security_policy="([a-z_]+)"', "\n".join(
                l for l in zt.splitlines() if l.startswith("istio_tcp_connections_opened_total")
                and f'destination_workload_namespace="{ns}"' in l and 'reporter="destination"' in l))
        sec[ns] = sorted(set(pol))
    return save("mtls", {"test": "mtls", "cases": rows, "connection_security_policy": sec})


BAD = """apiVersion: apps/v1
kind: Deployment
metadata: {{name: echo-bad, namespace: {ns}}}
spec:
  replicas: 1
  selector: {{matchLabels: {{app: echo, version: bad}}}}
  template:
    metadata: {{labels: {{app: echo, version: bad}}}}
    spec:
      nodeSelector: {{lab-role: server}}
      containers:
      - name: echo
        image: {image}
        args: ["server", "-http-port", "8080", "-echo-server-default-params", "status=500"]
        readinessProbe: {{httpGet: {{path: /fortio/, port: 8080}}, periodSeconds: 2}}
        resources: {{requests: {{cpu: 200m, memory: 64Mi}}}}
"""
DR = """apiVersion: networking.istio.io/v1
kind: DestinationRule
metadata: {{name: echo-outlier, namespace: {ns}}}
spec:
  host: echo.{ns}.svc.cluster.local
  trafficPolicy:
    outlierDetection: {{consecutive5xxErrors: 5, interval: 5s, baseEjectionTime: 60s, maxEjectionPercent: 50}}
"""


def t_outlier(qps=100, secs=60):
    rows = []
    for ns in ("base", "sidecar", "ambient-l4", "ambient-l7"):
      try:
        apply(BAD.format(ns=ns, image=os.environ["FORTIO_IMAGE"]))
        kubectl("-n", ns, "rollout", "status", "deploy/echo-bad", "--timeout=180s")
        time.sleep(8)
        for policy in ("istio-default", "outlierDetection"):
            if policy == "outlierDetection":
                if ns == "base":
                    continue
                apply(DR.format(ns=ns))
                time.sleep(10)
            rep = summarize(fortio(ns, ["-allow-initial-errors", "-qps", str(qps), "-c", "8", "-t", f"{secs}s",
                                          "-p", PCTS, URL]))
            rows.append({"mode": ns, "policy": policy, **rep,
                         "error_pct": round(100 * rep["errors"] / max(rep["count"], 1), 2)})
            log(f"outlier {ns} {policy}: {rep['codes']}")
            delete(DR.format(ns=ns))
      finally:
        delete(DR.format(ns=ns))
        delete(BAD.format(ns=ns, image=os.environ["FORTIO_IMAGE"]))
    return save("outlier", {"test": "outlier-detection", "params": {"qps": qps, "seconds": secs, "connections": 8,
                                                                  "endpoints": "3 good + 1 always-500"},
                            "destination_rule": DR.format(ns="<ns>"), "runs": rows})


def t_outlier_conn(qps=100, secs=60):
    """Follow-up to `outlier`: L4 paths (kube-proxy, ztunnel) pick an endpoint per TCP connection, so with keep-alive
    the bad endpoint is hit only if one of the 8 connections landed on it. A new connection per request shows the
    per-connection spread, and whether ztunnel applies the DestinationRule outlierDetection at all."""
    rows = []
    for ns in ("base", "ambient-l4"):
        try:
            apply(BAD.format(ns=ns, image=os.environ["FORTIO_IMAGE"]))
            kubectl("-n", ns, "rollout", "status", "deploy/echo-bad", "--timeout=180s")
            time.sleep(8)
            for policy in ("istio-default", "outlierDetection"):
                if policy == "outlierDetection":
                    if ns == "base":
                        continue
                    apply(DR.format(ns=ns))
                    time.sleep(10)
                for keepalive in ("true", "false"):
                    rep = summarize(fortio(ns, ["-allow-initial-errors", f"-keepalive={keepalive}", "-qps", str(qps),
                                                "-c", "8", "-t", f"{secs}s", "-p", PCTS, URL]))
                    rows.append({"mode": ns, "policy": policy, "keepalive": keepalive == "true", **rep,
                                 "error_pct": round(100 * rep["errors"] / max(rep["count"], 1), 2)})
                    log(f"outlier-conn {ns} {policy} keepalive={keepalive}: {rep['codes']} sockets={rep['sockets']}")
                delete(DR.format(ns=ns))
        finally:
            delete(DR.format(ns=ns))
            delete(BAD.format(ns=ns, image=os.environ["FORTIO_IMAGE"]))
    return save("outlier-conn", {"test": "outlier-detection-per-connection",
                                 "params": {"qps": qps, "seconds": secs, "connections": 8,
                                            "endpoints": "3 good + 1 always-500"}, "runs": rows})


TESTS = {"latency": t_latency, "rollout": t_rollout, "obs": t_obs, "policy": t_policy, "mtls": t_mtls,
         "outlier": t_outlier, "outlier-conn": t_outlier_conn}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in TESTS:
        sys.exit(f"usage: run.py {{{'|'.join(TESTS)}}}")
    TESTS[sys.argv[1]](*sys.argv[2:])
