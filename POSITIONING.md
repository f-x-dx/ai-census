# The AI Census

**A discovery instrument for AI transformation.**

Every large org has the same problem right now. Hundreds of people are building
with AI. Nobody can see the whole board.

Staff subscribed to five overlapping AI tools. Spend is visible, efficiency is not.
Three teams built the same summarizer on three different platforms and none of them
know about the other two. Someone in field ops built a tool that saves 12 people an
hour a day, and it will never leave that team. That is the state of AI in most
enterprises: heavy adoption, zero visibility.

You cannot transform what you cannot see. The census is the seeing.

## What it does

The census reads the evidence, not the opinions. It scans what your people
actually built and what your org actually pays for:

- **Repos.** Every repo on your GitHub, GitLab, or file server. Which AI tools
  built it. Whether it shipped or stalled.
- **Spend.** Seat and usage exports from every AI vendor. Who holds seats where,
  and which seats sit idle.
- **The shadow portfolio.** A short builder survey: what people made, for what
  job, on which tools, who uses it.
- **Your systems.** App registries, CMDBs, wikis, any database you already keep.

Then it clusters the patterns and puts every asset in one of four buckets:

| Bucket | Meaning |
|---|---|
| **SCALE** | It works. Roll it out company-wide. |
| **MERGE** | Five versions become one. |
| **SUNSET** | Redundant tool or idle spend. Retire it. |
| **BUILD** | Real gap. Nothing exists yet. |

Every verdict traces to evidence: commit activity, deploys, real users, seat data.
No verdict rests on how impressive something sounds.

## What you get

1. **The pattern report.** The executive read, the duplicate patterns, the
   verdict map, and the gaps against your stated initiatives.
2. **The map.** An interactive graph of how every asset, person, team, and tool
   connects. Your org's AI second brain, on one screen.
3. **The wiki.** A browsable page per pattern and per asset, cross-linked, built
   for the people who will act on it.
4. **The promotion list.** Personal tools worth turning into company assets, each
   with a concrete path: owner, platform, rollout.
5. **The tool itself.** It stays with you. Re-run it quarterly. A monthly lint
   pass flags where reality has drifted from the map.

## How it runs

Inside your walls. All of it.

One Python file, no installs, no external services, no telemetry. It reads your
systems with read-only access and writes files on your infrastructure. Names and
emails are replaced with stable pseudonyms before analysis. Nothing leaves your
environment. You own every byte of the output.

It is also tool-neutral. The census does not care whether your people build with
Claude, Copilot, Cursor, custom GPTs, or no-code agents. It judges every asset by
the same standard: does it work, does anyone use it, does it already exist.

## Where it fits

The census is phase one of AI transformation done in the right order:

**See** what exists. **Decide** what scales, merges, sunsets. **Build** what is
actually missing. **Govern** so the sprawl never grows back.

Most transformation programs start at build. That is why 95% of enterprise AI
pilots show no measurable return. Start at see.
