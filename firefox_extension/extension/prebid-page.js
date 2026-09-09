// Runs in the page context so it can inspect the publisher's Prebid instance.
(() => {
  if (window.__tpdPrebidCapture) return;
  window.__tpdPrebidCapture = true;
  const seen = new Set();

  function findChains(value, found = [], visited = new WeakSet()) {
    if (!value || found.length >= 100) return found;
    if (typeof value !== "object") return found;
    if (visited.has(value)) return found;
    visited.add(value);
    if (Array.isArray(value)) {
      for (const item of value) findChains(item, found, visited);
      return found;
    }
    if (Array.isArray(value.nodes)
        && value.nodes.some((node) => node && node.asi && node.sid)) {
      found.push({ver: value.ver || "", complete: value.complete, nodes: value.nodes});
    }
    for (const item of Object.values(value)) findChains(item, found, visited);
    return found;
  }

  function publish(payload) {
    const requestId = payload && (payload.auctionId || payload.bidderRequestId
      || payload.requestId || payload.bidId) || "";
    const chains = findChains(payload).map((chain) => ({
      ...chain, request_id: requestId,
    })).filter((chain) => {
      const key = JSON.stringify(chain);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    if (chains.length) window.postMessage({kind: "tpd-prebid-schain", schains: chains}, "*");
  }

  function attach() {
    const pbjs = window.pbjs;
    if (!pbjs || typeof pbjs.onEvent !== "function") return false;
    for (const event of ["bidRequested", "beforeRequestBids", "bidResponse"]) {
      try { pbjs.onEvent(event, publish); } catch (e) { /* unsupported event */ }
    }
    try { publish(pbjs.getBidResponses && pbjs.getBidResponses()); } catch (e) { /* no bids */ }
    return true;
  }

  if (!attach()) {
    let attempts = 0;
    const timer = setInterval(() => {
      attempts += 1;
      if (attach() || attempts >= 60) clearInterval(timer);
    }, 500);
  }
})();
