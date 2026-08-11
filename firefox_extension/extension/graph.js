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

const HIT_CELL = 140;
const HIT_GRID_SPAN = 120;

// ---------------------------------------------------------------- state ---
const state = {
  origin: "",
  tabId: null,
  hops: 1,
  timeLimit: 0,
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
  minK: 0.05,
  render: null,
  grid: null,
  gridCell: HIT_CELL,
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

function nodeFill(node) {
  if (node.type === "entity" && node.grounded === false) return UNGROUNDED_COLOR;
  return NODE_COLOR[node.type] || "#9aa0b0";
}

// ---------------------------------------------------- draw-ready geometry ---

function buildRenderModel() {
  const nodes = [];
  const index = new Map();
  for (const [id, p] of state.layout) {
    const node = state.byId.get(id);
    if (!node) continue;
    index.set(id, nodes.length);
    nodes.push({
      id,
      x: p.x,
      y: p.y,
      r: nodeRadius(id),
      fill: nodeFill(node),
      unexpanded: node.type !== "domain" && terminationOf(id) === "unexpanded",
      name: truncate(node.display_name || node.id, 24),
      hop: p.hop,
      wedge: (p.a1 - p.a0) * Math.hypot(p.x, p.y),
    });
  }

  const edges = [];
  for (const e of state.graph.edges) {
    const a = state.layout.get(e.src);
    const b = state.layout.get(e.dst);
    if (!a || !b || !edgePasses(e)) continue;
    const sources = edgeSources(e);
    let colour = "#5b6478";
    for (const s of sources) { colour = SOURCE_COLOR[s] || colour; break; }
    edges.push({
      src: e.src, dst: e.dst,
      x1: a.x, y1: a.y, x2: b.x, y2: b.y,
      lo: Math.min(a.x, b.x), hi: Math.max(a.x, b.x),
      top: Math.min(a.y, b.y), bot: Math.max(a.y, b.y),
      colour,
      tree: isTreeEdge(e),
    });
  }

  const colours = new Map();
  edges.forEach((e, i) => {
    if (!colours.has(e.colour)) colours.set(e.colour, []);
    colours.get(e.colour).push(i);
  });
  const fills = new Map();
  nodes.forEach((n, i) => {
    if (!fills.has(n.fill)) fills.set(n.fill, []);
    fills.get(n.fill).push(i);
  });

  let extent = 1;
  const rings = new Map();
  for (const n of nodes) {
    const radius = Math.hypot(n.x, n.y);
    extent = Math.max(extent, radius);
    if (n.hop) rings.set(n.hop, radius);
  }

  state.render = {
    nodes, index, edges, colours, fills, extent,
    rings: [...rings.values()],
  };
  state.minK = fitScale() * 0.4;
  buildHitGrid(nodes, extent);
}

function buildHitGrid(nodes, extent) {
  const size = Math.max(HIT_CELL, (2 * extent) / HIT_GRID_SPAN);
  const grid = new Map();
  for (let i = 0; i < nodes.length; i++) {
    const n = nodes[i];
    const key = `${Math.floor(n.x / size)},${Math.floor(n.y / size)}`;
    const cell = grid.get(key);
    if (cell) cell.push(i); else grid.set(key, [i]);
  }
  state.grid = grid;
  state.gridCell = size;
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

function fitScale() {
  const rect = canvas.getBoundingClientRect();
  const extent = (state.render && state.render.extent) || 1;
  return Math.min(rect.width, rect.height) / (2 * extent + 90);
}

function fit() {
  state.view.x = 0;
  state.view.y = 0;
  state.view.k = fitScale();
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

const RING_STROKE = "rgba(255,255,255,.055)";
const CROSS_EDGE_CEILING = 500;
const MAX_LABELS = 400;
const LABEL_CELL = 24;

let pendingFrame = 0;

function draw() {
  if (pendingFrame) return;
  pendingFrame = requestAnimationFrame(() => { pendingFrame = 0; paint(); });
}

function paint() {
  const rect = canvas.getBoundingClientRect();
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, rect.width, rect.height);
  const model = state.render;
  if (!model || !model.nodes.length) return;

  const k = state.view.k;
  const vx = state.view.x;
  const vy = state.view.y;
  const ox = rect.width / 2;
  const oy = rect.height / 2;
  const sx = (x) => ox + (x + vx) * k;
  const sy = (y) => oy + (y + vy) * k;

  const pad = 60 / k;
  const wx0 = -vx - ox / k - pad;
  const wx1 = -vx + ox / k + pad;
  const wy0 = -vy - oy / k - pad;
  const wy1 = -vy + oy / k + pad;

  const focus = highlightSet();
  const cx = sx(0);
  const cy = sy(0);

  // Hop rings.
  ctx.strokeStyle = RING_STROKE;
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (const radius of model.rings) {
    ctx.moveTo(cx + radius * k, cy);
    ctx.arc(cx, cy, radius * k, 0, Math.PI * 2);
  }
  ctx.stroke();

  drawEdges(model, { k, sx, sy, cx, cy, wx0, wx1, wy0, wy1, focus });
  const labelled = drawNodes(model, { k, sx, sy, wx0, wx1, wy0, wy1, focus });
  drawLabels(labelled, rect, k);
}

function drawEdges(model, v) {
  const { k, sx, sy, cx, cy, focus } = v;
  const total = model.edges.length;
  if (!total) return;
  const showCross = total < CROSS_EDGE_CEILING || k > 0.55;
  const baseAlpha = Math.max(0.05, Math.min(0.34, 260 / total));
  ctx.lineWidth = Math.max(0.4, Math.min(1.5, k * 1.6));

  const focused = [];
  for (const [colour, indices] of model.colours) {
    let started = false;
    for (const i of indices) {
      const e = model.edges[i];
      if (e.hi < v.wx0 || e.lo > v.wx1 || e.bot < v.wy0 || e.top > v.wy1) continue;
      if (focus) {
        if (focus.has(e.src) && focus.has(e.dst)) { focused.push(i); continue; }
        if (!e.tree) continue;
      } else if (!e.tree && !showCross) {
        continue;
      }
      if (!started) { ctx.beginPath(); started = true; }
      traceEdge(e, sx, sy, cx, cy);
    }
    if (!started) continue;
    ctx.globalAlpha = focus ? baseAlpha * 0.3 : baseAlpha;
    ctx.strokeStyle = colour;
    ctx.stroke();
  }

  if (focused.length) {
    ctx.globalAlpha = Math.max(0.1, Math.min(0.95, 150 / focused.length));
    let colour = "";
    for (const i of focused) {
      const e = model.edges[i];
      if (e.colour !== colour) {
        if (colour) ctx.stroke();
        colour = e.colour;
        ctx.strokeStyle = colour;
        ctx.beginPath();
      }
      traceEdge(e, sx, sy, cx, cy);
    }
    if (colour) ctx.stroke();
  }
  ctx.globalAlpha = 1;
}

function traceEdge(e, sx, sy, cx, cy) {
  const x1 = sx(e.x1), y1 = sy(e.y1), x2 = sx(e.x2), y2 = sy(e.y2);
  ctx.moveTo(x1, y1);
  if (e.tree) {
    ctx.lineTo(x2, y2);
    return;
  }
  // A link between rings is bent toward the middle.
  ctx.quadraticCurveTo(
    (x1 + x2) / 2 * 0.65 + cx * 0.35,
    (y1 + y2) / 2 * 0.65 + cy * 0.35,
    x2, y2,
  );
}

function drawNodes(model, v) {
  const { k, sx, sy, focus } = v;
  const scale = Math.min(1.4, Math.max(0.3, k));
  const labelled = [];
  const rings = [];
  const marked = [];

  for (const pass of focus ? [false, true] : [true]) {
    for (const [colour, indices] of model.fills) {
      let started = false;
      for (const i of indices) {
        const n = model.nodes[i];
        if (n.x < v.wx0 || n.x > v.wx1 || n.y < v.wy0 || n.y > v.wy1) continue;
        const match = state.matches.has(n.id);
        const lit = !focus || focus.has(n.id) || match;
        if (lit !== pass) continue;
        const r = Math.max(0.9, n.r * scale);
        if (!started) { ctx.beginPath(); started = true; }
        ctx.moveTo(sx(n.x) + r, sy(n.y));
        ctx.arc(sx(n.x), sy(n.y), r, 0, Math.PI * 2);
        if (n.unexpanded && lit) rings.push([sx(n.x), sy(n.y), r]);
        if (n.id === state.selected || match) {
          marked.push([sx(n.x), sy(n.y), r, match]);
        }
        if (match || n.id === state.selected || n.id === state.hovered
            || n.hop === 0 || n.wedge * k > 13) {
          labelled.push([n, sx(n.x), sy(n.y), r, !lit]);
        }
      }
      if (!started) continue;
      ctx.globalAlpha = pass ? 1 : 0.18;
      ctx.fillStyle = colour;
      ctx.fill();
    }
  }
  ctx.globalAlpha = 1;

  if (rings.length) {
    ctx.strokeStyle = "rgba(240,179,91,.75)";
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    for (const [x, y, r] of rings) {
      ctx.moveTo(x + r + 2.2, y);
      ctx.arc(x, y, r + 2.2, 0, Math.PI * 2);
    }
    ctx.stroke();
  }
  for (const [x, y, r, match] of marked) {
    ctx.strokeStyle = match ? "#f0b35b" : "#ffffff";
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.arc(x, y, r + 4, 0, Math.PI * 2);
    ctx.stroke();
  }
  return labelled;
}

function drawLabels(labelled, rect, k) {
  if (!labelled.length) return;
  ctx.font = "11px Inter, system-ui, sans-serif";
  ctx.textBaseline = "middle";
  labelled.sort((a, b) => b[3] - a[3]);

  const taken = new Set();
  const cells = (box) => {
    const out = [];
    const x0 = Math.floor(box.x / LABEL_CELL);
    const x1 = Math.floor((box.x + box.w) / LABEL_CELL);
    const y0 = Math.floor(box.y / LABEL_CELL);
    const y1 = Math.floor((box.y + box.h) / LABEL_CELL);
    for (let cx = x0; cx <= x1; cx++) {
      for (let cy = y0; cy <= y1; cy++) out.push(`${cx},${cy}`);
    }
    return out;
  };

  let placed = 0;
  for (const [n, x, y, r, dim] of labelled) {
    if (placed >= MAX_LABELS) break;
    const forced = n.id === state.selected || n.id === state.hovered
      || state.matches.has(n.id) || n.hop === 0;
    const left = Math.abs(Math.atan2(n.y, n.x)) > Math.PI / 2 && n.hop > 0;
    ctx.textAlign = n.hop === 0 ? "center" : (left ? "right" : "left");
    const dx = n.hop === 0 ? 0 : (left ? -(r + 6) : r + 6);
    const dy = n.hop === 0 ? -(r + 11) : 0;
    const w = ctx.measureText(n.name).width;
    const bx = ctx.textAlign === "center" ? x - w / 2
      : (ctx.textAlign === "right" ? x + dx - w : x + dx);
    const box = { x: bx - 2, y: y + dy - 8, w: w + 4, h: 16 };
    if (box.x > rect.width || box.x + box.w < 0) continue;
    const keys = cells(box);
    if (!forced && keys.some((key) => taken.has(key))) continue;
    for (const key of keys) taken.add(key);
    placed++;
    ctx.globalAlpha = dim ? 0.25 : 1;
    ctx.fillStyle = "rgba(20,22,28,.8)";
    ctx.fillRect(box.x, box.y, box.w, box.h);
    ctx.fillStyle = n.hop === 0 ? "#ffffff" : "#c9cedd";
    ctx.fillText(n.name, x + dx, y + dy);
  }
  ctx.globalAlpha = 1;
}

function truncate(s, n) {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

function nodeAt(px, py) {
  const model = state.render;
  if (!model || !state.grid) return null;
  const world = screenToWorld(px, py);
  const k = state.view.k;
  const reach = 16 / k;
  const size = state.gridCell || HIT_CELL;
  let best = null;
  let bestD = 16;
  const x0 = Math.floor((world.x - reach) / size);
  const x1 = Math.floor((world.x + reach) / size);
  const y0 = Math.floor((world.y - reach) / size);
  const y1 = Math.floor((world.y + reach) / size);
  for (let cx = x0; cx <= x1; cx++) {
    for (let cy = y0; cy <= y1; cy++) {
      const cell = state.grid.get(`${cx},${cy}`);
      if (!cell) continue;
      for (const i of cell) {
        const n = model.nodes[i];
        const d = Math.hypot((n.x - world.x) * k, (n.y - world.y) * k);
        const r = Math.max(4, n.r * Math.min(1.4, Math.max(0.45, k)));
        if (d < Math.max(r + 4, 7) && d < bestD) { best = n.id; bestD = d; }
      }
    }
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
  const floor = Math.min(0.05, state.minK);
  state.view.k = Math.max(floor, Math.min(14, state.view.k * factor));
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
    ["", "not analysed", "ring"],
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

let lastSnapshot = null;

function renderStats(snapshot) {
  if (snapshot) lastSnapshot = snapshot;
  snapshot = lastSnapshot;
  const recipients = new Set();
  for (const e of state.graph.edges) {
    if (!edgePasses(e)) continue;
    if (!state.visible.has(e.src) || !state.visible.has(e.dst)) continue;
    if (e.kind === "discloses_sharing_with" || e.kind === "owned_by") {
      recipients.add(e.dst);
    }
  }
  const named = (id) => {
    const n = state.byId.get(id);
    return n && n.type !== "domain" && n.type !== "target";
  };
  const arrangements = state.graph.edges.filter(
    (e) => edgePasses(e) && e.kind !== "owned_by"
      && state.visible.has(e.src) && state.visible.has(e.dst),
  ).length;
  $("stat-recipients").textContent = [...recipients].filter(named).length;
  $("stat-edges").textContent = arrangements;
  $("stat-edges-label").textContent = state.track === "both"
    ? "arrangements (both tracks)"
    : `${TRACK_LABEL[state.track]} arrangements`;
  $("stat-crawled").textContent = (snapshot && snapshot.progress.crawled) || 0;

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
function clockLabel(seconds) {
  const s = Math.max(0, Math.round(seconds || 0));
  const m = Math.floor(s / 60);
  return m ? `${m}m ${String(s % 60).padStart(2, "0")}s` : `${s}s`;
}

function setProgress(snapshot) {
  const box = $("progress");
  if (!snapshot) { box.hidden = true; return; }
  const p = snapshot.progress;
  box.hidden = !state.running && p.phase === "done" && !p.error;
  const done = p.parties_total ? `${p.parties_done}/${p.parties_total} parties` : "";
  const clock = p.time_limit
    ? ` · ${clockLabel(p.elapsed)} of ${clockLabel(p.time_limit)}`
    : (p.elapsed ? ` · ${clockLabel(p.elapsed)}` : "");
  const text = p.error
    ? `Error: ${p.error}`
    : `hop ${p.hop} of ${p.hops} · ${p.phase}${done ? " · " + done : ""}${clock}`
      + (p.current ? ` · ${p.current}` : "");
  $("progress-text").textContent = text;
  // A walk given a clock is measured by it; the ring it is on says little
  // about how much of the ecosystem is left.
  const frac = p.time_limit
    ? p.elapsed / p.time_limit
    : (p.parties_total
      ? (p.hop - 1 + p.parties_done / p.parties_total) / Math.max(1, p.hops)
      : p.hop / Math.max(1, p.hops));
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
      body: JSON.stringify({
        url: state.origin, hops: state.hops,
        time_limit: state.timeLimit * 60, requests, cmp,
      }),
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
  buildRenderModel();
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

$("hops").addEventListener("change", () => {
  state.hops = Math.max(1, Math.round(Number($("hops").value) || 1));
  $("hops").value = state.hops;
});
$("time-limit").addEventListener("change", () => {
  state.timeLimit = Math.max(0, Math.round(Number($("time-limit").value) || 0));
  $("time-limit").value = state.timeLimit;
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
