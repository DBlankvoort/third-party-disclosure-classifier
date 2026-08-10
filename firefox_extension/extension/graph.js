"use strict";

const BRIDGE = "http://127.0.0.1:8765";
const POLL_MS = 1200;

const NODE_COLOR = {
  target: "#6ea8fe",
  entity: "#9ec5ff",
  generic: "#d8b4fe",
  domain: "#5b6478",
};
const UNGROUNDED_COLOR = "#7f8bab";
const SOURCE_COLOR = {
  policy: "#6ee7b7",
  registry: "#f0b35b",
  traffic: "#f0abfc",
  resolution: "#5b6478",
};
const KIND_LABEL = {
  discloses_sharing_with: "discloses sharing with",
  supplies: "supplies data to",
  contacts: "contacts",
  owned_by: "operated by",
};
const TERMINATION_LABEL = {
  internal: "shares onward",
  terminal: "analysed",
  unexpanded: "not analysed",
};
const TRACK_LABEL = {
  personal_data: "personal data",
  inventory: "ad inventory",
};
const SUBJECT_LABEL = {
  site_visitor: "this party's own visitors",
  service_data: "data received from its customers",
  not_applicable: "no personal data",
  unknown: "population not stated",
};

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------- state ---
const state = {
  origin: "",
  tabId: null,
  hops: 1,
  jobId: null,
  polling: null,
  running: false,
  graph: { nodes: [], edges: [] },
  byId: new Map(),
  layout: new Map(),      // node id -> {x, y, r, hop}
  visible: new Set(),
  outgoing: new Map(),    // node id -> edges
  incoming: new Map(),
  selected: null,
  hovered: null,
  matches: new Set(),
  view: { x: 0, y: 0, k: 1 },
  track: "personal_data",
  filters: {
    policy: true, registry: true, traffic: true, generic: true, domains: false,
    ungrounded: true,
  },
  editing: false,
  mergeFrom: null,
};

// ---------------------------------------------------------------- model ---
function indexGraph(graph) {
  state.graph = graph;
  state.byId = new Map(graph.nodes.map((n) => [n.id, n]));
  state.outgoing = new Map();
  state.incoming = new Map();
  for (const e of graph.edges) {
    if (!state.outgoing.has(e.src)) state.outgoing.set(e.src, []);
    state.outgoing.get(e.src).push(e);
    if (!state.incoming.has(e.dst)) state.incoming.set(e.dst, []);
    state.incoming.get(e.dst).push(e);
  }
}

function edgeEvidence(edge) {
  const evidence = edge.evidence || [];
  if (state.track === "both") return evidence;
  return evidence.filter((ev) => (ev.track || "personal_data") === state.track);
}

function edgeSources(edge) {
  return new Set(edgeEvidence(edge).map((ev) => ev.source));
}

function edgeTracks(edge) {
  return new Set((edge.evidence || []).map((ev) => ev.track || "personal_data"));
}

function edgeSubjects(edge) {
  return new Set(edgeEvidence(edge).map((ev) => ev.subject).filter(Boolean));
}

function edgePasses(edge) {
  const f = state.filters;
  const evidence = edgeEvidence(edge);
  if (!evidence.length) return false;
  const sources = new Set(evidence.map((ev) => ev.source));
  if (sources.size === 1 && sources.has("resolution")) return true;
  for (const s of sources) {
    if (s === "policy" && f.policy) return true;
    if (s === "registry" && f.registry) return true;
    if (s === "traffic" && f.traffic) return true;
  }
  return false;
}

function nodePasses(node) {
  if (node.type === "generic" && !state.filters.generic) return false;
  if (node.type === "domain" && !state.filters.domains) return false;
  if (node.type === "entity" && node.grounded === false
      && !state.filters.ungrounded) return false;
  return true;
}

function computeVisible() {
  const visible = new Set();
  for (const n of state.graph.nodes) {
    if (!nodePasses(n)) continue;
    if (n.hop_first_seen === 0) { visible.add(n.id); continue; }
    const edges = [...(state.incoming.get(n.id) || []), ...(state.outgoing.get(n.id) || [])];
    if (edges.some(edgePasses)) visible.add(n.id);
  }
  state.visible = visible;
}

function terminationOf(nodeId) {
  const out = (state.outgoing.get(nodeId) || []).filter(
    (e) => state.visible.has(e.dst) && edgePasses(e),
  );
  if (out.length) return "internal";
  const node = state.byId.get(nodeId);
  return node && node.expanded ? "terminal" : "unexpanded";
}

