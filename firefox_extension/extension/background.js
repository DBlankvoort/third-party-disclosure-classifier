// Records the hosts each tab contacts

const MAX_PER_TAB = 3000;
const MAX_SCHAINS_PER_TAB = 500;

// tabId -> { origin, requests, schains, seen: Set }
const perTab = new Map();
// tabId -> vendor list
const cmpByTab = new Map();

function originOf(url) {
  try {
    const u = new URL(url);
    return `${u.protocol}//${u.host}`;
  } catch (e) {
    return "";
  }
}

function minimizedRequestUrl(url) {
  try {
    const u = new URL(url);
    if (u.protocol !== "http:" && u.protocol !== "https:") return "";
    return `${u.protocol}//${u.host}/`;
  } catch (e) {
    return "";
  }
}

function reset(tabId, origin) {
  perTab.set(tabId, {
    origin, requests: [], schains: [], seen: new Set(), chainSeen: new Set(),
  });
  cmpByTab.delete(tabId);
}

function addChains(rec, chains) {
  for (const chain of chains) {
    if (rec.schains.length >= MAX_SCHAINS_PER_TAB) return;
    const key = JSON.stringify([chain.source, chain.request_id,
      chain.complete, chain.nodes]);
    if (rec.chainSeen.has(key)) continue;
    rec.chainSeen.add(key);
    rec.schains.push(chain);
  }
}

function supplyChains(value, found = [], visited = new WeakSet()) {
  if (!value || found.length >= 100) return found;
  if (typeof value !== "object") return found;
  if (visited.has(value)) return found;
  visited.add(value);
  if (Array.isArray(value)) {
    for (const item of value) supplyChains(item, found, visited);
    return found;
  }
  const nodes = value.nodes;
  if (Array.isArray(nodes) && nodes.some((node) => node && node.asi && node.sid)) {
    found.push({ver: value.ver || "", complete: value.complete, nodes});
  }
  for (const item of Object.values(value)) supplyChains(item, found, visited);
  return found;
}

function requestText(details) {
  const body = details.requestBody || {};
  if (body.formData) return new URLSearchParams(Object.entries(body.formData)
    .flatMap(([key, values]) => (values || []).map((value) => [key, value]))).toString();
  const chunks = body.raw || [];
  let size = 0;
  const bytes = [];
  for (const chunk of chunks) {
    if (!chunk.bytes) continue;
    const part = new Uint8Array(chunk.bytes);
    size += part.length;
    if (size > 512 * 1024) return "";
    for (const byte of part) bytes.push(byte);
  }
  return bytes.length ? new TextDecoder().decode(new Uint8Array(bytes)) : "";
}

function chainsFromRequest(details) {
  const candidates = [];
  const text = requestText(details);
  if (text) candidates.push(text);
  try {
    const url = new URL(details.url);
    for (const value of url.searchParams.values()) candidates.push(value);
  } catch (e) { /* malformed request URL */ }
  const found = [];
  for (const candidate of candidates) {
    try {
      supplyChains(JSON.parse(candidate), found);
    } catch (e) {
      try {
        supplyChains(JSON.parse(decodeURIComponent(candidate)), found);
      } catch (ignored) { /* not JSON */ }
    }
  }
  return found.map((chain) => ({
    ...chain, source: "network", request_url: details.url,
    request_id: details.requestId || "",
  }));
}

browser.webRequest.onBeforeRequest.addListener(
  (details) => {
    const { tabId, url, type } = details;
    if (tabId < 0) return;
    if (type === "main_frame") {
      reset(tabId, originOf(url));
      return;
    }
    const rec = perTab.get(tabId);
    if (!rec || rec.requests.length >= MAX_PER_TAB) return;
    let host;
    try {
      host = new URL(url).host;
    } catch (e) {
      return;
    }
    addChains(rec, chainsFromRequest(details));
    const key = `${url}|${type}`;
    if (rec.seen.has(key)) return;
    rec.seen.add(key);
    rec.requests.push({
      url: minimizedRequestUrl(url), type, requestId: details.requestId || "",
      originUrl: minimizedRequestUrl(details.originUrl || ""),
      documentUrl: minimizedRequestUrl(details.documentUrl || ""),
      observedMs: Date.now(),
    });
  },
  { urls: ["<all_urls>"] },
  ["requestBody"],
);

browser.webRequest.onBeforeRedirect.addListener(
  (details) => {
    const rec = perTab.get(details.tabId);
    if (!rec) return;
    const request = rec.requests.find((item) => item.requestId === details.requestId);
    if (request) {
      request.redirected = true;
      request.redirectUrl = minimizedRequestUrl(details.redirectUrl || "");
    }
  },
  {urls: ["<all_urls>"]},
);

browser.tabs.onRemoved.addListener((tabId) => perTab.delete(tabId));
browser.tabs.onRemoved.addListener((tabId) => cmpByTab.delete(tabId));

browser.runtime.onMessage.addListener((msg, sender) => {
  if (msg && msg.kind === "getRequests") {
    const rec = perTab.get(msg.tabId);
    return Promise.resolve({
      origin: rec ? rec.origin : "",
      requests: rec ? rec.requests : [],
      schains: rec ? rec.schains : [],
    });
  }
  if (msg && msg.kind === "clearRequests") {
    perTab.delete(msg.tabId);
    cmpByTab.delete(msg.tabId);
    return Promise.resolve({ ok: true });
  }
  if (msg && msg.kind === "cmpVendors") {
    const tabId = sender && sender.tab ? sender.tab.id : -1;
    if (tabId >= 0 && msg.payload) cmpByTab.set(tabId, msg.payload);
    return Promise.resolve({ ok: true });
  }
  if (msg && msg.kind === "getCmp") {
    return Promise.resolve({ cmp: cmpByTab.get(msg.tabId) || null });
  }
  if (msg && msg.kind === "prebidSchains") {
    const tabId = sender && sender.tab ? sender.tab.id : -1;
    const rec = perTab.get(tabId);
    if (rec && Array.isArray(msg.schains)) {
      addChains(rec, msg.schains.slice(0, 100).map((chain) => ({
        ...chain, source: "prebid",
      })));
    }
    return Promise.resolve({ok: true});
  }
  return false;
});
