"use strict";

const BRIDGE = "http://127.0.0.1:8765";
const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const origin = params.get("url") || "";
const tabId = Number(params.get("tab"));
let kind = params.get("kind") || "discloses_relation_with";

const META = {
  discloses_relation_with: {
    title: "Prose disclosures", colour: "#526d82",
    proposition: "discloses_relation_with",
    meaning: "An analysed textual source attributed to a party asserts a relation involving the named organisation.",
    scope: "Privacy policies, cookie policies, data-processing documents, and structured disclosure tables are presented with their supporting text and fields.",
  },
  lists_vendor: {
    title: "Vendor listings", colour: "#755c7f", proposition: "lists_vendor",
    meaning: "A captured consent interface or vendor record lists the named organisation and any purposes exposed by that source.",
    scope: "Consent interfaces and machine-readable vendor records are retained as distinct evidence records.",
  },
  authorises_inventory_sale: {
    title: "Ad-tech authorisations", colour: "#996f28",
    proposition: "authorises_inventory_sale",
    meaning: "A publisher advertising record authorises an advertising-system account to sell or resell inventory.",
    scope: "Authorisations use the registry's own direct or reseller relationship and do not establish personal-data transmission.",
  },
  contacts_domain: {
    title: "Web traffic", colour: "#27786d", proposition: "contacts_domain",
    meaning: "The browser contacted the recorded domain during the bounded observation session and consent state.",
    scope: "Request metadata describes contact. It does not establish payload, purpose, legal role, or lawful basis.",
  },
};
const PROSE = new Set(["policy", "cookie_table", "vendor_table"]);
const VENDORS = new Set(["cmp", "tcf_gvl", "vendors_json", "sellers_json"]);

// A view the site's kind does not carry is withdrawn from the nav, and asking
// for one anyway falls back to the first view that does speak about this site.
function applyViews(data) {
  const views = data.views || Object.keys(META);
  for (const button of $("view-nav").querySelectorAll("button")) {
    button.hidden = !views.includes(button.dataset.kind);
  }
  if (views.length && !views.includes(kind)) {
    kind = views[0];
    params.set("kind", kind);
    history.replaceState(null, "", `?${params}`);
  }
  for (const button of $("view-nav").querySelectorAll("button")) {
    button.classList.toggle("on", button.dataset.kind === kind);
  }
}

function sources(relation) { return new Set(relation.sources || []); }
function belongs(relation) {
  const held = sources(relation);
  if (kind === "discloses_relation_with") return [...held].some((x) => PROSE.has(x));
  if (kind === "lists_vendor") return [...held].some((x) => VENDORS.has(x));
  return kind === "authorises_inventory_sale" && held.has("ads_txt");
}
function uniqueRelations(relations) {
  const seen = new Set();
  return relations.filter((relation) => {
    if (!belongs(relation) || relation.party === "first") return false;
    const key = [relation.entity, relation.data_type, relation.action,
      relation.direction, [...sources(relation)].sort().join(",")].join("|");
    if (seen.has(key)) return false;
    seen.add(key); return true;
  });
}
function badge(text) { const span = document.createElement("span"); span.className = "badge"; span.textContent = text; return span; }

function recordCard(relation) {
  const card = document.createElement("article"); card.className = "record";
  const head = document.createElement("div"); head.className = "record-head";
  const left = document.createElement("div"); const title = document.createElement("h3");
  title.textContent = relation.entity; const badges = document.createElement("div"); badges.className = "badges";
  for (const value of [...sources(relation), relation.negative ? "negative" : "positive"])
    badges.append(badge(value.replaceAll("_", " ")));
  left.append(title, badges); head.append(left); card.append(head);
  const dl = document.createElement("dl");
  for (const [label, value] of [
    ["Data category", relation.data_type], ["Purpose", (relation.purposes || []).join(", ")],
    ["Relationship", relation.qualifier], ["Subject", relation.subject],
  ]) {
    if (!value) continue;
    const dt = document.createElement("dt"); dt.textContent = label;
    const dd = document.createElement("dd"); dd.textContent = value.replaceAll("_", " "); dl.append(dt, dd);
  }
  if (dl.children.length) card.append(dl);
  if (relation.text) { const quote = document.createElement("blockquote"); quote.textContent = relation.text; card.append(quote); }
  return card;
}