// --------------------------------------------------------------- layout ---
// Parties are placed on a ring per hop and given an angular wedge inherited
// from the party that led to them.
function layout() {
  const nodes = state.graph.nodes.filter((n) => state.visible.has(n.id));
  state.layout = new Map();
  if (!nodes.length) return;

  const adjacency = new Map();
  const add = (a, b) => {
    if (!adjacency.has(a)) adjacency.set(a, new Set());
    adjacency.get(a).add(b);
  };
  for (const e of state.graph.edges) {
    if (!state.visible.has(e.src) || !state.visible.has(e.dst)) continue;
    if (!edgePasses(e)) continue;
    add(e.src, e.dst);
    add(e.dst, e.src);
  }

  const seed = nodes.find((n) => n.hop_first_seen === 0) || nodes[0];
  const children = new Map();
  const parent = new Map();
  const order = [seed.id];
  const seen = new Set([seed.id]);
  for (let i = 0; i < order.length; i++) {
    const id = order[i];
    for (const other of adjacency.get(id) || []) {
      if (seen.has(other)) continue;
      seen.add(other);
      parent.set(other, id);
      if (!children.has(id)) children.set(id, []);
      children.get(id).push(other);
      order.push(other);
    }
  }
  // Parties in no connected position still belong on the canvas.
  const orphans = nodes.filter((n) => !seen.has(n.id)).map((n) => n.id);
  if (orphans.length) {
    children.set(seed.id, (children.get(seed.id) || []).concat(orphans));
    for (const id of orphans) { parent.set(id, seed.id); order.push(id); }
  }

  const depth = new Map([[seed.id, 0]]);
  const perDepth = new Map();
  for (const id of order) {
    const d = id === seed.id ? 0 : (depth.get(parent.get(id)) || 0) + 1;
    depth.set(id, d);
    perDepth.set(d, (perDepth.get(d) || 0) + 1);
  }
  state.tree = { parent, children, depth };

  const ARC = 16;
  const maxDepth = Math.max(...depth.values());
  const radii = [0];
  for (let d = 1; d <= maxDepth; d++) {
    const needed = ((perDepth.get(d) || 1) * ARC) / (Math.PI * 2);
    radii[d] = Math.max(radii[d - 1] + 170, needed);
  }
  for (let d = 1; d < maxDepth; d++) {
    const floor = radii[maxDepth] * (0.45 + (0.55 * (d - 1)) / Math.max(1, maxDepth - 1));
    radii[d] = Math.max(radii[d], floor);
  }

  // Each ring divides the full circle equally between the parties on it.
  const angleOf = new Map([[seed.id, 0]]);
  state.layout.set(seed.id, { x: 0, y: 0, hop: 0, a0: 0, a1: Math.PI * 2 });
  const byDepth = new Map();
  for (const id of order) {
    const d = depth.get(id);
    if (!d) continue;
    if (!byDepth.has(d)) byDepth.set(d, []);
    byDepth.get(d).push(id);
  }
  for (let d = 1; d <= maxDepth; d++) {
    const ring = byDepth.get(d) || [];
    // Ordering by the angle of the party that led here keeps a branch's
    // recipients side by side.
    ring.sort((a, b) => {
      const pa = angleOf.get(parent.get(a)) ?? 0;
      const pb = angleOf.get(parent.get(b)) ?? 0;
      if (pa !== pb) return pa - pb;
      const na = state.byId.get(a), nb = state.byId.get(b);
      return (na.display_name || a).localeCompare(nb.display_name || b);
    });
    const step = (Math.PI * 2) / ring.length;
    ring.forEach((id, i) => {
      const angle = i * step;
      angleOf.set(id, angle);
      state.layout.set(id, {
        x: Math.cos(angle) * radii[d],
        y: Math.sin(angle) * radii[d],
        hop: d, a0: angle - step / 2, a1: angle + step / 2,
      });
    });
  }
}

function isTreeEdge(edge) {
  const tree = state.tree;
  if (!tree) return false;
  return tree.parent.get(edge.dst) === edge.src || tree.parent.get(edge.src) === edge.dst;
}

function nodeRadius(id) {
  const node = state.byId.get(id);
  if (!node) return 3;
  if (node.hop_first_seen === 0) return 9;
  if (node.type === "domain") return 2.5;
  const out = (state.outgoing.get(id) || []).length;
  return Math.min(7, 3.4 + Math.sqrt(out) * 0.7);
}

// ---------------------------------------------------------------- canvas ---
const canvas = $("canvas");
const ctx = canvas.getContext("2d");
let dpr = window.devicePixelRatio || 1;

function resize() {
  dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.round(rect.width * dpr);
  canvas.height = Math.round(rect.height * dpr);
  draw();
}

function worldToScreen(p) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: rect.width / 2 + (p.x + state.view.x) * state.view.k,
    y: rect.height / 2 + (p.y + state.view.y) * state.view.k,
  };
}

function screenToWorld(sx, sy) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: (sx - rect.width / 2) / state.view.k - state.view.x,
    y: (sy - rect.height / 2) / state.view.k - state.view.y,
  };
}

