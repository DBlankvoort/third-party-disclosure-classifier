// Records the hosts each tab contacts

const MAX_PER_TAB = 3000;

// tabId -> { origin, requests: [{url, type}], seen: Set }
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

function reset(tabId, origin) {
  perTab.set(tabId, { origin, requests: [], seen: new Set() });
  cmpByTab.delete(tabId);
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
    const key = `${host}|${type}`;
    if (rec.seen.has(key)) return;
    rec.seen.add(key);
    rec.requests.push({ url, type });
  },
  { urls: ["<all_urls>"] },
);

browser.tabs.onRemoved.addListener((tabId) => perTab.delete(tabId));
browser.tabs.onRemoved.addListener((tabId) => cmpByTab.delete(tabId));

browser.runtime.onMessage.addListener((msg, sender) => {
  if (msg && msg.kind === "getRequests") {
    const rec = perTab.get(msg.tabId);
    return Promise.resolve({
      origin: rec ? rec.origin : "",
      requests: rec ? rec.requests : [],
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
  return false;
});

