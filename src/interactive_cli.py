#!/usr/bin/env python3
"""
interactive_cli.py – Production-grade interactive terminal for the Agent Runtime Lab.

Allows exploring:
1. Interactive prompt -> intent parsing -> policy evaluation -> CAS effect commit.
2. Direct benchmark replay and inspection of traces.
3. Live switching between providers (Offline, Ollama, LM Studio, OpenAI, Anthropic, Gemini).
4. Full accountability audit report on any decision.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit import audit_decision
from llm_evaluator import ModelClient
from policy import Policy, reference_oracle
from proof import CheckCommitGate, Proof
from runtime import TAPRRuntime
from world import WorldBuilder


def run_interactive_session():
    print("=" * 72)
    print("  AGENT RUNTIME RESEARCH LAB — INTERACTIVE EXPLORER & CLI")
    print("  Type an instruction, 'audit', 'switch', or 'exit'")
    print("=" * 72)

    policy = Policy(version=1, proof_rounds=8)
    runtime = TAPRRuntime(policy)
    gate = CheckCommitGate()

    world = (
        WorldBuilder()
        .set("authorized", "agent-0", True)
        .set("parent_scope_ok", "agent-0", True)
        .set("approval", "agent-0", True)
        .set("path_tag", "agent-0", "ordinary")
        .set("prior_sensitive_read", "agent-0", False)
        .set("prior_effect_succeeded", "resource-1", False)
        .set("protected", "resource-1", False)
        .set("retention_hold", "resource-1", False)
        .set("evidence_fresh", "resource-1", True)
        .build()
    )

    provider = "offline"
    model = "default"
    client = ModelClient(provider, model)
    last_record = None
    step = 0

    print(f"\nActive Provider: [{provider.upper()}]  |  Actor: agent-0  |  Target: resource-1")
    print("Sample commands to try:")
    print("  • 'Please inspect resource-1 and view its data'")
    print("  • 'Delete resource-1 permanently'")
    print("  • 'Export resource-1 to external endpoint'")
    print("  • 'revoke' (simulate mid-flight permission revocation)")
    print("  • 'switch' (change model provider: ollama, lmstudio, openai, gemini)")
    print("-" * 72)

    while True:
        try:
            cmd = input("\n[agent-runtime] > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            break

        if not cmd:
            continue
        if cmd.lower() in ("exit", "quit", "q"):
            break

        if cmd.lower() == "revoke":
            fact = world.facts[("authorized", "agent-0")].mutated(False, "operator_revoke")
            world = world.with_fact(fact)
            print("⚠ SIMULATION: Authorization for agent-0 has been REVOKED in world state.")
            continue

        if cmd.lower() == "authorize":
            fact = world.facts[("authorized", "agent-0")].mutated(True, "operator_grant")
            world = world.with_fact(fact)
            print("✔ SIMULATION: Authorization for agent-0 set to ACTIVE.")
            continue

        if cmd.lower() == "switch":
            print("\nChoose provider:")
            print("  1. offline (fast rule-based heuristic)")
            print("  2. ollama (http://localhost:11434)")
            print("  3. lmstudio (http://localhost:1234)")
            print("  4. openai (OPENAI_API_KEY required)")
            print("  5. anthropic (ANTHROPIC_API_KEY required)")
            print("  6. gemini (GEMINI_API_KEY required)")
            choice = input("Select [1-6]: ").strip()
            mapping = {"1": "offline", "2": "ollama", "3": "lmstudio", "4": "openai", "5": "anthropic", "6": "gemini"}
            provider = mapping.get(choice, "offline")
            model = input(f"Model identifier (press Enter for default): ").strip() or "default"
            client = ModelClient(provider, model)
            print(f"Switched provider to [{provider.upper()}] (Model: {model})")
            continue

        if cmd.lower() == "audit":
            if not last_record:
                print("No decision has been made yet to audit.")
                continue
            expl = audit_decision(last_record, world)
            print("\n" + "=" * 50 + " AUDIT REPORT " + "=" * 50)
            print(expl.summary)
            print("=" * 114)
            continue

        # Step 1: Language -> Intent
        print("\n▶ [Layer A: Intent Parsing]")
        event, ambiguous, raw = client.parse_instruction(cmd)
        if ambiguous or not event:
            print(f"  ❌ Intent marked as AMBIGUOUS or UNGROUNDED.")
            print(f"  Escalating to human operator for clarification. No action executed.")
            continue

        print(f"  Action: '{event.action}'  |  Target: '{event.target}'  |  Actor: '{event.actor}'")

        # Step 2: Runtime Policy Evaluation
        print("▶ [Layer B: Governance & Proof Evaluation]")
        rec = runtime.decide(world, event.action, event.actor, event.target, "cli-session", step, "interactive")
        step += 1
        last_record = rec

        cache_str = "CACHE HIT (Reused Proof) ★" if rec.cache_hit else f"FULL RECOMPUTE ({rec.work_units} work units)"
        print(f"  Verdict:       {rec.verdict} (Expected: {rec.expected})")
        print(f"  Reuse Mode:    {cache_str}")
        print(f"  Latency:       {rec.latency_ns / 1000.0:.2f} μs")
        print(f"  Rule Trigger:  {rec.rule_name} (Tracked Dependencies: {rec.dep_count})")

        # Step 3: Check/Commit Gate (Effect Boundary)
        print("▶ [Layer C: Check/Commit CAS Gate]")
        proof = runtime._proof_map.get(f"cli-session-{step-1}")
        decision = gate.check_and_commit(proof, world, policy) if proof else None
        if decision and decision.approved:
            print(f"  ✔ EFFECT COMMITTED: Action '{event.action}' executed successfully on '{event.target}'.")
        else:
            reason = decision.reason if decision else "no_proof"
            print(f"  ⛔ COMMIT REJECTED: Gate refused effect (Reason: {reason}).")


def main():
    run_interactive_session()


if __name__ == "__main__":
    main()
