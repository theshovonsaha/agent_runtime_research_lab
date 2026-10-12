"""
Tests for the runtime governor and proof store.

These tests verify the properties we need for TAPR to be both safe and useful:
  1. TAPR never disagrees with the reference oracle.
  2. TAPR invalidates cached proofs when relevant dependencies change.
  3. TAPR does NOT invalidate when only irrelevant metadata changes.
  4. The commit gate catches stale proofs after revocation.
  5. The commit gate catches stale proofs after evidence expiry.
  6. Coarse cache and stale-proof-accept produce incorrect decisions
     in adversarial scenarios (negative-control verification).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import unittest
from world import WorldBuilder, Fact
from policy import Policy
from runtime import (make_runtime, SAFE_SYSTEMS, UNSAFE_SYSTEMS,
                     TAPRRuntime, CoarseCacheRuntime, StaleProofAcceptRuntime,
                     NoCommitGateRuntime, FullRecomputeRuntime)
from scenarios import generate_scenarios
from benchmark import replay_scenario


def _policy(rounds=0):
    return Policy(version=1, proof_rounds=rounds)


def _allow_world(actor="alice", target="res-1"):
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


class TestTAPRSafety(unittest.TestCase):
    """TAPR must always match the oracle."""

    def test_tapr_matches_oracle_on_generated_scenarios(self):
        policy = _policy()
        runtime = TAPRRuntime(policy)
        scenarios = generate_scenarios(seed=1234, n=150)
        errors = 0
        for sc in scenarios:
            recs = replay_scenario(sc, runtime, policy)
            for r in recs:
                if not r.correct:
                    errors += 1
        self.assertEqual(errors, 0,
                         f"TAPR had {errors} decisions differing from oracle")

    def test_full_recompute_matches_oracle(self):
        policy = _policy()
        runtime = FullRecomputeRuntime(policy)
        scenarios = generate_scenarios(seed=5678, n=100)
        for sc in scenarios:
            recs = replay_scenario(sc, runtime, policy)
            for r in recs:
                self.assertTrue(r.correct,
                    f"Full recompute error: {r.verdict} vs {r.expected} "
                    f"in {r.scenario_id}")


class TestCacheInvalidation(unittest.TestCase):
    """Dependency-aware invalidation must fire on relevant changes."""

    def test_cache_miss_after_authorization_revoked(self):
        policy = _policy()
        rt = TAPRRuntime(policy)
        w = _allow_world()

        # First decision: ALLOW, proof cached.
        r1 = rt.decide(w, "read", "alice", "res-1", "s-001", 0, "test")
        self.assertEqual(r1.verdict, "ALLOW")
        self.assertFalse(r1.cache_hit)

        # Same world: should hit cache.
        r2 = rt.decide(w, "read", "alice", "res-1", "s-001", 1, "test")
        self.assertTrue(r2.cache_hit, "Second identical decision should hit cache")

        # Revoke authorization.
        w2 = w.with_fact(Fact("authorized", "alice", False, 2, "revocation"))
        r3 = rt.decide(w2, "read", "alice", "res-1", "s-001", 2, "test")
        self.assertFalse(r3.cache_hit, "Cache must miss after authorization revoked")
        self.assertEqual(r3.verdict, "DENY")

    def test_no_cache_miss_on_metadata_noise(self):
        policy = _policy()
        rt = TAPRRuntime(policy)
        w = _allow_world()

        r1 = rt.decide(w, "read", "alice", "res-1", "s-002", 0, "test")
        self.assertFalse(r1.cache_hit)

        # Change only metadata_version (irrelevant).
        w2 = w.with_fact(Fact("metadata_version", "res-1", 9999, 2, "noise"))
        r2 = rt.decide(w2, "read", "alice", "res-1", "s-002", 1, "test")
        self.assertTrue(r2.cache_hit,
                        "Irrelevant metadata change must not invalidate TAPR cache")

    def test_cache_miss_on_evidence_expiry(self):
        policy = _policy()
        rt = TAPRRuntime(policy)
        w = _allow_world()

        rt.decide(w, "write", "alice", "res-1", "s-003", 0, "test")
        w2 = w.with_fact(Fact("evidence_fresh", "res-1", False, 2, "expiry"))
        r2 = rt.decide(w2, "write", "alice", "res-1", "s-003", 1, "test")
        self.assertFalse(r2.cache_hit,
                         "Evidence expiry must invalidate cache")


class TestCommitGate(unittest.TestCase):
    """Commit gate must block stale proofs at the effect boundary."""

    def test_commit_gate_blocks_revoked_proof(self):
        from scenarios import generate_scenarios
        # Use the "revocation" scenario family.
        scenarios = [s for s in generate_scenarios(seed=99, n=50)
                     if s.family == "revocation"]
        if not scenarios:
            self.skipTest("No revocation scenarios generated with this seed")

        policy = _policy()
        rt = TAPRRuntime(policy)
        stale_accepted = 0

        for sc in scenarios:
            recs = replay_scenario(sc, rt, policy)
            for r in recs:
                if (r.commit_approved is True
                        and r.commit_expected_approved is False):
                    stale_accepted += 1

        self.assertEqual(stale_accepted, 0,
                         f"Commit gate accepted {stale_accepted} stale revocation proofs")

    def test_no_commit_gate_accepts_stale(self):
        """No-commit-gate runtime should accept stale revocation proofs."""
        from scenarios import generate_scenarios
        scenarios = [s for s in generate_scenarios(seed=99, n=50)
                     if s.family == "revocation"]
        if not scenarios:
            self.skipTest("No revocation scenarios generated with this seed")

        policy = _policy()
        rt = NoCommitGateRuntime(policy)
        stale_accepted = 0

        for sc in scenarios:
            recs = replay_scenario(sc, rt, policy)
            for r in recs:
                if (r.commit_approved is True
                        and r.commit_expected_approved is False):
                    stale_accepted += 1

        self.assertGreater(stale_accepted, 0,
                           "No-commit-gate should accept at least one stale proof "
                           "in revocation scenarios (negative control)")


class TestNegativeControls(unittest.TestCase):
    """Unsafe systems must produce incorrect decisions (validates metrics)."""

    def _run_adversarial(self, system_name, n=150):
        policy = _policy()
        rt = make_runtime(system_name, policy)
        # Use adversarial-heavy mix.
        scenarios = generate_scenarios(seed=777, n=n, weights={
            "revocation":       5,
            "evidence_expiry":  5,
            "path_dependent":   4,
            "retry_safety":     3,
        })
        errors = 0
        for sc in scenarios:
            recs = replay_scenario(sc, rt, policy)
            for r in recs:
                if not r.correct:
                    errors += 1
        return errors

    def test_coarse_cache_has_errors(self):
        errors = self._run_adversarial("coarse_cache")
        self.assertGreater(errors, 0,
                           "Coarse cache must make errors on adversarial scenarios")

    def test_stale_proof_accept_has_errors(self):
        errors = self._run_adversarial("stale_proof_accept")
        self.assertGreater(errors, 0,
                           "Stale proof accept must make errors on adversarial scenarios")

    def test_pathless_policy_has_errors(self):
        errors = self._run_adversarial("pathless_policy")
        self.assertGreater(errors, 0,
                           "Pathless policy must make errors on path-dependent scenarios")

    def test_tapr_has_zero_errors_same_scenarios(self):
        """TAPR must have zero errors on the same adversarial scenarios."""
        errors = self._run_adversarial("tapr_dep_cache")
        self.assertEqual(errors, 0,
                         f"TAPR had {errors} errors on adversarial scenarios")


class TestExactSnapshotCache(unittest.TestCase):
    def test_snapshot_cache_always_correct(self):
        policy = _policy()
        rt = make_runtime("exact_snapshot_cache", policy)
        scenarios = generate_scenarios(seed=321, n=100)
        for sc in scenarios:
            recs = replay_scenario(sc, rt, policy)
            for r in recs:
                self.assertTrue(r.correct,
                    f"Snapshot cache error in {r.scenario_id}: "
                    f"{r.verdict} vs {r.expected}")

    def test_snapshot_misses_on_metadata_change(self):
        """Snapshot cache should miss when metadata changes (unlike TAPR)."""
        policy = _policy()
        rt = make_runtime("exact_snapshot_cache", policy)
        w = _allow_world()

        r1 = rt.decide(w, "read", "alice", "res-1", "s-snap", 0, "test")
        self.assertFalse(r1.cache_hit)

        w2 = w.with_fact(Fact("metadata_version", "res-1", 9999, 2, "noise"))
        r2 = rt.decide(w2, "read", "alice", "res-1", "s-snap", 1, "test")
        self.assertFalse(r2.cache_hit,
                         "Snapshot cache SHOULD miss on metadata change "
                         "(unlike TAPR, which correctly reuses)")


class TestDeterminism(unittest.TestCase):
    def test_same_seed_same_scenarios(self):
        s1 = generate_scenarios(seed=42, n=30)
        s2 = generate_scenarios(seed=42, n=30)
        ids1 = [s.scenario_id for s in s1]
        ids2 = [s.scenario_id for s in s2]
        self.assertEqual(ids1, ids2)
        fams1 = [s.family for s in s1]
        fams2 = [s.family for s in s2]
        self.assertEqual(fams1, fams2)

    def test_different_seeds_different_scenarios(self):
        s1 = generate_scenarios(seed=1, n=20)
        s2 = generate_scenarios(seed=2, n=20)
        fams1 = [s.family for s in s1]
        fams2 = [s.family for s in s2]
        # Very unlikely to be identical across all 20.
        self.assertNotEqual(fams1, fams2)


if __name__ == "__main__":
    unittest.main()
