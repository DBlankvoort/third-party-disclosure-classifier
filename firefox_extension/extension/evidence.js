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
    scope: "Authorisations use the registry's own direct or reseller relationship and do not establish personal-data transmission. Each one is checked against the ad system's own sellers.json, which either names this site as a seller or does not: an entry may be withheld as confidential, so silence is not a denial.",
  },
  contacts_domain: {
    title: "Web traffic", colour: "#27786d", proposition: "contacts_domain",
    meaning: "A bounded browser session sent one or more requests to the recorded domain.",
    scope: "This proves destination contact only. Request values and bodies are deliberately not retained, so it does not establish that personal data was transmitted, nor payload, purpose, legal role, or lawful basis. Tracker-list status describes a domain generally, never this request.",
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
function badge(text, cls) { const span = document.createElement("span"); span.className = cls ? `badge ${cls}` : "badge"; span.textContent = text; return span; }

const CORROBORATION = {
  confirmed: ["sellers.json confirms", "confirmed"],
  absent: ["not named in sellers.json", "unconfirmed"],
  confidential_only: ["sellers.json withholds every seller", "unconfirmed"],
  no_sellers_json: ["no sellers.json published", "unconfirmed"],
  not_collected: ["sellers.json not read", "unconfirmed"],
};

const TRACKER = {
  confirmed: ["recognised as a tracker", "confirmed"],
  known_not_tracking: ["listed, but not as a tracker", "unconfirmed"],
  unlisted: ["on no tracker list we hold", "unconfirmed"],
};

function recordCard(relation) {
  const card = document.createElement("article"); card.className = "record";
  const head = document.createElement("div"); head.className = "record-head";
  const left = document.createElement("div"); const title = document.createElement("h3");
  title.textContent = relation.entity; const badges = document.createElement("div"); badges.className = "badges";
  for (const value of [...sources(relation), relation.negative ? "negative" : "positive"])
    badges.append(badge(value.replaceAll("_", " ")));
  const corroboration = CORROBORATION[relation.corroboration];
  if (corroboration) badges.append(badge(corroboration[0], corroboration[1]));
  left.append(title, badges); head.append(left); card.append(head);
  const dl = document.createElement("dl");
  for (const [label, value] of [
    ["Data category", relation.data_type], ["Purpose", (relation.purposes || []).join(", ")],
    ["Relationship", relation.qualifier], ["Subject", relation.subject],
    ["Account", relation.seller_id], ["Listed as", relation.seller_type],
    ["Confirmed by", relation.corroborated_by],
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
  const listing = TRACKER[party.tracker];
  if (listing) badges.append(badge(listing[0], listing[1]));
  card.append(title, badges);
  const dl = document.createElement("dl");
  const dt = document.createElement("dt"); dt.textContent = "Resolution basis";
  const dd = document.createElement("dd"); dd.textContent = party.entity
    ? party.basis.replaceAll("_", " ") : "none";
  dl.append(dt, dd);
  if ((party.tracker_categories || []).length) {
    const ct = document.createElement("dt"); ct.textContent = "Listed as";
    const cd = document.createElement("dd");
    cd.textContent = party.tracker_categories.join(", ");
    dl.append(ct, cd);
  }
  if (party.infrastructure) badges.append(badge(
    "shared infrastructure; operator contact may not identify the data recipient",
    "unconfirmed"));
  card.append(dl); return card;
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
  // Inventory authorisations are seed-relative and single-hop. Their evidence
  // cards show more than a network rendering would, so do not offer that graph.
  $("open-graph").hidden = kind === "authorises_inventory_sale";
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
    authorises_inventory_sale: `${records.length} organisations authorised to sell inventory`
      + (((data.corroboration || {}).counts || {}).confirmed
        ? `, ${data.corroboration.counts.confirmed} confirmed by the ad system's own record` : ""),
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
