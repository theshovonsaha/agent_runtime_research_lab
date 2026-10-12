# External Engine Adapters & Baselines Status

The policy contract defined in `src/policy.py` has been translated into formal policy specifications for external engines located in [`src/adapters/opa_cedar_adapters.py`](src/adapters/opa_cedar_adapters.py).

## Formal Policy Specifications Implemented

### 1. Open Policy Agent (OPA) / Rego
The benchmark policy contract has been translated into production-grade Rego rules (`package agent.governance`):
- Explicit denial rules for missing authorization, scope mismatches, protected/retention-hold targets, path-dependent sensitive reads, and retry idempotency.
- Escalation rules for unapproved mutations and stale evidence.
- Serialization routines mapping `WorldState` snapshots to standard OPA JSON documents.
- Execution via `opa eval --stdin-input "data.agent.governance.verdict"`.

### 2. AWS Cedar
The policy contract has been translated into Cedar specifications:
- `forbid` statements for protected resources, retention holds, prior sensitive exports, and non-idempotent retries.
- `permit` statement conditioned on active authorization, scope validity, and fresh evidence.
- Query runner interface for `cedar authorize`.

---

## Free & Local Installation (Optional)

Both OPA and Cedar CLI are open-source and **100% free of charge**. If you wish to execute the binaries directly on your macOS host:

```bash
# 1. Install OPA CLI (free, open source)
brew install opa

# 2. Install Cedar CLI (free, Apache 2.0 open source by AWS)
cargo install cedar-policy-cli
# or via homebrew if tapped:
# brew install cedar-policy
```

When installed, `OPAEngineAdapter` and `CedarEngineAdapter` in `src/adapters/opa_cedar_adapters.py` automatically detect the executables in your `PATH` and benchmark warm/cold latency and decision parity.
