# AI Census — Architecture

> The why lives in [POSITIONING.md](POSITIONING.md).
> This document covers architecture, privacy posture, and how to run one.

**What this is:** how the AI Census works inside a large
organization — the pattern engine that turns "everybody is playing with AI,
nobody is running it" into a governed portfolio decision. Vendor-neutral, runs
inside the org's own walls, and the org owns every byte of output.

---

## 1. The problem this instrument measures

The situation this was built for is the common one:

- **Heavy but fragmented adoption.** Multiple harnesses in live use — Cursor, GitHub
  Copilot, Claude Code, Microsoft Copilot, custom GPTs, no-code agents — with
  no shared picture of what any of them produced.
- **The Uber problem.** Spend is visible (seats, subscriptions), efficiency is not.
  Nobody can say which of five overlapping AI subscriptions is earning its keep.
- **The shadow portfolio.** Employees build personal productivity apps that work,
  then never circulate: the same summarizer gets built five times on four vendors'
  tools, and none of the five teams knows about the other four.

The census is an **evidence-first discovery instrument**: it does not ask people what
they think about AI; it reads what they actually built, what actually shipped, and
what actually gets used — then clusters the patterns and hands leadership a
four-bucket decision map.

### Hard requirement: harness-agnostic

The tool must never assume one vendor's harness. Concretely:

- Repo fingerprinting detects **every** harness by its config artifacts
  (`CLAUDE.md`/`.claude/`, `.cursorrules`/`.cursor/`,
  `.github/copilot-instructions.md`, `AGENTS.md`/`.codex`, `GEMINI.md`,
  `.windsurfrules`, `.aider.conf.yml`, generic `prompts/`/`skills/` dirs,
  `.mcp.json`). Adding a harness is one line in a fingerprint table.
- The survey and usage adapters treat the tool name as **data**, not schema — a
  Zapier+GPT hack and a Claude Code skill normalize into the same asset model.
- Claude is the **analysis engine**, not a filter on what gets analyzed. The
  clustering pass judges a Copilot Studio bot and a Cursor-built dashboard by the
  same evidence standards.

---

## 2. Architecture

```
                        ┌──────────────────────────────┐
   SOURCES              │  ADAPTERS (one per source)    │
   ────────             ├──────────────────────────────┤
   org GitHub/GitLab ──▶│ repos-github / repos-gitlab   │
   local checkouts   ──▶│ repos-local                   │
   vendor CSV exports──▶│ usage-csv (vendor profiles)   │──▶ NORMALIZER ──▶ inventory.json
   builder survey    ──▶│ survey-csv                    │    (one asset model
   wikis / docs      ──▶│ docs-markdown                 │     for everything)
                        └──────────────────────────────┘
                                                              │
                       ┌──────────────────────────────────────┘
                       ▼
        HEURISTIC PRE-PASS (deterministic floor: categories, token
        clustering, stall/ship signals, seat-inactivity, gap coverage)
                       │
                       ▼
        CLAUDE PATTERN PASS (semantic clustering, duplicate detection,
        verdicts, gap analysis, executive narrative) — three interchangeable
        transports:
          a. local `claude` CLI  (claude -p, JSON out)
          b. --emit-inventory / --analysis file round-trip: ANY harness or
             in-VPC endpoint (Bedrock, api.anthropic.com, a reviewer) produces
             the analysis against a documented contract
          c. run as a Claude Code skill: the session's own model IS the engine
                       │
                       ▼
        GUARDRAILS (hallucinated asset ids dropped; unclustered assets
        backfilled as singletons; invalid verdicts fall back to the
        deterministic floor — every asset always ends with exactly one
        cluster and one verdict)
                       │
                       ▼
        FOUR-BUCKET SCORER ──▶ REPORT (report.md + report.json)
        SCALE / MERGE / SUNSET / BUILD + distribution paths + gap list
```

### The normalized asset model

Every source collapses into one record shape, so the pattern engine reasons over
repos, subscriptions, and survey rows uniformly:

| Field | Meaning |
|---|---|
| `id`, `source`, `name`, `description` | identity + what it is |
| `owners`, `team` | who (pseudonymized under `--deidentify`) |
| `harnesses` | which AI tools built it (list — multi-harness is common) |
| `uses_ai_runtime` | repo calls AI at runtime (distinct from built-with-AI) |
| `status` | shipped / active / stalled / idea — from evidence, not claims |
| `users_claimed`, `spend_monthly` | adoption + cost signals |
| `signals` | raw evidence: last commit, commits/90d, CI, deploy markers, seats, inactivity |

### What each adapter needs from the org

