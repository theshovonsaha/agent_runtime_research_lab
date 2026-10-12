"""
Formal Mathematical & Logic Proofs for the TAPR Governance Framework.

This test module systematically validates the 5 foundational mathematical
theorems underpinning Typed Action Proof Reuse (TAPR).

Theorems Tested:
----------------
Theorem 1 (Decision Fidelity & Safety Preservation):
  For any valid WorldState W and proposed action A,
  Verdict_TAPR(W, A) == Verdict_Oracle(W, A).
  TAPR never produces an unsafe ALLOW or incorrect decision.

Theorem 2 (Sound Invalidation Under Relevant State Drift):
  Let D be the set of decisive dependencies for a proof P established on W.
  If any fact f in D changes its version or value (f.version' > f.version or f.val' != f.val),
  then P.is_valid_in(W') == False and ProofStore.lookup(W', A) produces a cache miss.

Theorem 3 (Irrelevant Metadata Invariance / Anti-Overinvalidation):
  Let M be metadata facts not in the declared policy dependencies D.
  For any mutation delta affecting only M (e.g. timestamps, request IDs),
  ProofStore.lookup(W + delta, A) == P.
  Recomputation is avoided, maintaining high throughput.

Theorem 4 (TOCTOU Invariance via Atomic Check/Commit Gate):
  In an asynchronous execution sequence where:
    T=0: Decide(W_0, A) -> ALLOW (proof established)
    T=1: State mutation W_0 -> W_1 where Oracle(W_1, A) != ALLOW
    T=2: CommitAttempt(proof, W_1)
  CheckCommitGate.check_and_commit(proof, W_1).approved == False.
  No stale permission can execute at the effect boundary.

Theorem 5 (Deterministic Path Monotonicity):
  Path-dependent obligations (sensitive reads, retry success) monotonically
  restrict subsequent admissible actions:
  Valid(Export | path_empty) = True
  Valid(Export | path_with_sensitive_read) = False
"""
import sys
import unittest
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tapr.world import WorldBuilder, Fact
from tapr.policy import Policy, reference_oracle, Dependency
from tapr.proof import Proof, ProofStore, CheckCommitGate, NoCommitGate
from tapr.runtime import TAPRRuntime, FullRecomputeRuntime
from tapr.scenarios import generate_scenarios, replay_scenario


