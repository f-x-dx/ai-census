#!/usr/bin/env python3
"""
AI Census — second-brain graph layer.

Turns a census report.json into a knowledge graph: how the org's assets,
people, teams, harnesses, clusters, initiatives, and notes actually connect.
Emits graph.json (machine) and graph.html (self-contained interactive viewer,
zero external dependencies — safe inside your own walls).

Node types: asset · person · team · harness · cluster · initiative
Edge types: member_of · built_by · owned_by · built_with · covers · gap ·
            links_to (explicit note links) · similar_to (content overlap)

Usage:
  python3 brain.py out/report.json --out out/
  # or via census.py --graph
"""

import argparse
import json
import os
import re
import sys
from collections import Counter

TOKEN_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = set("the a an and or for of to in on with using use built by our my is are "
                "app tool internal ai llm gpt claude copilot this that from it as be "
                "was were has have will can".split())

SIMILARITY_THRESHOLD = 0.18   # token-Jaccard floor for similar_to edges
MAX_SIM_EDGES_PER_NODE = 3    # keep the graph readable, not hairball


def stem(t):
    return t[:6] if len(t) > 6 else t


def tokens(text):
    return {stem(t) for t in TOKEN_RE.findall(text.lower())
            if len(t) > 2 and t not in STOPWORDS}


def build_graph(report):
    nodes, edges = {}, []

    def add_node(nid, ntype, label, **props):
        if nid not in nodes:
            nodes[nid] = {"id": nid, "type": ntype, "label": label, **props}
        return nid

    def add_edge(src, dst, etype, **props):
        edges.append({"source": src, "target": dst, "type": etype, **props})

    assets = report.get("assets", [])
    verdicts = report.get("verdicts", {})

    for a in assets:
        v = verdicts.get(a["id"], {})
        add_node(a["id"], "asset", a["name"],
                 source=a["source"], status=a["status"],
                 verdict=v.get("verdict", "?"),
                 rationale=v.get("rationale", ""))
        for h in a.get("harnesses", []):
            add_node("harness:%s" % h, "harness", h)
            add_edge(a["id"], "harness:%s" % h, "built_with")
        for o in a.get("owners", []):
            add_node("person:%s" % o, "person", o)
            add_edge("person:%s" % o, a["id"], "built_by")
        if a.get("team"):
            add_node("team:%s" % a["team"], "team", a["team"])
            add_edge(a["id"], "team:%s" % a["team"], "owned_by")

    for cl in report.get("clusters", []):
        # singleton clusters add hub-noise, not connection insight
        if len(cl.get("asset_ids", [])) < 2:
            continue
        cid = "cluster:%s" % cl["id"]
        add_node(cid, "cluster", cl["name"],
                 duplicate=bool(cl.get("duplicate_pattern")),
                 rationale=cl.get("rationale", ""))
        for aid in cl["asset_ids"]:
            if aid in nodes:
                add_edge(aid, cid, "member_of")

    by_name = {a["name"].strip().lower(): a["id"] for a in assets}
    gap_initiatives = {g["initiative"] for g in report.get("gaps", [])}

    # explicit note links (docs adapter emits signals.links)
    for a in assets:
        for target in a.get("signals", {}).get("links", []):
            tid = by_name.get(target)
            if tid and tid != a["id"]:
                add_edge(a["id"], tid, "links_to")

    # initiatives: covered or gap
    for g in report.get("gaps", []):
        iid = "initiative:%s" % g["initiative"]
        add_node(iid, "initiative", g["initiative"], gap=True,
                 rationale=g.get("rationale", ""))

    # content-similarity edges: the "how do these notes/builds actually
    # relate" layer — top-K per node above a Jaccard floor, cross-source only
    # (same-source similarity is usually just the cluster again)
    toks = {a["id"]: tokens("%s %s" % (a["name"], a["description"])) for a in assets}
    candidates = []
    ids = [a["id"] for a in assets]
    src = {a["id"]: a["source"] for a in assets}
    for i, x in enumerate(ids):
        for y in ids[i + 1:]:
            if src[x] == src[y] and src[x] != "docs":
                continue
            inter = len(toks[x] & toks[y])
            if inter < 2:
                continue
            sim = inter / (len(toks[x] | toks[y]) or 1)
            if sim >= SIMILARITY_THRESHOLD:
                candidates.append((sim, x, y))
    per_node = Counter()
    for sim, x, y in sorted(candidates, reverse=True):
        if per_node[x] >= MAX_SIM_EDGES_PER_NODE or per_node[y] >= MAX_SIM_EDGES_PER_NODE:
            continue
        add_edge(x, y, "similar_to", weight=round(sim, 3))
        per_node[x] += 1
        per_node[y] += 1

    # prune isolated harness/team/person hubs with a single connection? no —
    # single links are real facts; keep everything and let the viewer filter.
    return {"meta": {"org": report.get("org", ""),
                     "generated": report.get("meta", {}).get("generated", ""),
                     "counts": dict(Counter(n["type"] for n in nodes.values()))},
            "nodes": list(nodes.values()), "edges": edges}