| Adapter | Data needed | Access required | Sensitivity |
|---|---|---|---|
| **repos-github / -gitlab** | repo metadata, root file listing, README, commit dates | read-only org token (`read:org`, repo metadata) | Low — no source code content beyond READMEs |
| **repos-local** | a directory of checkouts (air-gapped alternative) | filesystem only | Same |
| **usage-csv** | Claude admin analytics export; Copilot seat/usage export; any vendor CSV via the generic profile (`user/tool/spend/seats/last_active` column synonyms) | admin-portal export, done by an org admin | Medium — contains emails → de-identify |
| **survey-csv** | builder survey: builder, team, asset, description, job-to-be-done, tools used, who uses it, status, link | a form (MS Forms/Google) exported to CSV | Medium — names → de-identify |
| **docs-markdown** | wiki/Notion/Confluence export of internal-tool pages | export, optional | Low-medium |
| **repos-gitlab** | project metadata + root tree from any GitLab instance | read-only group token via env var | Low |
| **db (any database)** | rows from app registries, CMDBs, warehouse tables — sqlite natively, everything else via the org's own DB CLI emitting CSV/JSON, mapped by a column map | read-only DB account | Depends on query — de-identify owners |

The whole setup for an org is one JSON config (sources + initiatives +
extension points: custom harness fingerprints, usage profiles, survey columns,
category vocabulary — org vocabulary takes precedence). No code changes to
onboard an org; secrets only ever come from environment variables.

New sources (Slack app inventories, ServiceNow CMDB, SSO app logs) plug in as new
adapters emitting the same asset model — that is the whole contract.

### Output

- **`report.md`** — the human deliverable: executive narrative, four-bucket verdict
  map, harness footprint, pattern clusters with duplicate flags, gaps vs stated
  initiatives, distribution-path recommendations, full inventory appendix.
- **`report.json`** — every asset, signal, cluster, verdict, and rationale, machine-
  readable, so verdicts are traceable to evidence and the org can re-slice.

### The four buckets

| Bucket | Meaning | Typical evidence |
|---|---|---|
| **SCALE** | Works — roll out company-wide | shipped + CI/deploy + real users; active seat base |
| **MERGE** | Many versions become one | duplicate cluster; clone drift; same job on N vendors |
| **SUNSET** | Redundant tool or spend | stalled >90d; majority-idle seats; idea duplicating an existing pattern |
| **BUILD** | Real gap, nothing exists | stated initiative with zero or failed coverage |

Plus, for every personal asset marked SCALE: a **distribution path** — the concrete
route from someone's side-tool to a company asset (platform owner, SSO, data access
review, internal catalog listing).

---

## 3. Privacy & compliance posture (enterprise grade)

1. **Runs inside the org's walls.** Single-file Python, stdlib only, no pip
   installs, no telemetry. Network calls: the org's own git host (optional) and
   the org's own Claude endpoint (their AWS Bedrock deployment or an approved API key). Fully air-gapped mode exists:
   local checkouts + `--no-llm` (deterministic heuristics only), or the
   `--emit-inventory` / `--analysis` round-trip through whatever approved
   model endpoint you designate.
2. **You own the output.** Reports are files in your environment. Nothing is
   uploaded, phoned home, or retained anywhere else.
3. **De-identification.** `--deidentify` replaces every person identifier (emails,
   names) with stable salted pseudonyms (`person-3fa2c8d1`) before anything reaches
   the pattern engine or the report — stable, so "the same person built three of
   these" survives; opaque, so no name does. The salt stays with the org.
4. **No sensitive records by design.** The census reads engineering metadata,
   seat data, and self-reported tool descriptions — never customer records,
   regulated data, or content repositories. The survey instructions explicitly
   prohibit pasting confidential data; the
   docs adapter is scoped to internal-tools pages only.
5. **Read-only everywhere.** The tool never writes to any system it reads; the
   token is read-only; the audit trail is the report.json itself (which verdict,
   from which evidence, when).
6. **Human-in-the-loop.** The four-bucket map is a recommendation instrument.
   Nothing is sunset, merged, or scaled by the tool — decisions happen in the
   review with whoever governs AI in your org, with a human in the loop
   throughout.

---

## 4. Running a census

The tool runs in minutes. The work around it is collection and review, and most
orgs spread that over about two weeks.

**Collect.**

- Agree the scope: which orgs, which vendors, which business units. Get a
  read-only token for your git host and decide who holds the de-identification
  salt.
- Write your priority AI projects into `initiatives.txt`. Gap analysis is scored
  against them, so this list is what "white space" gets measured from.
- Pull seat and usage exports from each AI vendor. Any CSV works, because the
  generic profile maps column synonyms rather than requiring a per-vendor
  integration.
- Run the builder survey. Distribution matters more than the questions: announce
  it as amnesty, not audit. Nothing gets taken away, good tools get resourced.
  The census fails if people hide their side-tools.
- Sweep the repos. Read-only, minutes. You have a heuristic report before any
  model runs, which is your sanity check on the data.

**Review.**

- Run the full census with the pattern pass inside your environment.
- Have people check every MERGE and SUNSET verdict against the evidence. The
  engine proposes, humans confirm. Disputed verdicts are overridden in the
  analysis file, and those overrides are first-class in the round-trip design.
- Walk the duplicate clusters and the gap list with the people who own the
  assets. They know things the evidence does not show.
- Re-run with corrections.

