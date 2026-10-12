"""Tests for the v2 benchmark runner and scenario replay."""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from benchmark import run_once, replay_scenario, summarise
from policy import Policy
from runtime import make_runtime
from scenarios import generate_scenarios

class BenchmarkRunnerTests(unittest.TestCase):
    def test_replay_scenario_runs_cleanly(self):
        policy = Policy(version=1, proof_rounds=0)
        rt = make_runtime("tapr_dep_cache", policy)
        scenarios = generate_scenarios(seed=101, n=5)
        for sc in scenarios:
            recs = replay_scenario(sc, rt, policy)
            self.assertIsInstance(recs, list)
            for r in recs:
                self.assertTrue(hasattr(r, "verdict"))
                self.assertTrue(hasattr(r, "expected"))

    def test_run_once_produces_records(self):
        scenarios = generate_scenarios(seed=202, n=10)
        systems = ["full_recompute", "tapr_dep_cache"]
        records = run_once(scenarios, proof_rounds=0, systems=systems, seed=303)
        self.assertGreater(len(records), 0)
        for s in systems:
            summary = summarise(records, s, rep=0)
            self.assertEqual(summary["system"], s)
            self.assertIn("accuracy", summary)
            self.assertIn("cache_hit_rate", summary)
            self.assertEqual(summary["accuracy"], 1.0)

    def test_deterministic_scenario_generation(self):
        a = generate_scenarios(seed=999, n=20)
        b = generate_scenarios(seed=999, n=20)
        self.assertEqual([s.scenario_id for s in a], [s.scenario_id for s in b])
        self.assertEqual([s.family for s in a], [s.family for s in b])

if __name__ == '__main__':
    unittest.main()
