#!/usr/bin/env python3
"""
AI Census — wiki + lint layer (the Karpathy second-brain ops).

Methodology (karpathy LLM-wiki, via askglitch.com/blog/build-a-second-brain):
  Layer 1  sources — immutable evidence        -> inventory.json / report.json
  Layer 2  wiki    — maintained entity pages    -> this module's `build`
  Layer 3  schema  — conventions the agent obeys-> SKILL.md / LLM_SYSTEM
Plus the maintenance ops that keep a second brain honest:
  /lint   -> `lint`: contradictions, orphans, stale claims, coverage gaps
  /digest -> the report.md executive read (already exists)

No embeddings, by design: connections are explicit links and LLM editorial
judgment — every page human-inspectable, every claim traceable to evidence.

Usage:
  python3 wiki.py build out/report.json --out out/wiki
  python3 wiki.py lint  out/report.json          # re-checks verdicts vs evidence
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone

STALLED_DAYS = 90


def log(msg):
    print("[wiki] %s" % msg, file=sys.stderr)


def slug(name):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", name.lower())).strip("-")[:60]


def wl(name):
    """Wiki-link to a page by display name."""
    return "[[%s]]" % name


# ---------------------------------------------------------------------------
# build — entity pages with backlinks, precomputed at ingestion time
# ---------------------------------------------------------------------------

def build(report, outdir):
    assets = report["assets"]
    verdicts = report.get("verdicts", {})
    clusters = report.get("clusters", [])
    gaps = report.get("gaps", [])
    by_id = {a["id"]: a for a in assets}
    cluster_of = {}
    for cl in clusters:
        for aid in cl.get("asset_ids", []):
            cluster_of[aid] = cl

    pages = {}          # filename -> markdown lines
    links = defaultdict(set)   # filename -> set of filenames it links to

    def page(name, kind):
        fn = "%s--%s.md" % (kind, slug(name))
        if fn not in pages:
            pages[fn] = ["---", "title: %s" % name, "kind: %s" % kind,
                         "generated: %s" % report.get("meta", {}).get("generated", ""),
                         "---", ""]
        return fn

    def link(src_fn, dst_fn):
        if dst_fn != src_fn:
            links[src_fn].add(dst_fn)

    # cluster pages — the core entity: one page per pattern
    cluster_fn = {}
    for cl in clusters:
        if len(cl.get("asset_ids", [])) < 2 and not cl.get("duplicate_pattern"):
            continue
        fn = page(cl["name"], "pattern")
        cluster_fn[cl["id"]] = fn
        w = pages[fn].append
        if cl.get("duplicate_pattern"):
            w("**DUPLICATE PATTERN** — %d independent builds of the same thing." %
              len(cl["asset_ids"]))
            w("")
        w(cl.get("rationale", ""))
        w("")
        w("## Members")
        w("")
        for aid in cl["asset_ids"]:
            a = by_id.get(aid)
            if not a:
                continue
            v = verdicts.get(aid, {})
            w("- %s — `%s` · **%s** — %s" % (
                wl(a["name"]), a["status"], v.get("verdict", "?"),
                v.get("rationale", "")))
        w("")

    # asset pages
    for a in assets:
        fn = page(a["name"], "asset")
        v = verdicts.get(a["id"], {})
        w = pages[fn].append
        w("**%s** · %s · %s" % (v.get("verdict", "?"), a["source"], a["status"]))
        w("")
        if a.get("description"):
            w(a["description"][:500])
            w("")
        if v.get("rationale"):
            w("> Verdict: %s" % v["rationale"])
            w("")
        if v.get("distribution_path"):
            w("**Distribution path:** %s" % v["distribution_path"])
            w("")
        cl = cluster_of.get(a["id"])
        if cl and cl["id"] in cluster_fn:
            w("Pattern: %s" % wl(cl["name"]))
            link(fn, cluster_fn[cl["id"]])
            w("")
        facts = []
        sig = a.get("signals", {})
        if sig.get("last_commit"):
            facts.append("last activity %s" % str(sig["last_commit"])[:10])
        if sig.get("commits_90d"):
            facts.append("%s commits/90d" % sig["commits_90d"])
        if sig.get("seats"):
            facts.append("%s seats, %s idle >30d" % (sig["seats"], sig.get("inactive_30d", 0)))
        if a.get("harnesses"):
            facts.append("built with " + ", ".join(a["harnesses"]))
        if a.get("team"):
            facts.append("team " + a["team"])
        if facts:
            w("_Evidence: %s._" % "; ".join(facts))
            w("")
        # explicit note links from the docs adapter become wiki links
        for target in sig.get("links", []):
            t = next((x for x in assets if x["name"].strip().lower() == target), None)
            if t:
                w("See also: %s" % wl(t["name"]))
                link(fn, page(t["name"], "asset"))
        # let cluster pages link back to members
        if cl and cl["id"] in cluster_fn:
            link(cluster_fn[cl["id"]], fn)

    # initiative pages
    for g in gaps:
        fn = page(g["initiative"], "initiative")
        w = pages[fn].append
        w("**BUILD** — open gap.")
        w("")
        w(g.get("rationale", ""))
        w("")

    # backlinks (computed, appended last — the wiki stays navigable both ways)
    inbound = defaultdict(set)
    for src, dsts in links.items():
        for d in dsts:
            inbound[d].add(src)
    titles = {fn: lines[1].split("title: ", 1)[1] for fn, lines in pages.items()}
    for fn, srcs in inbound.items():
        pages[fn] += ["## Backlinks", ""] + \
                     ["- %s" % wl(titles[s]) for s in sorted(srcs)] + [""]

    # index
    idx = ["---", "title: AI Census Wiki — %s" % report.get("org", ""), "kind: index",
           "---", "", "## Patterns", ""]
    for cl in clusters:
        if cl["id"] in cluster_fn:
            idx.append("- %s%s" % (wl(cl["name"]),
                       " — **duplicate**" if cl.get("duplicate_pattern") else ""))
    idx += ["", "## Open gaps (BUILD)", ""]
    idx += ["- %s" % wl(g["initiative"]) for g in gaps]
    idx += ["", "## All assets", ""]
    idx += ["- %s (%s)" % (wl(a["name"]), verdicts.get(a["id"], {}).get("verdict", "?"))
            for a in assets]
    pages["index.md"] = idx + [""]

    os.makedirs(outdir, exist_ok=True)
    for fn, lines in pages.items():
        with open(os.path.join(outdir, fn), "w") as f:
            f.write("\n".join(lines))
    log("wiki: %d pages -> %s" % (len(pages), outdir))
    return pages


# ---------------------------------------------------------------------------
# lint — re-check the report's claims against fresh evidence
# ---------------------------------------------------------------------------

def _git_last_commit(path):
    try:
        r = subprocess.run(["git", "-C", path, "log", "-1", "--format=%cI"],
                           capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def lint(report, now=None):
    """The /lint op: does the wiki still agree with the world?
    Returns findings: contradictions, resolved items, orphans, coverage gaps."""
    now = now or datetime.now(timezone.utc)
    assets = report["assets"]
    verdicts = report.get("verdicts", {})
    findings = []

    def add(kind, subject, detail):
        findings.append({"kind": kind, "subject": subject, "detail": detail})

    for a in assets:
        v = verdicts.get(a["id"], {}).get("verdict")
        path = a.get("signals", {}).get("path")
        if not path:
            continue
        if not os.path.isdir(path):
            if v in ("MERGE", "SUNSET"):
                add("resolved", a["name"],
                    "verdict %s and the checkout is gone — done, update the page" % v)
            else:
                add("stale", a["name"], "checkout no longer exists at %s" % path)
            continue
        last = _git_last_commit(path)
        if not last:
            continue
        try:
            age = (now - datetime.fromisoformat(last.replace("Z", "+00:00"))).days
        except ValueError:
            continue
        recorded = str(a.get("signals", {}).get("last_commit", ""))[:10]
        if v == "SUNSET" and age <= 14 and last[:10] > recorded:
            add("contradiction", a["name"],
                "verdict SUNSET but new commits since the census (%s) — resumed, revisit" % last[:10])
        if v == "SCALE" and age > STALLED_DAYS:
            add("contradiction", a["name"],
                "verdict SCALE but no commits in %d days — momentum lost" % age)

    for a in assets:
        if not a.get("owners") and not a.get("team") and a["source"] == "survey":
            add("orphan", a["name"], "no owner or team on record — who maintains this?")

    for g in report.get("gaps", []):
        add("coverage", g["initiative"], "still an open BUILD gap")

    return findings


def render_lint(findings, org):
    order = ["contradiction", "resolved", "stale", "orphan", "coverage"]
    lines = ["# Census lint — %s" % org, "",
             "%d findings. Contradictions first: these are places where the "
             "world moved after the census and the pages are now wrong." % len(findings), ""]
    for kind in order:
        rows = [f for f in findings if f["kind"] == kind]
        if not rows:
            continue
        lines.append("## %s (%d)" % (kind, len(rows)))
        lines.append("")
        for f in rows:
            lines.append("- **%s** — %s" % (f["subject"], f["detail"]))
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="wiki",
                                 description="Second-brain wiki + lint ops over a census report")
    ap.add_argument("command", choices=["build", "lint"])
    ap.add_argument("report", help="path to report.json")
    ap.add_argument("--out", default=None,
                    help="output dir (build: wiki pages; lint: lint.md), "
                         "default alongside the report")
    args = ap.parse_args(argv)
    try:
        with open(args.report) as f:
            report = json.load(f)
    except (OSError, ValueError) as e:
        log("error: cannot read report: %s" % e)
        return 1
    if not isinstance(report, dict) or "assets" not in report:
        log("error: %s is not a census report.json" % args.report)
        return 1
    base = args.out or os.path.join(os.path.dirname(args.report) or ".", "wiki")
    if args.command == "build":
        build(report, base)
    else:
        findings = lint(report)
        md = render_lint(findings, report.get("org", ""))
        out = args.out or os.path.join(os.path.dirname(args.report) or ".", "lint.md")
        if os.path.isdir(out):
            out = os.path.join(out, "lint.md")
        with open(out, "w") as f:
            f.write(md)
        counts = defaultdict(int)
        for f_ in findings:
            counts[f_["kind"]] += 1
        log("lint: %s -> %s" % (dict(counts) or "clean", out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
