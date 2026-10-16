from pathlib import Path
import tempfile
import unittest

from agent4x.domain import ClaimDraft, DomainError, TaskAssessment
from agent4x.review import build_review, render_review
from agent4x.store import Store


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name))
        self.task_id = self.store.new_task("选型", "review", {"deployment": "允许托管", "license": "示例许可"})["id"]
        self.source_id = self.store.add_source(self.task_id, "Fixture", "fixture.md", "Alpha仅支持托管。Beta支持内网自托管。两者使用示例许可。", "demo_fixture")["source_id"]
        self.deployment = self.save("可考虑Alpha", ["deployment"])
        self.license = self.save("匹配示例许可", ["license"])

    def save(self, text, dependencies, version=1, claim_id=""):
        draft = ClaimDraft(
            text=text, kind="inference", depends_on=dependencies, limitations="虚构资料，未经人工批准",
            evidence=[{"source_id": self.source_id, "quote": "Alpha仅支持托管。Beta支持内网自托管。两者使用示例许可。"}],
        )
        return self.store.save_claim(self.task_id, draft, version, claim_id)

    def amend(self):
        return self.store.amend(self.task_id, "deployment", "必须内网自托管")

    def ready(self, version=2):
        return self.store.assess(self.task_id, TaskAssessment(status="ready", reason="用户条件明确", questions=[]), version)

    def test_review_before_any_change_is_rejected(self):
        with self.assertRaisesRegex(DomainError, "before any constraint change"):
            build_review(self.store, self.task_id)

    def test_invalid_baselines_are_rejected(self):
        self.amend()
        for baseline in (-1, 0, 2, 3, True, "1"):
            with self.subTest(baseline=baseline), self.assertRaises(DomainError):
                build_review(self.store, self.task_id, baseline)

    def test_pending_review_exposes_old_advice_and_preserves_other_claim(self):
        self.amend()
        review = build_review(self.store, self.task_id)
        self.assertEqual(review["state"], "needs_review")
        self.assertEqual(review["summary"], {"needs_review": 1, "revised": 0, "preserved": 1, "added": 0})
        self.assertEqual(review["claims"][0]["before"]["text"], "可考虑Alpha")
        report = render_review(review)
        self.assertIn("不可沿用为当前建议", report)
        self.assertIn("保留未改写", report)
        self.assertIn(self.source_id, report)
        self.assertIn("SHA-256", report)

    def test_revised_claim_has_before_after_and_original_id(self):
        self.amend()
        self.ready()
        self.save("撤回Alpha，改为Beta候选", ["deployment"], 2, self.deployment["claim_id"])
        review = build_review(self.store, self.task_id)
        entry = review["claims"][0]
        self.assertEqual(entry["claim_id"], self.deployment["claim_id"])
        self.assertEqual(entry["change"], "revised")
        self.assertEqual(entry["before"]["text"], "可考虑Alpha")
        self.assertEqual(entry["after"]["text"], "撤回Alpha，改为Beta候选")
        self.assertEqual(entry["after"]["revision"], 2)
        self.assertEqual(review["state"], "recorded_unverified")
        self.assertEqual(review["claims"][1]["after"]["contract_version"], 1)
        self.assertIn("尚未经人工批准", render_review(review))

    def test_same_text_new_revision_is_not_labeled_unchanged(self):
        self.amend()
        self.save("可考虑Alpha", ["deployment"], 2, self.deployment["claim_id"])
        review = build_review(self.store, self.task_id)
        self.assertEqual(review["claims"][0]["change"], "revised")

    def test_new_claim_since_baseline_is_added(self):
        self.amend()
        self.save("新发现", ["deployment"], 2)
        review = build_review(self.store, self.task_id)
        self.assertEqual(review["summary"]["added"], 1)
        self.assertIsNone(review["claims"][-1]["before"])

    def test_new_constraint_conservatively_marks_all_for_review(self):
        self.store.amend(self.task_id, "hardware", "只有CPU")
        self.assertEqual(build_review(self.store, self.task_id)["summary"]["needs_review"], 2)

    def test_explicit_and_default_baseline_across_multiple_changes(self):
        self.amend()
        self.save("Beta候选", ["deployment"], 2, self.deployment["claim_id"])
        self.store.amend(self.task_id, "license", "用户更新许可范围")
        latest = build_review(self.store, self.task_id)
        whole = build_review(self.store, self.task_id, 1)
        self.assertEqual(latest["since_version"], 2)
        self.assertEqual(latest["claims"][0]["change"], "preserved")
        self.assertEqual(whole["claims"][0]["change"], "revised")
        self.assertEqual(len(latest["constraint_changes"]), 1)
        self.assertEqual(len(whole["constraint_changes"]), 2)

    def test_baseline_uses_last_revision_within_contract_version(self):
        self.save("同版本修正后的Alpha候选", ["deployment"], 1, self.deployment["claim_id"])
        self.amend()
        self.save("Beta候选", ["deployment"], 2, self.deployment["claim_id"])
        review = build_review(self.store, self.task_id, 1)
        self.assertEqual(review["claims"][0]["before"]["revision"], 2)
        self.assertEqual(review["claims"][0]["before"]["text"], "同版本修正后的Alpha候选")

    def test_waiting_state_overrides_pending_review_and_only_current_questions_show(self):
        self.store.question(self.task_id, "旧问题", "旧条件", 1)
        self.amend()
        self.store.question(self.task_id, "新问题", "新条件", 2)
        review = build_review(self.store, self.task_id)
        self.assertEqual(review["state"], "needs_clarification")
        self.assertEqual([question["question"] for question in review["questions"]], ["新问题"])
        report = render_review(review)
        self.assertIn("等待用户澄清", report)
        self.assertNotIn("旧问题", report)

    def test_empty_claims_do_not_look_like_success(self):
        task_id = self.store.new_task("还未读资料", "empty", {"deployment": "允许托管"})["id"]
        self.store.amend(task_id, "deployment", "必须自托管")
        review = build_review(self.store, task_id)
        self.assertEqual(review["state"], "no_claims")
        self.assertIn("尚无已登记结论", render_review(review))

    def test_unassessed_contract_does_not_look_like_completed_review(self):
        self.amend()
        self.save("Beta候选", ["deployment"], 2, self.deployment["claim_id"])
        self.assertEqual(build_review(self.store, self.task_id)["state"], "unassessed")

    def test_review_is_read_only_and_noop_amend_does_not_add_changes(self):
        self.amend()
        self.store.amend(self.task_id, "deployment", "必须内网自托管")
        before_task = self.store.task(self.task_id)
        before_events = self.store.events(self.task_id)
        review = build_review(self.store, self.task_id)
        render_review(review)
        self.assertEqual(len(review["constraint_changes"]), 1)
        self.assertEqual(self.store.task(self.task_id), before_task)
        self.assertEqual(self.store.events(self.task_id), before_events)

    def test_unresolved_items_remain_visible_after_review_finishes(self):
        self.amend()
        self.ready()
        self.save("Beta候选", ["deployment"], 2, self.deployment["claim_id"])
        unknown = ClaimDraft(text="运维投入未知", kind="unresolved", depends_on=["deployment"], limitations="缺少资料")
        saved = self.store.save_claim(self.task_id, unknown, 2)
        review = build_review(self.store, self.task_id)
        self.assertEqual(review["unresolved_claim_ids"], [saved["claim_id"]])
        self.assertEqual(review["summary"]["needs_review"], 0)
        self.assertIn("仍有未解决项：1", render_review(review))
        self.assertEqual(review["sources"][0]["origin"], "fixture.md")
        self.assertNotIn("content", review["sources"][0])


if __name__ == "__main__":
    unittest.main()
