from __future__ import annotations

from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
SCAN_JOB = WORKFLOW["jobs"]["dependency-image-scan"]
SECRET_SCAN_JOB = WORKFLOW["jobs"]["secret-scan"]


class CiScanningTest(unittest.TestCase):
    def test_dependency_scan_blocks_high_and_critical_library_vulnerabilities(self):
        dependency_scan = next(
            step for step in SCAN_JOB["steps"] if step.get("name") == "Scan dependency manifests"
        )

        self.assertEqual(dependency_scan["uses"], "aquasecurity/trivy-action@v0.36.0")
        self.assertEqual(dependency_scan["with"]["version"], "v0.70.0")
        self.assertEqual(dependency_scan["with"]["scan-type"], "fs")
        self.assertEqual(dependency_scan["with"]["scan-ref"], ".")
        self.assertEqual(dependency_scan["with"]["scanners"], "vuln")
        self.assertEqual(dependency_scan["with"]["vuln-type"], "library")
        self.assertEqual(
            dependency_scan["with"]["skip-dirs"],
            ".trivy-cache,node_modules,services/email-agent/.venv,services/security/.venv,agent-IA-ETHIK-PORTAGE-",
        )
        self.assertEqual(dependency_scan["with"]["severity"], "HIGH,CRITICAL")
        self.assertEqual(dependency_scan["with"]["exit-code"], "1")

    def test_image_scan_builds_and_blocks_first_party_images(self):
        images = {
            entry["image"]: entry["context"]
            for entry in SCAN_JOB["strategy"]["matrix"]["include"]
        }
        image_scan = next(
            step for step in SCAN_JOB["steps"] if step.get("name") == "Scan service image"
        )

        self.assertEqual(
            images,
            {
                "email-agent": "email-agent",
                "security": "security",
                "gateway": "gateway",
                "web-react": "web-react",
            },
        )
        self.assertEqual(image_scan["uses"], "aquasecurity/trivy-action@v0.36.0")
        self.assertEqual(image_scan["with"]["version"], "v0.70.0")
        self.assertEqual(image_scan["with"]["vuln-type"], "os,library")
        self.assertEqual(image_scan["with"]["severity"], "HIGH,CRITICAL")
        self.assertEqual(image_scan["with"]["exit-code"], "1")

    def test_local_runner_matches_ci_scanner_policy(self):
        script = (ROOT / "infra" / "scan-security.sh").read_text()

        self.assertIn("TRIVY_VERSION=\"${TRIVY_VERSION:-0.70.0}\"", script)
        self.assertIn("TRIVY_IMAGE=\"aquasec/trivy:${TRIVY_VERSION}\"", script)
        self.assertIn("--pkg-types library", script)
        self.assertIn("--pkg-types os,library", script)
        self.assertIn("--severity HIGH,CRITICAL", script)
        self.assertIn("--exit-code 1", script)
        for image in ("email-agent", "security", "gateway", "web-react"):
            self.assertIn(f"scan_image {image} {image}", script)

    def test_secret_scan_covers_full_git_history_and_blocks_findings(self):
        checkout = next(
            step for step in SECRET_SCAN_JOB["steps"] if step.get("uses", "").startswith("actions/checkout")
        )
        scan_step = next(
            step for step in SECRET_SCAN_JOB["steps"] if step.get("name") == "Scan git history for secrets"
        )

        self.assertEqual(checkout["with"]["fetch-depth"], 0)
        self.assertIn("zricethezav/gitleaks:v8.30.1", scan_step["run"])
        self.assertIn("detect --source /repo", scan_step["run"])
        self.assertIn("--config /repo/.gitleaks.toml", scan_step["run"])
        self.assertIn("--exit-code 1", scan_step["run"])

    def test_local_secret_scanner_matches_ci_gitleaks_version(self):
        script = (ROOT / "infra" / "scan-secrets.sh").read_text()

        self.assertIn('GITLEAKS_VERSION="${GITLEAKS_VERSION:-8.30.1}"', script)
        self.assertIn('GITLEAKS_IMAGE="zricethezav/gitleaks:v${GITLEAKS_VERSION}"', script)
        self.assertIn("--config /repo/.gitleaks.toml", script)
        self.assertIn("--exit-code 1", script)

    def test_runtime_images_refresh_vulnerable_base_packages(self):
        dockerfiles = {
            "email-agent": (ROOT / "services" / "email-agent" / "Dockerfile").read_text(),
            "security": (ROOT / "services" / "security" / "Dockerfile").read_text(),
            "gateway": (ROOT / "services" / "gateway" / "Dockerfile").read_text(),
            "web-react": (ROOT / "services" / "web-react" / "Dockerfile").read_text(),
        }

        for service in ("email-agent", "security"):
            self.assertIn(
                "RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel",
                dockerfiles[service],
            )
        for service in ("gateway", "web-react"):
            self.assertIn("RUN apk upgrade --no-cache", dockerfiles[service])
        for service in ("web-react",):
            self.assertIn("USER root", dockerfiles[service])
            self.assertIn("USER 101", dockerfiles[service])


if __name__ == "__main__":
    unittest.main()
