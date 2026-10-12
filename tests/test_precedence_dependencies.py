"""Regression tests for dependencies of earlier policy rules."""
import sys
from itertools import product
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tapr.oracle import evaluate as evaluate_oracle
from tapr.policy import Policy
from tapr.runtime import TAPRRuntime
from tapr.world import Fact, WorldBuilder


def make_escalation_world():
    return (
        WorldBuilder()
        .set("authorized", "agent-0", True)
        .set("parent_scope_ok", "agent-0", True)
        .set("approval", "agent-0", False)
        .set("path_tag", "agent-0", "ordinary")
        .set("prior_sensitive_read", "agent-0", False)
        .set("prior_effect_succeeded", "resource-0", False)
        .set("protected", "resource-0", False)
        .set("retention_hold", "resource-0", False)
        .set("evidence_fresh", "resource-0", True)
        .build()
    )


@pytest.mark.parametrize(
    ("action", "fact_name"),
    [
        ("write", "authorized"),
        ("write", "parent_scope_ok"),
        ("delete", "authorized"),
        ("delete", "parent_scope_ok"),
        ("delete", "protected"),
        ("delete", "retention_hold"),
        ("export", "authorized"),
        ("export", "parent_scope_ok"),
        ("export", "protected"),
        ("export", "retention_hold"),
        ("export", "prior_sensitive_read"),
    ],
)
def test_cached_escalation_is_invalidated_by_earlier_deny_rule(
    action, fact_name
):
    world = make_escalation_world()
    runtime = TAPRRuntime(Policy(version=world.policy_version))
    original = runtime.decide(
        world, action, "agent-0", "resource-0", "precedence", 0
    )
    assert original.verdict == "ESCALATE"

    old = world.facts[(fact_name, (
        "resource-0" if fact_name in {
            "protected", "retention_hold",
        } else "agent-0"
    ))]
    new_value = (
        True if fact_name in {"protected", "retention_hold",
                              "prior_sensitive_read"}
        else False
    )
    changed = world.with_fact(Fact(
        old.name, old.subject, new_value, old.version + 1, "precedence-test"
    ))

    expected = evaluate_oracle(
        changed, action, "agent-0", "resource-0"
    ).proposal_verdict
    observed = runtime.decide(
        changed, action, "agent-0", "resource-0", "precedence", 1
    )

    assert expected == "DENY"
    assert observed.verdict == expected
    assert observed.cache_hit is False


@pytest.mark.parametrize("action", ["write", "delete", "export"])
def test_escalation_dependency_contract_includes_all_earlier_applicable_rules(
    action,
):
    required = Policy().required_fact_keys(
        make_escalation_world(), action, "agent-0", "resource-0",
        "write_escalation",
    )
    assert {("authorized", "agent-0"), ("parent_scope_ok", "agent-0")} \
        <= required
    if action in {"delete", "export"}:
        assert {("protected", "resource-0"),
                ("retention_hold", "resource-0")} <= required
    if action == "export":
        assert ("prior_sensitive_read", "agent-0") in required


def test_dependency_contract_is_metamorphic_against_exhaustive_oracle_states():
    actions = ("read", "write", "delete", "export", "delegate", "retry",
               "approve")
    boolean_facts = (
        "authorized", "parent_scope_ok", "approval",
        "prior_sensitive_read", "prior_effect_succeeded", "protected",
        "retention_hold", "evidence_fresh",
    )
    candidate_keys = (
        ("authorized", "agent-0"), ("authorized", "agent-1"),
        ("parent_scope_ok", "agent-0"), ("parent_scope_ok", "agent-1"),
        ("approval", "agent-0"), ("approval", "agent-1"),
        ("path_tag", "agent-0"), ("path_tag", "agent-1"),
        ("prior_sensitive_read", "agent-0"),
        ("prior_sensitive_read", "agent-1"),
        ("prior_effect_succeeded", "resource-0"),
        ("prior_effect_succeeded", "resource-1"),
        ("protected", "resource-0"), ("protected", "resource-1"),
        ("retention_hold", "resource-0"), ("retention_hold", "resource-1"),
        ("evidence_fresh", "resource-0"), ("evidence_fresh", "resource-1"),
        ("metadata_version", "resource-0"),
    )
    policy = Policy()
    checked_mutations = 0

    for flags in product((False, True), repeat=len(boolean_facts)):
        for path_tag in ("ordinary", "untrusted_delegation"):
            values = dict(zip(boolean_facts, flags))
            builder = (
                WorldBuilder()
                .set("authorized", "agent-0", values["authorized"])
                .set("parent_scope_ok", "agent-0",
                     values["parent_scope_ok"])
                .set("approval", "agent-0", values["approval"])
                .set("path_tag", "agent-0", path_tag)
                .set("prior_sensitive_read", "agent-0",
                     values["prior_sensitive_read"])
                .set("prior_effect_succeeded", "resource-0",
                     values["prior_effect_succeeded"])
                .set("protected", "resource-0", values["protected"])
                .set("retention_hold", "resource-0",
                     values["retention_hold"])
                .set("evidence_fresh", "resource-0",
                     values["evidence_fresh"])
                .set("metadata_version", "resource-0", 1)
                .set("authorized", "agent-1", True)
                .set("parent_scope_ok", "agent-1", True)
                .set("approval", "agent-1", True)
                .set("path_tag", "agent-1", "ordinary")
                .set("prior_sensitive_read", "agent-1", False)
                .set("prior_effect_succeeded", "resource-1", False)
                .set("protected", "resource-1", False)
                .set("retention_hold", "resource-1", False)
                .set("evidence_fresh", "resource-1", True)
            )
            world = builder.build()

            for action in actions:
                baseline = evaluate_oracle(
                    world, action, "agent-0", "resource-0"
                ).proposal_verdict
                evaluation = policy.evaluate(
                    world, action, "agent-0", "resource-0"
                )
                required = policy.required_fact_keys(
                    world, action, "agent-0", "resource-0",
                    evaluation.rule_name,
                )
                for name, subject in candidate_keys:
                    if (name, subject) in required:
                        continue
                    old = world.facts.get((name, subject))
                    if old is None:
                        new_value = (
                            2 if name == "metadata_version"
                            else True if name in boolean_facts
                            else "untrusted_delegation"
                        )
                        version = 1
                    elif isinstance(old.value, bool):
                        new_value = not old.value
                        version = old.version + 1
                    elif name == "path_tag":
                        new_value = (
                            "untrusted_delegation"
                            if old.value == "ordinary" else "ordinary"
                        )
                        version = old.version + 1
                    else:
                        new_value = old.value + 1
                        version = old.version + 1
                    changed = world.with_fact(Fact(
                        name, subject, new_value, version,
                        "metamorphic_irrelevant_flip",
                    ))
                    actual = evaluate_oracle(
                        changed, action, "agent-0", "resource-0"
                    ).proposal_verdict
                    assert actual == baseline, (
                        f"undeclared oracle dependency changed verdict: "
                        f"action={action}, state={flags}, path={path_tag}, "
                        f"fact={(name, subject)}, "
                        f"baseline={baseline}, mutated={actual}"
                    )
                    checked_mutations += 1

    assert checked_mutations > 50_000
