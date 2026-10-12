
from src.world import WorldBuilder


def test_policy_version_rollback_can_recreate_old_fingerprint():
    world_v7 = (
        WorldBuilder(policy_version=7)
        .set("authorized", "alice", True)
        .build()
    )

    original = world_v7.fingerprint_keys([("authorized", "alice")])

    world_v8 = world_v7.with_policy_version(8)
    assert world_v8.fingerprint_keys([("authorized", "alice")]) != original

    world_back_to_7 = world_v8.with_policy_version(7)
    assert world_back_to_7.fingerprint_keys([("authorized", "alice")]) == original