function trafficCard(party) {
  const card = document.createElement("article"); card.className = "record";
  const title = document.createElement("h3"); title.textContent = party.domain;
  const badges = document.createElement("div"); badges.className = "badges";
  badges.append(badge(`${party.requests || 0} requests`),
    badge(party.entity ? `attributed to ${party.entity}` : "organisation not attributed"),
    badge(party.consent || "consent state unknown"));
  card.append(title, badges);
  const dl = document.createElement("dl");
  const dt = document.createElement("dt"); dt.textContent = "Resolution basis";
  const dd = document.createElement("dd"); dd.textContent = party.entity
    ? party.basis.replaceAll("_", " ") : "none";
  dl.append(dt, dd); card.append(dl); return card;
}

function renderSources(documents) {
  const box = $("sources"); box.innerHTML = "";
  const accepted = kind === "discloses_relation_with"
    ? new Set(["privacy_policy", "cookie_policy", "dpa", "subprocessor_list", "partners_page"])
    : kind === "lists_vendor" ? new Set(["vendor_list", "tcf_gvl", "vendors_json", "sellers_json"])
      : kind === "authorises_inventory_sale" ? new Set(["ads_txt", "app_ads_txt"]) : new Set();
  for (const doc of (documents || []).filter((item) => accepted.has(item.role))) {
    const link = document.createElement("a"); link.className = "source";
    link.href = doc.url; link.target = "_blank"; link.rel = "noreferrer";
    link.textContent = doc.role.replaceAll("_", " "); box.append(link);
  }
  if (!box.children.length) box.textContent = kind === "contacts_domain"
    ? "Bounded browser observation" : "No source of this kind was collected.";
}

function render(data) {
  applyViews(data);
  const meta = META[kind]; document.documentElement.style.setProperty("--active", meta.colour);
  $("title").textContent = meta.title; document.title = `${meta.title} — Disclosure Lens`;
  // An app is named by its own identifier: the store's host is not the target.
  $("origin").textContent = (data.target_name
    && ["play_store_app", "app_store_app"].includes(data.site_kind))
    ? data.target_name : data.origin;
  $("proposition").textContent = meta.proposition;
  $("scope").textContent = meta.scope; $("meaning").textContent = meta.meaning;
  const records = kind === "contacts_domain"
    ? data.traffic_contacts || [] : uniqueRelations(data.sharing_relations || []);
  const claims = {
    discloses_relation_with: `${records.length} organisations named in disclosed relations`,
    lists_vendor: `${records.length} organisations listed in vendor records`,
    authorises_inventory_sale: `${records.length} organisations authorised to sell inventory`,
    contacts_domain: `${records.length} domains contacted during this observation`,
  };
  $("claim").textContent = claims[kind];
  const box = $("records"); box.innerHTML = "";
  for (const item of records) box.append(kind === "contacts_domain" ? trafficCard(item) : recordCard(item));
  $("empty").hidden = records.length > 0; renderSources(data.documents);
  const email = ((data.rights || {}).emails || [])[0]; $("email").hidden = !email;
  $("no-email").hidden = Boolean(email); if (email) { $("email").textContent = email; $("email").href = `mailto:${email}`; }
}

async function context() {
  const requests = await browser.runtime.sendMessage({kind: "getRequests", tabId}).catch(() => ({requests: []}));
  const cmp = await browser.runtime.sendMessage({kind: "getCmp", tabId}).catch(() => ({cmp: null}));
  return {requests: requests.requests || [], cmp: cmp.cmp || null};
}
async function analyse(force = false) {
  try {
    const extra = await context();
    const response = await fetch(`${BRIDGE}/analyze`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({url: origin, force, ...extra})});
    const data = await response.json(); if (!response.ok) throw new Error(data.error); render(data);
  } catch (error) { $("offline").hidden = false; $("offline").textContent = `Analysis unavailable: ${error.message}`; }
}

for (const button of $("view-nav").querySelectorAll("button")) {
  button.addEventListener("click", () => { kind = button.dataset.kind; params.set("kind", kind); history.replaceState(null, "", `?${params}`); for (const item of $("view-nav").querySelectorAll("button")) item.classList.toggle("on", item === button); analyse(false); });
}
$("open-graph").addEventListener("click", () => browser.tabs.create({url: browser.runtime.getURL("graph.html") + `?url=${encodeURIComponent(origin)}&tab=${tabId}&kind=${kind}`}));
$("rescan").addEventListener("click", () => analyse(true));
$("back").addEventListener("click", (event) => { event.preventDefault(); window.close(); });
analyse(false);
