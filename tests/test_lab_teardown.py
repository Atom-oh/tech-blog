"""Exercise teardown's account boundary without calling any AWS or Kubernetes CLI."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


TEARDOWN = Path(__file__).resolve().parents[1] / "assets/data/istio-lab-2026-10/lab/teardown.sh"


class TeardownAccountTests(unittest.TestCase):
    def run_teardown(self, expected="111111111111", actual="111111111111", sts_status=0):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "commands.log"
            commands = {
                "aws": (
                    'printf "aws %s\\n" "$*" >> "$LAB_TEST_LOG"\n'
                    'if [ "$1 $2" = "sts get-caller-identity" ]; then\n'
                    '  printf "%s\\n" "$LAB_TEST_ACCOUNT"\n'
                    '  exit "$LAB_TEST_STS_STATUS"\n'
                    'fi\n'
                ),
                "eksctl": 'printf "eksctl %s\\n" "$*" >> "$LAB_TEST_LOG"\n',
                "kubectl": 'printf "kubectl %s\\n" "$*" >> "$LAB_TEST_LOG"\nexit 99\n',
            }
            for name, body in commands.items():
                command = root / name
                command.write_text("#!/bin/sh\n" + body)
                command.chmod(0o755)
            env = {**os.environ, "PATH": str(root) + os.pathsep + os.defpath,
                   "LAB_TEST_LOG": str(log), "LAB_TEST_ACCOUNT": actual,
                   "LAB_TEST_STS_STATUS": str(sts_status)}
            env.pop("EXPECTED_ACCOUNT", None)
            if expected is not None:
                env["EXPECTED_ACCOUNT"] = expected
            result = subprocess.run(["bash", str(TEARDOWN)], env=env, capture_output=True,
                                    text=True, timeout=5)
            return result, log.read_text().splitlines() if log.exists() else []

    def assert_blocked(self, **kwargs):
        result, commands = self.run_teardown(**kwargs)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(line.startswith("eksctl ") for line in commands), commands)

    def test_wrong_account_cannot_delete_cluster(self):
        self.assert_blocked(actual="222222222222")

    def test_missing_expected_account_cannot_delete_cluster(self):
        self.assert_blocked(expected=None)

    def test_empty_expected_account_cannot_delete_cluster(self):
        self.assert_blocked(expected="")

    def test_failed_identity_lookup_cannot_delete_cluster(self):
        self.assert_blocked(sts_status=1)

    def test_matching_account_can_delete_without_kube_context(self):
        result, commands = self.run_teardown()
        self.assertEqual(result.returncode, 0, result.stderr)
        deletes = [line for line in commands if line.startswith("eksctl ")]
        self.assertEqual(len(deletes), 1, commands)
        self.assertIn("delete cluster --name istio-lab --region ap-northeast-2", deletes[0])
        self.assertTrue(commands[0].startswith("aws sts get-caller-identity"), commands)
        self.assertFalse(any(line.startswith("kubectl ") for line in commands), commands)


if __name__ == "__main__":
    unittest.main()
