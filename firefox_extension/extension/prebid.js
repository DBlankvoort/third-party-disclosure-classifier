// Bridge Prebid events from the page context to the extension.
(() => {
  const marker = "tpd-prebid-schain";
  const onMessage = (event) => {
    if (event.source !== window || !event.data || event.data.kind !== marker) return;
    browser.runtime.sendMessage({
      kind: "prebidSchains", schains: event.data.schains || [],
    }).catch(() => {});
  };
  window.addEventListener("message", onMessage);
  const script = document.createElement("script");
  script.src = browser.runtime.getURL("prebid-page.js");
  script.dataset.tpdPrebid = "1";
  (document.documentElement || document.head).appendChild(script);
})();
