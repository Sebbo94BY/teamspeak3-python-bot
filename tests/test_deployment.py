"""Tests for the service unit and virtual-environment bootstrap script."""

# pylint: disable=missing-class-docstring,missing-function-docstring
import hashlib
import os
import re
import stat
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def write_executable(path, contents):
    """Write a temporary executable used to isolate the bootstrap script."""
    path.write_text(contents, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class DeploymentTests(unittest.TestCase):
    def test_prepare_venv_skips_rebuild_for_matching_requirements_hash(self):
        requirements_hash = hashlib.sha256(
            (PROJECT_ROOT / "requirements.txt").read_bytes()
        ).hexdigest()

        with TemporaryDirectory() as tempdir:
            venv_dir = Path(tempdir) / "venv"
            python = venv_dir / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.write_text("#!/bin/sh\n", encoding="utf-8")
            python.chmod(python.stat().st_mode | stat.S_IXUSR)
            (venv_dir / ".requirements.sha256").write_text(
                f"{requirements_hash}\n", encoding="utf-8"
            )
            sentinel = venv_dir / "sentinel"
            sentinel.write_text("keep", encoding="utf-8")

            result = subprocess.run(
                ["bash", str(PROJECT_ROOT / "prepare_python_venv.sh"), str(venv_dir)],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("already up to date", result.stdout)
            self.assertTrue(sentinel.exists())

    def test_prepare_venv_rebuilds_stale_hash_without_network_access(self):
        with TemporaryDirectory() as tempdir:
            tempdir_path = Path(tempdir)
            fake_bin = tempdir_path / "bin"
            fake_bin.mkdir()
            venv_dir = tempdir_path / "venv"
            (venv_dir / "bin").mkdir(parents=True)
            (venv_dir / ".requirements.sha256").write_text(
                "stale-hash\n", encoding="utf-8"
            )
            old_file = venv_dir / "old-file"
            old_file.write_text("old", encoding="utf-8")
            pip_log = tempdir_path / "pip.log"

            write_executable(
                fake_bin / "python3",
                "#!/usr/bin/env bash\n"
                "set -e\n"
                'if [[ "$1" != "-m" || "$2" != "venv" ]]; then\n'
                "    exit 2\n"
                "fi\n"
                'mkdir -p "$3/bin"\n'
                "printf '%s\\n' '#!/usr/bin/env bash' 'exit 0' > \"$3/bin/python\"\n"
                'chmod +x "$3/bin/python"\n'
                ': > "$3/bin/activate"\n',
            )
            write_executable(
                fake_bin / "pip",
                "#!/usr/bin/env bash\n"
                "set -e\n"
                'printf \'%s\\n\' "$*" >> "$PIP_LOG"\n',
            )

            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
            environment["PIP_LOG"] = str(pip_log)
            result = subprocess.run(
                ["bash", str(PROJECT_ROOT / "prepare_python_venv.sh"), str(venv_dir)],
                cwd=PROJECT_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(old_file.exists())
            expected_hash = hashlib.sha256(
                (PROJECT_ROOT / "requirements.txt").read_bytes()
            ).hexdigest()
            self.assertEqual(
                (venv_dir / ".requirements.sha256").read_text(encoding="utf-8"),
                f"{expected_hash}\n",
            )
            pip_calls = pip_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(pip_calls), 2)
            self.assertEqual(pip_calls[0], "install --quiet wheel")
            self.assertEqual(pip_calls[1], "install --quiet -r requirements.txt")

    def test_systemd_unit_has_restart_limits_and_matching_memory_bounds(self):
        service = (PROJECT_ROOT / "teamspeak-bot.service").read_text(encoding="utf-8")

        for setting in (
            "Wants=network-online.target",
            "After=network-online.target",
            "Restart=on-failure",
            "RestartSec=10",
            "TimeoutStopSec=30s",
            "TasksMax=64",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "ProtectSystem=strict",
        ):
            with self.subTest(setting=setting):
                self.assertIn(setting, service)

        memory_high = re.search(r"^MemoryHigh=(\d+)M$", service, re.MULTILINE)
        memory_max = re.search(r"^MemoryMax=(\d+)M$", service, re.MULTILINE)
        self.assertIsNotNone(memory_high)
        self.assertIsNotNone(memory_max)
        self.assertLess(int(memory_high.group(1)), int(memory_max.group(1)))

        for writable_path in ("logs", "venv", ".ssh"):
            with self.subTest(writable_path=writable_path):
                self.assertRegex(
                    service, rf"ReadWritePaths=.*{re.escape(writable_path)}"
                )


if __name__ == "__main__":
    unittest.main()
