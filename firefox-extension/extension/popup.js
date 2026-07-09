"use strict";

// The local bridge (server/server.py). Loopback only — see manifest permissions.
const BRIDGE = "http://127.0.0.1:8765";

const MEDIA = ["prose", "structured", "machine_readable", "other_doc"];
const SPECIFICITIES = ["named", "category", "generic"];
const MEDIA_LABEL = {
  prose: "prose",
  structured: "structured",
  machine_readable: "machine-readable",
  other_doc: "other doc",
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

  // Headline: the typology class + provenance.
  $("class-pill").textContent = d.typology_class || "no disclosure detected";
  $("source-pill").textContent =
    (d.cached ? "cached" : "live crawl") + ` · ${d.fetched_docs} doc(s) fetched`;

  renderDocs(d.documents);
  renderMatrix(new Set(d.facets));
  renderRollup("orgs", d.named_orgs);
  renderRollup("cats", d.category_terms);
}

// (1) WHERE — one row per document the tool kept for this origin.
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

// (2) WHAT — medium × specificity matrix of the aggregate (union) facet set.
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
