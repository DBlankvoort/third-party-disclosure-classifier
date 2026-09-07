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
    // Only vendors this dialog's consent string actually covers. Falling back
    // to the whole vendor list would report the ~1000-entry global registry as
    // though this site had named every one of them.
    if (!ids.size) return null;
    for (const id of [...ids]) {
      const v = vendors[String(id)];
      if (!v || !v.name) {
        if (id > 0) out.push({ id });
        continue;
      }
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

  function queryRoots() {
    const roots = [document];
    for (let i = 0; i < roots.length; i++) {
      const root = roots[i];
      for (const el of root.querySelectorAll("*")) {
        if (el.shadowRoot) roots.push(el.shadowRoot);
        if (el.tagName === "IFRAME") {
          try {
            if (el.contentDocument) roots.push(el.contentDocument);
          } catch (e) {
            // Cross-origin consent frames require separate instrumentation.
          }
        }
      }
    }
    return roots;
  }

  function dialogRoots() {
    const dialogs = [];
    for (const searchRoot of queryRoots()) {
      for (const sel of DIALOG_SELECTORS) {
        try {
          for (const el of searchRoot.querySelectorAll(sel)) dialogs.push(el);
        } catch (e) {
          // A vendor-specific selector may be unsupported by an older page.
        }
      }
    }
    return dialogs;
  }

  function fromDom() {
    const names = [];
    const seen = new Set();
    const roots = dialogRoots();
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

  async function revealVendorList() {
    const controlText = /^(?:manage|show|view|see|customi[sz]e|partners?|vendors?|preferences?|settings)/i;
    for (const root of dialogRoots()) {
      for (const control of root.querySelectorAll("button, a, [role='button']")) {
        if (!controlText.test((control.textContent || "").trim())) continue;
        try {
          control.click();
          await new Promise((resolve) => setTimeout(resolve, 350));
          return;
        } catch (e) {
          // Continue through accessible controls without changing consent.
        }
      }
    }
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
        if (!payload) {
          await revealVendorList();
          payload = fromDom();
        }
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
