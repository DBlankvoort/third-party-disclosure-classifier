"use strict";

const BRIDGE = "http://127.0.0.1:8765";
const $ = (id) => document.getElementById(id);
let pageUrl = "";
let pageTab = null;

const VIEW_KIND = {
  prose: "discloses_relation_with",
  vendors: "lists_vendor",
  adtech: "authorises_inventory_sale",
  traffic: "contacts_domain",
};
// How each kind of site is named.
const SITE_KIND = {
  website: {label: "website"},
  data_broker: {label: "vendor-side site"},
  play_store_app: {label: "Play Store app", app: true},
  app_store_app: {label: "App Store app", app: true},
};
const PROSE_SOURCES = new Set(["policy", "cookie_table", "vendor_table"]);
const VENDOR_SOURCES = new Set(["cmp", "tcf_gvl", "vendors_json", "sellers_json"]);

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

async function activeTab() {
  const [tab] = await browser.tabs.query({active: true, currentWindow: true});
  return tab || null;
}

async function observedRequests() {
  if (!pageTab) return [];
  try {
    const result = await browser.runtime.sendMessage({kind: "getRequests", tabId: pageTab.id});
    return (result && result.requests) || [];
  } catch (error) {
    return [];
  }
}

async function capturedCmp() {
  if (!pageTab) return null;
  try {
    await browser.tabs.executeScript(pageTab.id, {file: "cmp.js"});
  } catch (error) {
    return null;
  }
  for (let attempt = 0; attempt < 12; attempt += 1) {
    try {
      const result = await browser.runtime.sendMessage({kind: "getCmp", tabId: pageTab.id});
      if (result && result.cmp) return result.cmp;
    } catch (error) {
      return null;
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  return null;
}

function sourcesOf(relation) {
  return new Set(relation.sources || []);
}

function hasSource(relation, allowed) {
  return [...sourcesOf(relation)].some((source) => allowed.has(source));
}

function relationCount(relations, predicate) {
  return new Set(relations.filter(predicate).map((relation) => relation.entity)).size;
}

function roles(docs, accepted) {
  const labels = {
    privacy_policy: "privacy policy", cookie_policy: "cookie policy",
    dpa: "data processing agreement", subprocessor_list: "subprocessor list",
    vendor_list: "vendor list", tcf_gvl: "TCF vendor list",
    vendors_json: "vendor record", sellers_json: "sellers.json",
    ads_txt: "ads.txt", app_ads_txt: "app-ads.txt",
  };
  return [...new Set((docs || []).filter((doc) => doc.ok !== false && accepted.has(doc.role))
    .map((doc) => labels[doc.role] || doc.role.replaceAll("_", " ")))];
}

function siteKind(data) {
  return SITE_KIND[data.site_kind] || SITE_KIND.website;
}

// The store's own traffic and advertising registry belong to Google or Apple,
// and a vendor registry read off a publisher names the registry rather than the
// publisher, so each site kind carries only the views that speak about it.
function applyViews(data) {
  const views = new Set(data.views || Object.values(VIEW_KIND));
  for (const el of document.querySelectorAll("[data-view]")) {
    const kind = VIEW_KIND[el.dataset.view];
    el.hidden = Boolean(kind) && !views.has(kind);
  }
}

function openWorkspace(view) {
  const kind = VIEW_KIND[view] || "main";
  const page = view === "main" ? "graph.html" : "evidence.html";
  browser.tabs.create({
    url: browser.runtime.getURL(page)
      + `?url=${encodeURIComponent(pageUrl)}&tab=${pageTab ? pageTab.id : ""}`
      + `&kind=${encodeURIComponent(kind)}`,
  });
}

function renderRights(rights, origin) {
  const emails = rights.emails || [];
  const links = rights.links || {};
  const email = emails[0] || "";
  const subject = `Data rights request concerning ${origin}`;
  const href = email
    ? `mailto:${email}?subject=${encodeURIComponent(subject)}`
    : (links.do_not_sell || links.privacy_policy || "#");
  $("rights-action").href = href;
  if (!href.startsWith("mailto:") && href !== "#") {
    $("rights-action").target = "_blank";
    $("rights-action").rel = "noreferrer";
  }
  $("rights-detail").textContent = "Manage data access, correction, and deletion requests";
  $("contact").hidden = !email;
  if (email) {
    $("privacy-email").textContent = email;
    $("privacy-email").href = href;
  }
}

function render(data) {
  $("status").hidden = true;
  $("offline").hidden = true;
  $("result").hidden = false;
  $("rescan").hidden = false;
  applyViews(data);
  const kind = siteKind(data);
  // An app is named by its own identifier: the store's host is not the target.
  $("origin").textContent = (kind.app && data.target_name)
    ? data.target_name : data.origin.replace(/^https?:\/\//, "");
  $("site-kind").textContent = kind.label;
  $("site-kind").hidden = false;
  $("observed-at").textContent = new Date().toLocaleDateString("en-GB", {
    day: "numeric", month: "short", year: "numeric",
  });

  const relations = data.sharing_relations || [];
  const prose = relationCount(relations, (relation) => hasSource(relation, PROSE_SOURCES));
  const vendors = relationCount(relations, (relation) => hasSource(relation, VENDOR_SOURCES));
  const adtech = relationCount(relations, (relation) => sourcesOf(relation).has("ads_txt"));
  const domains = new Set((data.traffic_contacts || []).map((contact) => contact.domain));
  $("prose-count").textContent = prose;
  $("vendor-count").textContent = vendors;
  $("adtech-count").textContent = adtech;
  $("traffic-count").textContent = domains.size;

  const proseRoles = roles(data.documents, new Set([
    "privacy_policy", "cookie_policy", "dpa", "subprocessor_list", "partners_page",
  ]));
  const vendorRoles = roles(data.documents, new Set(["vendor_list", "tcf_gvl", "vendors_json", "sellers_json"]));
  const adtechRoles = roles(data.documents, new Set(["ads_txt", "app_ads_txt"]));
  if (proseRoles.length) $("prose-sources").textContent = proseRoles.join("\n");
  if (vendorRoles.length || (data.cmp_parties || []).length) {
    $("vendor-sources").textContent = [...vendorRoles, ...((data.cmp_parties || []).length
      ? ["consent interface"] : [])].join("\n");
  }
  if (adtechRoles.length) $("adtech-sources").textContent = adtechRoles.join("\n");
  renderRights(data.rights || {links: {}, emails: []}, data.origin);
}

async function analyse(force = false) {
  showStatus(force ? "Collecting fresh evidence…" : "Analysing collected sources…");
  const [requests, cmp] = await Promise.all([observedRequests(), capturedCmp()]);
  try {
    const response = await fetch(`${BRIDGE}/analyze`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({url: pageUrl, force, requests, cmp}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    render(data);
  } catch (error) {
    if (error instanceof TypeError) showOffline();
    else showStatus(`Error: ${error.message}`);
  }
}

document.querySelectorAll("[data-view]").forEach((button) => {
  button.addEventListener("click", () => {
    if (button.dataset.view !== "main") openWorkspace(button.dataset.view);
  });
});
$("open-graph").addEventListener("click", () => openWorkspace("main"));
$("rescan").addEventListener("click", () => analyse(true));
$("copy-email").addEventListener("click", async () => {
  await navigator.clipboard.writeText($("privacy-email").textContent);
  $("copy-email").textContent = "Copied";
});

(async function init() {
  pageTab = await activeTab();
  pageUrl = pageTab && pageTab.url;
  if (!pageUrl || !/^https?:\/\//.test(pageUrl)) {
    showStatus("Open an HTTP or HTTPS page to analyse it.");
    return;
  }
  analyse(false);
}());
