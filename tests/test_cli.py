from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent4x.cli import main


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.base = ["--data-dir", str(self.directory / "data")]

    def command(self, *arguments):
        output, errors = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            status = main(self.base + list(arguments))
        return status, output.getvalue(), errors.getvalue()

    def new_task(self):
        status, output, errors = self.command("task", "new", "选部署方式", "--project", "cli-test", "--constraint", "deployment=允许托管")
        self.assertEqual(status, 0, errors)
        return json.loads(output)["id"]

    def test_task_amend_source_and_report(self):
        task_id = self.new_task()
        (self.directory / "source.md").write_text("Beta支持内网部署。")
        status, output, errors = self.command("source", "add", task_id, "source.md", "--root", str(self.directory))
        self.assertEqual(status, 0, errors)
        self.assertIn("source_id", json.loads(output))
        status, output, errors = self.command("task", "amend", task_id, "deployment", "仅内网")
        self.assertEqual(json.loads(output)["contract_version"], 2)
        destination = self.directory / "reports" / "decision.md"
        status, output, errors = self.command("report", task_id, "--output", str(destination))
        self.assertEqual(status, 0, errors)
        self.assertIn("仅内网", destination.read_text())

    def test_duplicate_constraint_is_rejected(self):
        status, output, errors = self.command("task", "new", "需求", "--project", "test", "--constraint", "scope=one", "--constraint", "scope=two")
        self.assertEqual(status, 1)
        self.assertIn("distinct", errors)

    def test_memory_user_confirmation_flow(self):
        task_id = self.new_task()
        status, output, errors = self.command("memory", "propose", task_id, "优先自托管")
        memory_id = json.loads(output)["id"]
        self.assertEqual(json.loads(output)["status"], "pending")
        status, output, errors = self.command("memory", "confirm", memory_id)
        self.assertEqual(json.loads(output)["status"], "active")
        status, output, errors = self.command("memory", "delete", memory_id)
        self.assertEqual(json.loads(output)["status"], "deleted")

    def test_unknown_task_returns_error(self):
        status, output, errors = self.command("task", "show", "unknown")
        self.assertEqual(status, 1)
        self.assertIn("Unknown task", errors)

    def test_live_requires_credentials_without_network(self):
        task_id = self.new_task()
        with patch("dotenv.load_dotenv"), patch.dict("os.environ", {"DEEPSEEK_API_KEY": ""}), patch("socket.socket.connect", side_effect=AssertionError("no network")):
            status, output, errors = self.command("run", task_id)
        self.assertEqual(status, 1)
        self.assertIn("Set DEEPSEEK_API_KEY", errors)

    def test_trace_is_structured_json(self):
        task_id = self.new_task()
        status, output, errors = self.command("trace", task_id)
        self.assertEqual(status, 0, errors)
        self.assertEqual(json.loads(output)[0]["kind"], "task.created")


if __name__ == "__main__":
    unittest.main()
