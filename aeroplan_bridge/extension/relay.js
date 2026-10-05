// Runs in the extension's isolated world on the same page and passes captured responses to the
// background service worker, which page scripts cannot reach directly.
window.addEventListener("message", event => {
  if (event.source !== window || event.data?.source !== "aeroplan-bridge") return;
  chrome.runtime.sendMessage({ type: "award-response", ...event.data, href: location.href });
});
