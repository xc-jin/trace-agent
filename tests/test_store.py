from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError

from agent4x.domain import ClaimDraft, DomainError
from agent4x.evidence import import_source
from agent4x.report import render_report
from agent4x.store import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / "data")
        self.task = self.store.new_task("选择部署方式", "team-a", {"deployment": "允许托管", "license": "宽松许可"})
        self.task_id = self.task["id"]
        self.source_id = self.store.add_source(self.task_id, "Fixture", "fixture.md", "Alpha需要托管。Beta可以自托管。许可为示例宽松许可。", "demo_fixture")["source_id"]

    def draft(self, depends_on=None, quote="Alpha需要托管。"):
        return ClaimDraft(
            text="可考虑Alpha", kind="inference", depends_on=["deployment"] if depends_on is None else depends_on,
            evidence=[{"source_id": self.source_id, "quote": quote}], limitations="仅为课程资料",
        )

    def save(self, draft=None):
        return self.store.save_claim(self.task_id, draft or self.draft(), 1)["claim_id"]

    def test_targeted_invalidation(self):
        affected = self.save()
        unaffected = self.save(self.draft(["license"]))
        result = self.store.amend(self.task_id, "deployment", "只允许自托管")
        self.assertEqual(result["affected"], [affected])
        claims = {entry["id"]: entry for entry in self.store.task(self.task_id)["claims"]}
        self.assertEqual(claims[affected]["status"], "needs_review")
        self.assertEqual(claims[unaffected]["status"], "current")
        self.assertEqual(claims[unaffected]["contract_version"], 1)

    def test_new_constraint_rechecks_all(self):
        claim_id = self.save()
        result = self.store.amend(self.task_id, "budget", "不超过100")
        self.assertEqual(result["affected"], [claim_id])

    def test_missing_dependencies_are_conservative(self):
        claim_id = self.save(self.draft([]))
        self.assertEqual(self.store.amend(self.task_id, "license", "新的条件")["affected"], [claim_id])

    def test_noop_does_not_change_version(self):
        result = self.store.amend(self.task_id, "deployment", "允许托管")
        self.assertFalse(result["changed"])
        self.assertEqual(self.store.task(self.task_id)["contract_version"], 1)

    def test_revision_keeps_history(self):
        claim_id = self.save()
        self.store.amend(self.task_id, "deployment", "必须自托管")
        revised = self.draft(quote="Beta可以自托管。")
        revised.text = "改为建议Beta"
        self.store.save_claim(self.task_id, revised, 2, claim_id)
        claim = self.store.task(self.task_id)["claims"][0]
        self.assertEqual(claim["revision"], 2)
        self.assertEqual(claim["history"][0]["text"], "可考虑Alpha")
        self.assertEqual(claim["status"], "current")
        self.assertIn("改为建议Beta", render_report(self.store, self.task_id))

    def test_invented_quote_rejected_atomically(self):
        with self.assertRaises(DomainError):
            self.save(self.draft(quote="不存在的句子"))
        self.assertEqual(self.store.task(self.task_id)["claims"], [])

    def test_foreign_source_rejected(self):
        other = self.store.new_task("别的任务", "team-a", {})
        with self.assertRaises(DomainError):
            self.store.save_claim(other["id"], self.draft([]), 1)

    def test_stale_contract_rejected(self):
        self.store.amend(self.task_id, "deployment", "新要求")
        with self.assertRaises(DomainError):
            self.save()

    def test_unknown_dependency_rejected(self):
        with self.assertRaises(DomainError):
            self.save(self.draft(["imaginary"]))

    def test_evidence_required_except_unresolved(self):
        draft = ClaimDraft(text="待确认", kind="unresolved", limitations="缺少证据")
        self.save(draft)
        draft.kind = "fact"
        with self.assertRaises(DomainError):
            self.save(draft)

    def test_blank_and_extra_schema_fields_rejected(self):
        with self.assertRaises(ValidationError):
            ClaimDraft(text=" ", kind="unresolved", limitations="待查")
        with self.assertRaises(ValidationError):
            ClaimDraft(text="待查", kind="unresolved", limitations="待查", arbitrary="bad")

    def test_pins_have_exact_offsets_and_hash(self):
        self.save()
        task = self.store.task(self.task_id)
        pin = task["claims"][0]["evidence"][0]
        source = task["sources"][0]
        self.assertEqual(source["content"][pin["start"]:pin["end"]], pin["quote"])
        self.assertEqual(pin["source_sha256"], source["sha256"])

    def test_duplicate_source_is_reused(self):
        source = self.store.task(self.task_id)["sources"][0]
        added = self.store.add_source(self.task_id, "Same", source["origin"], source["content"], source["kind"])
        self.assertTrue(added["duplicate"])
        self.assertEqual(added["source_id"], self.source_id)

    def test_source_search_and_slicing(self):
        self.assertEqual(self.store.search(self.task_id, "自托管")[0]["source_id"], self.source_id)
        self.assertEqual(len(self.store.read_source(self.task_id, self.source_id, 0, 5)["content"]), 5)
        with self.assertRaises(DomainError):
            self.store.read_source(self.task_id, self.source_id, -1)

    def test_memory_confirmation_scope_edit_and_delete(self):
        memory = self.store.propose_memory(self.task_id, "偏好自托管")
        self.assertEqual(self.store.snapshot(self.task_id)["memories"], [])
        self.store.memory_action(memory["id"], "confirm")
        self.assertEqual(len(self.store.snapshot(self.task_id)["memories"]), 1)
        self.assertEqual(self.store.memories("team-b", True), [])
        self.store.memory_action(memory["id"], "edit", "仅当前项目优先自托管")
        self.assertEqual(self.store.snapshot(self.task_id)["memories"], [])
        self.store.memory_action(memory["id"], "confirm")
        self.store.memory_action(memory["id"], "delete")
        self.assertEqual(self.store.memories("team-a"), [])

    def test_clarification_is_versioned(self):
        self.store.question(self.task_id, "允许外网吗？", "影响部署选择", 1)
        self.assertIn("允许外网吗", render_report(self.store, self.task_id))
        self.store.amend(self.task_id, "deployment", "不允许外网")
        self.assertNotIn("## 待澄清问题", render_report(self.store, self.task_id))

    def test_source_import_boundary(self):
        sources = self.root / "sources"
        sources.mkdir()
        (sources / "good.md").write_text("公开演示资料")
        imported = import_source(self.store, self.task_id, sources, "good.md")
        self.assertIn("source_id", imported)
        (sources / ".hidden.md").write_text("hidden")
        (self.root / "outside.md").write_text("outside")
        (sources / "escape.md").symlink_to(self.root / "outside.md")
        (sources / "hidden-link.md").symlink_to(sources / ".hidden.md")
        for value in ("../outside.md", ".hidden.md", "escape.md", "hidden-link.md", str(sources / "good.md")):
            with self.subTest(path=value), self.assertRaises(DomainError):
                import_source(self.store, self.task_id, sources, value)

    def test_source_import_size_and_extension(self):
        (self.root / "large.txt").write_text("a" * 65001)
        (self.root / "code.py").write_text("print('not a source')")
        for value in ("large.txt", "code.py"):
            with self.subTest(path=value), self.assertRaises(DomainError):
                import_source(self.store, self.task_id, self.root, value)

    def test_database_persistence_and_permissions(self):
        reopened = Store(self.root / "data")
        self.assertEqual(reopened.task(self.task_id)["project"], "team-a")
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)

    def test_needs_review_is_explicit_in_report(self):
        self.save()
        self.store.amend(self.task_id, "deployment", "必须自托管")
        report = render_report(self.store, self.task_id)
        self.assertIn("需复核，请勿沿用", report)
        self.assertIn("历史结论，不是当前推荐", report)


if __name__ == "__main__":
    unittest.main()