HTML_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<title>AI Census — Second Brain</title>
<style>
  html,body{margin:0;height:100%;background:#0b0d12;color:#dfe3ea;
    font:13px/1.45 -apple-system,'Segoe UI',sans-serif;overflow:hidden}
  #c{position:absolute;inset:0}
  #hud{position:absolute;top:14px;left:14px;background:#12151dee;border:1px solid #232838;
    border-radius:10px;padding:12px 14px;max-width:300px}
  #hud h1{font-size:14px;margin:0 0 2px;color:#fff}
  #hud .sub{color:#8b93a7;font-size:11px;margin-bottom:8px}
  .leg{display:flex;align-items:center;gap:7px;margin:3px 0;cursor:pointer;user-select:none}
  .leg.off{opacity:.3}
  .dot{width:10px;height:10px;border-radius:50%}
  #info{position:absolute;bottom:14px;left:14px;right:14px;max-width:520px;
    background:#12151dee;border:1px solid #232838;border-radius:10px;
    padding:10px 14px;display:none}
  #info b{color:#fff}
  #info .k{color:#8b93a7}
  #search{width:100%;box-sizing:border-box;margin-top:8px;background:#0b0d12;
    border:1px solid #232838;border-radius:6px;color:#dfe3ea;padding:5px 8px;font-size:12px}
</style></head><body>
<canvas id="c"></canvas>
<div id="hud">
  <h1>Second Brain — __ORG__</h1>
  <div class="sub">__NN__ nodes · __NE__ edges · click legend to filter · drag nodes · scroll to zoom</div>
  <div id="legend"></div>
  <input id="search" placeholder="find a node…">
</div>
<div id="info"></div>
<script>
const DATA = __DATA__;
const COLORS = {asset:"#6ea8fe", person:"#f5c14e", team:"#e07be0",
                harness:"#57e0b2", cluster:"#ff7a6e", initiative:"#b39dfb"};
const VERDICT_RING = {SCALE:"#57e0b2", MERGE:"#f5c14e", SUNSET:"#ff7a6e", BUILD:"#b39dfb"};
const EDGE_STYLE = {similar_to:"#3a4258", links_to:"#6ea8fe", member_of:"#2b3242",
                    built_with:"#234238", built_by:"#4a4030", owned_by:"#43304a",
                    covers:"#3d3560", gap:"#5a3050"};
const cv = document.getElementById("c"), cx = cv.getContext("2d");
let W, H, dpr = window.devicePixelRatio || 1;
function resize(){W=innerWidth;H=innerHeight;cv.width=W*dpr;cv.height=H*dpr;
  cv.style.width=W+"px";cv.style.height=H+"px";cx.setTransform(dpr,0,0,dpr,0,0);}
resize(); addEventListener("resize", resize);

const nodes = DATA.nodes.map(n => ({...n,
  x: Math.random()*800-400, y: Math.random()*800-400, vx:0, vy:0,
  deg:0, r:5}));
const byId = Object.fromEntries(nodes.map(n=>[n.id,n]));
const edges = DATA.edges.filter(e=>byId[e.source]&&byId[e.target])
  .map(e=>({...e, s:byId[e.source], t:byId[e.target]}));
edges.forEach(e=>{e.s.deg++; e.t.deg++;});
nodes.forEach(n=>{n.r = 4 + Math.min(10, Math.sqrt(n.deg)*2);});

const hidden = new Set();
const legend = document.getElementById("legend");
Object.entries(COLORS).forEach(([t,c])=>{
  const count = nodes.filter(n=>n.type===t).length;
  if(!count) return;
  const div = document.createElement("div");
  div.className="leg";
  div.innerHTML = `<span class="dot" style="background:${c}"></span>${t} (${count})`;
  div.onclick = ()=>{ hidden.has(t)?hidden.delete(t):hidden.add(t);
    div.classList.toggle("off"); };
  legend.appendChild(div);
});

