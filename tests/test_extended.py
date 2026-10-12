"""Tests for Concurrent Race Harness, Intent Extractor, and OPA/Cedar Adapters."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from concurrent_harness import run_concurrency_race
from intent_extractor import evaluate_intent_layer, DeterministicIntentExtractor
from adapters.opa_cedar_adapters import OPA_REGO_POLICY, CEDAR_POLICY_SPEC, OPAEngineAdapter, CedarEngineAdapter
from world import WorldBuilder


class TestExtendedResearchComponents(unittest.TestCase):
    def test_concurrent_harness_catches_toctou(self):
        """Atomic CAS gate must produce 0 unsafe commits, while negative control catches races."""
        safe_res = run_concurrency_race(num_threads=2, operations_per_thread=40, mutation_frequency=0.25, use_cas_gate=True)
        self.assertEqual(safe_res.unsafe_commits, 0, "Safe CAS gate must not permit any TOCTOU violations")

        unsafe_res = run_concurrency_race(num_threads=2, operations_per_thread=40, mutation_frequency=0.25, use_cas_gate=False)
        self.assertGreater(unsafe_res.unsafe_commits, 0, "Unsafe baseline must detect TOCTOU race occurrences")

    def test_layer_a_intent_extractor(self):
        """Layer A must correctly classify intents and escalate ambiguous inputs."""
        extractor = DeterministicIntentExtractor()
        prop, amb = extractor.extract("Please view resource-1 now")
        self.assertFalse(amb)
        self.assertIsNotNone(prop)
        self.assertEqual(prop.action, "read")
        self.assertEqual(prop.target, "resource-1")

        _, amb_flag = extractor.extract("Just do whatever you want with the system")
        self.assertTrue(amb_flag, "Ambiguous instruction must trigger escalation")

    def test_intent_layer_suite_benchmarks_accurately(self):
        metrics = evaluate_intent_layer()
        self.assertGreater(metrics["action_accuracy"], 0.85)
        self.assertGreater(metrics["target_accuracy"], 0.85)
        self.assertGreater(metrics["ambiguity_escalations"], 0)

    def test_opa_and_cedar_policies_syntactically_present(self):
        """Verify formal OPA and Cedar policy definitions exist and serialization matches."""
        self.assertIn("package agent.governance", OPA_REGO_POLICY)
        self.assertIn("forbid", CEDAR_POLICY_SPEC)
        self.assertIn("permit", CEDAR_POLICY_SPEC)

        adapter = OPAEngineAdapter()
        w = WorldBuilder().set("authorized", "agent-0", True).build()
        serialized = adapter.serialize_input(w, "read", "agent-0", "res-1")
        self.assertEqual(serialized["action"], "read")
        self.assertEqual(serialized["authorized"], True)


if __name__ == "__main__":
    unittest.main()
