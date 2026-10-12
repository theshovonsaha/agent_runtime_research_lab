# Research charter: benchmark agents as language-to-world execution systems

## Thesis

A language-model agent is not adequately characterized by a prompt, a final answer, or a list of tool calls. It is a translation-and-execution system:

`natural-language input → interpreted intent → typed task/state → evidence and obligations → plan/path → proposed effect → committed world change → observed outcome → durable memory`

Every arrow is a possible loss, ambiguity, stale assumption, policy violation, or unverified side effect. A benchmark that measures only final text or isolated tool-call success observes only a small slice of the system.

The ambition is not to replace task benchmarks. It is to add a systems benchmark that evaluates whether an agent runtime preserves meaning, authority, evidence, and accountability across these transitions.

## Design principles

1. **Measure transitions, not just endpoints.** Every proposal must be traceable to the request, typed interpretation, state snapshot, evidence, policy version, decision, and effect result.
2. **Separate semantic errors from enforcement errors.** If language is misunderstood, score that at the interpretation layer. If the intent is represented correctly but the runtime permits an unsafe action, score it at the governance layer. Do not hide one behind the other.
3. **Use executable world state.** A tool result is not necessarily a successful outcome. Compare expected postconditions against observed state; distinguish success, failure, partial success, and unknown.
4. **Treat history as state.** The legality of the next action may depend on prior reads, delegations, approvals, retries, disclosures, and effects.
5. **Make proof reuse falsifiable.** A cached conclusion must name its dependencies, scope, policy version, evidence versions, and expiry/commit condition. A dependency change that can alter the verdict must invalidate the cached conclusion.
6. **Test irrelevance too.** If unrelated metadata invalidates every proof, correctness may be preserved but the optimization may be useless. Measure both false reuse and needless recomputation.
7. **Use adversarial temporal sequences.** Revocation after planning, evidence drift, duplicate retries, partial side effects, reordering, concurrent changes, and policy rollover matter more than static happy paths.
8. **Make the oracle inspectable.** Freeze policy semantics, publish the generator and all failures, and seek independent review. Synthetic correctness is not proof of real-world completeness.
9. **Measure the full cost boundary.** Report parsing, serialization, dependency discovery, cache lookup, policy evaluation, evidence verification, audit writes, network calls, and commit guards separately.
10. **No universal leaderboard score.** Report a vector of safety, completion, semantic fidelity, recovery, provenance, cost, latency, and reproducibility. A weighted scalar can hide catastrophic trade-offs.

## Proposed benchmark layers

### A. Language interpretation

Input: natural-language request, conversation history, untrusted retrieved text, user identity and constraints.

Output: typed intent, arguments, scope, preconditions, expected postconditions, uncertainty, provenance, and unresolved questions.

Metrics: intent accuracy; argument/target accuracy; constraint retention; uncertainty calibration; ambiguity escalation; prompt-injection influence; semantic drift between paraphrases; reversible mapping to source spans. Keep LLM/model and prompt versions pinned.

### B. Runtime semantics and governance

Input: typed proposal plus path and current state.

Output: allow / deny / escalate / transform, obligations, dependency vector, explanation/proof reference, and commit constraints.

Metrics: unsafe allows; false denials; missed escalations; dependency omissions; stale-proof acceptance; unnecessary invalidation; work avoided; policy-version consistency; path-sensitive violations.

### C. Effect and recovery

Input: approved action and a controllable fake/real adapter.

Output: observed postcondition and durable event record.

Metrics: attempted-vs-committed distinction; partial-effect detection; idempotency; compensation quality; unknown-outcome reconciliation; retry safety; crash recovery; audit reconstruction; state divergence.

### D. System performance

Metrics: cold/warm latency distributions; CPU and memory; policy/evidence work; cache hit rate paired with correctness; end-to-end cost; p95/p99; scaling with path length, dependency fan-out, mutation rate, policy complexity, and concurrency.

## Scenario grammar

Each scenario should be a stateful event stream, not an isolated prompt:

- `Request`: language, actor, goal, constraints, source provenance.
- `Interpretation`: typed action, argument bindings, uncertainty, references to source spans.
- `Observation`: evidence and versioned facts.
- `Transition`: explicit state mutation, revocation, approval, delegation, external event, or partial effect.
- `Proposal`: next action and claimed preconditions.
- `Decision`: policy version, verdict, obligations, proof/dependency identifiers.
- `Commit`: compare-and-swap or equivalent validation at the effect boundary.
- `Outcome`: observed postcondition, side-effect status, and reconciliation state.
- `Memory`: what can safely be persisted as fact vs candidate vs unresolved claim.

Generators should vary the ordering and timing of transitions, not only the values. Include paired cases that differ in exactly one decisive fact and metamorphic cases that should preserve verdict under irrelevant wording or metadata changes.

## Initial falsifiable TAPR hypothesis

Given equivalent policy semantics and the same current state, dependency-aware proof reuse should:

- preserve every verdict produced by full recomputation;
- never reuse a proof when a verdict-relevant dependency has changed;
- reduce expensive obligation work when the same relevant dependency vector recurs;
- avoid invalidation on declared irrelevant changes;
- survive a check/commit gap by validating dependencies at the effect boundary.

The hypothesis fails if dependency discovery costs more than recomputation across realistic workloads, if dependency declarations cannot be kept complete, or if concurrent/distributed invalidation cannot be made reliable.

## Research guardrails

- No novelty claim from this simulator alone.
- No performance comparison to external published numbers without workload and hardware parity.
- No claim of production safety based solely on an in-process synthetic oracle.
- Keep semantic extraction, deterministic governance, and external effect verification as separate scorecards before considering end-to-end composition.
- The first serious paper should publish failures, not only wins.
