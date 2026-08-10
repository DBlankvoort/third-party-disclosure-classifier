// Capture the vendor list a consent dialog holds.

(() => {
  const CALL_TIMEOUT = 2000;

  function locator() {
    let f = window;
    while (f) {
      try {
        if (f.frames.__tcfapiLocator) return f;
      } catch (e) {
        // continue
      }
      if (f === window.top) break;
      f = f.parent;
    }
    return null;
  }

  function tcfCall(command, parameter) {
    return new Promise((resolve) => {
      const host = locator();
      if (!host) return resolve(null);
      const callId = `tpd-${command}-${Math.random().toString(36).slice(2)}`;
      let done = false;

      const onMessage = (event) => {
        let data = event.data;
        if (typeof data === "string") {
          try {
            data = JSON.parse(data);
          } catch (e) {
            return;
          }
        }
        const ret = data && data.__tcfapiReturn;
        if (!ret || ret.callId !== callId) return;
        done = true;
        window.removeEventListener("message", onMessage);
        resolve(ret.success === false ? null : ret.returnValue);
      };

      window.addEventListener("message", onMessage);
      host.postMessage(
        { __tcfapiCall: { command, version: 2, callId, parameter } },
        "*",
      );
      setTimeout(() => {
        if (done) return;
        window.removeEventListener("message", onMessage);
        resolve(null);
      }, CALL_TIMEOUT);
    });
  }

  // Vendor ids the dialog covers
  function vendorIds(tcData) {
    const ids = new Set();
    const vendor = (tcData && tcData.vendor) || {};
    for (const bucket of [vendor.consents, vendor.legitimateInterests]) {
      for (const id of Object.keys(bucket || {})) {
        const n = parseInt(id, 10);
        if (n > 0) ids.add(n);
      }
    }
    return ids;
  }

  async function fromTcf() {
    const tcData = await tcfCall("getTCData");
    const gvl = await tcfCall("getVendorList");
    const vendors = (gvl && gvl.vendors) || {};
    const ids = vendorIds(tcData);
    const out = [];
    const keys = ids.size ? [...ids] : Object.keys(vendors).map(Number);
    for (const id of keys) {
      const v = vendors[String(id)];
      if (!v || !v.name) continue;
      out.push({
        id,
        name: v.name,
        purposes: [...(v.purposes || []), ...(v.legIntPurposes || [])],
      });
    }
    const cmpId = tcData && tcData.cmpId;
    return out.length ? { vendors: out, source: "tcf", cmp: cmpId ? `cmpId ${cmpId}` : "" } : null;
  }

  const DIALOG_SELECTORS = [
    "#onetrust-consent-sdk", "#onetrust-pc-sdk", "#CybotCookiebotDialog",
    "#didomi-host", ".qc-cmp2-container", "[id^='sp_message_container']",
    ".truste_box_overlay", "#usercentrics-root", "[class*='cookie-banner']",
    "[id*='cookie-consent']", "[aria-label*='consent' i]",
    "[aria-label*='cookie' i]",
  ];
  const MAX_NAME = 60;
  const MAX_DOM_NAMES = 1000;

  function fromDom() {
    const names = [];
    const seen = new Set();
    const roots = [];
    for (const sel of DIALOG_SELECTORS) {
      let found;
      try {
        found = document.querySelectorAll(sel);
      } catch (e) {
        continue;
      }
      for (const el of found) roots.push(el);
    }
    for (const root of roots) {
      const cells = root.querySelectorAll(
        "li, td:first-child, th:first-child, [class*='vendor'], [class*='partner']",
      );
      for (const cell of cells) {
        const text = (cell.textContent || "").trim().replace(/\s+/g, " ");
        if (!text || text.length > MAX_NAME) continue;
        if (cell.querySelector("li, table, p")) continue;
        const key = text.toLowerCase();
        if (seen.has(key)) continue;
        seen.add(key);
        names.push({ name: text });
        if (names.length >= MAX_DOM_NAMES) break;
      }
    }
    return names.length ? { vendors: names, source: "dom", cmp: "" } : null;
  }

  return (async () => {
    let payload = null;
    try {
      payload = await fromTcf();
    } catch (e) {
      payload = null;
    }
    if (!payload) {
      try {
        payload = fromDom();
      } catch (e) {
        payload = null;
      }
    }
    payload = payload || { vendors: [], source: "none", cmp: "" };
    payload.url = location.href;
    try {
      await browser.runtime.sendMessage({ kind: "cmpVendors", payload });
    } catch (e) {
      // Popup closed before the capture finished.
    }
    return payload.vendors.length;
  })();
})();
