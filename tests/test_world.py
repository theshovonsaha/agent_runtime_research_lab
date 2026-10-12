"""Tests for the world state model."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import unittest
from world import Fact, WorldBuilder, WorldState, EffectRecord


class TestFact(unittest.TestCase):
    def test_mutation_increments_version(self):
        f = Fact("authorized", "alice", True, 1, "initial")
        f2 = f.mutated(False)
        self.assertEqual(f2.version, 2)
        self.assertEqual(f2.value, False)
        self.assertEqual(f2.name, "authorized")

    def test_key_tuple(self):
        f = Fact("evidence_fresh", "res-1", True, 1, "initial")
        self.assertEqual(f.key(), ("evidence_fresh", "res-1"))

    def test_bumped_increments_version_preserves_value(self):
        f = Fact("approval", "bob", True, 3, "initial")
        b = f.bumped()
        self.assertEqual(b.version, 4)
        self.assertEqual(b.value, True)


class TestWorldBuilder(unittest.TestCase):
    def test_basic_set_and_get(self):
        w = WorldBuilder().set("authorized", "alice", True).build()
        self.assertTrue(w.get("authorized", "alice"))
        self.assertIsNone(w.get("missing", "alice"))

    def test_version_increments_on_overwrite(self):
        wb = WorldBuilder()
        wb.set("authorized", "alice", True)
        wb.set("authorized", "alice", False)  # overwrite
        w = wb.build()
        f = w.facts[("authorized", "alice")]
        self.assertEqual(f.version, 2)
        self.assertFalse(f.value)

    def test_push_effect(self):
        w = (WorldBuilder()
             .set("authorized", "alice", True)
             .push_effect("read", "alice", "file-x", "success")
             .build())
        self.assertEqual(len(w.path), 1)
        self.assertTrue(w.has_prior_effect("read", "file-x"))
        self.assertFalse(w.has_prior_effect("write", "file-x"))


class TestWorldState(unittest.TestCase):
    def _make_world(self):
        return (WorldBuilder()
                .set("authorized", "alice", True)
                .set("evidence_fresh", "res-1", True)
                .set("metadata_version", "res-1", 42)
                .build())

    def test_with_fact_creates_new_state(self):
        w = self._make_world()
        wv0 = w.world_version
        new_fact = Fact("authorized", "alice", False, 2, "revocation")
        w2 = w.with_fact(new_fact)
        self.assertEqual(w2.world_version, wv0 + 1)
        self.assertFalse(w2.get("authorized", "alice"))
        # Original unchanged.
        self.assertTrue(w.get("authorized", "alice"))

    def test_fingerprint_all_changes_on_any_mutation(self):
        w = self._make_world()
        fp1 = w.fingerprint_all()
        w2 = w.with_fact(Fact("metadata_version", "res-1", 999, 2, "bump"))
        self.assertNotEqual(fp1, w2.fingerprint_all())

    def test_fingerprint_keys_ignores_irrelevant(self):
        w = self._make_world()
        keys = [("authorized", "alice"), ("evidence_fresh", "res-1")]
        fp1 = w.fingerprint_keys(keys)
        # Change only metadata_version (not in keys).
        w2 = w.with_fact(Fact("metadata_version", "res-1", 999, 2, "bump"))
        fp2 = w2.fingerprint_keys(keys)
        self.assertEqual(fp1, fp2, "Irrelevant metadata must not change dep-key fingerprint")

    def test_fingerprint_keys_changes_on_relevant_mutation(self):
        w = self._make_world()
        keys = [("authorized", "alice"), ("evidence_fresh", "res-1")]
        fp1 = w.fingerprint_keys(keys)
        w2 = w.with_fact(Fact("authorized", "alice", False, 2, "revocation"))
        fp2 = w2.fingerprint_keys(keys)
        self.assertNotEqual(fp1, fp2, "Relevant fact change must change fingerprint")

    def test_count_effects(self):
        w = (WorldBuilder()
             .push_effect("read", "alice", "file-x", "success")
             .push_effect("read", "alice", "file-x", "success")
             .push_effect("write", "alice", "file-x", "success")
             .build())
        self.assertEqual(w.count_effects("read", "file-x"), 2)
        self.assertEqual(w.count_effects("write", "file-x"), 1)
        self.assertEqual(w.count_effects("delete"), 0)

    def test_last_outcome(self):
        w = (WorldBuilder()
             .push_effect("read", "alice", "file-x", "success")
             .push_effect("read", "alice", "file-x", "partial")
             .build())
        self.assertEqual(w.last_outcome("read", "file-x"), "partial")
        self.assertIsNone(w.last_outcome("write", "file-x"))


if __name__ == "__main__":
    unittest.main()
