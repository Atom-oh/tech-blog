#!/usr/bin/env python3
"""Observe fresh-connection Aurora MySQL writes using a locally installed mysql client.

No AWS calls are made. Non-successful requests have an unconfirmed outcome:
an INSERT might have committed even if the client lost its response.
"""

import argparse
import json
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def positive(value):
    number = float(value)
    if number <= 0 or not number < float("inf"):
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="Aurora writer cluster endpoint")
    parser.add_argument("--database", required=True, help="Existing scratch database")
    parser.add_argument("--ca", required=True, type=Path, help="RDS CA PEM bundle")
    parser.add_argument("--login-path", default="aurora-demo")
    parser.add_argument("--port", type=int, default=3306)
    parser.add_argument("--duration", type=positive, default=180)
    parser.add_argument("--interval", type=positive, default=1)
    parser.add_argument("--timeout", type=positive, default=8)
    parser.add_argument("--output", required=True, type=Path, help="New JSONL file")
    args = parser.parse_args()
    mysql = shutil.which("mysql")
    if not mysql:
        parser.error("MySQL 8 mysql client is required (MariaDB clients use different TLS flags)")
    if not args.ca.is_file():
        parser.error("--ca must point to an existing RDS CA PEM bundle")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    command = [
        mysql,
        f"--login-path={args.login_path}",
        f"--host={args.host}",
        f"--port={args.port}",
        f"--database={args.database}",
        "--protocol=TCP",
        "--ssl-mode=VERIFY_IDENTITY",
        f"--ssl-ca={args.ca.resolve()}",
        "--connect-timeout=3",
        "--batch",
        "--raw",
        "--skip-column-names",
    ]
    started = time.monotonic()
    try:
        # Refuse to overwrite an earlier measurement.
        log = args.output.open("x", encoding="utf-8")
    except OSError as exc:
        parser.error(f"cannot create output: {exc}")
    with log:
        try:
            while time.monotonic() - started < args.duration:
                remaining = args.duration - (time.monotonic() - started)
                if remaining <= 0:
                    break
                request_id = str(uuid.uuid4())
                # Only a generated UUID is interpolated. Host/database are argv values.
                sql = (
                    "SELECT @@aurora_server_id, @@innodb_read_only;\n"
                    "START TRANSACTION;\n"
                    f"INSERT INTO failover_probe (request_id) VALUES ('{request_id}');\n"
                    "COMMIT;\n"
                    f"SELECT request_id FROM failover_probe WHERE request_id='{request_id}';\n"
                )
                attempt_started = time.monotonic()
                interrupted = False
                record = {
                    "started_at": utc_now(),
                    "request_id": request_id,
                    "connection_mode": "fresh",
                    "outcome": "unconfirmed",
                }
                try:
                    result = subprocess.run(
                        command,
                        input=sql,
                        text=True,
                        capture_output=True,
                        timeout=min(args.timeout, remaining),
                        check=False,
                    )
                    lines = result.stdout.strip().splitlines()
                    if lines and "\t" in lines[0]:
                        record["instance_id"], record["read_only"] = lines[0].split("\t", 1)
                    if result.returncode == 0 and len(lines) == 2 and lines[-1] == request_id:
                        record["outcome"] = "confirmed_write"
                    else:
                        record["error"] = result.stderr.strip()[:500] or "unexpected SQL output"
                        record["client_exit_code"] = result.returncode
                except subprocess.TimeoutExpired:
                    record["error"] = "client timeout; INSERT outcome requires reconciliation"
                except OSError as exc:
                    record["error"] = str(exc)[:500]
                except KeyboardInterrupt:
                    record["error"] = "client interrupted; INSERT outcome requires reconciliation"
                    interrupted = True
                record["completed_at"] = utc_now()
                record["duration_ms"] = round((time.monotonic() - attempt_started) * 1000, 1)
                line = json.dumps(record, ensure_ascii=False)
                print(line, flush=True)
                log.write(line + "\n")
                log.flush()
                if interrupted:
                    break
                remaining = args.duration - (time.monotonic() - started)
                if remaining > 0:
                    time.sleep(min(args.interval, remaining))
        except KeyboardInterrupt:
            print("Stopped. Reconcile any last in-flight request separately.", flush=True)


if __name__ == "__main__":
    main()
