from __future__ import annotations

from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())


class TenantIsolationDeploymentTest(unittest.TestCase):
    def test_runtime_uses_non_bypass_role_after_owner_migrations(self):
        services = COMPOSE["services"]
        bootstrap = services["postgres-agent-role"]
        migration = services["email-agent-migrate"]
        runtime = services["email-agent"]

        self.assertIn("AGENT_POSTGRES_PASSWORD", bootstrap["environment"])
        self.assertIn("${POSTGRES_USER:-agora}", migration["environment"]["DATABASE_URL"])
        self.assertEqual(migration["environment"]["AGENT_RUN_MIGRATIONS"], "true")
        self.assertIn("agora_email_agent", runtime["environment"]["DATABASE_URL"])
        self.assertEqual(runtime["environment"]["AGENT_RUN_MIGRATIONS"], "false")
        self.assertEqual(
            runtime["depends_on"]["email-agent-migrate"]["condition"],
            "service_completed_successfully",
        )

    def test_bootstrap_role_cannot_bypass_rls(self):
        script = (ROOT / "postgres" / "ensure-agent-role.sh").read_text()

        self.assertIn("NOSUPERUSER NOBYPASSRLS", script)
        self.assertNotIn("BYPASSRLS", script.replace("NOBYPASSRLS", ""))


if __name__ == "__main__":
    unittest.main()
