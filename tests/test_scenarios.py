from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent4x.domain import DomainError
from agent4x.scenario_demo import run_knowledge_base_demo
from agent4x.scenarios import create_scenario, list_scenarios
from agent4x.store import Store


class ScenarioTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name) / "data")
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "socket.getaddrinfo"):
            self.stack.enter_context(patch(target, side_effect=RuntimeError("Tests forbid external network")))

    def test_scenario_catalog_is_explicit_about_fixtures(self):
        scenario = list_scenarios()[0]
        self.assertEqual(scenario["name"], "knowledge-base")
        self.assertIn("虚构", scenario["example_materials"])
        self.assertEqual(scenario["constraints"], ["deployment", "license"])

    def test_real_template_does_not_import_fictional_materials(self):
        setup = create_scenario(self.store, "knowledge-base", "real-project")
        task = self.store.task(setup["task_id"])
        self.assertFalse(setup["example_materials"])
        self.assertEqual(task["sources"], [])
        self.assertEqual(task["claims"], [])
        self.assertIn("尚未确认", task["constraints"]["deployment"])
        self.assertIn("尚未确认", task["constraints"]["license"])

    def test_explicit_fixture_import_includes_limitations(self):
        setup = create_scenario(self.store, "knowledge-base", "example-project", example_materials=True)
        task = self.store.task(setup["task_id"])
        self.assertEqual(len(task["sources"]), 1)
        self.assertEqual(task["sources"][0]["kind"], "demo_fixture")
        self.assertIn("虚构", task["goal"])
        self.assertIn("资料未说明是否提供托管服务", task["sources"][0]["content"])

    def test_overrides_do_not_mutate_future_templates(self):
        overrides = {"deployment": "必须自托管", "license": "仅使用用户清单", "document_type": "Markdown"}
        first = create_scenario(self.store, "knowledge-base", "first", constraints=overrides)
        second = create_scenario(self.store, "knowledge-base", "second")
        self.assertEqual(first["constraints"], overrides)
        self.assertNotIn("document_type", second["constraints"])
        self.assertIn("尚未确认", second["constraints"]["deployment"])

    def test_unknown_scenario_and_invalid_constraint_are_rejected(self):
        with self.assertRaises(DomainError):
            create_scenario(self.store, "unknown", "project")
        with self.assertRaises(DomainError):
            create_scenario(self.store, "knowledge-base", "project", constraints={"bad-key": "value"})
        with self.store.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)

    def test_full_demo_has_clarification_revision_and_private_artifacts(self):
        with patch("dotenv.load_dotenv", side_effect=AssertionError("Offline demo must not load credentials")):
            result = run_knowledge_base_demo(self.store)
        self.assertTrue(result["all_mechanical_checks_passed"])
        self.assertTrue(all(result["checks"].values()))
        self.assertEqual(result["model_calls"], {"clarification": 1, "waiting_rerun": 0, "initial": 7, "revision": 5})
        self.assertEqual(result["review_summary"], {"needs_review": 0, "revised": 2, "preserved": 1, "added": 0})
        directory = Path(result["reports"])
        self.assertEqual(len(list(directory.iterdir())), 8)
        for artifact in directory.iterdir():
            self.assertEqual(artifact.stat().st_mode & 0o777, 0o600)
        self.assertIn("不可沿用为当前建议", (directory / "04-review-before.md").read_text())
        self.assertIn("保留未改写", (directory / "06-review-after.md").read_text())

    def test_demo_repeats_without_reusing_confirmed_memory(self):
        first = run_knowledge_base_demo(self.store)
        self.store.memory_action(first["pending_memories"][0], "confirm")
        second = run_knowledge_base_demo(self.store)
        self.assertNotEqual(first["project"], second["project"])
        self.assertNotEqual(first["task_id"], second["task_id"])
        self.assertEqual(self.store.memories(second["project"], True), [])


if __name__ == "__main__":
    unittest.main()
