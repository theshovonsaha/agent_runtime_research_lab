"""
concurrent_harness.py – Asynchronous & Multi-threaded Check/Commit Race Harness.

Simulates concurrent agent runtime execution under high contention to measure:
1. TOCTOU (Time-Of-Check to Time-Of-Use) vulnerabilities under state races.
2. Atomic Compare-And-Swap (CAS) state validation at the effect boundary.
3. Proof invalidation performance under concurrent state mutations.
4. Contention retry rates and throughput under varying thread counts.

Runs completely locally, in-memory, zero network or external dependencies.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

try:
    from .policy import Policy
    from .oracle import evaluate as evaluate_oracle
    from .proof import CheckCommitGate, CommitDecision, NoCommitGate, Proof
    from .runtime import TAPRRuntime
    from .world import EffectRecord, Fact, WorldBuilder, WorldState
except ImportError:
    from policy import Policy
    from tapr.oracle import evaluate as evaluate_oracle
    from proof import CheckCommitGate, CommitDecision, NoCommitGate, Proof
    from runtime import TAPRRuntime
    from world import EffectRecord, Fact, WorldBuilder, WorldState


@dataclass
class RaceResult:
    total_attempts: int
    successful_commits: int
    aborted_due_to_stale_proof: int
    unsafe_commits: int          # TOCTOU: committed despite world making action invalid
    contention_retries: int
    mean_commit_latency_us: float
    p50_commit_latency_us: float = 0.0
    p95_commit_latency_us: float = 0.0
    p99_commit_latency_us: float = 0.0


@dataclass(frozen=True)
class CommitTrace:
    attempt: int
    proof_id: str
    proof_state_version: int
    proof_policy_version: int
    state_version_before: int
    state_version_after: int
    interleaving: Tuple[str, ...]
    decision: bool
    reason: str
    effect_outcome: str
    effect_occurred: bool
    effect_count: int
    expected_authorized: bool
    oracle_disagreement: bool


class AtomicWorldStore:
    """Thread-safe transactional WorldStore supporting Atomic CAS transitions."""

    def __init__(self, initial_world: WorldState):
        self._lock = threading.Lock()
        self._world = initial_world
        self._interleaving: List[str] = []
        self.traces: List[CommitTrace] = []

    def get_snapshot(self) -> WorldState:
        with self._lock:
            return self._world

    def _before_effect_recording(self) -> None:
        """Synchronization seam for deterministic tests of the commit boundary."""

    def compare_and_swap(self, expected_version: int, new_world: WorldState) -> bool:
        """Atomic Compare-And-Swap conditioned on world_version."""
        with self._lock:
            if self._world.world_version == expected_version:
                self._world = new_world
                self._interleaving.append(
                    f"cas:{expected_version}->{new_world.world_version}:success"
                )
                return True
            self._interleaving.append(
                f"cas:{expected_version}:failed_at:{self._world.world_version}"
            )
            return False

    def mutate_fact(self, name: str, subject: str, new_value: Any, provenance: str = "race_mutation") -> int:
        """Mutate a fact unconditionally, incrementing world_version."""
        with self._lock:
            existing = self._world.facts.get((name, subject))
            ver = (existing.version + 1) if existing else 1
            new_fact = Fact(name, subject, new_value, ver, provenance)
            self._world = self._world.with_fact(new_fact)
            self._interleaving.append(
                f"mutation:{name}:{subject}:v{ver}:world{self._world.world_version}"
            )
            return self._world.world_version

    def commit_effect(
        self,
        proof: Proof,
        policy: Policy,
        gate: CheckCommitGate,
        timestamp: int = 0,
        effect_outcome: str = "success",
    ) -> Tuple[CommitDecision, bool, int, str]:
        """Return (decision, committed, resulting_version, oracle_verdict).

        Validation, oracle observation, and simulated effect recording occur
        under one lock. The oracle is for measurement, not enforcement.

        The lock covers reading the latest state, checking the proof,
        recomputing stale authorization when required, and recording the
        effect. Concurrent mutations cannot interleave with this sequence.

        Returns (decision, effect_committed, resulting_world_version).
        This guarantees atomicity only for this in-memory simulation.
        """
        if effect_outcome not in {"success", "partial", "failed"}:
            raise ValueError(
                "effect_outcome must be 'success', 'partial', or 'failed'"
            )

        with self._lock:
            current = self._world
            expected = evaluate_oracle(
                current, proof.action, proof.actor, proof.target,
                active_policy_version=policy.version,
            )
            decision = gate.check_and_commit(proof, current, policy)

            if not decision.approved:
                self.traces.append(CommitTrace(
                    attempt=len(self.traces) + 1,
                    proof_id=proof.proof_id,
                    proof_state_version=proof.established_at,
                    proof_policy_version=proof.policy_version,
                    state_version_before=current.world_version,
                    state_version_after=current.world_version,
                    interleaving=tuple(self._interleaving),
                    decision=False,
                    reason=decision.reason,
                    effect_outcome="not_attempted",
                    effect_occurred=False,
                    effect_count=sum(
                        effect.action == proof.action
                        and effect.target == proof.target
                        and effect.outcome in {"success", "partial"}
                        for effect in current.path
                    ),
                    expected_authorized=expected.commit_authorized,
                    oracle_disagreement=(
                        decision.approved != expected.commit_authorized
                    ),
                ))
                return (
                    decision, False, current.world_version,
                    expected.proposal_verdict,
                )

            self._before_effect_recording()
            effect = EffectRecord(
                action=proof.action,
                actor=proof.actor,
                target=proof.target,
                outcome=effect_outcome,
                timestamp=timestamp or current.world_version + 1,
            )
            self._world = current.with_effect(effect)
            self._interleaving.append(
                f"effect_attempt:{proof.action}:{proof.target}:"
                f"{effect_outcome}:world{self._world.world_version}"
            )
            effect_occurred = effect_outcome in {"success", "partial"}
            self.traces.append(CommitTrace(
                attempt=len(self.traces) + 1,
                proof_id=proof.proof_id,
                proof_state_version=proof.established_at,
                proof_policy_version=proof.policy_version,
                state_version_before=current.world_version,
                state_version_after=self._world.world_version,
                interleaving=tuple(self._interleaving),
                decision=True,
                reason=(
                    "effect_failed"
                    if effect_outcome == "failed" else decision.reason
                ),
                effect_outcome=effect_outcome,
                effect_occurred=effect_occurred,
                effect_count=sum(
                    item.action == proof.action
                    and item.target == proof.target
                    and item.outcome in {"success", "partial"}
                    for item in self._world.path
                ),
                expected_authorized=expected.commit_authorized,
                oracle_disagreement=(
                    decision.approved != expected.commit_authorized
                ),
            ))
            return (
                decision,
                    effect_occurred,
                self._world.world_version,
                expected.proposal_verdict,
            )


def run_concurrency_race(
    num_threads: int = 8,
    operations_per_thread: int = 100,
    mutation_frequency: float = 0.25,
    use_cas_gate: bool = True,
) -> RaceResult:
    """Execute concurrent workers attempting decisions and effects while state mutates."""
    if num_threads < 1:
        raise ValueError("num_threads must be at least 1")
    if operations_per_thread < 1:
        raise ValueError("operations_per_thread must be at least 1")
    if not 0.0 <= mutation_frequency <= 1.0:
        raise ValueError("mutation_frequency must be between 0.0 and 1.0")

    initial_world = (
        WorldBuilder()
        .set("authorized", "agent-0", True)
        .set("parent_scope_ok", "agent-0", True)
        .set("approval", "agent-0", True)
        .set("path_tag", "agent-0", "ordinary")
        .set("prior_sensitive_read", "agent-0", False)
        .set("prior_effect_succeeded", "res-1", False)
        .set("protected", "res-1", False)
        .set("retention_hold", "res-1", False)
        .set("evidence_fresh", "res-1", True)
        .build()
    )

    store = AtomicWorldStore(initial_world)
    policy = Policy(version=1, proof_rounds=4)
    gate = CheckCommitGate() if use_cas_gate else NoCommitGate()

    total_attempts = 0
    successful_commits = 0
    stale_aborts = 0
    unsafe_commits = 0
    retries = 0
    latencies: List[float] = []
    stats_lock = threading.Lock()

    def worker_loop(thread_id: int):
        nonlocal total_attempts, successful_commits, stale_aborts, unsafe_commits, retries
        runtime = TAPRRuntime(policy)

        for i in range(operations_per_thread):
            # Phase 1: Read snapshot and decide
            snap = store.get_snapshot()
            action = "write" if (i % 2 == 0) else "read"
            rec = runtime.decide(snap, action, "agent-0", "res-1", f"race-{thread_id}", i, "race")
            proof = runtime._proof_map.get(f"race-{thread_id}-{i}")

            # Interleaved mutation simulator
            if (i + thread_id) % int(1.0 / mutation_frequency if mutation_frequency > 0 else 9999) == 0:
                # Interleaved race mutation: flip authorization or evidence
                store.mutate_fact("authorized", "agent-0", (i % 4 != 0), "concurrent_race")

            # Phase 2: validate and record the effect atomically.
            t0 = time.perf_counter_ns()
            if proof is not None:
                decision, committed, _, oracle_verdict = store.commit_effect(
                    proof, policy, gate
                )
            else:
                decision, committed, oracle_verdict = None, False, "DENY"
            lat = (time.perf_counter_ns() - t0) / 1000.0

            with stats_lock:
                total_attempts += 1
                latencies.append(lat)

                if committed and oracle_verdict != "ALLOW":
                    unsafe_commits += 1
                elif committed:
                    successful_commits += 1
                else:
                    stale_aborts += 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker_loop, tid) for tid in range(num_threads)]
        for future in futures:
            future.result()

    mean_lat = (sum(latencies) / len(latencies)) if latencies else 0.0
    ordered_latencies = sorted(latencies)

    def percentile(q: float) -> float:
        if not ordered_latencies:
            return 0.0
        position = (len(ordered_latencies) - 1) * q
        lower = int(position)
        upper = min(lower + 1, len(ordered_latencies) - 1)
        return (ordered_latencies[lower]
                + (ordered_latencies[upper] - ordered_latencies[lower])
                * (position - lower))

    return RaceResult(
        total_attempts=total_attempts,
        successful_commits=successful_commits,
        aborted_due_to_stale_proof=stale_aborts,
        unsafe_commits=unsafe_commits,
        contention_retries=retries,
        mean_commit_latency_us=mean_lat,
        p50_commit_latency_us=percentile(0.50),
        p95_commit_latency_us=percentile(0.95),
        p99_commit_latency_us=percentile(0.99),
    )


def main():
    p = argparse.ArgumentParser(description="Concurrent Race Harness for Agent Runtime Governance")
    p.add_argument("--threads", type=int, default=8, help="Number of concurrent worker threads")
    p.add_argument("--ops", type=int, default=150, help="Operations per thread")
    p.add_argument("--mutation-rate", type=float, default=0.20, help="Frequency of concurrent state mutations")
    args = p.parse_args()

    print("======================================================================")
    print("  AGENT RUNTIME CONCURRENCY & RACE HARNESS")
    print(f"  Workers: {args.threads}  Ops/worker: {args.ops}  Mutation rate: {args.mutation_rate}")
    print("======================================================================")

    print("\n[1] Atomic check-and-effect boundary (safe gate)...")
    safe_res = run_concurrency_race(args.threads, args.ops, args.mutation_rate, use_cas_gate=True)
    print(f"  Total attempts:        {safe_res.total_attempts}")
    print(f"  Successful commits:    {safe_res.successful_commits}")
    print(f"  Stale proof aborts:    {safe_res.aborted_due_to_stale_proof}")
    print(f"  UNSAFE COMMITS:        {safe_res.unsafe_commits} (must be 0)")
    print(f"  Commit latency:        {safe_res.mean_commit_latency_us:.2f} μs")

    print("\n[2] Atomic boundary with stale-proof negative-control gate...")
    unsafe_res = run_concurrency_race(args.threads, args.ops, args.mutation_rate, use_cas_gate=False)
    print(f"  Total attempts:        {unsafe_res.total_attempts}")
    print(f"  Successful commits:    {unsafe_res.successful_commits}")
    print(f"  Stale proof aborts:    {unsafe_res.aborted_due_to_stale_proof}")
    print(f"  UNSAFE COMMITS:        {unsafe_res.unsafe_commits} (TOCTOU race detected!)")
    print(f"  Commit latency:        {unsafe_res.mean_commit_latency_us:.2f} μs")

    print("\n----------------------------------------------------------------------")
    if safe_res.unsafe_commits == 0 and unsafe_res.unsafe_commits > 0:
        print("✔ CONCURRENCY HARNESS PASSED: Safe gate prevented observed TOCTOU violations in this run.")
    else:
        print("✘ UNEXPECTED BEHAVIOR in race test.")


if __name__ == "__main__":
    main()
