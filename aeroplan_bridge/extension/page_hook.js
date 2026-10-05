// Runs in the page's own JavaScript world at document_start, before Air Canada's scripts (and
// Akamai's wrapper) replace window.fetch. It copies award-search API responses to relay.js.
(() => {
  const forward = (url, body) => {
    if (!url || !url.includes("dbaas.aircanada.com")) return;
    let json;
    try { json = JSON.parse(body); } catch { return; }
    const isResult = json?.data?.airBoundGroups !== undefined;
    const noFlights = Array.isArray(json?.errors) && json.errors.some(e => e.code === "7959");
    if (isResult || noFlights) {
      window.postMessage({ source: "aeroplan-bridge", search: location.search, noFlights, body: json }, location.origin);
    }
  };

  const originalFetch = window.fetch;
  window.fetch = async function (...args) {
    const url = typeof args[0] === "string" ? args[0] : args[0]?.url;
    const response = await originalFetch.apply(this, args);
    response.clone().text().then(text => forward(url, text)).catch(() => {});
    return response;
  };

  const originalOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this.addEventListener("load", () => {
      if (this.responseType === "" || this.responseType === "text") forward(String(url), this.responseText);
    });
    return originalOpen.call(this, method, url, ...rest);
  };
})();
