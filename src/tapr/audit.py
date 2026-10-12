"""
audit.py – Accountability reconstruction for governance decisions.

Given a DecisionRecord and the world state at decision time, the audit
module reconstructs a human-readable explanation of why the decision was
made: which policy rule fired, which facts were consulted, what values they
had, and whether any dependency has since changed.

This is Layer D of the benchmark: Accountability.

The ability to reconstruct *why* a decision was made is a necessary property
of any trustworthy governance runtime.  A runtime that produces correct
decisions but cannot explain them is not auditable.

Usage in tests and analysis:
    from audit import audit_decision
    explanation = audit_decision(record, world_at_decision_time)
    print(explanation.summary)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

try:
    from .world import WorldState
except ImportError:
    from world import WorldState


@dataclass
class AuditEntry:
    """One fact consulted during the decision."""
    fact_name: str
    subject: str
    value: Any
    version: int
    provenance: str
    still_valid: bool    # is this fact still at the same version?
    impact: str          # "decisive" | "supporting" | "irrelevant"


@dataclass
class AuditExplanation:
    """Full reconstruction of a governance decision."""
    record: DecisionRecord
    rule_fired: str
    entries: List[AuditEntry]
    summary: str
    reconstruction_complete: bool  # False if we lost dependency info


def audit_decision(record: Any,
                   world_at_decision: WorldState,
                   current_world: Optional[WorldState] = None) -> AuditExplanation:
    """Reconstruct why a decision was made.

    Parameters
    ----------
    record              The DecisionRecord to audit.
    world_at_decision   The WorldState when the decision was made.
    current_world       If provided, flags dependencies that have since changed.
    """
    # Collect all facts from the world that are policy-relevant.
    POLICY_FACTS = {
        "authorized", "parent_scope_ok", "approval", "path_tag",
        "prior_sensitive_read", "prior_effect_succeeded",
        "protected", "retention_hold", "evidence_fresh",
    }

    entries: List[AuditEntry] = []
    for (name, subject), fact in sorted(world_at_decision.facts.items()):
        if name not in POLICY_FACTS:
            continue
        still = True
        if current_world is not None:
            cur = current_world.facts.get((name, subject))
            still = (cur is not None and cur.version == fact.version
                     and cur.value == fact.value)
        # Determine impact based on the rule that fired.
        impact = _classify_impact(name, record.rule_name, record.action,
                                  record.actor, record.target, subject)
        entries.append(AuditEntry(
            fact_name=name,
            subject=subject,
            value=fact.value,
            version=fact.version,
            provenance=fact.provenance,
            still_valid=still,
            impact=impact,
        ))

    decisive = [e for e in entries if e.impact == "decisive"]
    stale    = [e for e in entries if not e.still_valid]

    lines = [
        f"Decision: {record.verdict}",
        f"Rule: {record.rule_name}",
        f"Action: {record.action} by {record.actor} on {record.target}",
        f"World version at decision: {world_at_decision.world_version}",
        f"Correct: {record.correct} (expected {record.expected})",
        "",
        "Decisive facts:",
    ]
    for e in decisive:
        lines.append(f"  [{e.fact_name}:{e.subject}] = {e.value!r} "
                     f"(v{e.version}, {e.provenance})"
                     + ("  ⚠ changed since decision" if not e.still_valid else ""))
    if stale:
        lines.append("")
        lines.append("Facts that changed since decision (potential staleness):")
        for e in stale:
            if e.impact != "decisive":
                lines.append(f"  [{e.fact_name}:{e.subject}] "
                             f"(was v{e.version})")
    rec_path = getattr(record, "path", None)
    if rec_path:
        lines.append("")
        lines.append(f"Execution path at decision: {len(rec_path)} effects")

    return AuditExplanation(
        record=record,
        rule_fired=record.rule_name,
        entries=entries,
        summary="\n".join(lines),
        reconstruction_complete=True,
    )


def _classify_impact(fact_name: str, rule: str, action: str,
                     actor: str, target: str, subject: str) -> str:
    """Classify whether a fact was decisive for the verdict."""
    if rule == "authorization" and fact_name in ("authorized", "parent_scope_ok"):
        return "decisive"
    if rule == "protected_resource" and fact_name in ("protected", "retention_hold"):
        return "decisive"
    if rule == "export_after_sensitive_read" and fact_name == "prior_sensitive_read":
        return "decisive"
    if rule == "retry_idempotency" and fact_name == "prior_effect_succeeded":
        return "decisive"
    if rule == "untrusted_delegation" and fact_name == "path_tag":
        return "decisive"
    if rule == "write_escalation" and fact_name in ("approval", "evidence_fresh"):
        return "decisive"
    if rule == "allow":
        return "supporting"
    return "irrelevant"



