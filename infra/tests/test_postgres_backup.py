from __future__ import annotations

from pathlib import Path
import stat
import unittest


ROOT = Path(__file__).resolve().parents[2]
BACKUP = ROOT / "infra" / "backup-postgres.sh"
RESTORE = ROOT / "infra" / "restore-postgres.sh"
README = ROOT / "infra" / "README.md"
GITIGNORE = ROOT / ".gitignore"


class PostgresBackupTest(unittest.TestCase):
    def test_backup_script_uses_custom_format_and_retention(self):
        script = BACKUP.read_text()

        self.assertIn("pg_dump", script)
        self.assertIn("-Fc", script)
        self.assertIn("--no-owner --no-acl", script)
        self.assertIn("pg_restore --list", script)
        self.assertIn("AGORA_BACKUP_RETENTION_DAYS", script)
        self.assertIn('find "$BACKUP_DIR"', script)
        self.assertIn("${POSTGRES_DB}-latest.dump", script)

    def test_restore_script_requires_explicit_confirmation(self):
        script = RESTORE.read_text()

        self.assertIn("AGORA_RESTORE_CONFIRM", script)
        self.assertIn("AGORA_RESTORE_DB", script)
        self.assertIn("AGORA_RESTORE_CREATE_DB", script)
        self.assertIn("restore-${TARGET_DB}", script)
        self.assertIn("DROP DATABASE IF EXISTS", script)
        self.assertIn("CREATE DATABASE", script)
        self.assertIn("pg_restore --list", script)
        self.assertIn("--clean --if-exists", script)
        self.assertIn("--no-owner --no-acl", script)

    def test_scripts_are_executable(self):
        for script in (BACKUP, RESTORE):
            mode = script.stat().st_mode
            self.assertTrue(mode & stat.S_IXUSR, script)

    def test_backup_artifacts_are_ignored_and_documented(self):
        self.assertIn("infra/backups/", GITIGNORE.read_text())
        readme = README.read_text()
        self.assertIn("./backup-postgres.sh", readme)
        self.assertIn("./restore-postgres.sh", readme)
        self.assertIn("AGORA_RESTORE_CONFIRM", readme)


if __name__ == "__main__":
    unittest.main()