let zoom=1, panX=0, panY=0, drag=null, panning=false, px=0, py=0, hover=null, pinned=null;
function toWorld(mx,my){return [(mx-W/2-panX)/zoom, (my-H/2-panY)/zoom];}
cv.addEventListener("wheel", e=>{e.preventDefault();
  zoom = Math.max(.15, Math.min(4, zoom*(e.deltaY<0?1.1:0.9)));},{passive:false});
cv.addEventListener("mousedown", e=>{
  const [wx,wy]=toWorld(e.clientX,e.clientY);
  drag = visible().find(n=>((n.x-wx)**2+(n.y-wy)**2) < (n.r+4)**2/zoom);
  if(!drag){panning=true;} px=e.clientX; py=e.clientY;
  if(drag) pinned = drag;
  showInfo(drag);
});
addEventListener("mousemove", e=>{
  if(drag){const [wx,wy]=toWorld(e.clientX,e.clientY); drag.x=wx; drag.y=wy; drag.vx=drag.vy=0;}
  else if(panning){panX+=e.clientX-px; panY+=e.clientY-py; px=e.clientX; py=e.clientY;}
  else {const [wx,wy]=toWorld(e.clientX,e.clientY);
    hover = visible().find(n=>((n.x-wx)**2+(n.y-wy)**2) < (n.r+4)**2/zoom) || null;
    cv.style.cursor = hover ? "pointer" : "default";}
});
addEventListener("mouseup", ()=>{drag=null; panning=false;});
document.getElementById("search").addEventListener("input", e=>{
  const q = e.target.value.toLowerCase();
  if(!q){pinned=null; showInfo(null); return;}
  const hit = nodes.find(n=>n.label.toLowerCase().includes(q));
  if(hit){pinned=hit; panX = -hit.x*zoom; panY = -hit.y*zoom; showInfo(hit);}
});

function visible(){return nodes.filter(n=>!hidden.has(n.type));}
function esc(s){const d=document.createElement("span");d.textContent=String(s);return d.innerHTML;}
function showInfo(n){
  const el = document.getElementById("info");
  if(!n){el.style.display="none"; return;}
  // node labels/rationales come from scanned repos and surveys — untrusted;
  // escape everything interpolated into markup
  let html = `<b>${esc(n.label)}</b> <span class="k">· ${esc(n.type)}</span>`;
  if(n.verdict && n.verdict!=="?") html += ` · <span style="color:${VERDICT_RING[n.verdict]||'#fff'}">${esc(n.verdict)}</span>`;
  if(n.status) html += ` <span class="k">· ${esc(n.status)}</span>`;
  if(n.rationale) html += `<br><span class="k">${esc(n.rationale)}</span>`;
  const nb = edges.filter(e=>e.s===n||e.t===n).length;
  html += `<br><span class="k">${nb} connection${nb===1?"":"s"}</span>`;
  el.innerHTML = html; el.style.display="block";
}

function step(){
  const vis = visible(), visSet = new Set(vis);
  for(const n of vis){
    // centering gravity
    n.vx -= n.x*0.0006; n.vy -= n.y*0.0006;
    // repulsion (grid-free O(n^2) — fine to ~1500 nodes)
    for(const m of vis){ if(m===n) continue;
      let dx=n.x-m.x, dy=n.y-m.y, d2=dx*dx+dy*dy+0.01;
      if(d2<40000){const f=900/d2; n.vx+=dx*f*0.016; n.vy+=dy*f*0.016;}
    }
  }
  for(const e of edges){
    if(!visSet.has(e.s)||!visSet.has(e.t)) continue;
    let dx=e.t.x-e.s.x, dy=e.t.y-e.s.y;
    const d=Math.sqrt(dx*dx+dy*dy)+0.01, want=e.type==="member_of"?70:110;
    const f=(d-want)*0.004;
    e.s.vx+=dx/d*f; e.s.vy+=dy/d*f; e.t.vx-=dx/d*f; e.t.vy-=dy/d*f;
  }
  for(const n of vis){ n.vx*=0.85; n.vy*=0.85; n.x+=n.vx; n.y+=n.vy; }
}

