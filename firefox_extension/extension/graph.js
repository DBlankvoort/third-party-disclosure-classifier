"use strict";

const BRIDGE = "http://127.0.0.1:8765";
const POLL_MS = 1200;

const NODE_COLOR = {
  target: "#171a18",
  entity: "#6f8580",
  generic: "#a7aca8",
  domain: "#27786d",
};
const UNGROUNDED_COLOR = "#a7aca8";
const KIND_COLOR = {
  discloses_relation_with: "#526d82",
  lists_vendor: "#755c7f",
  authorises_inventory_sale: "#996f28",
  contacts_domain: "#27786d",
  resolves_to: "#8c928e",
};
const CORROBORABLE_KINDS = new Map([
  ["authorises_inventory_sale", {
    type: "sellers_json_confirmation",
    label: "Only sales the sellers.json confirms",
  }],
  ["contacts_domain", {
    type: "tracker_list_confirmation",
    label: "Only domains a tracker list knows",
  }],
]);
const KIND_LABEL = {
  discloses_relation_with: "discloses a relation with",
  lists_vendor: "lists as a vendor",
  authorises_inventory_sale: "authorises inventory sale by",
  contacts_domain: "contacts domain",
  resolves_to: "resolves to",
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

const SOURCE_LABEL = {
  policy: "policy text",
  registry: "registry file",
  traffic: "observed traffic",
  resolution: "name resolution",
};

const RECONCILE_LABEL = {
  confirmed: "confirmed by the receiving party's sellers.json",
  absent: "not named in the receiving party's sellers.json",
  confidential_only: "receiving party withholds every seller as confidential",
  no_sellers_json: "receiving party publishes no sellers.json",
  not_collected: "receiving party was not collected",
  unknown_domain: "no site known for one of the two parties",
  no_seller_id: "ads.txt provides no seller account to match",
  relationship_mismatch: "seller account exists, but its role conflicts with ads.txt",
};
const RECONCILE_ORDER = [
  "confirmed", "relationship_mismatch", "absent", "no_seller_id", "confidential_only", "no_sellers_json",
  "not_collected", "unknown_domain",
];

// Tracker-list result for a contacted domain.
const TRACKER_LABEL = {
  confirmed: "recognised as a tracker",
  known_not_tracking: "listed, but not as a tracker",
  unlisted: "on no list we hold",
};
const TRACKER_ORDER = ["confirmed", "known_not_tracking", "unlisted"];

const SITE_KIND_LABEL = {
  website: "website",
  data_broker: "vendor-side site",
  play_store_app: "Play Store app",
  app_store_app: "App Store app",
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
  kind: "main",
  siteKind: "website",
  showAllLabels: false,
  filters: {
    generic: true, domains: true,
    ungrounded: true,
    corroborated: true,
    prose: false,
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
  return edge.evidence || [];
}

function edgeSources(edge) {
  return new Set(edgeEvidence(edge).map((ev) => ev.source));
}

function edgeTypes(edge) {
  return new Set(edgeEvidence(edge).map((ev) => ev.evidence_type).filter(Boolean));
}

function isCorroborated(edge) {
  const rule = CORROBORABLE_KINDS.get(state.kind);
  return Boolean(rule) && edgeTypes(edge).has(rule.type);
}

function corroborationApplies() {
  return CORROBORABLE_KINDS.has(state.kind);
}

function edgeLabel(edge) {
  return KIND_LABEL[edge.kind] || edge.kind;
}

function mainEvidenceEdge(edge) {
  const types = edgeTypes(edge);
  const destination = state.byId.get(edge.dst);
  const hop = destination && destination.hop_first_seen;
  if (edge.kind === "contacts_domain") {
    return hop === 1 && types.has("network_contact")
      && types.has("tracker_list_confirmation");
  }
  if (edge.kind === "authorises_inventory_sale") {
    return hop > 1 && types.has("ads_txt_authorisation")
      && types.has("sellers_json_confirmation");
  }
  if (edge.kind === "resolves_to") {
    return (state.incoming.get(edge.src) || []).some((candidate) =>
      candidate.kind === "contacts_domain" && mainEvidenceEdge(candidate));
  }
  return false;
}

function disclosedDestination(edge) {
  if (edge.kind === "contacts_domain") {
    const domain = state.byId.get(edge.dst);
    return domain && domain.owner_entity_id;
  }
  return edge.dst;
}

function disclosedInProse(edge) {
  const dst = disclosedDestination(edge);
  if (!dst) return false;
  return (state.outgoing.get(edge.src) || []).some((candidate) =>
    candidate.kind === "discloses_relation_with" && candidate.dst === dst
      && edgeEvidence(candidate).some((ev) => !ev.negative));
}

function edgeTracks(edge) {
  return new Set((edge.evidence || []).map((ev) => ev.track || "personal_data"));
}

function edgeSubjects(edge) {
  return new Set(edgeEvidence(edge).map((ev) => ev.subject).filter(Boolean));
}

function edgeInView(edge) {
  if (!edgeEvidence(edge).length) return false;
  if (state.kind === "main") return mainEvidenceEdge(edge);
  if (state.kind === "contacts_domain") {
    return edge.kind === "contacts_domain" || edge.kind === "resolves_to";
  }
  return edge.kind === state.kind;
}

function edgePasses(edge) {
  if (!edgeInView(edge)) return false;
  if (edge.kind === "resolves_to") return true;
  if (state.kind === "main" && state.filters.prose
      && !disclosedInProse(edge)) return false;
  if (corroborationApplies() && state.filters.corroborated
      && !isCorroborated(edge)) return false;
  return true;
}

function nodePasses(node) {
  if (node.type === "generic" && !state.filters.generic) return false;
  if (node.type === "domain" && (!state.filters.domains
      || !["main", "contacts_domain"].includes(state.kind))) return false;
  if (node.type === "entity" && node.grounded === false
      && !state.filters.ungrounded) return false;
  return true;
}

function computeVisible() {
  const visible = new Set();
  for (const n of state.graph.nodes) {
    if (nodePasses(n) && n.hop_first_seen === 0) visible.add(n.id);
  }
  let changed = true;
  while (changed) {
    changed = false;
    for (const edge of state.graph.edges) {
      if (!visible.has(edge.src) || visible.has(edge.dst)
          || !edgePasses(edge)) continue;
      const node = state.byId.get(edge.dst);
      if (!node || !nodePasses(node)) continue;
      visible.add(edge.dst);
      changed = true;
    }
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
    const colour = KIND_COLOR[e.kind] || "#8c928e";
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

const RING_STROKE = "rgba(23,26,24,.1)";
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
        if (state.showAllLabels || match || n.id === state.selected || n.id === state.hovered
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
    ctx.strokeStyle = "rgba(153,111,40,.75)";
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    for (const [x, y, r] of rings) {
      ctx.moveTo(x + r + 2.2, y);
      ctx.arc(x, y, r + 2.2, 0, Math.PI * 2);
    }
    ctx.stroke();
  }
  for (const [x, y, r, match] of marked) {
    ctx.strokeStyle = match ? "#996f28" : "#171a18";
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.arc(x, y, r + 4, 0, Math.PI * 2);
    ctx.stroke();
  }
  return labelled;
}

function drawLabels(labelled, rect, k) {
  if (!labelled.length) return;
  ctx.font = "11px Aptos, Segoe UI, system-ui, sans-serif";
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
    ctx.fillStyle = "rgba(251,251,248,.88)";
    ctx.fillRect(box.x, box.y, box.w, box.h);
    ctx.fillStyle = n.hop === 0 ? "#171a18" : "#343a37";
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
    + edgeLabel(edge)
    + (dataTypes.length ? ` — ${dataTypes.slice(0, 4).join(", ")}` : "");
  card.append(what);

  const parts = [...edgeSources(edge)].map((s) => SOURCE_LABEL[s] || s);
  if (edgeEvidence(edge).some((e) => e.evidence_type === "sellers_json_confirmation")) {
    parts.push("cross-checked");
  }
  if (state.kind === "main" && edge.kind !== "resolves_to") {
    parts.push(disclosedInProse(edge) ? "disclosed in prose" : "not found in prose");
  }
  for (const t of edgeTracks(edge)) if (TRACK_LABEL[t]) parts.push(TRACK_LABEL[t]);
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
    ["Outgoing propositions", out, "out"],
    ["Incoming propositions", inc, "in"],
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

function shownKinds() {
  const buttons = [...$("kind-nav").querySelectorAll("button")];
  return new Set(buttons.filter((b) => !b.hidden).map((b) => b.dataset.kind));
}

function renderLegend() {
  const kinds = shownKinds();
  const legend = $("legend");
  legend.innerHTML = "";
  const rows = [
    [NODE_COLOR.target, "analysed target", "", null],
    [NODE_COLOR.entity, "named organisation", "", null],
    [UNGROUNDED_COLOR, "named, but no register knows it", "", null],
    [NODE_COLOR.generic, "generic category", "", null],
    [KIND_COLOR.discloses_relation_with, "disclosed relation", "", "discloses_relation_with"],
    [KIND_COLOR.lists_vendor, "vendor listing", "", "lists_vendor"],
    [KIND_COLOR.authorises_inventory_sale, "sale authorisation", "", "authorises_inventory_sale"],
    [KIND_COLOR.contacts_domain, "observed contact", "", "contacts_domain"],
    ["", "not analysed", "ring", null],
  ].filter(([, , , kind]) => kind === null || kinds.has(kind));
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
    if (["discloses_relation_with", "lists_vendor",
      "authorises_inventory_sale", "contacts_domain"].includes(e.kind)) {
      recipients.add(e.dst);
    }
  }
  const named = (id) => {
    const n = state.byId.get(id);
    if (!n || n.type === "target") return false;
    return state.kind === "contacts_domain" ? n.type === "domain" : n.type !== "domain";
  };
  const arrangements = state.graph.edges.filter(
    (e) => edgePasses(e) && e.kind !== "resolves_to"
      && state.visible.has(e.src) && state.visible.has(e.dst),
  ).length;
  $("stat-recipients").textContent = [...recipients].filter(named).length;
  $("stat-edges").textContent = arrangements;
  const labels = {
    main: "typed propositions shown",
    discloses_relation_with: "disclosed relations",
    lists_vendor: "vendor listings",
    authorises_inventory_sale: "sale authorisations",
    contacts_domain: "observed contacts",
  };
  $("stat-edges-label").textContent = labels[state.kind];
  $("stat-parties-label").textContent = state.kind === "contacts_domain"
    ? "domains shown" : "organisations shown";
  $("stat-crawled").textContent = (snapshot && snapshot.progress.crawled) || 0;
  renderVerification(snapshot);
}

function renderVerification(snapshot) {
  const box = $("verify");
  const sellers = (snapshot && snapshot.reconciliation) || {};
  const trackers = (snapshot && snapshot.tracker_check) || {};
  const pruned = (snapshot && snapshot.pruned) || {};
  const lines = [];
  if (sellers.checked) {
    lines.push([`${(sellers.counts || {}).confirmed || 0} of ${sellers.checked} `
      + "inventory arrangements confirmed by the receiving party's sellers.json",
    sellers.counts || {}, RECONCILE_ORDER, RECONCILE_LABEL]);
  }
  if (trackers.checked) {
    lines.push([`${(trackers.counts || {}).confirmed || 0} of ${trackers.checked} `
      + "contacted domains recognised by a tracker list",
    trackers.counts || {}, TRACKER_ORDER, TRACKER_LABEL]);
  }
  if (!lines.length && !pruned.dropped) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  $("verify-summary").textContent = snapshot && snapshot.corroborated_only
    ? "Main findings retain tracker-list-confirmed contacts on the first hop "
      + "and account-matched inventory authorisations on later hops."
    : "Every arrangement is drawn, corroborated or not.";
  const rows = $("verify-rows");
  rows.innerHTML = "";
  for (const [heading, counts, order, labels] of lines) {
    const head = document.createElement("div");
    head.className = "what";
    head.textContent = heading;
    rows.append(head);
    for (const status of order) {
      const n = counts[status];
      if (!n) continue;
      const row = document.createElement("div");
      row.className = "src";
      row.textContent = `${n} — ${labels[status] || status}`;
      rows.append(row);
    }
  }
  if (pruned.dropped) {
    const head = document.createElement("div");
    head.className = "what";
    head.textContent = `${pruned.dropped} parties left out by the rule`;
    const note = document.createElement("div");
    note.className = "src";
    note.textContent = `${pruned.half_met || 0} of them met one record but not `
      + "the other";
    rows.append(head, note);
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
        time_limit: state.timeLimit * 60,
        requests,
        cmp,
        evidence_kind: state.kind,
        corroborated_only: state.kind === "main",
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
  renderEmptyState();
}

function renderEmptyState() {
  const box = $("empty");
  const collected = state.graph.nodes.length > 0;
  const shown = state.graph.edges.some(edgePasses);
  const withheld = collected && !shown && corroborationApplies()
    && state.filters.corroborated && state.graph.edges.some(edgeInView);
  box.hidden = collected && (shown || !withheld);
  if (box.hidden) return;
  box.innerHTML = "";
  const head = document.createElement("strong");
  const note = document.createElement("span");
  if (withheld) {
    head.textContent = "Nothing here is confirmed by a second record";
    note.textContent = "Arrangements were collected, but no receiving party's "
      + "sellers.json names the party that supplies it. Untick the filter to "
      + "see the claims that stand on one side only.";
  } else {
    head.textContent = "No evidence network collected yet";
    note.textContent = "Choose a depth and collect evidence from this source.";
  }
  box.append(head, note);
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
  ["f-generic", "generic"], ["f-domains", "domains"],
  ["f-ungrounded", "ungrounded"], ["f-corroborated", "corroborated"],
]) {
  $(id).addEventListener("change", (ev) => {
    state.filters[key] = ev.target.checked;
    rebuild(false);
    renderStats(lastSnapshot);
    renderEmptyState();
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
$("kind-nav").addEventListener("click", (ev) => {
  const button = ev.target.closest("button");
  if (!button || button.hidden) return;
  selectKind(button.dataset.kind);
  rebuild(false);
  renderStats(lastSnapshot);
  renderLegend();
  renderEmptyState();
});
$("fit-view").addEventListener("click", fit);
$("show-labels").addEventListener("change", (ev) => {
  state.showAllLabels = ev.target.checked;
  draw();
});
$("f-rule").addEventListener("change", (ev) => {
  state.filters.prose = ev.target.checked;
  rebuild(false);
  renderStats(lastSnapshot);
  renderEmptyState();
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
function selectKind(kind) {
  state.kind = kind;
  let label = "";
  for (const b of $("kind-nav").querySelectorAll("button")) {
    const on = b.dataset.kind === kind;
    b.classList.toggle("on", on);
    if (on) label = b.textContent.trim().toLowerCase();
  }
  $("run").textContent = kind === "main"
    ? "Collect all evidence" : `Collect ${label}`;
  const rule = CORROBORABLE_KINDS.get(kind);
  $("f-corroborated-box").hidden = !rule;
  if (rule) $("f-corroborated-label").textContent = rule.label;
  $("rule-box").hidden = kind !== "main";
}

// The bridge decides which propositions are evidence about this kind of site:
// a store listing's traffic is the store's, and a vendor registry read off a
// publisher names the registry rather than the publisher.
function applyProfile(profile) {
  state.siteKind = profile.site_kind || "website";
  const views = profile.graph_views || [];
  if (!views.length) return;
  for (const button of $("kind-nav").querySelectorAll("button")) {
    button.hidden = !views.includes(button.dataset.kind);
  }
  selectKind(views.includes(state.kind) ? state.kind
    : (profile.default_view && views.includes(profile.default_view)
      ? profile.default_view : views[0]));
  $("site-kind").textContent = SITE_KIND_LABEL[state.siteKind] || state.siteKind;
  $("site-kind").hidden = false;
  renderLegend();
}

async function loadProfile() {
  try {
    const resp = await fetch(
      `${BRIDGE}/site?url=${encodeURIComponent(state.origin)}`);
    if (!resp.ok) return;
    applyProfile(await resp.json());
  } catch (e) {
    // The bridge being down is reported when a walk is attempted.
  }
}

$("run").addEventListener("click", start);
$("stop").addEventListener("click", stop);

// -------------------------------------------------------------------- boot ---
(function init() {
  const params = new URLSearchParams(location.search);
  state.origin = params.get("url") || "";
  const tab = params.get("tab");
  state.tabId = tab === null ? null : Number(tab);
  state.kind = params.get("kind") || "main";
  if (!Object.keys(KIND_COLOR).includes(state.kind) && state.kind !== "main") {
    state.kind = "main";
  }
  selectKind(state.kind);
  $("origin").textContent = state.origin || "no URL supplied";
  $("origin").title = state.origin;
  $("run").disabled = !state.origin;
  renderLegend();
  renderDetail();
  renderEditBox();
  resize();
  if (state.origin) loadProfile();
})();
