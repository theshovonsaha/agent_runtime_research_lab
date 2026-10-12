"""Tests for the scenario generator."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import unittest
from scenarios import (generate_scenarios, FAMILIES,
                       ProposalEvent, WorldMutationEvent, CommitAttemptEvent,
                       PolicyRolloverEvent)
from world import WorldState


class TestScenarioStructure(unittest.TestCase):
    def setUp(self):
        self.scenarios = generate_scenarios(seed=1000, n=100)

    def test_all_families_present(self):
        found = {s.family for s in self.scenarios}
        # Not all families may appear in 100, but at least 6 should.
        self.assertGreaterEqual(len(found), 6,
                                f"Only {len(found)} families in 100 scenarios: {found}")

    def test_scenario_ids_unique(self):
        ids = [s.scenario_id for s in self.scenarios]
        self.assertEqual(len(ids), len(set(ids)))

    def test_initial_world_is_worldstate(self):
        for sc in self.scenarios:
            self.assertIsInstance(sc.initial_world, WorldState)

    def test_events_are_typed(self):
        valid_types = (ProposalEvent, WorldMutationEvent,
                       CommitAttemptEvent, PolicyRolloverEvent)
        for sc in self.scenarios:
            for ev in sc.events:
                self.assertIsInstance(ev, valid_types,
                                      f"Unknown event type in {sc.scenario_id}")

    def test_proposal_events_have_action(self):
        for sc in self.scenarios:
            for ev in sc.events:
                if isinstance(ev, ProposalEvent):
                    self.assertTrue(ev.action,
                                    f"Empty action in {sc.scenario_id}")
                    self.assertTrue(ev.actor)
                    self.assertTrue(ev.target)

    def test_mutation_events_have_mutations(self):
        for sc in self.scenarios:
            for ev in sc.events:
                if isinstance(ev, WorldMutationEvent):
                    self.assertGreater(len(ev.mutations), 0,
                                       f"Empty mutation list in {sc.scenario_id}")


class TestRevocationScenarios(unittest.TestCase):
    def test_revocation_has_proposal_then_mutation_then_commit(self):
        scenarios = [s for s in generate_scenarios(seed=42, n=200)
                     if s.family == "revocation"]
        self.assertGreater(len(scenarios), 0, "Should have at least one revocation scenario")
        for sc in scenarios:
            kinds = [type(e).__name__ for e in sc.events]
            self.assertIn("ProposalEvent",      kinds, sc.scenario_id)
            self.assertIn("WorldMutationEvent", kinds, sc.scenario_id)
            self.assertIn("CommitAttemptEvent", kinds, sc.scenario_id)

    def test_revocation_commit_expected_not_approved(self):
        scenarios = [s for s in generate_scenarios(seed=42, n=200)
                     if s.family == "revocation"]
        for sc in scenarios:
            commits = [e for e in sc.events if isinstance(e, CommitAttemptEvent)]
            self.assertTrue(any(not c.expected_approved for c in commits),
                            f"Revocation scenario {sc.scenario_id} should have "
                            f"at least one commit expected_approved=False")


class TestIrrelevantNoiseScenarios(unittest.TestCase):
    def test_noise_scenarios_have_multiple_metadata_mutations(self):
        scenarios = [s for s in generate_scenarios(seed=42, n=200)
                     if s.family == "irrelevant_noise"]
        for sc in scenarios:
            mutations = [e for e in sc.events if isinstance(e, WorldMutationEvent)]
            self.assertGreater(len(mutations), 0,
                               f"Noise scenario should have metadata mutations")
            # At least one mutation should touch metadata_version.
            meta_mutations = [
                e for e in mutations
                if any(m[0] in ("metadata_version", "request_id")
                       for m in e.mutations)
            ]
            self.assertGreater(len(meta_mutations), 0,
                               f"Noise scenario should have irrelevant mutations")


class TestPathDependentScenarios(unittest.TestCase):
    def test_path_dependent_has_sensitive_read_mutation(self):
        scenarios = [s for s in generate_scenarios(seed=42, n=200)
                     if s.family == "path_dependent"]
        for sc in scenarios:
            mutations = [e for e in sc.events if isinstance(e, WorldMutationEvent)]
            path_mutations = [
                e for e in mutations
                if any(m[0] == "prior_sensitive_read" for m in e.mutations)
            ]
            self.assertGreater(len(path_mutations), 0,
                               f"Path-dependent scenario {sc.scenario_id} "
                               f"should mutate prior_sensitive_read")


class TestPolicyRolloverScenarios(unittest.TestCase):
    def test_rollover_scenarios_have_policy_rollover_event(self):
        scenarios = [s for s in generate_scenarios(seed=42, n=200)
                     if s.family == "policy_rollover"]
        for sc in scenarios:
            rollovers = [e for e in sc.events if isinstance(e, PolicyRolloverEvent)]
            self.assertGreater(len(rollovers), 0,
                               f"Policy rollover scenario {sc.scenario_id} "
                               f"should have a PolicyRolloverEvent")


if __name__ == "__main__":
    unittest.main()
