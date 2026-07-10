"use strict";

const BRIDGE = "http://127.0.0.1:8765";

const MEDIA = ["prose", "structured", "machine_readable", "other_doc"];
const SPECIFICITIES = ["named", "category", "generic"];
const MEDIA_LABEL = {
  prose: "prose",
  structured: "structured",
  machine_readable: "machine-readable",
  other_doc: "other doc",
};

const ACTION_LABEL = {
  collect: "collected",
  be_shared: "shared",
  be_sold: "sold",
  use: "used",
  store: "stored",
};
const ACTION_COLOR = {
  collect: "#6ee7b7",
  be_shared: "#9ec5ff",
  be_sold: "#f0b35b",
  use: "#d8b4fe",
  store: "#f0abfc",
};
const NEG_COLOR = "#f87171";

const SOURCE_LABEL = {
  ads_txt: "Authorized ad sellers — ads.txt",
  sellers_json: "Ad-exchange sellers — sellers.json",
  tcf_gvl: "IAB-TCF consent vendors",
  vendors_json: "Declared vendors — vendors.json",
  cookie_table: "Cookie / tracking providers",
  vendor_table: "Vendor & sub-processor tables",
};
const SOURCE_SHORT = {
  ads_txt: "ad sellers",
  sellers_json: "exchange sellers",
  tcf_gvl: "TCF vendors",
  vendors_json: "vendors",
  cookie_table: "cookie providers",
  vendor_table: "sub-processors",
};

const ROLE_LABEL = {
  privacy_policy: "Privacy policy",
  cookie_policy: "Cookie policy",
  subprocessor_list: "Sub-processor list",
  vendor_list: "Vendor list",
  do_not_sell: "Do-not-sell / privacy choices",
  dpa: "Data processing agreement",
  partners_page: "Partners page",
  help_doc: "Help / FAQ",
  ads_txt: "ads.txt",
  app_ads_txt: "app-ads.txt",
  sellers_json: "sellers.json",
  vendors_json: "vendors.json",
  tcf_gvl: "IAB-TCF vendor list",
};

const $ = (id) => document.getElementById(id);