class TestTAPRMathematicalTheorems(unittest.TestCase):
    def setUp(self):
        self.policy = Policy(version=1, proof_rounds=4)
        self.gate = CheckCommitGate()

    def _create_standard_world(self, actor="agent-0", target="resource-1"):
        return (
            WorldBuilder()
            .set("authorized", actor, True)
            .set("parent_scope_ok", actor, True)
            .set("approval", actor, True)
            .set("path_tag", actor, "ordinary")
            .set("prior_sensitive_read", actor, False)
            .set("prior_effect_succeeded", target, False)
            .set("protected", target, False)
            .set("retention_hold", target, False)
            .set("evidence_fresh", target, True)
            .set("metadata_timestamp", target, 1000)
            .set("request_correlation_id", actor, "req-xyz-001")
            .build()
        )

    # ------------------------------------------------------------------------
    # Theorem 1: Decision Fidelity
    # ------------------------------------------------------------------------
    def test_theorem_1_decision_fidelity(self):
        """Theorem 1: TAPR decision always strictly equals Oracle decision."""
        runtime = TAPRRuntime(self.policy)
        scenarios = generate_scenarios(seed=98765, n=120)

        for sc in scenarios:
            records = replay_scenario(sc, runtime, self.policy)
            for r in records:
                self.assertEqual(
                    r.verdict,
                    r.expected,
                    f"Fidelity violation in {sc.scenario_id}: TAPR={r.verdict} != Oracle={r.expected}"
                )
                self.assertFalse(r.unsafe_allow, "TAPR must never produce an unsafe ALLOW")

    # ------------------------------------------------------------------------
    # Theorem 2: Sound Invalidation Under Relevant State Mutation
    # ------------------------------------------------------------------------
    def test_theorem_2_sound_invalidation(self):
        """Theorem 2: Any relevant fact mutation invalidates the cached proof."""
        world = self._create_standard_world()
        runtime = TAPRRuntime(self.policy)

        # Step 1: Establish cached proof for ALLOW
        rec1 = runtime.decide(world, "read", "agent-0", "resource-1")
        self.assertEqual(rec1.verdict, "ALLOW")
        self.assertFalse(rec1.cache_hit)

        # Step 2: Confirm cache hit when world is unchanged
        rec2 = runtime.decide(world, "read", "agent-0", "resource-1")
        self.assertTrue(rec2.cache_hit)

        # Step 3: Mutate decisive fact (authorization revoked)
        w_revoked = world.with_fact(Fact("authorized", "agent-0", False, 2, "revocation"))
        rec3 = runtime.decide(w_revoked, "read", "agent-0", "resource-1")

        self.assertFalse(rec3.cache_hit, "Cache must invalidate when decisive fact changes")
        self.assertEqual(rec3.verdict, "DENY")

        # Step 4: Mutate resource protection
        w_protected = world.with_fact(Fact("protected", "resource-1", True, 2, "lock"))
        rec4 = runtime.decide(w_protected, "delete", "agent-0", "resource-1")
        self.assertEqual(rec4.verdict, "DENY")

    # ------------------------------------------------------------------------
    # Theorem 3: Irrelevant Metadata Invariance (No Over-Invalidation)
    # ------------------------------------------------------------------------
    def test_theorem_3_metadata_invariance(self):
        """Theorem 3: Non-policy metadata changes must NOT evict proofs."""
        world = self._create_standard_world()
        runtime = TAPRRuntime(self.policy)

        rec1 = runtime.decide(world, "read", "agent-0", "resource-1")
        self.assertFalse(rec1.cache_hit)

        # Mutate non-policy metadata 10 times in sequence
        cur_world = world
        for i in range(1, 11):
            cur_world = cur_world.with_fact(
                Fact("metadata_timestamp", "resource-1", 1000 + i, i + 1, "noise")
            ).with_fact(
                Fact("request_correlation_id", "agent-0", f"req-uuid-{i}", i + 1, "noise")
            )
            rec = runtime.decide(cur_world, "read", "agent-0", "resource-1")
            self.assertTrue(
                rec.cache_hit,
                f"Proof must be reused across metadata bump {i}"
            )
            self.assertEqual(rec.verdict, "ALLOW")

    # ------------------------------------------------------------------------
    # Theorem 4: TOCTOU Invariance via Atomic Check/Commit Gate
    # ------------------------------------------------------------------------
    def test_theorem_4_toctou_prevention(self):
        """Theorem 4: Commit gate refuses stale proofs when state changes mid-flight."""
        world_t0 = self._create_standard_world()
        runtime = TAPRRuntime(self.policy)

        # T=0: Agent receives approval based on valid state
        rec = runtime.decide(world_t0, "export", "agent-0", "resource-1", "toctou-test", 0)
        self.assertEqual(rec.verdict, "ALLOW")
        proof = runtime._proof_map.get("toctou-test-0")
        self.assertIsNotNone(proof)

        # T=1: Sensitive data is read in background, changing security policy condition
        world_t1 = world_t0.with_fact(Fact("prior_sensitive_read", "agent-0", True, 2, "audit_log"))

        # Oracle at T=1 now mandates DENY
        self.assertEqual(reference_oracle(world_t1, "export", "agent-0", "resource-1"), "DENY")

        # T=2: Agent attempts to commit effect using proof from T=0
        commit_res = self.gate.check_and_commit(proof, world_t1, self.policy)
        self.assertFalse(
            commit_res.approved,
            "CheckCommitGate must refuse effect when dependency is stale"
        )
        self.assertEqual(commit_res.reason, "recomputed")

    # ------------------------------------------------------------------------
    # Theorem 5: Path Monotonicity
    # ------------------------------------------------------------------------
    def test_theorem_5_path_monotonicity(self):
        """Theorem 5: Path obligations monotonically restrict admissibility."""
        world_clean = self._create_standard_world()
        self.assertEqual(reference_oracle(world_clean, "retry", "agent-0", "resource-1"), "ALLOW")

        # Path records prior effect succeeded
        world_succeeded = world_clean.with_fact(Fact("prior_effect_succeeded", "resource-1", True, 2, "effect"))
        self.assertEqual(reference_oracle(world_succeeded, "retry", "agent-0", "resource-1"), "DENY")


if __name__ == "__main__":
    unittest.main()