function fit() {
  const rect = canvas.getBoundingClientRect();
  let maxR = 1;
  for (const p of state.layout.values()) {
    maxR = Math.max(maxR, Math.hypot(p.x, p.y));
  }
  state.view.x = 0;
  state.view.y = 0;
  state.view.k = Math.min(rect.width, rect.height) / (2 * maxR + 90);
  draw();
}

function highlightSet() {
  const focus = state.hovered || state.selected;
  if (!focus) return null;
  const set = new Set([focus]);
  for (const e of state.outgoing.get(focus) || []) set.add(e.dst);
  for (const e of state.incoming.get(focus) || []) set.add(e.src);
  return set;
}

function draw() {
  const rect = canvas.getBoundingClientRect();
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, rect.width, rect.height);
  if (!state.layout.size) return;

  const focus = highlightSet();
  const k = state.view.k;

  // Hop rings.
  const centre = worldToScreen({ x: 0, y: 0 });
  const hops = new Set([...state.layout.values()].map((p) => p.hop));
  ctx.strokeStyle = "rgba(255,255,255,.055)";
  ctx.lineWidth = 1;
  const ringRadius = new Map();
  for (const p of state.layout.values()) {
    if (p.hop) ringRadius.set(p.hop, Math.hypot(p.x, p.y));
  }
  for (const hop of hops) {
    if (!hop) continue;
    ctx.beginPath();
    ctx.arc(centre.x, centre.y, ringRadius.get(hop) * k, 0, Math.PI * 2);
    ctx.stroke();
  }

  // Edges
  const drawn = state.graph.edges.filter(
    (e) => state.layout.has(e.src) && state.layout.has(e.dst) && edgePasses(e),
  );
  const showCross = drawn.length < 500 || k > 0.55;
  const baseAlpha = Math.max(0.05, Math.min(0.34, 260 / drawn.length));
  // A selection's lines thin out as they multiply.
  const focusCount = focus
    ? drawn.filter((e) => focus.has(e.src) && focus.has(e.dst)).length : 0;
  const focusAlpha = Math.max(0.1, Math.min(0.95, 150 / Math.max(1, focusCount)));
  ctx.lineWidth = Math.max(0.4, Math.min(1.5, k * 1.6));
  for (const e of drawn) {
    const tree = isTreeEdge(e);
    const touchesFocus = focus && focus.has(e.src) && focus.has(e.dst);
    if (!touchesFocus && !(tree || showCross)) continue;
    // Thousands of faint lines stack into a solid wash.
    if (focus && !touchesFocus && !tree) continue;
    const p1 = worldToScreen(state.layout.get(e.src));
    const p2 = worldToScreen(state.layout.get(e.dst));
    const sources = [...edgeSources(e)];
    ctx.strokeStyle = SOURCE_COLOR[sources[0]] || "#5b6478";
    if (focus) ctx.globalAlpha = touchesFocus ? focusAlpha : baseAlpha * 0.3;
    else ctx.globalAlpha = baseAlpha;
    ctx.beginPath();
    ctx.moveTo(p1.x, p1.y);
    if (tree) {
      ctx.lineTo(p2.x, p2.y);
    } else {
      // A link between rings is bent toward the middle.
      ctx.quadraticCurveTo(
        (p1.x + p2.x) / 2 * 0.65 + centre.x * 0.35,
        (p1.y + p2.y) / 2 * 0.65 + centre.y * 0.35,
        p2.x, p2.y,
      );
    }
    ctx.stroke();
  }
  ctx.globalAlpha = 1;

  // Nodes.
  const labelled = [];
  for (const [id, p] of state.layout) {
    const node = state.byId.get(id);
    if (!node) continue;
    const s = worldToScreen(p);
    if (s.x < -40 || s.y < -40 || s.x > rect.width + 40 || s.y > rect.height + 40) continue;
    const r = Math.max(0.9, nodeRadius(id) * Math.min(1.4, Math.max(0.3, k)));
    const dim = focus && !focus.has(id);
    const match = state.matches.has(id);

    ctx.globalAlpha = dim && !match ? 0.18 : 1;
    ctx.beginPath();
    ctx.arc(s.x, s.y, r, 0, Math.PI * 2);
    ctx.fillStyle = (node.type === "entity" && node.grounded === false)
      ? UNGROUNDED_COLOR : (NODE_COLOR[node.type] || "#9aa0b0");
    ctx.fill();
    if (terminationOf(id) === "unexpanded" && node.type !== "domain") {
      ctx.strokeStyle = "rgba(240,179,91,.75)";
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.arc(s.x, s.y, r + 2.2, 0, Math.PI * 2);
      ctx.stroke();
    }
    if (id === state.selected || match) {
      ctx.strokeStyle = match ? "#f0b35b" : "#ffffff";
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      ctx.arc(s.x, s.y, r + 4, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;

    const wedge = (p.a1 - p.a0) * Math.hypot(p.x, p.y) * k;
    if (match || id === state.selected || id === state.hovered ||
        p.hop === 0 || wedge > 13) {
      labelled.push([id, s, r, dim && !match]);
    }
  }

  ctx.font = "11px Inter, system-ui, sans-serif";
  ctx.textBaseline = "middle";
  labelled.sort((a, b) => b[2] - a[2]);
  const placed = [];
  const clear = (box) => !placed.some(
    (q) => box.x < q.x + q.w && box.x + box.w > q.x
      && box.y < q.y + q.h && box.y + box.h > q.y,
  );
  for (const [id, s, r, dim] of labelled) {
    const node = state.byId.get(id);
    const p = state.layout.get(id);
    const left = Math.abs(Math.atan2(p.y, p.x)) > Math.PI / 2 && p.hop > 0;
    const text = truncate(node.display_name || node.id, 24);
    ctx.textAlign = p.hop === 0 ? "center" : (left ? "right" : "left");
    const dx = p.hop === 0 ? 0 : (left ? -(r + 6) : r + 6);
    const dy = p.hop === 0 ? -(r + 11) : 0;
    const w = ctx.measureText(text).width;
    const bx = ctx.textAlign === "center" ? s.x - w / 2
      : (ctx.textAlign === "right" ? s.x + dx - w : s.x + dx);
    const box = { x: bx - 2, y: s.y + dy - 8, w: w + 4, h: 16 };
    const forced = id === state.selected || id === state.hovered
      || state.matches.has(id) || p.hop === 0;
    if (!forced && !clear(box)) continue;
    placed.push(box);
    ctx.globalAlpha = dim ? 0.25 : 1;
    ctx.fillStyle = "rgba(20,22,28,.8)";
    ctx.fillRect(box.x, box.y, box.w, box.h);
    ctx.fillStyle = p.hop === 0 ? "#ffffff" : "#c9cedd";
    ctx.fillText(text, s.x + dx, s.y + dy);
  }
  ctx.globalAlpha = 1;
}

function truncate(s, n) {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

function nodeAt(sx, sy) {
  let best = null;
  let bestD = 16;
  for (const [id, p] of state.layout) {
    const s = worldToScreen(p);
    const d = Math.hypot(s.x - sx, s.y - sy);
    const r = Math.max(4, nodeRadius(id) * Math.min(1.4, Math.max(0.45, state.view.k)));
    if (d < Math.max(r + 4, 7) && d < bestD) { best = id; bestD = d; }
  }
  return best;
}

// ---------------------------------------------------------- interaction ---
let dragging = false;
let dragFrom = null;

canvas.addEventListener("mousedown", (ev) => {
  dragging = true;
  dragFrom = { x: ev.clientX, y: ev.clientY, vx: state.view.x, vy: state.view.y };
  canvas.classList.add("dragging");
});
window.addEventListener("mouseup", () => {
  dragging = false;
  canvas.classList.remove("dragging");
});
canvas.addEventListener("mousemove", (ev) => {
  if (dragging && dragFrom) {
    state.view.x = dragFrom.vx + (ev.clientX - dragFrom.x) / state.view.k;
    state.view.y = dragFrom.vy + (ev.clientY - dragFrom.y) / state.view.k;
    hideTooltip();
    draw();
    return;
  }
  const rect = canvas.getBoundingClientRect();
  const id = nodeAt(ev.clientX - rect.left, ev.clientY - rect.top);
  if (id !== state.hovered) {
    state.hovered = id;
    draw();
  }
  if (id) showTooltip(id, ev.clientX - rect.left, ev.clientY - rect.top);
  else hideTooltip();
});
canvas.addEventListener("mouseleave", () => {
  state.hovered = null;
  hideTooltip();
  draw();
});
canvas.addEventListener("wheel", (ev) => {
  ev.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const before = screenToWorld(ev.clientX - rect.left, ev.clientY - rect.top);
  const factor = Math.exp(-ev.deltaY * 0.0016);
  state.view.k = Math.max(0.05, Math.min(14, state.view.k * factor));
  const after = screenToWorld(ev.clientX - rect.left, ev.clientY - rect.top);
  state.view.x += after.x - before.x;
  state.view.y += after.y - before.y;
  draw();
}, { passive: false });
canvas.addEventListener("click", (ev) => {
  const rect = canvas.getBoundingClientRect();
  select(nodeAt(ev.clientX - rect.left, ev.clientY - rect.top));
});
canvas.addEventListener("dblclick", () => fit());
window.addEventListener("resize", resize);

function showTooltip(id, x, y) {
  const node = state.byId.get(id);
  if (!node) return;
  const tip = $("tooltip");
  const out = (state.outgoing.get(id) || []).filter(edgePasses).length;
  const inc = (state.incoming.get(id) || []).filter(edgePasses).length;
  const termination = terminationOf(id);
  tip.innerHTML = "";
  const name = document.createElement("div");
  name.className = "t-name";
  name.textContent = node.display_name || node.id;
  const meta = document.createElement("div");
  meta.className = "t-meta";
  meta.textContent = `${node.type} · hop ${node.hop_first_seen ?? "?"}`
    + (node.primary_domain ? ` · ${node.primary_domain}` : "");
  const line = document.createElement("div");
  line.className = "t-line";
  line.textContent = `${out} onward · ${inc} incoming — ${TERMINATION_LABEL[termination]}`;
  tip.append(name, meta, line);
  tip.hidden = false;
  const rect = canvas.getBoundingClientRect();
  tip.style.left = `${Math.min(x + 14, rect.width - tip.offsetWidth - 8)}px`;
  tip.style.top = `${Math.min(y + 14, rect.height - tip.offsetHeight - 8)}px`;
}

function hideTooltip() { $("tooltip").hidden = true; }

function select(id) {
  if (state.editing && state.mergeFrom && id && id !== state.mergeFrom) {
    completeMerge(id);
    return;
  }
  state.selected = id;
  renderDetail();
  if (state.editing) renderEditBox();
  draw();
}

// ----------------------------------------------------------------- panel ---
function relationCard(edge, direction) {
  const otherId = direction === "out" ? edge.dst : edge.src;
  const other = state.byId.get(otherId);
  const evidence = edgeEvidence(edge);
  const negative = evidence.length > 0 && evidence.every((e) => e.negative);

  const card = document.createElement("div");
  card.className = "rel" + (negative ? " neg" : "");

  const who = document.createElement("div");
  who.className = "who";
  who.textContent = (other && other.display_name) || otherId;
  card.append(who);

  const dataTypes = [...new Set(evidence.map((e) => e.data_type).filter(Boolean))];
  const what = document.createElement("div");
  what.className = "what";
  what.textContent = (negative ? "states it does not share " : "")
    + (KIND_LABEL[edge.kind] || edge.kind)
    + (dataTypes.length ? ` — ${dataTypes.slice(0, 4).join(", ")}` : "");
  card.append(what);

  const parts = [...edgeSources(edge)];
  for (const t of edgeTracks(edge)) {
    if (state.track === "both" && TRACK_LABEL[t]) parts.push(TRACK_LABEL[t]);
  }
  for (const s of edgeSubjects(edge)) {
    if (SUBJECT_LABEL[s]) parts.push(SUBJECT_LABEL[s]);
  }
  if (parts.length) {
    const src = document.createElement("div");
    src.className = "src";
    src.textContent = parts.join(" · ");
    card.append(src);
  }
  const quote = evidence.find((e) => e.snippet);
  if (quote) {
    const q = document.createElement("div");
    q.className = "quote";
    q.textContent = truncate(quote.snippet, 240);
    card.append(q);
  }
  if (state.editing) card.append(edgeEditRow(edge));
  card.addEventListener("click", (ev) => {
    if (ev.target.closest(".edit-row")) return;
    if (state.mergeFrom) { completeMerge(otherId); return; }
    select(otherId);
  });
  return card;
}

function edgeEditRow(edge) {
  const row = document.createElement("div");
  row.className = "edit-row";
  const drop = document.createElement("button");
  drop.className = "mini danger";
  drop.textContent = "remove";
  drop.title = "Remove this arrangement from the graph";
  drop.addEventListener("click", () => sendEdits([{
    op: "delete_edge", kind: edge.kind, src: edge.src, dst: edge.dst,
  }]));
  row.append(drop);
  for (const track of ["personal_data", "inventory"]) {
    if (edgeTracks(edge).has(track) && edgeTracks(edge).size === 1) continue;
    const move = document.createElement("button");
    move.className = "mini";
    move.textContent = `→ ${TRACK_LABEL[track]}`;
    move.title = `Record this arrangement as ${TRACK_LABEL[track]}`;
    move.addEventListener("click", () => sendEdits([{
      op: "set_track", kind: edge.kind, src: edge.src, dst: edge.dst, track,
    }]));
    row.append(move);
  }
  return row;
}

function renderDetail() {
  const box = $("detail");
  box.innerHTML = "";
  const title = document.createElement("h2");
  title.textContent = "Selection";
  box.append(title);

  const node = state.selected && state.byId.get(state.selected);
  if (!node) {
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = "Click a node to see who it receives from and hands on to.";
    box.append(p);
    return;
  }

  const name = document.createElement("div");
  name.className = "sel-name";
  name.textContent = node.display_name || node.id;
  const meta = document.createElement("div");
  meta.className = "sel-meta";
  const termination = terminationOf(node.id);
  for (const [text, cls] of [
    [node.type, ""],
    [`hop ${node.hop_first_seen ?? "?"}`, ""],
    [TERMINATION_LABEL[termination], termination],
    ...(node.primary_domain ? [[node.primary_domain, ""]] : []),
    ...(node.country ? [[node.country.toUpperCase(), ""]] : []),
    ...(node.type === "entity" && node.grounded === false
      ? [["no register knows this name", "unexpanded"]] : []),
    ...(node.edited ? [["corrected by hand", ""]] : []),
  ]) {
    const chip = document.createElement("span");
    chip.className = "chip " + cls;
    chip.textContent = text;
    meta.append(chip);
  }
  box.append(name, meta);

  // The surfaces the sources used for this party.
  const aliases = (node.aliases || []).filter((a) => a.toLowerCase() !== (node.display_name || "").toLowerCase());
  if (aliases.length) {
    const named = document.createElement("p");
    named.className = "sel-aliases";
    named.textContent = `named as ${aliases.slice(0, 6).join(", ")}`
      + (aliases.length > 6 ? ` and ${aliases.length - 6} more` : "");
    box.append(named);
  }

  const out = (state.outgoing.get(node.id) || []).filter(edgePasses);
  const inc = (state.incoming.get(node.id) || []).filter(edgePasses);
  for (const [label, edges, dir] of [
    ["Hands data to", out, "out"],
    ["Receives data from", inc, "in"],
  ]) {
    const h = document.createElement("p");
    h.className = "sub";
    h.textContent = `${label} (${edges.length})`;
    box.append(h);
    if (!edges.length) {
      const p = document.createElement("p");
      p.className = "empty tight";
      p.textContent = dir === "out" && termination === "unexpanded"
        ? "The walk never analysed this party; a deeper collection may find more."
        : "Nothing recorded.";
      box.append(p);
      continue;
    }
    for (const e of edges.slice(0, 120)) box.append(relationCard(e, dir));
    if (edges.length > 120) {
      const p = document.createElement("p");
      p.className = "empty tight";
      p.textContent = `+${edges.length - 120} more`;
      box.append(p);
    }
  }
}

function renderEditBox() {
  const box = $("edit-box");
  box.hidden = !state.editing;
  $("edit-toggle").classList.toggle("primary", state.editing);
  if (!state.editing) { state.mergeFrom = null; return; }

  const actions = $("edit-actions");
  actions.innerHTML = "";
  const node = state.selected && state.byId.get(state.selected);
  if (!node) {
    $("edit-status").textContent = "Select a party to correct it.";
    return;
  }
  if (state.mergeFrom) {
    const from = state.byId.get(state.mergeFrom);
    $("edit-status").textContent =
      `Folding ${(from && from.display_name) || state.mergeFrom} into the next `
      + "party you click. Press Escape to stop.";
  } else {
    $("edit-status").textContent = `Editing ${node.display_name || node.id}.`;
  }

  const rename = document.createElement("button");
  rename.className = "mini";
  rename.textContent = "rename";
  rename.addEventListener("click", () => {
    const next = prompt("Name for this party", node.display_name || node.id);
    if (next && next.trim() && next !== node.display_name) {
      sendEdits([{ op: "rename", node: node.id, display_name: next.trim() }]);
    }
  });

  const merge = document.createElement("button");
  merge.className = "mini";
  merge.textContent = state.mergeFrom ? "cancel merge" : "fold into…";
  merge.addEventListener("click", () => {
    state.mergeFrom = state.mergeFrom ? null : node.id;
    renderEditBox();
  });

  const drop = document.createElement("button");
  drop.className = "mini danger";
  drop.textContent = "remove party";
  drop.addEventListener("click", () => {
    sendEdits([{ op: "delete_node", node: node.id }]);
    state.selected = null;
  });

  actions.append(rename, merge, drop);
}

function completeMerge(intoId) {
  const from = state.mergeFrom;
  state.mergeFrom = null;
  if (!from || from === intoId) { renderEditBox(); return; }
  sendEdits([{ op: "merge", node: from, into: intoId }]);
  state.selected = intoId;
}

async function sendEdits(edits) {
  if (!state.jobId) {
    $("edit-status").textContent = "Corrections need a collected walk.";
    return;
  }
  try {
    const resp = await fetch(`${BRIDGE}/graph/edit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: state.jobId, edits }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
    $("edit-status").textContent = data.applied
      ? `${data.applied} correction(s) applied.`
      : "Nothing changed.";
  } catch (err) {
    $("edit-status").textContent = `Correction failed: ${err.message}`;
    return;
  }
  await poll(false);
  renderEditBox();
}

function renderLegend() {
  const legend = $("legend");
  legend.innerHTML = "";
  const rows = [
    ["#6ea8fe", "this site", ""],
    ["#9ec5ff", "named organisation", ""],
    [UNGROUNDED_COLOR, "named, but no register knows it", ""],
    ["#d8b4fe", "unnamed category", ""],
    [SOURCE_COLOR.policy, "stated in a policy", ""],
    [SOURCE_COLOR.registry, "named in a registry", ""],
    [SOURCE_COLOR.traffic, "observed in traffic", ""],
    ["", "not analysed (walk stopped)", "ring"],
  ];
  for (const [colour, text, cls] of rows) {
    const row = document.createElement("div");
    row.className = "row";
    const swatch = document.createElement("i");
    swatch.className = cls;
    if (colour) swatch.style.background = colour;
    row.append(swatch, document.createTextNode(text));
    legend.append(row);
  }
}

function carriesUpstreamData(subjects) {
  if (!subjects.size) return true;
  if (subjects.has("service_data") || subjects.has("not_applicable")) return true;
  return subjects.has("unknown");
}

function countChainsOnTrack(track, parties, limit) {
  const adjacency = new Map();
  const owners = new Map();
  for (const e of state.graph.edges) {
    if (e.kind === "owned_by") owners.set(e.src, e.dst);
  }
  for (const e of state.graph.edges) {
    if (!edgePasses(e)) continue;
    const positive = (e.evidence || []).filter(
      (ev) => !ev.negative && (ev.track || "personal_data") === track,
    );
    if (!positive.length) continue;
    let src = e.src;
    let dst = e.dst;
    if (e.kind === "contacts") dst = owners.get(e.dst) || "";
    else if (e.kind !== "discloses_sharing_with" && e.kind !== "supplies") continue;
    if (!dst || src === dst) continue;
    const node = state.byId.get(dst);
    const from = state.byId.get(src);
    if (!node || !from) continue;
    if (!["entity", "target"].includes(node.type)) continue;
    if (!["entity", "target"].includes(from.type)) continue;
    if (!adjacency.has(src)) adjacency.set(src, new Map());
    adjacency.get(src).set(dst, new Set(positive.map((ev) => ev.subject).filter(Boolean)));
  }
  let found = 0;
  let steps = 60000;
  const walk = (path) => {
    if (found >= limit || steps-- <= 0) return;
    if (path.length === parties) { found++; return; }
    for (const [next, subjects] of adjacency.get(path[path.length - 1]) || []) {
      if (path.includes(next)) continue;
      if (path.length > 1 && !carriesUpstreamData(subjects)) continue;
      walk(path.concat(next));
    }
  };
  for (const start of adjacency.keys()) {
    if (found >= limit || steps <= 0) break;
    walk([start]);
  }
  return found;
}

function countChains(parties = 4, limit = 400) {
  const tracks = state.track === "both"
    ? ["personal_data", "inventory"] : [state.track];
  return tracks.reduce((n, t) => n + countChainsOnTrack(t, parties, limit), 0);
}

let lastSnapshot = null;

function renderStats(snapshot) {
  if (snapshot) lastSnapshot = snapshot;
  snapshot = lastSnapshot;
  const parties = [...state.visible].filter((id) => {
    const n = state.byId.get(id);
    return n && n.type !== "domain";
  }).length;
  const arrangements = state.graph.edges.filter(
    (e) => edgePasses(e) && e.kind !== "owned_by"
      && state.visible.has(e.src) && state.visible.has(e.dst),
  ).length;
  $("stat-nodes").textContent = parties;
  $("stat-edges").textContent = arrangements;
  $("stat-edges-label").textContent = state.track === "both"
    ? "arrangements (both tracks)"
    : `${TRACK_LABEL[state.track]} arrangements`;
  $("stat-crawled").textContent = (snapshot && snapshot.progress.crawled) || 0;
  $("stat-chains").textContent = countChains();

  const unresolved = (snapshot && snapshot.unresolved) || [];
  $("unresolved-box").hidden = unresolved.length === 0;
  $("unresolved-count").textContent = unresolved.length;
  const wrap = $("unresolved");
  wrap.innerHTML = "";
  for (const name of unresolved.slice(0, 60)) {
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = name;
    wrap.append(tag);
  }
}

// -------------------------------------------------------------- collection ---
function setProgress(snapshot) {
  const box = $("progress");
  if (!snapshot) { box.hidden = true; return; }
  const p = snapshot.progress;
  box.hidden = !state.running && p.phase === "done" && !p.error;
  const done = p.parties_total ? `${p.parties_done}/${p.parties_total} parties` : "";
  const text = p.error
    ? `Error: ${p.error}`
    : `hop ${p.hop} of ${p.hops} · ${p.phase}${done ? " · " + done : ""}`
      + (p.current ? ` · ${p.current}` : "");
  $("progress-text").textContent = text;
  const frac = p.parties_total
    ? (p.hop - 1 + p.parties_done / p.parties_total) / Math.max(1, p.hops)
    : p.hop / Math.max(1, p.hops);
  $("progress-bar").style.width = `${Math.round(Math.min(1, frac) * 100)}%`;
}

async function observedRequests() {
  if (state.tabId == null) return [];
  try {
    const res = await browser.runtime.sendMessage({
      kind: "getRequests", tabId: state.tabId,
    });
    return (res && res.requests) || [];
  } catch (e) {
    return [];
  }
}

// The dialog captured on the seed page, which the popup left in the background
// page when it last analysed the tab.
async function capturedCmp() {
  if (state.tabId == null) return null;
  try {
    const res = await browser.runtime.sendMessage({
      kind: "getCmp", tabId: state.tabId,
    });
    return (res && res.cmp) || null;
  } catch (e) {
    return null;
  }
}

async function start() {
  const requests = await observedRequests();
  const cmp = await capturedCmp();
  let data;
  try {
    const resp = await fetch(`${BRIDGE}/graph`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: state.origin, hops: state.hops, requests, cmp }),
    });
    data = await resp.json();
    if (!resp.ok && resp.status !== 202) throw new Error(data.error || `HTTP ${resp.status}`);
  } catch (err) {
    if (err instanceof TypeError) { $("offline").hidden = false; return; }
    $("progress").hidden = false;
    $("progress-text").textContent = `Error: ${err.message}`;
    return;
  }
  state.jobId = data.job_id;
  state.running = true;
  $("run").hidden = true;
  $("stop").hidden = false;
  $("empty").hidden = true;
  $("progress").hidden = false;
  poll(true);
  state.polling = setInterval(poll, POLL_MS);
}

async function stop() {
  if (!state.jobId) return;
  try {
    await fetch(`${BRIDGE}/graph/stop`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: state.jobId }),
    });
  } catch (e) { /* the walk ends with the bridge either way */ }
}

async function poll(first) {
  if (!state.jobId) return;
  let snapshot;
  try {
    const resp = await fetch(`${BRIDGE}/graph?job=${encodeURIComponent(state.jobId)}`);
    snapshot = await resp.json();
    if (!resp.ok) throw new Error(snapshot.error || `HTTP ${resp.status}`);
  } catch (err) {
    if (err instanceof TypeError) $("offline").hidden = false;
    return;
  }
  applySnapshot(snapshot, first);
  if (!snapshot.running) {
    clearInterval(state.polling);
    state.polling = null;
    state.running = false;
    $("run").hidden = false;
    $("stop").hidden = true;
  }
}

function applySnapshot(snapshot, refit) {
  indexGraph(snapshot.graph);
  rebuild(refit);
  setProgress(snapshot);
  renderStats(snapshot);
  $("empty").hidden = state.graph.nodes.length > 0;
}

function rebuild(refit) {
  computeVisible();
  layout();
  applySearch($("search").value);
  if (refit) fit(); else draw();
  renderDetail();
}

// ------------------------------------------------------------------ search ---
function applySearch(query) {
  const q = (query || "").trim().toLowerCase();
  state.matches = new Set();
  if (!q) return;
  for (const id of state.visible) {
    const node = state.byId.get(id);
    if (node && (node.display_name || "").toLowerCase().includes(q)) {
      state.matches.add(id);
    }
  }
}

$("search").addEventListener("input", (ev) => {
  applySearch(ev.target.value);
  draw();
});
$("search").addEventListener("keydown", (ev) => {
  if (ev.key !== "Enter" || !state.matches.size) return;
  const id = [...state.matches][0];
  const p = state.layout.get(id);
  if (!p) return;
  state.view.x = -p.x;
  state.view.y = -p.y;
  state.view.k = Math.max(state.view.k, 1.1);
  select(id);
});

for (const [id, key] of [
  ["f-policy", "policy"], ["f-registry", "registry"], ["f-traffic", "traffic"],
  ["f-generic", "generic"], ["f-domains", "domains"],
  ["f-ungrounded", "ungrounded"],
]) {
  $(id).addEventListener("change", (ev) => {
    state.filters[key] = ev.target.checked;
    rebuild(false);
    renderStats(lastSnapshot);
  });
}

$("hops").addEventListener("click", (ev) => {
  const button = ev.target.closest("button");
  if (!button) return;
  state.hops = Number(button.dataset.hops);
  for (const b of $("hops").querySelectorAll("button")) b.classList.toggle("on", b === button);
});
$("track").addEventListener("click", (ev) => {
  const button = ev.target.closest("button");
  if (!button) return;
  state.track = button.dataset.track;
  for (const b of $("track").querySelectorAll("button")) b.classList.toggle("on", b === button);
  rebuild(false);
  renderStats(lastSnapshot);
});
$("edit-toggle").addEventListener("click", () => {
  state.editing = !state.editing;
  renderEditBox();
  renderDetail();
});
window.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && state.mergeFrom) {
    state.mergeFrom = null;
    renderEditBox();
  }
});
$("run").addEventListener("click", start);
$("stop").addEventListener("click", stop);

// -------------------------------------------------------------------- boot ---
(function init() {
  const params = new URLSearchParams(location.search);
  state.origin = params.get("url") || "";
  const tab = params.get("tab");
  state.tabId = tab === null ? null : Number(tab);
  $("origin").textContent = state.origin || "no URL supplied";
  $("origin").title = state.origin;
  $("run").disabled = !state.origin;
  renderLegend();
  renderDetail();
  renderEditBox();
  resize();
})();
