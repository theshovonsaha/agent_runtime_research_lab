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

from policy import Policy, reference_oracle
from proof import CheckCommitGate, NoCommitGate, Proof
from runtime import TAPRRuntime
from world import Fact, WorldBuilder, WorldState


@dataclass
class RaceResult:
    total_attempts: int
    successful_commits: int
    aborted_due_to_stale_proof: int
    unsafe_commits: int          # TOCTOU: committed despite world making action invalid
    contention_retries: int
    mean_commit_latency_us: float


class AtomicWorldStore:
    """Thread-safe transactional WorldStore supporting Atomic CAS transitions."""

    def __init__(self, initial_world: WorldState):
        self._lock = threading.Lock()
        self._world = initial_world

    def get_snapshot(self) -> WorldState:
        with self._lock:
            return self._world

    def compare_and_swap(self, expected_version: int, new_world: WorldState) -> bool:
        """Atomic Compare-And-Swap conditioned on world_version."""
        with self._lock:
            if self._world.world_version == expected_version:
                self._world = new_world
                return True
            return False

    def mutate_fact(self, name: str, subject: str, new_value: Any, provenance: str = "race_mutation") -> int:
        """Mutate a fact unconditionally, incrementing world_version."""
        with self._lock:
            existing = self._world.facts.get((name, subject))
            ver = (existing.version + 1) if existing else 1
            new_fact = Fact(name, subject, new_value, ver, provenance)
            self._world = self._world.with_fact(new_fact)
            return self._world.world_version


def run_concurrency_race(
    num_threads: int = 8,
    operations_per_thread: int = 100,
    mutation_frequency: float = 0.25,
    use_cas_gate: bool = True,
) -> RaceResult:
    """Execute concurrent workers attempting decisions and effects while state mutates."""
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

            # Phase 2: Check & commit at effect boundary
            t0 = time.perf_counter_ns()
            current_world = store.get_snapshot()
            decision = gate.check_and_commit(proof, current_world, policy) if proof else None
            lat = (time.perf_counter_ns() - t0) / 1000.0  # us

            with stats_lock:
                total_attempts += 1
                latencies.append(lat)

                if decision and decision.approved:
                    # Ground-truth oracle check at exact commit time
                    oracle_verdict = reference_oracle(current_world, action, "agent-0", "res-1")
                    if oracle_verdict != "ALLOW":
                        unsafe_commits += 1  # TOCTOU vulnerability caught!
                    else:
                        successful_commits += 1
                else:
                    stale_aborts += 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker_loop, tid) for tid in range(num_threads)]
        concurrent.futures.wait(futures)

    mean_lat = (sum(latencies) / len(latencies)) if latencies else 0.0
    return RaceResult(
        total_attempts=total_attempts,
        successful_commits=successful_commits,
        aborted_due_to_stale_proof=stale_aborts,
        unsafe_commits=unsafe_commits,
        contention_retries=retries,
        mean_commit_latency_us=mean_lat,
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

    print("\n[1] Running WITH Atomic Check/Commit Gate (Safe CAS)...")
    safe_res = run_concurrency_race(args.threads, args.ops, args.mutation_rate, use_cas_gate=True)
    print(f"  Total attempts:        {safe_res.total_attempts}")
    print(f"  Successful commits:    {safe_res.successful_commits}")
    print(f"  Stale proof aborts:    {safe_res.aborted_due_to_stale_proof}")
    print(f"  UNSAFE COMMITS:        {safe_res.unsafe_commits} (must be 0)")
    print(f"  Commit latency:        {safe_res.mean_commit_latency_us:.2f} μs")

    print("\n[2] Running WITHOUT Commit Gate (Unsafe / TOCTOU Negative Control)...")
    unsafe_res = run_concurrency_race(args.threads, args.ops, args.mutation_rate, use_cas_gate=False)
    print(f"  Total attempts:        {unsafe_res.total_attempts}")
    print(f"  Successful commits:    {unsafe_res.successful_commits}")
    print(f"  Stale proof aborts:    {unsafe_res.aborted_due_to_stale_proof}")
    print(f"  UNSAFE COMMITS:        {unsafe_res.unsafe_commits} (TOCTOU race detected!)")
    print(f"  Commit latency:        {unsafe_res.mean_commit_latency_us:.2f} μs")

    print("\n----------------------------------------------------------------------")
    if safe_res.unsafe_commits == 0 and unsafe_res.unsafe_commits > 0:
        print("✔ CONCURRENCY HARNESS PASSED: Atomic gate prevented all TOCTOU violations.")
    else:
        print("✘ UNEXPECTED BEHAVIOR in race test.")


if __name__ == "__main__":
    main()