function prettyRole(role) {
  return ROLE_LABEL[role] || role.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function showStatus(text) {
  $("status").hidden = false;
  $("status-text").textContent = text;
  $("result").hidden = true;
  $("offline").hidden = true;
}

function showOffline() {
  $("status").hidden = true;
  $("result").hidden = true;
  $("offline").hidden = false;
  $("rescan").hidden = true;
}

async function activeTabUrl() {
  const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
  return tab ? tab.url : null;
}

async function analyze(url, force) {
  showStatus(force ? "Re-crawling…" : "Analyzing…");
  const q = new URLSearchParams({ url });
  if (force) q.set("force", "1");
  let data;
  try {
    const resp = await fetch(`${BRIDGE}/analyze?${q}`);
    data = await resp.json();
    if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
  } catch (err) {
    // A failed fetch to loopback almost always means the bridge isn't running.
    if (err instanceof TypeError) return showOffline();
    showStatus(`Error: ${err.message}`);
    return;
  }
  render(data);
}

function render(d) {
  $("status").hidden = true;
  $("offline").hidden = true;
  $("result").hidden = false;
  $("rescan").hidden = false;

  $("origin").textContent = d.origin;
  $("origin").title = d.origin;

  $("class-pill").textContent = d.typology_class || "no disclosure detected";
  $("source-pill").textContent =
    (d.cached ? "cached" : "live crawl") + ` · ${d.fetched_docs} doc(s) fetched`;

  const relations = d.sharing_relations || [];
  renderDocs(d.documents);
  renderMatrix(new Set(d.facets));
  renderRelations(relations, d.poligraph !== false);
  renderGraph(relations, d.origin);
  renderRights(d.rights || { links: {}, emails: [] }, d.origin);
  renderRollup("orgs", d.named_orgs);
  renderRollup("cats", d.category_terms);
}

function renderDocs(docs) {
  const ul = $("docs");
  ul.innerHTML = "";
  const recognised = docs.filter((x) => x.medium);
  $("docs-empty").hidden = recognised.length > 0;

  for (const doc of recognised) {
    const li = document.createElement("li");
    li.className = "doc";

    const dot = document.createElement("span");
    dot.className = "dot" + (doc.relevant ? " on" : "");
    dot.title = doc.relevant ? "discloses third parties" : "recognised, none found";

    const main = document.createElement("div");
    main.className = "doc-main";
    const role = document.createElement("div");
    role.className = "role";
    role.textContent = prettyRole(doc.role);
    const a = document.createElement("a");
    a.className = "durl";
    a.href = doc.url;
    a.target = "_blank";
    a.rel = "noreferrer";
    a.textContent = doc.url;
    a.title = doc.url;
    main.append(role, a);

    const tag = document.createElement("span");
    tag.className = `medium-tag ${doc.medium}`;
    tag.textContent = MEDIA_LABEL[doc.medium] || doc.medium;

    li.append(dot, main, tag);
    ul.append(li);
  }
}

function renderMatrix(facets) {
  const tbody = $("matrix").querySelector("tbody");
  tbody.innerHTML = "";
  for (const m of MEDIA) {
    const tr = document.createElement("tr");
    const th = document.createElement("th");
    th.textContent = MEDIA_LABEL[m];
    tr.append(th);
    for (const s of SPECIFICITIES) {
      const td = document.createElement("td");
      const hit = facets.has(`${m}:${s}`);
      td.className = "cell " + (hit ? "hit" : "miss");
      td.textContent = hit ? "●" : "·";
      td.title = `${m}:${s}` + (hit ? " — present" : " — not found");
      tr.append(td);
    }
    tbody.append(tr);
  }
}

function isProse(r) {
  return (r.sources || []).includes("policy");
}
function primarySource(r) {
  return (r.sources || []).find((s) => s !== "policy") || "policy";
}

function renderRelations(relations, enabled) {
  const ul = $("relations");
  ul.innerHTML = "";
  const third = relations.filter((r) => r.party === "third");
  const first = relations.filter((r) => r.party === "first");
  const prose = third.filter(isProse);
  const synth = third.filter((r) => !isProse(r));

  $("relations-off").hidden = enabled;
  $("relations-empty").hidden = third.length > 0;

  const groups = new Map();
  for (const r of prose) {
    if (!groups.has(r.entity)) {
      groups.set(r.entity, { examples: new Set(), items: [] });
    }
    const g = groups.get(r.entity);
    for (const ex of r.examples || []) g.examples.add(ex);
    g.items.push(r);
  }
  for (const [entity, g] of groups) {
    const li = document.createElement("li");
    li.className = "rel-card";

    const head = document.createElement("div");
    head.className = "rel-entity";
    const name = document.createElement("span");
    name.className = "rel-name";
    name.textContent = entity;
    head.append(name);
    if (g.examples.size) {
      const ex = document.createElement("span");
      ex.className = "rel-examples";
      ex.textContent = "e.g. " + [...g.examples].join(", ");
      head.append(ex);
    }
    li.append(head);

    for (const r of g.items) {
      const row = document.createElement("div");
      row.className = "rel-item" + (r.negative ? " neg" : "");
      row.title = r.text || "";

      const act = document.createElement("span");
      act.className = `act ${r.action}` + (r.negative ? " neg" : "");
      act.textContent = (r.negative ? "not " : "") + (ACTION_LABEL[r.action] || r.action);

      const data = document.createElement("span");
      data.className = "rel-data";
      data.textContent = r.data_type;

      row.append(act, data);
      if (r.purposes && r.purposes.length) {
        const p = document.createElement("span");
        p.className = "rel-purposes";
        p.textContent = "for " + r.purposes.join(", ");
        row.append(p);
      }
      li.append(row);
    }
    ul.append(li);
  }

  const bySource = new Map();
  for (const r of synth) {
    const s = primarySource(r);
    if (!bySource.has(s)) bySource.set(s, []);
    bySource.get(s).push(r);
  }
  for (const [source, items] of bySource) {
    const li = document.createElement("li");
    li.className = "rel-card";

    const head = document.createElement("div");
    head.className = "rel-entity";
    const name = document.createElement("span");
    name.className = "rel-name plain";
    name.textContent = SOURCE_LABEL[source] || source;
    const n = document.createElement("span");
    n.className = "rel-examples";
    n.textContent = `${items.length} entit${items.length === 1 ? "y" : "ies"} · ` +
      `${items[0].data_type} ${ACTION_LABEL[items[0].action] || items[0].action}`;
    head.append(name, n);
    li.append(head);

    const wrap = document.createElement("div");
    wrap.className = "taglist tight";
    for (const r of items) {
      const tag = document.createElement("span");
      tag.className = "tag" + (r.qualifier === "reseller" || r.qualifier === "intermediary"
        ? " dim" : "");
      tag.textContent = r.entity + (r.qualifier ? ` · ${r.qualifier}` : "");
      tag.title = [r.text, r.purposes && r.purposes.length ? "for " + r.purposes.join(", ") : ""]
        .filter(Boolean).join(" — ");
      wrap.append(tag);
    }
    li.append(wrap);
    ul.append(li);
  }

  const box = $("firstparty-box");
  box.hidden = first.length === 0;
  const count = $("firstparty-count");
  count.textContent = first.length;
  count.classList.toggle("zero", first.length === 0);
  const wrap = $("firstparty");
  wrap.innerHTML = "";
  for (const r of first) {
    const tag = document.createElement("span");
    tag.className = "tag" + (r.negative ? " neg" : "");
    tag.title = r.text || "";
    tag.textContent =
      `${r.data_type} · ${(r.negative ? "not " : "") + (ACTION_LABEL[r.action] || r.action)}`;
    wrap.append(tag);
  }
}

const GRAPH_MAX_DATA = 8;
const GRAPH_MAX_ENTS = 8;

function graphModel(relations, originHost) {
  const third = relations.filter((r) => r.party === "third");
  const prose = third.filter(isProse);
  const synth = third.filter((r) => !isProse(r));

  const entities = []; // {id, label, dim}
  const edges = [];    // {ent, data, action, negative, title}
  const entIdx = new Map();

  const proseEnts = [...new Set(prose.map((r) => r.entity))];
  for (const e of proseEnts.slice(0, GRAPH_MAX_ENTS)) {
    entIdx.set(e, entities.length);
    entities.push({ id: e, label: e });
  }
  const overflow = proseEnts.length - GRAPH_MAX_ENTS;
  if (overflow > 0) {
    entIdx.set("__more__", entities.length);
    entities.push({ id: "__more__", label: `+${overflow} more`, dim: true });
  }
  for (const r of prose) {
    const idx = entIdx.has(r.entity) ? entIdx.get(r.entity) : entIdx.get("__more__");
    edges.push({
      ent: idx, data: r.data_type, action: r.action, negative: r.negative,
      title: `${r.entity} ← ${r.data_type} (${ACTION_LABEL[r.action] || r.action}` +
        `${r.negative ? ", stated NOT" : ""})`,
    });
  }
  const bySource = new Map();
  for (const r of synth) {
    const s = primarySource(r);
    if (!bySource.has(s)) bySource.set(s, []);
    bySource.get(s).push(r);
  }
  for (const [source, items] of bySource) {
    const idx = entities.length;
    entities.push({
      id: source,
      label: `${items.length} ${SOURCE_SHORT[source] || source}`,
      dim: true,
    });
    const combos = new Map();
    for (const r of items) combos.set(`${r.data_type}|${r.action}`, r);
    for (const r of combos.values()) {
      edges.push({
        ent: idx, data: r.data_type, action: r.action, negative: false,
        title: `${items.length} × ${SOURCE_SHORT[source] || source} ← ${r.data_type}`,
      });
    }
  }

  const dataCount = new Map();
  for (const e of edges) dataCount.set(e.data, (dataCount.get(e.data) || 0) + 1);
  const dataNames = [...dataCount.keys()].sort((a, b) => dataCount.get(b) - dataCount.get(a));
  const dataIdx = new Map();
  const data = [];
  for (const nm of dataNames.slice(0, GRAPH_MAX_DATA)) {
    dataIdx.set(nm, data.length);
    data.push({ label: nm });
  }
  if (dataNames.length > GRAPH_MAX_DATA) {
    const rest = dataNames.length - GRAPH_MAX_DATA;
    dataIdx.set("__more__", data.length);
    data.push({ label: `+${rest} more`, dim: true });
  }
  const drawn = new Set();
  const finalEdges = [];
  for (const e of edges) {
    const di = dataIdx.has(e.data) ? dataIdx.get(e.data) : dataIdx.get("__more__");
    const key = `${di}|${e.ent}|${e.action}|${e.negative}`;
    if (drawn.has(key)) continue;
    drawn.add(key);
    finalEdges.push({ ...e, data: di });
  }
  return { site: originHost, data, entities, edges: finalEdges };
}

function truncate(s, n) {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

function svgEl(name, attrs) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

function renderGraph(relations, origin) {
  const svg = $("graph");
  svg.innerHTML = "";
  const model = graphModel(relations, origin.replace(/^https?:\/\//, ""));
  const empty = model.edges.length === 0;
  $("graph-empty").hidden = !empty;
  $("graph-wrap").hidden = empty;
  $("graph-legend").innerHTML = "";
  if (empty) return;

  const W = 340;
  const ROW = 24;
  const rows = Math.max(model.data.length, model.entities.length, 1);
  const H = rows * ROW + 30;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("width", "100%");

  const xSite = 12, xData = 152, xEnt = W - 12;
  const yFor = (i, n) => (H - n * ROW) / 2 + i * ROW + ROW / 2;
  const ySite = H / 2;

  const seenData = new Set(model.edges.map((e) => e.data));
  for (const di of seenData) {
    const y = yFor(di, model.data.length);
    svg.append(svgEl("path", {
      d: `M ${xSite + 5} ${ySite} C ${xSite + 60} ${ySite}, ${xData - 70} ${y}, ${xData - 42} ${y}`,
      stroke: "#3a4154", "stroke-width": 1.2, fill: "none",
    }));
  }
  for (const e of model.edges) {
    const y1 = yFor(e.data, model.data.length);
    const y2 = yFor(e.ent, model.entities.length);
    const color = e.negative ? NEG_COLOR : (ACTION_COLOR[e.action] || "#5b6478");
    const path = svgEl("path", {
      d: `M ${xData + 42} ${y1} C ${xData + 90} ${y1}, ${xEnt - 90} ${y2}, ${xEnt - 8} ${y2}`,
      stroke: color, "stroke-width": 1.4, fill: "none", opacity: 0.85,
    });
    if (e.negative) path.setAttribute("stroke-dasharray", "4 3");
    const t = svgEl("title", {});
    t.textContent = e.title;
    path.append(t);
    svg.append(path);
  }

  // Site node.
  svg.append(svgEl("circle", { cx: xSite, cy: ySite, r: 5, fill: "#6ea8fe" }));
  const siteLabel = svgEl("text", {
    x: xSite - 4, y: ySite - 10, fill: "#e8eaf0", "font-size": 10, "font-weight": 600,
  });
  siteLabel.textContent = truncate(model.site, 24);
  svg.append(siteLabel);

  // Data nodes.
  model.data.forEach((d, i) => {
    const y = yFor(i, model.data.length);
    const label = svgEl("text", {
      x: xData, y: y - 5, fill: d.dim ? "#9aa0b0" : "#e8eaf0",
      "font-size": 9.5, "text-anchor": "middle",
    });
    label.textContent = truncate(d.label, 18);
    const t = svgEl("title", {});
    t.textContent = d.label;
    label.append(t);
    svg.append(label);
  });

  // Entity nodes.
  model.entities.forEach((n, i) => {
    const y = yFor(i, model.entities.length);
    svg.append(svgEl("circle", {
      cx: xEnt - 4, cy: y, r: 3.5, fill: n.dim ? "#9aa0b0" : "#9ec5ff",
    }));
    const label = svgEl("text", {
      x: xEnt - 12, y: y - 5, fill: n.dim ? "#9aa0b0" : "#e8eaf0",
      "font-size": 9.5, "text-anchor": "end",
    });
    label.textContent = truncate(n.label, 22);
    const t = svgEl("title", {});
    t.textContent = n.label;
    label.append(t);
    svg.append(label);
  });

  // Legend chips.
  const actions = [...new Set(model.edges.map((e) => (e.negative ? "not" : e.action)))];
  const legend = $("graph-legend");
  for (const a of actions) {
    const chip = document.createElement("span");
    chip.className = "gl-chip";
    const sw = document.createElement("i");
    sw.style.background = a === "not" ? NEG_COLOR : (ACTION_COLOR[a] || "#5b6478");
    chip.append(sw, document.createTextNode(a === "not" ? "stated NOT" : ACTION_LABEL[a] || a));
    legend.append(chip);
  }
}

const INDUSTRY_OPTOUTS = [
  ["DAA WebChoices (ad opt-out)", "https://optout.aboutads.info/"],
  ["NAI opt-out", "https://optout.networkadvertising.org/"],
  ["Your Online Choices (EU)", "https://www.youronlinechoices.eu/"],
  ["Global Privacy Control", "https://globalprivacycontrol.org/"],
];

function dsarTemplate(origin) {
  return `To whom it may concern,

Regarding my personal data connected to my use of ${origin}, I request under applicable data-protection law (GDPR Art. 15/17/21; CCPA/CPRA):

1. Access to the personal data you hold about me;
2. The categories of third parties with whom it has been shared or sold;
3. Deletion of my personal data; and
4. That you stop selling or sharing my personal data with third parties.

Please respond within the statutory deadline.

Kind regards`;
}

function rightsRow(label, href, cls) {
  const a = document.createElement("a");
  a.className = "rbtn " + (cls || "");
  a.textContent = label;
  a.href = href;
  if (!href.startsWith("mailto:")) {
    a.target = "_blank";
    a.rel = "noreferrer";
  }
  return a;
}

function renderRights(rights, origin) {
  const box = $("rights");
  box.innerHTML = "";
  const links = rights.links || {};
  const emails = rights.emails || [];

  if (links.do_not_sell) {
    box.append(rightsRow("Opt out of sale / sharing on this site", links.do_not_sell, "primary"));
  } else {
    const p = document.createElement("p");
    p.className = "empty tight";
    p.textContent = "No do-not-sell / privacy-choices page found on this site.";
    box.append(p);
  }

  if (emails.length) {
    const subject = "Personal data access / deletion / opt-out request";
    const mailto = `mailto:${emails[0]}?subject=${encodeURIComponent(subject)}` +
      `&body=${encodeURIComponent(dsarTemplate(origin))}`;
    box.append(rightsRow(`Email privacy contact — ${emails[0]}`, mailto, "primary"));
  }

  const copy = document.createElement("button");
  copy.className = "rbtn";
  copy.textContent = "Copy data-request template";
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(dsarTemplate(origin));
      copy.textContent = "Copied ✓";
      setTimeout(() => { copy.textContent = "Copy data-request template"; }, 1600);
    } catch {
      copy.textContent = "Copy failed";
    }
  });
  box.append(copy);

  if (links.privacy_policy) {
    box.append(rightsRow("Read the privacy policy", links.privacy_policy));
  }
  if (links.cookie_policy) {
    box.append(rightsRow("Read the cookie policy", links.cookie_policy));
  }

  const h = document.createElement("p");
  h.className = "rights-sub";
  h.textContent = "Industry-wide opt-outs";
  box.append(h);
  for (const [label, url] of INDUSTRY_OPTOUTS) {
    box.append(rightsRow(label, url, "small"));
  }
}

function renderRollup(prefix, items) {
  const count = $(`${prefix}-count`);
  count.textContent = items.length;
  count.classList.toggle("zero", items.length === 0);
  const box = $(`${prefix}-box`);
  box.style.opacity = items.length ? "1" : "0.55";
  const wrap = $(prefix);
  wrap.innerHTML = "";
  for (const it of items) {
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = it;
    wrap.append(tag);
  }
}

// -- boot ------------------------------------------------------------------ //
let currentUrl = null;

(async function init() {
  currentUrl = await activeTabUrl();
  if (!currentUrl || !/^https?:/.test(currentUrl)) {
    showStatus("Open a website tab (http/https) to analyze it.");
    return;
  }
  analyze(currentUrl, false);
})();

$("rescan").addEventListener("click", () => {
  if (currentUrl) analyze(currentUrl, true);
});
