---
name: ai-census
description: Run an AI Census — harness-agnostic discovery of everything an org built with AI (repos, usage exports, builder survey), clustered into patterns with a SCALE/MERGE/SUNSET/BUILD verdict map. Use when asked to run a census, audit AI adoption, find duplicate AI tools, or map an org's shadow AI portfolio.
---

# AI Census skill

You are the **pattern engine** of the census. The Python CLI collects and
normalizes evidence; you do the semantic reasoning. Never shell out to a nested
`claude` CLI — you are the model.

## Procedure

1. **Locate the tool.** `census.py` lives in this repo (or ask for the path).
   Prefer an org config file (`census.json`, see samples/census.example.json):
   it declares all sources — local checkouts, GitHub orgs, GitLab groups,
   usage CSVs, surveys, docs, and databases (sqlite directly, any other DB via
   its CLI + a column map) — plus initiatives and org-specific extensions.
   If none exists, offer to write one from what the user describes. When the
   data covers real people, default de-identification ON. Never put tokens or passwords in the
   config; DB and GitLab credentials come from env vars, and DB accounts
   should be read-only.

   For a fresh org, start with `python3 census.py init` (or hand them
   `census.pyz`, built via `python3 census.py dist`) and
   `python3 census.py check --config census.json` before collecting anything.

2. **Emit the inventory:**
   ```bash
   python3 census.py <input flags or --config census.json> --deidentify \
     --emit-inventory out/inventory.json
   ```

3. **Be the engine.** Read `out/inventory.json`. The `instructions` field is your
   contract — follow it exactly. Reason over every asset's evidence (activity,
   CI/deploy, seats, inactivity, users_claimed), not names. Judge all harnesses
   by the same standard. Produce the analysis JSON (clusters, verdicts, gaps,
   narrative) and write it to `out/analysis.json`. Rules that matter most:
   - every asset id in exactly one cluster, exactly one verdict
   - name the survivor in every MERGE cluster
   - distribution_path for every personal asset marked SCALE
   - idea-stage assets never get MERGE (nothing exists to merge)

4. **Render:**
   ```bash
   python3 census.py <same input flags> --deidentify \
     --analysis out/analysis.json --out out/
   ```
   The CLI applies guardrails (drops hallucinated ids, backfills singletons);
   check its stderr for what it corrected — corrections mean you made an error
   worth re-checking.

5. **Deliver.** Summarize the four-bucket counts, the duplicate patterns, the
   top distribution-path candidates, and the BUILD gaps. Point to
   `out/report.md` and `out/report.json`.

## Guardrails

- Read-only toward the org: never modify scanned repos.
- Never put real names/emails in the report when `--deidentify` is on — if you
  see one in inventory.json, the adapter missed it; flag it, don't propagate it.
- Verdicts are recommendations for a human review, not actions. Never delete,
  archive, or cancel anything based on a SUNSET verdict.