**Then keep it.** The census is built to be re-run quarterly, with `wiki.py lint`
monthly in between. That is the difference between a governance instrument and a
one-time audit.

---

## 5. Second-brain methodology (after Karpathy)

Two reference points shape how the census stays alive after the first run
(sources: Karpathy's LLM-wiki method, via askglitch.com/blog/build-a-second-brain,
and github.com/karpathy/autoresearch):

**The three-layer second brain.** The census maps exactly onto Karpathy's
architecture, which validates choices we'd already made and named what was
missing:

| Karpathy layer | Census equivalent |
|---|---|
| `sources/` — immutable raw evidence, LLM reads but never edits | `inventory.json` + vendor exports + survey CSVs |
| wiki — LLM-maintained entity pages with backlinks; you read it, the LLM writes it | `wiki.py build` → one page per pattern/asset/initiative, cross-linked with backlinks (Obsidian-compatible) |
| schema — conventions the agent obeys | `SKILL.md` + the engine contract in the inventory |

Deliberately **no embeddings**, matching the method: connections are explicit
links plus LLM editorial judgment — every page human-inspectable, every claim
traceable. (This reverses our earlier "embeddings as v2" note; auditability
wins in any regulated context anyway.)

**Maintenance ops.** A second brain that isn't linted rots. `wiki.py lint`
re-checks the report's claims against fresh evidence: **contradictions**
(a SUNSET repo that resumed committing; a SCALE asset that lost momentum),
**resolutions** (a MERGE-verdict clone whose checkout is gone — done),
**orphans** (assets with no owner), and **coverage** (initiatives still
un-built). Run monthly between quarterly censuses: the four-bucket map stays
a governance instrument instead of a stale audit.

**The autoresearch frame.** Karpathy's autoresearch loop — one editable
artifact, a fixed time budget, a single metric, keep-or-discard, log
everything — is the governance model the census is designed to leave behind. The shipped-vs-stalled sprawl the census measures is exactly what
happens when experiments have no metric and no keep/discard discipline. The
recommendation: every internal AI experiment declares its metric and
budget up front; the census lint becomes the keep/discard log. (It's also how
we improve the census engine itself: the analysis contract is our
`program.md` — iterate on the prompt against a benchmark corpus, keep what
scores better.)

## 6. Prototype status & known limits

Working prototype in this repo (`census.py`, tests in `tests/`), dry-run against a
real developer org of 29 repos plus sample usage and survey CSVs: it correctly
surfaced genuine clone sprawl (several checkouts of one project, duplicated
tooling repos), a five-instance copy-paste pattern, a cross-vendor summarizer
duplicate, idle-seat SUNSET calls, and true white-space gaps. Output:
`out/dryrun-llm/report.md`.

Since the first cut (all landed and tested):
- **Batched GitHub adapter** — GraphQL, 50 repos (metadata + root tree for
  harness sniffing) per API call with pagination; live-tested against a real
  org. enterprise-scale sweeps no longer pay one round-trip per repo.
- **Cross-vendor seat join** — vendor identities normalized onto one key
  (email local-part ≡ login) *before* pseudonymization, so `--deidentify`
  preserves the join; the report now answers the Uber problem per person
  ("holds seats at Claude + Copilot, idle at both — consolidation candidate").
- **Second-brain graph** (`--graph` / `brain.py`) — the census as a knowledge
  graph: assets, people, teams, harnesses, clusters, initiatives, explicit
  note links, and content-similarity edges, rendered as a self-contained
  interactive `graph.html` (no CDN, safe in-VPC). In review this is the
  exploration surface; report.md is the decision surface.
- **Content-stable asset ids** — ids are hashes of source+name, not positions,
  so an analysis produced against one inventory can never silently re-bind
  verdicts to the wrong assets when a repo appears between passes (caught as a
  real off-by-one during the dry run).

Hardening pass (v1.0.0): `--check` config preflight (paths, env vars, CLI auth,
plain-http rejection, secrets-in-config rejection); sqlite opened read-only at
the driver level; DB commands validated as argv lists (no shell); BOM-tolerant
CSV parsing (Excel exports); prompt-injection guard in the engine contract
(scanned text is untrusted data — embedded instructions are flagged as gaming,
not followed); graph viewer escapes all scanned content (no script breakout via
hostile repo names); weak-salt warning under --deidentify; clear one-line
errors instead of tracebacks.

Known limits, deliberate for a prototype:
- Heuristic gap-matching is lexical (df-filtered token overlap); the semantic pass
  is the Claude engine's job — one dry-run collision (a lawsuit-intake app matching
  "case intake triage") was correctly resolved by the engine pass.
- The `claude -p` transport requires a logged-in CLI; inside another Claude Code
  session, or where the CLI can't auth headless, use the `--emit-inventory` /
  `--analysis` round-trip (this is also the recommended in-VPC mode).
- Graph similarity edges are token-Jaccard; embedding-based similarity is a v2
  upgrade once an in-VPC embedding endpoint is approved.
