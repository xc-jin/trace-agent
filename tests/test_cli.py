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

    def test_scenario_list_and_real_initialization(self):
        status, output, errors = self.command("scenario", "list")
        self.assertEqual(status, 0, errors)
        self.assertEqual(json.loads(output)[0]["name"], "knowledge-base")
        status, output, errors = self.command("scenario", "init", "knowledge-base", "--project", "real", "--constraint", "deployment=必须自托管")
        self.assertEqual(status, 0, errors)
        setup = json.loads(output)
        self.assertEqual(setup["sources"], [])
        self.assertEqual(setup["constraints"]["deployment"], "必须自托管")
        self.assertFalse(setup["example_materials"])

    def test_scenario_example_import_is_opt_in(self):
        status, output, errors = self.command("scenario", "init", "knowledge-base", "--project", "fictional", "--example-materials")
        self.assertEqual(status, 0, errors)
        self.assertTrue(json.loads(output)["example_materials"])
        self.assertEqual(len(json.loads(output)["sources"]), 1)

    def test_review_json_export_and_markdown(self):
        task_id = self.new_task()
        self.command("task", "amend", task_id, "deployment", "必须自托管")
        destination = self.directory / "review.json"
        status, output, errors = self.command("review", task_id, "--since-version", "1", "--format", "json", "--output", str(destination))
        self.assertEqual(status, 0, errors)
        self.assertEqual(json.loads(destination.read_text())["state"], "no_claims")
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        status, output, errors = self.command("review", task_id)
        self.assertEqual(status, 0, errors)
        self.assertIn("技术选型变更对照", output)

    def test_review_before_change_returns_actionable_error(self):
        status, output, errors = self.command("review", self.new_task())
        self.assertEqual(status, 1)
        self.assertIn("use report", errors)

    def test_scenario_demo_runs_without_loading_credentials(self):
        with patch("dotenv.load_dotenv", side_effect=AssertionError("No credentials for demo")):
            status, output, errors = self.command("demo", "--scenario", "knowledge-base")
        self.assertEqual(status, 0, errors)
        self.assertTrue(json.loads(output)["all_mechanical_checks_passed"])


if __name__ == "__main__":
    unittest.main()
