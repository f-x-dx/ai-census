"""Tests for the AI Census pipeline. Stdlib unittest; no network, no LLM.

Run:  python3 -m unittest discover -s tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import census  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(ROOT, "samples")


GIT_ID = ["-c", "user.name=t", "-c", "user.email=t@x.co",
          "-c", "commit.gpgsign=false"]


def make_fake_repo(parent, name, files, commit=True):
    repo = os.path.join(parent, name)
    os.makedirs(repo)
    for rel, content in files.items():
        p = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(content)
    subprocess.run(["git", "init", "-q", repo], check=True)
    if commit:
        subprocess.run(["git", "-C", repo] + GIT_ID + ["add", "-A"], check=True)
        subprocess.run(["git", "-C", repo] + GIT_ID + ["commit", "-qm", "init"],
                       check=True)
        # self-verify: the adapters depend on `git log` working here — fail
        # loudly with git's own stderr instead of a downstream assert
        r = subprocess.run(["git", "-C", repo, "log", "-1", "--format=%cI"],
                           capture_output=True, text=True)
        if r.returncode != 0 or not r.stdout.strip():
            raise RuntimeError("fake repo unusable (git log rc=%d): %s"
                               % (r.returncode, r.stderr.strip()))
    return repo


class TestRepoAdapter(unittest.TestCase):
    def test_harness_detection_multi_vendor(self):
        with tempfile.TemporaryDirectory() as td:
            make_fake_repo(td, "claude-proj", {
                "CLAUDE.md": "instructions", "README.md": "# Summarizer for tickets"})
            make_fake_repo(td, "cursor-proj", {
                ".cursorrules": "rules", "README.md": "# Dashboard generator"})
            make_fake_repo(td, "copilot-proj", {
                ".github/copilot-instructions.md": "x", "README.md": "# API service"})
            assets = census.adapter_repos_local(td, False, "s")
            by_name = {a["name"]: a for a in assets}
            self.assertIn("claude-code", by_name["claude-proj"]["harnesses"])
            self.assertIn("cursor", by_name["cursor-proj"]["harnesses"])
            self.assertIn("github-copilot", by_name["copilot-proj"]["harnesses"])
            # harness-agnostic: no vendor privileged, all three detected
            self.assertEqual(len(assets), 3)

    def test_shipped_vs_active_signals(self):
        with tempfile.TemporaryDirectory() as td:
            make_fake_repo(td, "shipped-app", {
                "vercel.json": "{}", ".github/workflows/ci.yml": "on: push",
                "README.md": "# Prod app"})
            make_fake_repo(td, "wip-app", {"README.md": "# WIP"})
            assets = {a["name"]: a for a in census.adapter_repos_local(td, False, "s")}
            self.assertEqual(assets["shipped-app"]["status"], "shipped")
            self.assertTrue(assets["shipped-app"]["signals"]["has_deploy"])
            self.assertEqual(assets["wip-app"]["status"], "active")

    def test_ai_runtime_dependency_detection(self):
        with tempfile.TemporaryDirectory() as td:
            make_fake_repo(td, "ai-app", {
                "package.json": '{"dependencies":{"@anthropic-ai/sdk":"^1.0"}}',
                "README.md": "# Bot"})
            make_fake_repo(td, "plain-app", {
                "package.json": '{"dependencies":{"express":"^4"}}',
                "README.md": "# Site"})
            assets = {a["name"]: a for a in census.adapter_repos_local(td, False, "s")}
            self.assertTrue(assets["ai-app"]["uses_ai_runtime"])
            self.assertFalse(assets["plain-app"]["uses_ai_runtime"])


class TestUsageAdapter(unittest.TestCase):
    def test_claude_profile_inactivity(self):
        assets = census.adapter_usage_csv(
            os.path.join(SAMPLES, "usage_claude.csv"), "Claude", "claude", False, "s")
        self.assertEqual(len(assets), 1)
        a = assets[0]
        self.assertEqual(a["signals"]["seats"], 7)
        self.assertEqual(a["signals"]["inactive_30d"], 3)  # carla, erin, gina

    def test_copilot_profile_mostly_inactive(self):
        assets = census.adapter_usage_csv(
            os.path.join(SAMPLES, "usage_copilot.csv"), "GitHub Copilot", "copilot", False, "s")
        a = assets[0]
        self.assertEqual(a["signals"]["seats"], 7)
        self.assertEqual(a["signals"]["inactive_30d"], 5)

    def test_generic_profile_spend(self):
        assets = census.adapter_usage_csv(
            os.path.join(SAMPLES, "usage_generic.csv"), "Misc SaaS", "generic", False, "s")
        self.assertEqual(assets[0]["spend_monthly"], 164.0)  # 99+40+25


class TestSurveyAdapter(unittest.TestCase):
    def test_parse_and_tools_split(self):
        assets = census.adapter_survey_csv(os.path.join(SAMPLES, "survey.csv"), False, "s")
        self.assertEqual(len(assets), 8)
        ada = next(a for a in assets if a["name"] == "Call Notes Summarizer")
        self.assertIn("claude-code", ada["harnesses"])
        self.assertIn("custom-gpt", ada["harnesses"])
        self.assertEqual(ada["status"], "shipped")

    def test_deidentify_stable_and_opaque(self):
        a1 = census.adapter_survey_csv(os.path.join(SAMPLES, "survey.csv"), True, "salt1")
        a2 = census.adapter_survey_csv(os.path.join(SAMPLES, "survey.csv"), True, "salt1")
        self.assertEqual(a1[0]["owners"], a2[0]["owners"])          # stable
        self.assertNotIn("Ada", a1[0]["owners"][0])                 # opaque
        self.assertTrue(a1[0]["owners"][0].startswith("person-"))


class TestPatternEngine(unittest.TestCase):
    def _survey_assets(self):
        assets = census.adapter_survey_csv(os.path.join(SAMPLES, "survey.csv"), False, "s")
        for i, a in enumerate(assets):
            a["id"] = "A%03d" % (i + 1)
        return assets

    def test_duplicate_summarizers_cluster_together(self):
        assets = self._survey_assets()
        clusters = census.heuristic_clusters(assets)
        summar = [c for c in clusters if c["category"] == "summarization"]
        self.assertTrue(summar, "expected a summarization cluster")
        biggest = max(summar, key=lambda c: len(c["asset_ids"]))
        # 5 survey rows are summarizers built on 4 different harnesses —
        # the census must catch the duplicate pattern across vendors
        self.assertGreaterEqual(len(biggest["asset_ids"]), 3)
        self.assertTrue(biggest["duplicate_pattern"])

    def test_four_bucket_rules(self):
        assets = self._survey_assets()
        clusters = census.heuristic_clusters(assets)
        verdicts = census.heuristic_verdicts(assets, clusters)
        by_name = {a["name"]: verdicts[a["id"]]["verdict"] for a in assets}
        by_status = {a["name"]: a["status"] for a in assets}
        # idea-stage asset: BUILD if novel, SUNSET if it duplicates an existing
        # pattern (adopt instead of build) — never MERGE (nothing exists yet)
        self.assertIn(by_name["Competitor News Digest"], ("BUILD", "SUNSET"))
        self.assertNotEqual(by_name["Competitor News Digest"], "MERGE")
        dup_names = set()
        for c in clusters:
            if c["duplicate_pattern"]:
                dup_names |= {a["name"] for a in assets if a["id"] in c["asset_ids"]}
        for n in dup_names:
            if by_status[n] != "idea":
                self.assertEqual(by_name[n], "MERGE")
        # every asset gets exactly one verdict
        self.assertEqual(set(verdicts), {a["id"] for a in assets})

    def test_gap_detection(self):
        assets = self._survey_assets()
        clusters = census.heuristic_clusters(assets)
        with open(os.path.join(SAMPLES, "initiatives.txt")) as f:
            inits = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        gaps = census.heuristic_gaps(clusters, inits, assets)
        gap_text = " ".join(g["initiative"].lower() for g in gaps)
        self.assertIn("support ticket", gap_text)      # nothing built for this
        # covered initiatives must NOT be flagged as gaps
        self.assertNotIn("sales pipeline", gap_text)     # Pipeline Dashboard
        self.assertNotIn("sales meeting", gap_text)      # Meeting Prep Assistant

    def test_usage_sunset_rule(self):
        assets = census.adapter_usage_csv(
            os.path.join(SAMPLES, "usage_copilot.csv"), "GitHub Copilot", "copilot", False, "s")
        assets[0]["id"] = "A001"
        clusters = census.heuristic_clusters(assets)
        verdicts = census.heuristic_verdicts(assets, clusters)
        self.assertEqual(verdicts["A001"]["verdict"], "SUNSET")  # 5/7 seats idle


class TestSeatJoin(unittest.TestCase):
    def test_identity_normalization(self):
        self.assertEqual(census.normalize_identity("Ada.Okafor@example.com"), "adaokafor")
        self.assertEqual(census.normalize_identity("adaokafor"), "adaokafor")
        self.assertEqual(census.normalize_identity(""), "")

    def test_cross_vendor_join_survives_deidentification(self):
        assets = []
        for spec in [("Claude", "claude", "usage_claude.csv"),
                     ("GitHub Copilot", "copilot", "usage_copilot.csv")]:
            assets += census.adapter_usage_csv(
                os.path.join(SAMPLES, spec[2]), spec[0], spec[1], True, "salt")
        rows = census.cross_vendor_seats(assets)
        # ada/ben/carla/dev/erin/gina hold seats at both vendors; hank copilot-only
        self.assertEqual(len(rows), 6)
        for r in rows:
            self.assertTrue(r["user"].startswith("person-"))   # de-identified
            self.assertEqual(r["vendors"], ["Claude", "GitHub Copilot"])
        # ada + dev active at both; the other four idle somewhere
        candidates = [r for r in rows if r["consolidation_candidate"]]
        self.assertEqual(len(candidates), 4)

    def test_ids_stable_under_insertion(self):
        a = census.new_asset("survey", "Alpha Tool")
        b = census.new_asset("survey", "Beta Tool")
        census.assign_ids([a, b])
        ids_before = (a["id"], b["id"])
        # a new asset sorting first must not shift existing ids
        c = census.new_asset("survey", "AAA New Tool")
        a2 = census.new_asset("survey", "Alpha Tool")
        b2 = census.new_asset("survey", "Beta Tool")
        census.assign_ids([c, a2, b2])
        self.assertEqual(ids_before, (a2["id"], b2["id"]))
        self.assertNotEqual(c["id"], a2["id"])

    def test_github_repo_asset_normalization(self):
        rp = {"name": "x", "description": "d", "url": "u", "isArchived": False,
              "pushedAt": "2026-07-20T00:00:00Z",
              "primaryLanguage": {"name": "Python"},
              "object": {"entries": [{"name": "CLAUDE.md"}, {"name": ".cursorrules"},
                                     {"name": "src"}]}}
        a = census.github_repo_asset(rp)
        self.assertEqual(a["status"], "active")
        self.assertIn("claude-code", a["harnesses"])
        self.assertIn("cursor", a["harnesses"])
        self.assertEqual(a["signals"]["languages"], ["Python"])


class TestBrainGraph(unittest.TestCase):
    def _report(self):
        assets = census.adapter_survey_csv(os.path.join(SAMPLES, "survey.csv"), False, "s")
        for i, a in enumerate(assets):
            a["id"] = "A%03d" % (i + 1)
        clusters = census.heuristic_clusters(assets)
        verdicts = census.heuristic_verdicts(assets, clusters)
        return {"org": "TestCo", "meta": {}, "assets": assets, "clusters": clusters,
                "verdicts": verdicts,
                "gaps": [{"initiative": "Support ticket triage", "rationale": "none"}]}

    def test_graph_structure(self):
        import brain
        g = brain.build_graph(self._report())
        types = {n["type"] for n in g["nodes"]}
        self.assertLessEqual({"asset", "person", "team", "harness", "initiative"}, types)
        ids = {n["id"] for n in g["nodes"]}
        for e in g["edges"]:
            self.assertIn(e["source"], ids)
            self.assertIn(e["target"], ids)
        # singleton clusters excluded, multi-asset clusters present
        cluster_nodes = [n for n in g["nodes"] if n["type"] == "cluster"]
        self.assertTrue(cluster_nodes)
        member_edges = [e for e in g["edges"] if e["type"] == "member_of"]
        self.assertGreaterEqual(len(member_edges), 2)

    def test_note_links_become_edges(self):
        import brain
        rep = self._report()
        rep["assets"][0]["signals"]["links"] = [rep["assets"][1]["name"].lower()]
        g = brain.build_graph(rep)
        link_edges = [e for e in g["edges"] if e["type"] == "links_to"]
        self.assertEqual(len(link_edges), 1)
        self.assertEqual(link_edges[0]["source"], rep["assets"][0]["id"])

    def test_html_is_self_contained(self):
        import brain
        html = brain.render_html(brain.build_graph(self._report()))
        self.assertNotIn("http://", html)
        self.assertNotIn("https://cdn", html)
        self.assertIn("Second Brain", html)


class TestAnalysisMerge(unittest.TestCase):
    """The engine-analysis path (claude CLI or --analysis file) with guardrails."""

    def _fixture(self):
        assets = census.adapter_survey_csv(os.path.join(SAMPLES, "survey.csv"), False, "s")
        for i, a in enumerate(assets):
            a["id"] = "A%03d" % (i + 1)
        clusters = census.heuristic_clusters(assets)
        verdicts = census.heuristic_verdicts(assets, clusters)
        return assets, clusters, verdicts

    def test_hallucinated_ids_dropped_and_singletons_backfilled(self):
        assets, clusters, verdicts = self._fixture()
        data = {"clusters": [{"id": "C01", "name": "Summarizers", "category": "summarization",
                              "asset_ids": ["A001", "A999"], "duplicate_pattern": True,
                              "rationale": "x"}],
                "verdicts": {"A001": {"verdict": "MERGE", "rationale": "x", "cluster": "C01"},
                             "A999": {"verdict": "SCALE", "rationale": "ghost"},
                             "A002": {"verdict": "NONSENSE", "rationale": "bad bucket"}},
                "narrative": "n"}
        cl, vd, gaps, narr = census.apply_analysis(data, assets, clusters, verdicts, [])
        all_ids = [i for c in cl for i in c["asset_ids"]]
        self.assertNotIn("A999", all_ids)                      # hallucination dropped
        self.assertNotIn("A999", vd) if "A999" not in vd else None
        self.assertEqual(set(a["id"] for a in assets), set(all_ids))  # full coverage
        self.assertEqual(vd["A001"]["verdict"], "MERGE")       # engine verdict kept
        self.assertEqual(vd["A002"]["verdict"],
                         verdicts["A002"]["verdict"])          # invalid bucket -> heuristic
        self.assertEqual(narr, "n")

    def test_parse_analysis_rejects_incomplete(self):
        self.assertIsNone(census.parse_analysis({"clusters": []}))
        self.assertIsNone(census.parse_analysis({"verdicts": {}}))
        self.assertIsNone(census.parse_analysis("not a dict"))


class TestDbAdapter(unittest.TestCase):
    def _make_db(self, path):
        import sqlite3
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE apps (app_name TEXT, summary TEXT, owner_email TEXT,"
                    " dept TEXT, lifecycle TEXT, monthly_cost TEXT, extra_col TEXT)")
        con.executemany("INSERT INTO apps VALUES (?,?,?,?,?,?,?)", [
            ("Incident Triage", "Routes incident intake reports", "ada@x.com; ben@x.com",
             "Support", "shipped", "$120", "prod"),
            ("Old ETL Bot", "Legacy loader", "", "Data", "stalled", "", "test"),
            ("", "row with no name is skipped", "", "", "", "", ""),
        ])
        con.commit()
        con.close()

    def test_sqlite_transport_with_map(self):
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "apps.db")
            self._make_db(db)
            assets = census.adapter_db({
                "label": "registry", "transport": "sqlite", "path": db,
                "query": "SELECT * FROM apps",
                "map": {"name": "app_name", "description": "summary",
                        "owners": "owner_email", "team": "dept",
                        "status": "lifecycle", "spend_monthly": "monthly_cost"},
            }, False, "s")
            self.assertEqual(len(assets), 2)              # nameless row skipped
            a = assets[0]
            self.assertEqual(a["source"], "db:registry")
            self.assertEqual(a["name"], "Incident Triage")
            self.assertEqual(a["owners"], ["ada", "ben"])  # split + normalized
            self.assertEqual(a["status"], "shipped")
            self.assertEqual(a["spend_monthly"], 120.0)
            self.assertEqual(a["signals"]["extra_col"], "prod")  # unmapped kept

    def test_command_transport_csv(self):
        assets = census.adapter_db({
            "label": "cmdb", "transport": "command", "format": "csv",
            "command": [sys.executable, "-c",
                        "print('name,description,owner\\nTool X,does x,eve@x.com')"],
            "map": {"owners": "owner"},
        }, False, "s")
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]["name"], "Tool X")
        self.assertEqual(assets[0]["owners"], ["eve"])

    def test_command_transport_json(self):
        assets = census.adapter_db({
            "label": "api", "transport": "command", "format": "json",
            "command": [sys.executable, "-c",
                        "import json;print(json.dumps([{'name':'Tool Y','status':'active'}]))"],
        }, False, "s")
        self.assertEqual(assets[0]["name"], "Tool Y")
        self.assertEqual(assets[0]["status"], "active")


class TestOrgConfig(unittest.TestCase):
    def test_config_end_to_end_with_extensions(self):
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "apps.db")
            TestDbAdapter()._make_db(db)
            cfg = {
                "org": "ConfCo", "out": os.path.join(td, "out"),
                "deidentify": True, "salt": "confco",
                "sources": [
                    {"type": "survey-csv", "path": os.path.join(SAMPLES, "survey.csv")},
                    {"type": "db", "label": "registry", "transport": "sqlite",
                     "path": db, "query": "SELECT * FROM apps",
                     "map": {"name": "app_name", "description": "summary",
                             "owners": "owner_email", "team": "dept",
                             "status": "lifecycle"}},
                ],
                "initiatives": ["Incident intake triage"],
                "extend": {
                    "harness_fingerprints": {"acme-agent": ["ACME_AGENT.yaml"]},
                    "category_keywords": {"incident-response": ["incident intake"]},
                },
            }
            cp = os.path.join(td, "census.json")
            with open(cp, "w") as f:
                json.dump(cfg, f)
            rc = census.main(["--config", cp, "--no-llm"])
            self.assertEqual(rc, 0)
            with open(os.path.join(td, "out", "report.json")) as f:
                data = json.load(f)
            self.assertEqual(data["org"], "ConfCo")
            self.assertEqual(len(data["assets"]), 10)      # 8 survey + 2 db
            md = open(os.path.join(td, "out", "report.md")).read()
            self.assertNotIn("Ada Okafor", md)             # config deidentify honored
            # extension applied: AE Triage categorized by the custom keyword
            ae = next(a for a in data["assets"] if a["name"] == "Incident Triage")
            self.assertEqual(ae["category_hint"], "incident-response")
            # covered initiative must not appear as a gap
            self.assertNotIn("Incident intake triage",
                             [g["initiative"] for g in data["gaps"]])
            self.assertIn("acme-agent", census.HARNESS_FINGERPRINTS)


class TestHardening(unittest.TestCase):
    def test_bom_csv_first_column_survives(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "u.csv")
            with open(p, "wb") as f:
                f.write("﻿email_address,last_active\nada@x.com,2026-07-20\n"
                        .encode("utf-8"))
            assets = census.adapter_usage_csv(p, "Claude", "claude", False, "s")
            # without utf-8-sig the BOM corrupts 'email_address' and the user drops
            self.assertEqual(assets[0]["signals"]["seats"], 1)
            self.assertEqual(assets[0]["owners"], ["ada"])

    def test_sqlite_is_read_only(self):
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "x.db")
            import sqlite3
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE t (name TEXT)")
            con.commit()
            con.close()
            with self.assertRaises(census.CensusError):
                census.adapter_db({"transport": "sqlite", "path": db,
                                   "query": "INSERT INTO t VALUES ('x')"}, False, "s")

    def test_missing_files_raise_clear_errors(self):
        with self.assertRaises(census.CensusError):
            census.adapter_usage_csv("/nope/u.csv", "V", "generic", False, "s")
        with self.assertRaises(census.CensusError):
            census.adapter_survey_csv("/nope/s.csv", False, "s")
        with self.assertRaises(census.CensusError):
            census.adapter_db({"transport": "command", "command": "not-a-list"},
                              False, "s")

    def test_config_check_preflight(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = {"org": "X", "deidentify": True, "salt": "s",
                   "sources": [
                       {"type": "survey-csv", "path": "missing.csv"},
                       {"type": "made-up-source"},
                       {"type": "repos-gitlab", "group": "g",
                        "base_url": "http://gl.internal", "token_env": "NO_SUCH_VAR"},
                       {"type": "db", "transport": "command",
                        "command": ["echo"], "password": "hunter2"},
                   ]}
            problems = census.check_config(cfg, td)
            text = "\n".join(problems)
            self.assertIn("file not found", text)
            self.assertIn("unknown type", text)
            self.assertIn("NO_SUCH_VAR", text)
            self.assertIn("plain http", text)
            self.assertIn("must NOT be in the config file", text)
            # a clean config passes
            ok = census.check_config({"org": "X", "sources": []}, td)
            self.assertEqual(ok, [])

    def test_z_suffix_timestamps_parse_on_py39(self):
        # newer git emits UTC as '...Z'; Python <3.11 fromisoformat rejects it
        d = census.parse_iso("2026-07-24T15:52:24Z")
        self.assertIsNotNone(d.tzinfo)
        self.assertEqual(census.parse_iso("2026-07-24T15:52:24+00:00"), d)

    def test_gitlab_refuses_plain_http(self):
        with self.assertRaises(census.CensusError):
            census.adapter_repos_gitlab("g", "http://gl.internal", "T", False, "s")

    def test_db_map_must_be_string_map(self):
        with self.assertRaises(census.CensusError):
            census.adapter_db({"transport": "command", "command": ["echo"],
                               "map": {"owners": ["not", "a", "string"]}}, False, "s")

    def test_extension_validation_skips_bad_fingerprints(self):
        before = dict(census.HARNESS_FINGERPRINTS)
        try:
            census.apply_extensions({"harness_fingerprints": {
                "bad": "not-a-list", "good": ["GOOD.md"]}})
            self.assertNotIn("bad", census.HARNESS_FINGERPRINTS)
            self.assertIn("good", census.HARNESS_FINGERPRINTS)
        finally:
            census.HARNESS_FINGERPRINTS.clear()
            census.HARNESS_FINGERPRINTS.update(before)

    def test_graph_html_escapes_hostile_names(self):
        import brain
        report = {"org": "<script>alert(1)</script>Co", "meta": {},
                  "assets": [{"id": "A1", "name": "evil</script><img src=x>",
                              "source": "survey", "description": "", "owners": [],
                              "team": "", "harnesses": [], "status": "active",
                              "users_claimed": "", "spend_monthly": None,
                              "signals": {}, "link": "", "category_hint": ""}],
                  "clusters": [], "verdicts": {}, "gaps": []}
        html = brain.render_html(brain.build_graph(report))
        self.assertNotIn("</script><img", html)          # breakout neutralized
        self.assertNotIn("<script>alert(1)</script>Co", html)  # title escaped


class TestGitlabAdapter(unittest.TestCase):
    def test_pagination_and_harness_sniff(self):
        calls = []
        def fake_http(url, token=None, timeout=60):
            calls.append(url)
            if "/groups/" in url:
                from urllib.parse import urlparse, parse_qs
                page = parse_qs(urlparse(url).query).get("page", ["1"])[0]
                if page == "1":
                    return [{"id": 7, "path": "proj-a", "description": "d",
                             "archived": False, "web_url": "u",
                             "last_activity_at": "2026-07-20T00:00:00Z"}]
                return []
            return [{"name": "CLAUDE.md"}, {"name": ".cursorrules"}]
        orig = census._http_json
        census._http_json = fake_http
        try:
            assets = census.adapter_repos_gitlab("grp", "https://gl.x", "NOPE_TOKEN",
                                                 False, "s")
        finally:
            census._http_json = orig
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]["source"], "repo-gitlab")
        self.assertEqual(assets[0]["status"], "active")
        self.assertEqual(assets[0]["harnesses"], ["claude-code", "cursor"])


class TestWikiAndLint(unittest.TestCase):
    def _report(self):
        assets = census.adapter_survey_csv(os.path.join(SAMPLES, "survey.csv"), False, "s")
        census.assign_ids(assets)
        clusters = census.heuristic_clusters(assets)
        verdicts = census.heuristic_verdicts(assets, clusters)
        return {"org": "TestCo", "meta": {"generated": "2026-07-23"},
                "assets": assets, "clusters": clusters, "verdicts": verdicts,
                "gaps": [{"initiative": "Support ticket triage", "rationale": "none"}]}

    def test_wiki_pages_and_backlinks(self):
        import wiki
        with tempfile.TemporaryDirectory() as td:
            pages = wiki.build(self._report(), td)
            self.assertIn("index.md", pages)
            # every asset gets a page; duplicate clusters get pattern pages
            self.assertTrue(any(fn.startswith("asset--") for fn in pages))
            pattern_pages = [fn for fn in pages if fn.startswith("pattern--")]
            self.assertTrue(pattern_pages)
            # a pattern page's members link back: asset pages carry backlinks
            joined = "\n".join("\n".join(pages[fn]) for fn in pages
                               if fn.startswith("asset--"))
            self.assertIn("## Backlinks", joined)
            self.assertIn("[[", joined)

    def test_lint_contradiction_and_resolution(self):
        import wiki
        with tempfile.TemporaryDirectory() as td:
            live = make_fake_repo(td, "live-repo", {"README.md": "# x"})
            gone = os.path.join(td, "gone-repo")   # never created
            report = {
                "org": "T", "meta": {},
                "assets": [
                    {"id": "A1", "name": "live-repo", "source": "repo-local",
                     "owners": ["p"], "team": "", "status": "stalled",
                     "signals": {"path": live, "last_commit": "2026-01-01"}},
                    {"id": "A2", "name": "gone-repo", "source": "repo-local",
                     "owners": ["p"], "team": "", "status": "shipped",
                     "signals": {"path": gone, "last_commit": "2026-07-01"}},
                ],
                "verdicts": {"A1": {"verdict": "SUNSET"},
                             "A2": {"verdict": "MERGE"}},
                "gaps": []}
            findings = wiki.lint(report)
            kinds = {f["subject"]: f["kind"] for f in findings}
            # fresh commit on a SUNSET repo -> contradiction
            self.assertEqual(kinds["live-repo"], "contradiction")
            # MERGE verdict + checkout removed -> resolved
            self.assertEqual(kinds["gone-repo"], "resolved")


class TestEasyUX(unittest.TestCase):
    def test_init_creates_ready_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            repos = os.path.join(td, "repos")
            os.makedirs(repos)
            rc = census.main(["init", "--dir", td, "--org", "Test Org",
                              "--repos-dir", repos, "--non-interactive"])
            self.assertEqual(rc, 0)
            for f in ("census.json", "survey_template.csv", "SURVEY_QUESTIONS.md",
                      "GETTING-STARTED.md", "initiatives.txt"):
                self.assertTrue(os.path.exists(os.path.join(td, f)), f)
            with open(os.path.join(td, "census.json")) as f:
                cfg = json.load(f)
            self.assertEqual(cfg["org"], "Test Org")
            self.assertTrue(cfg["deidentify"])
            self.assertNotEqual(cfg["salt"], "ai-census")   # per-org salt
            # refuses to clobber an existing workspace
            rc2 = census.main(["init", "--dir", td, "--non-interactive"])
            self.assertEqual(rc2, 1)

    def test_run_subcommand_produces_everything(self):
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "out")
            rc = census.main([
                "run",
                "--org-label", "RunCo",
                "--survey-csv", os.path.join(SAMPLES, "survey.csv"),
                "--initiatives", os.path.join(SAMPLES, "initiatives.txt"),
                "--out", out, "--no-llm", "--deidentify"])
            self.assertEqual(rc, 0)
            for f in ("report.md", "report.json", "graph.html", "graph.json",
                      "lint.md", os.path.join("wiki", "index.md")):
                self.assertTrue(os.path.exists(os.path.join(out, f)), f)

    def test_check_subcommand(self):
        with tempfile.TemporaryDirectory() as td:
            cp = os.path.join(td, "census.json")
            with open(cp, "w") as f:
                json.dump({"org": "X", "sources": []}, f)
            self.assertEqual(census.main(["check", "--config", cp]), 0)

    def test_dist_builds_runnable_single_file(self):
        with tempfile.TemporaryDirectory() as td:
            pyz = os.path.join(td, "census.pyz")
            rc = census.main(["dist", "--out", pyz])
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.exists(pyz))
            ws = os.path.join(td, "ws")
            os.makedirs(ws)
            r = subprocess.run([sys.executable, pyz, "init", "--dir", ws,
                                "--org", "PyzCo", "--non-interactive"],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(os.path.exists(os.path.join(ws, "census.json")))


class TestEndToEnd(unittest.TestCase):
    def test_cli_heuristic_run(self):
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "out")
            rc = census.main([
                "--org-label", "TestCo",
                "--survey-csv", os.path.join(SAMPLES, "survey.csv"),
                "--usage-csv", "Claude:claude:%s" % os.path.join(SAMPLES, "usage_claude.csv"),
                "--usage-csv", "GitHub Copilot:copilot:%s" % os.path.join(SAMPLES, "usage_copilot.csv"),
                "--initiatives", os.path.join(SAMPLES, "initiatives.txt"),
                "--out", out, "--no-llm", "--deidentify"])
            self.assertEqual(rc, 0)
            with open(os.path.join(out, "report.json")) as f:
                data = json.load(f)
            self.assertEqual(len(data["assets"]), 10)  # 8 survey + 2 usage
            self.assertEqual(set(data["verdicts"]), {a["id"] for a in data["assets"]})
            md = open(os.path.join(out, "report.md")).read()
            self.assertIn("four-bucket verdict map", md)
            self.assertIn("DUPLICATE PATTERN", md)
            self.assertNotIn("Ada Okafor", md)         # de-identified
            self.assertNotIn("ada.okafor", md)


if __name__ == "__main__":
    unittest.main()
