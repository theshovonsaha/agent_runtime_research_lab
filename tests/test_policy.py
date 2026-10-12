"""Tests for the policy engine and dependency system."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import unittest
from world import WorldBuilder
from policy import Policy, reference_oracle, Dependency, deps_cache_key


def _allow_world(actor="alice", target="res-1", action="read"):
    return (WorldBuilder()
            .set("authorized",           actor,  True)
            .set("parent_scope_ok",      actor,  True)
            .set("approval",             actor,  True)
            .set("path_tag",             actor,  "ordinary")
            .set("prior_sensitive_read", actor,  False)
            .set("prior_effect_succeeded", target, False)
            .set("protected",            target, False)
            .set("retention_hold",       target, False)
            .set("evidence_fresh",       target, True)
            .set("metadata_version",     target, 1)
            .build())


class TestPolicyAndOracle(unittest.TestCase):
    def setUp(self):
        self.policy = Policy(version=1, proof_rounds=0)

    def _agree(self, world, action, actor="alice", target="res-1"):
        """Policy evaluator and oracle must agree."""
        r = self.policy.evaluate(world, action, actor, target)
        o = reference_oracle(world, action, actor, target)
        self.assertEqual(r.verdict, o,
                         f"Disagreement on {action}: evaluator={r.verdict} oracle={o}")
        return r

    def test_allow_baseline(self):
        w = _allow_world()
        r = self._agree(w, "read")
        self.assertEqual(r.verdict, "ALLOW")

    def test_deny_not_authorized(self):
        w = (WorldBuilder()
             .set("authorized",       "alice", False)
             .set("parent_scope_ok",  "alice", True)
             .set("approval",         "alice", True)
             .set("path_tag",         "alice", "ordinary")
             .set("prior_sensitive_read", "alice", False)
             .set("prior_effect_succeeded", "res-1", False)
             .set("protected",        "res-1", False)
             .set("retention_hold",   "res-1", False)
             .set("evidence_fresh",   "res-1", True)
             .build())
        self._agree(w, "read")

    def test_deny_protected_resource_delete(self):
        w = _allow_world()
        w = w.with_fact(
            w.facts[("protected", "res-1")].mutated(True))
        r = self._agree(w, "delete")
        self.assertEqual(r.verdict, "DENY")

    def test_escalate_missing_approval_write(self):
        w = _allow_world()
        w = w.with_fact(w.facts[("approval", "alice")].mutated(False))
        r = self._agree(w, "write")
        self.assertEqual(r.verdict, "ESCALATE")

    def test_deny_export_after_sensitive_read(self):
        w = _allow_world()
        w = w.with_fact(w.facts[("prior_sensitive_read", "alice")].mutated(True))
        r = self._agree(w, "export")
        self.assertEqual(r.verdict, "DENY")

    def test_deny_retry_after_success(self):
        w = _allow_world()
        w = w.with_fact(w.facts[("prior_effect_succeeded", "res-1")].mutated(True))
        r = self._agree(w, "retry")
        self.assertEqual(r.verdict, "DENY")

    def test_deny_untrusted_delegation(self):
        w = _allow_world()
        w = w.with_fact(w.facts[("path_tag", "alice")].mutated("untrusted_delegation"))
        r = self._agree(w, "delegate")
        self.assertEqual(r.verdict, "DENY")

    def test_dependencies_are_returned(self):
        w = _allow_world()
        r = self.policy.evaluate(w, "read", "alice", "res-1")
        self.assertGreater(len(r.dependencies), 0)
        names = {d.name for d in r.dependencies}
        self.assertIn("authorized", names)

    def test_oracle_evaluator_agree_on_1000_random(self):
        """Fuzz test: oracle and evaluator must agree on every generated world."""
        from scenarios import generate_scenarios
        scenarios = generate_scenarios(seed=42, n=200)
        for sc in scenarios:
            for event in sc.events:
                from scenarios import ProposalEvent
                if isinstance(event, ProposalEvent):
                    w = sc.initial_world
                    ev = reference_oracle(w, event.action, event.actor, event.target)
                    r  = self.policy.evaluate(w, event.action, event.actor, event.target)
                    self.assertEqual(r.verdict, ev,
                                     f"Disagree in {sc.scenario_id}: "
                                     f"evaluator={r.verdict} oracle={ev}")


class TestDependencyInvalidation(unittest.TestCase):
    def setUp(self):
        self.policy = Policy(version=1, proof_rounds=0)

    def test_relevant_mutation_changes_cache_key(self):
        w = _allow_world()
        r1 = self.policy.evaluate(w, "read", "alice", "res-1")
        # Mutate a relevant fact.
        w2 = w.with_fact(w.facts[("authorized", "alice")].mutated(False))
        r2 = self.policy.evaluate(w2, "read", "alice", "res-1")
        self.assertNotEqual(r1.cache_key(1), r2.cache_key(1),
                            "Relevant mutation must change cache key")

    def test_irrelevant_mutation_preserves_cache_key(self):
        from world import Fact
        w = _allow_world()
        r1 = self.policy.evaluate(w, "read", "alice", "res-1")
        # Mutate only metadata_version (not a policy fact).
        new_meta = Fact("metadata_version", "res-1", 9999, 2, "noise")
        w2 = w.with_fact(new_meta)
        r2 = self.policy.evaluate(w2, "read", "alice", "res-1")
        self.assertEqual(r1.cache_key(1), r2.cache_key(1),
                         "Irrelevant mutation must NOT change cache key")

    def test_dependency_still_valid_after_no_change(self):
        w = _allow_world()
        r = self.policy.evaluate(w, "read", "alice", "res-1")
        for dep in r.dependencies:
            self.assertTrue(dep.still_valid(w),
                            f"Dependency {dep.name} should still be valid")

    def test_dependency_invalid_after_version_bump(self):
        w = _allow_world()
        r = self.policy.evaluate(w, "read", "alice", "res-1")
        # Bump the version of an existing dependency without changing value.
        auth_dep = next(d for d in r.dependencies if d.name == "authorized")
        w2 = w.with_fact(w.facts[("authorized", "alice")].bumped())
        self.assertFalse(auth_dep.still_valid(w2),
                         "Version bump must invalidate dependency")

    def test_policy_version_in_cache_key(self):
        w = _allow_world()
        r = self.policy.evaluate(w, "read", "alice", "res-1")
        k1 = r.cache_key(1)
        k2 = r.cache_key(2)
        self.assertNotEqual(k1, k2,
                            "Different policy versions must produce different keys")


if __name__ == "__main__":
    unittest.main()
