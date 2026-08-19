# Dynamic Data — Hivemind & Security

*Captured 2026-07-14. How a team of AI agents shares one Dynamic Data memory
safely — authorship, trust, and tamper-evidence. Implemented in dd-core v0.3.*

Borrows a similar trust architecture pattern from other production systems that
need multi-agent authenticated claims: integrity via a hash chain, authenticity
via Ed25519 signatures, and independent verification you can run yourself
without trusting the store.

---

## The starting point: authorship is already an atom

Every claim carries `source` — *who* asserted it. For a hivemind, `source` is
the agent id. Because claims accumulate and are immutable, you get a permanent,
queryable record of who claimed what: git-blame for beliefs. The metadata *is*
the claim (source, confidence, time, derivation, context are the data, not
bolted on). So authorship isn't a new feature — it's the `source` atom, made
trustworthy.

---

## Two guarantees (the Ovyero split)

**Integrity — the hash chain.** Every claim is chained into an append-only
ledger: `entry_hash = sha256(content + prev_hash)`. Edit any past claim and its
hash changes, which breaks every entry after it — *the chain snaps visibly*.
`verify_chain()` (and `dd_verify.py`) walk the chain locally and report the exact
`seq` where it broke.

**Authenticity — Ed25519 signatures.** Integrity alone isn't enough: a tamperer
can recompute hashes to look consistent again. So each agent has an Ed25519
keypair; it signs each claim's `entry_hash`; the store keeps only the signature
and the public-key fingerprint. Anyone with the agent's **public** key can
verify — the store itself cannot forge an agent's signature. `verify_chain(check_signatures=True)`
checks every signed entry against its registered public key.

Together: integrity stops *modification*, authenticity stops *forgery*.

---

## Authenticated authorship (no impersonation)

Self-declared `source` is fine for cooperative agents, but a malicious agent
could type `source="human-ezra"`. The fix: the *infrastructure* stamps identity,
not the agent. The MCP server reads `DD_AGENT` from the environment/connection
and forces every claim's source to it — the model literally cannot lie about who
it is. Each agent in a hivemind connects with its own `DD_AGENT` (and its own
keypair for signing).

```
DD_AGENT=planner DD_DB=/shared/hive.ddb python dd_mcp_server.py   # planner's connection
DD_AGENT=scout   DD_DB=/shared/hive.ddb python dd_mcp_server.py   # scout's connection
```

---

## Trust ceilings (reputation)

Not all agents are equal. Each agent has a **trust ceiling** that caps how much
its claims can weigh: a claim's *effective* credence is `min(confidence, trust_ceiling(source))`.
So a low-trust agent asserting confidence 1.0 **cannot outshout** a trusted agent
— resolution honors trust, not volume. (This is the same idea as the classic
source hierarchy: human 1.0 > measured 0.99 > inference 0.6–0.9 > telemetry 0.3.)

The agent roster is itself **reflexive dynamic data**: `agent:<id>` with `kind`,
`trust_ceiling`, and `pubkey` claims. The hivemind's own org chart lives inside
the memory, editable from inside the system.

---

## How a hivemind collaborates (no locks needed)

1. **Shared blackboard** — all agents read/write one `.ddb`. A claim by one is
   instantly visible to all.
2. **Conflict is the coordination signal** — agents never overwrite each other
   (no races). Disagreement surfaces via `list_conflicts`; an arbiter (highest
   trusted credence, a `DETERMINED` authority, an arbiter agent, or a human)
   resolves it.
3. **Cross-agent reasoning** — when agent B builds on A's claim, `derived_from`
   links them, giving the inter-agent reasoning graph *and* cross-agent belief
   revision (retract A's premise → B's conclusion falls).
4. **Accountability** — `provenance()` traces any belief to the agent that
   authored it and the claims it was built on; `verify_chain` proves nobody
   rewrote history.

See `benchmark/hivemind_demo.py` for all of this end-to-end.

---

## Verify it yourself (don't trust the store)

```bash
python dd_verify.py --db hive.ddb                # integrity (offline)
python dd_verify.py --db hive.ddb --signatures   # + authenticity
```

Exit 0 = intact, exit 1 = tampering detected (with the broken `seq`). Like
`aios verify`, this needs no server and no vendor trust.

---

## Inversion invention — what else AI + Dynamic Data needs (roadmap)

Implemented in v0.3: hash-chain integrity, Ed25519 authenticity, authenticated
authorship, trust ceilings, agent registry, independent verifier, multi-agent demo.

Deliberately **not yet** built (documented so nothing is silently missing):

- **External anchoring** — periodically pin the ledger head to an RFC 3161
  timestamp authority (like Ovyero → DigiCert) so history can't be backdated.
  *Needs an external service; high value for audit.*
- **Authorization / capability scoping** — which agents may assert which
  predicates (write ACLs), not just how much their claims weigh. *The natural
  next security layer.*
- **Append-only retraction events** — today `retracted` is a mutable flag
  (excluded from the chain so legitimate retractions don't break it). Make each
  retraction its own signed, chained ledger entry so retractions are themselves
  tamper-evident.
- **Reputation decay** — auto-adjust an agent's trust ceiling as its claims get
  overruled/retracted, instead of a static number.
- **Data minimization / sealed claims** — never store secrets/PII in values;
  store hashes or sealed references (Ovyero logs hashes, never source).
- **Online verification mode** — compare the local chain head against an
  authoritative server head to catch server-side tampering too.
- **Sybil resistance** — bind agent registration to a vouching authority so a
  bad actor can't spin up many fake high-trust identities.
- **Merkle proofs** — for efficient partial verification of very large ledgers
  without walking every entry.
- **Encryption at rest / access control on the `.ddb`** — an ops/storage
  concern, orthogonal to the model.

The core stays minimal and reflexive; each of these lands as an additive layer,
never a rewrite of the eight atoms.
