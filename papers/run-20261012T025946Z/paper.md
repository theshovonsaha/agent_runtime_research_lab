# Dependency-aware proof reuse for agent runtime governance: a stateful execution benchmark

**Manuscript status:** automatically generated experimental draft; not submission-certified.  
**Lab version:** 2.0.0 · **Run ID:** run-20261012T025946Z · **UTC:** 2026-10-12T02:59:46.420955+00:00  
**Study type:** offline synthetic stateful systems experiment.

## Abstract

Runtime governance systems evaluate whether an AI agent's proposed action is admissible under policy, evidence, and execution history. Repeated evaluation may be computationally expensive, while naive reuse of prior decisions is unsound when relevant state changes. This study compares full recomputation, exact-snapshot caching, dependency-aware proof reuse (TAPR), and intentionally flawed negative controls across stateful scenario families (including mid-flight revocation, evidence expiry, and path dependencies). Across tested repetitions, dependency-aware caching preserved 100% agreement with the reference oracle while reducing full evaluations by 30.4% relative to full recomputation. These findings are bounded by the synthetic simulator; they do not establish production safety or novelty relative to prior art.

## 1. Introduction

Language-to-world agents propose discrete actions that affect state. When actions carry security or integrity obligations, a runtime gate must verify admissibility. While memoization has been widely explored, state changes (e.g. credential revocation, evidence staleness) invalidate prior proofs. We benchmark dependency-aware proof reuse against full recomputation and negative controls.

## 2. Experimental methods

### 2.1 Stateful scenario grammar
Scenarios are sequential event streams consisting of:
1. `WorldMutationEvent`: updates versioned, provenance-tracked facts.
2. `ProposalEvent`: an agent proposes an action evaluated against current state.
3. `CommitAttemptEvent`: checks whether the proof remains sound at the effect boundary.

### 2.2 Compared systems
- **Full recomputation**: re-evaluates all policy rules on every proposal.
- **TAPR dep-cache**: indexes proofs by explicit policy dependencies; evicts on mutation.
- **Exact snapshot cache**: keys cache by complete world state.
- **Negative controls**: coarse cache (action+target only), stale proof accept (never evicts), no commit gate (skips boundary validation), and pathless policy.

## 3. Results

| System | Oracle agreement | Unsafe ALLOW | False DENY | Cache hit | Full evaluations | Mean latency (μs) |
|---|---:|---:|---:|---:|---:|---:|
| Full recompute | 100.00% | 0.0 | 0.0 | 0.0% | 921 | 14.20 |
| TAPR dep-cache | 100.00% | 0.0 | 0.0 | 30.4% | 641 | 10.60 |
| Exact snapshot cache | 100.00% | 0.0 | 0.0 | 0.0% | 921 | 21.02 |
| Coarse cache (unsafe control) | 84.69% | 100.0 | 0.0 | 52.0% | 442 | 7.77 |
| Stale proof accept (unsafe control) | 84.69% | 100.0 | 0.0 | 52.0% | 442 | 7.48 |
| No commit gate (unsafe control) | 100.00% | 0.0 | 0.0 | 30.4% | 641 | 10.43 |
| Pathless policy (unsafe control) | 87.40% | 116.0 | 0.0 | 0.0% | 921 | 14.68 |

![Decision accuracy](figures/decision_accuracy.png)

![Unsafe allows](figures/unsafe_allows.png)

![Cache hit rate](figures/cache_hit_rate.png)

![Latency with confidence intervals](figures/latency_ci.png)

![Work savings](figures/work_savings.png)

![Commit gate](figures/commit_gate.png)

![Safety-efficiency frontier](figures/safety_efficiency.png)

## 4. Limitations

1. Workloads are synthetic and typed; natural language parsing and intent ambiguity are simulated.
2. The proof workload uses a deterministic SHA-256 CPU proxy rather than automated formal verification.
3. In-process gating does not model distributed consensus or asynchronous store races.
4. Independent human review of policy specifications is still required.

## 5. Artifact availability

This bundle contains copies of the selected run's available data and figures. The run manifest records input hashes and repository provenance where available. Reproduction requires the source code and dependencies; repository cleanliness and artifact availability must be checked separately.
