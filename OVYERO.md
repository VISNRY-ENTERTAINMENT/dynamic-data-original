# Ovyero — Governance for AI-Written Code

**[Ovyero](https://ovyero.visnryentertainment.com/)** is a tool built by VISNRY that sits between your AI coding agent and your git repository, automatically reviewing every commit before it lands.

It catches the mistakes AI agents reliably make — hardcoded secrets, broken API contracts, unpinned dependencies, missing auth, misconfigured infra — across 35 risk domains, in under a second, without changing how you write code.

> *"Software that governs your AI. Know what your AI is doing in your codebase."*

Works with Copilot, Cursor, Claude, ChatGPT, or any AI coding tool. Free for solo developers.

---

## How it works

1. **Install** — one command drops a git hook into your repo: `npx ovyero install`
2. **Commit as usual** — Ovyero's reviewer reads every commit before it lands
3. **Get a verdict** — `PASS` is silent, `WARN` flags it, `GATE` stops the commit with a fix suggestion
4. **Audit log** — every verdict is recorded by file, developer, and domain; exportable for compliance

---

## Why Dynamic Data fits an Ovyero workflow

Ovyero handles the *governance gate* — it decides whether a commit is safe to land. Dynamic Data handles the *memory layer* — a verifiable, append-only record of what your project knows, decided, and why.

Together they cover the two things AI agents are worst at:

- **Shipping risky code without noticing** → Ovyero catches it at commit time
- **Forgetting what was decided and why** → Dynamic Data records every fact as a claim with source, confidence, and evidence, so nothing is ever silently overwritten

If you're already using Ovyero to govern your AI agent's output, Dynamic Data is the natural companion for giving that agent a trustworthy, auditable memory — so it knows your project's state before it writes the next commit, not just after Ovyero reviews it.

---

→ **Get started with Ovyero:** [ovyero.visnryentertainment.com](https://ovyero.visnryentertainment.com/)