function draw(){
  cx.clearRect(0,0,W,H);
  cx.save(); cx.translate(W/2+panX, H/2+panY); cx.scale(zoom,zoom);
  const visSet = new Set(visible());
  const focus = pinned || hover;
  const neigh = new Set();
  if(focus) edges.forEach(e=>{if(e.s===focus)neigh.add(e.t); if(e.t===focus)neigh.add(e.s);});
  for(const e of edges){
    if(!visSet.has(e.s)||!visSet.has(e.t)) continue;
    const lit = focus && (e.s===focus||e.t===focus);
    cx.strokeStyle = EDGE_STYLE[e.type]||"#2b3242";
    cx.globalAlpha = focus ? (lit?0.9:0.06) : (e.type==="similar_to"?0.35:0.55);
    cx.lineWidth = (e.type==="similar_to" ? 0.7 : 1.1)/zoom + (lit?0.8:0);
    cx.beginPath(); cx.moveTo(e.s.x,e.s.y); cx.lineTo(e.t.x,e.t.y); cx.stroke();
  }
  cx.globalAlpha = 1;
  for(const n of visible()){
    const dim = focus && n!==focus && !neigh.has(n);
    cx.globalAlpha = dim?0.15:1;
    cx.fillStyle = COLORS[n.type]||"#999";
    cx.beginPath(); cx.arc(n.x,n.y,n.r,0,7); cx.fill();
    if(n.type==="asset" && VERDICT_RING[n.verdict]){
      cx.strokeStyle = VERDICT_RING[n.verdict]; cx.lineWidth=2/zoom;
      cx.beginPath(); cx.arc(n.x,n.y,n.r+2.5,0,7); cx.stroke();
    }
    if(n.type==="cluster" && n.duplicate){
      cx.strokeStyle="#ff7a6e"; cx.lineWidth=1.2/zoom; cx.setLineDash([3,3]);
      cx.beginPath(); cx.arc(n.x,n.y,n.r+5,0,7); cx.stroke(); cx.setLineDash([]);
    }
    if(zoom>0.55 || n.deg>4 || n===focus || neigh.has(n)){
      cx.fillStyle = dim?"#5a6274":"#c9cfdc";
      cx.font = `${11/zoom}px -apple-system,sans-serif`;
      cx.fillText(n.label.slice(0,34), n.x+n.r+4, n.y+3);
    }
  }
  cx.restore();
}
(function loop(){ step(); draw(); requestAnimationFrame(loop); })();
</script></body></html>
"""


def render_html(graph):
    import html as html_mod
    counts = graph["meta"]["counts"]
    html = HTML_TEMPLATE
    html = html.replace("__ORG__", html_mod.escape(graph["meta"].get("org") or "org"))
    html = html.replace("__NN__", str(sum(counts.values())))
    html = html.replace("__NE__", str(len(graph["edges"])))
    # "</" -> "<\/" so a "</script>" inside a scanned repo name or README
    # cannot break out of the inline data block
    data = json.dumps(graph, default=str).replace("</", "<\\/").replace("<!--", "<\\!--")
    return html.replace("__DATA__", data)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="brain",
                                 description="Second-brain graph from a census report.json")
    ap.add_argument("report", help="path to report.json")
    ap.add_argument("--out", default="out", help="output directory")
    args = ap.parse_args(argv)
    try:
        with open(args.report) as f:
            report = json.load(f)
    except (OSError, ValueError) as e:
        print("[brain] error: cannot read report: %s" % e, file=sys.stderr)
        return 1
    if not isinstance(report, dict) or "assets" not in report:
        print("[brain] error: %s is not a census report.json" % args.report,
              file=sys.stderr)
        return 1
    graph = build_graph(report)
    if len(graph["nodes"]) > 2000:
        print("[brain] warning: %d nodes — the viewer's physics is O(n^2); "
              "expect it to be slow above ~2000" % len(graph["nodes"]),
              file=sys.stderr)
    os.makedirs(args.out, exist_ok=True)
    gp = os.path.join(args.out, "graph.json")
    hp = os.path.join(args.out, "graph.html")
    with open(gp, "w") as f:
        json.dump(graph, f, indent=2, default=str)
    with open(hp, "w") as f:
        f.write(render_html(graph))
    c = graph["meta"]["counts"]
    print("[brain] %s nodes (%s), %d edges -> %s" %
          (sum(c.values()), ", ".join("%d %s" % (n, t) for t, n in sorted(c.items())),
           len(graph["edges"]), hp), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
