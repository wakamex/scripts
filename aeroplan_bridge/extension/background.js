// Connects to the native host, which receives search jobs from aeroplan_value.py, and runs each job
// by loading Aeroplan's award search page in a dedicated tab of this signed-in browser profile.

const HOST = "ca.mihaicosma.aeroplan_bridge";
const SEARCH_URL = "https://www.aircanada.com/aeroplan/redeem/availability/outbound";
const PAGE_TIMEOUT_MS = 60000;   // time allowed for the page to load and search
const SETTLE_MS = 4000;          // later result pages arrive shortly after the first

let port = null;
let job = null;  // { id, tabId, params, responses, timer, settle }

function connect() {
  port = chrome.runtime.connectNative(HOST);
  port.onMessage.addListener(onHostMessage);
  port.onDisconnect.addListener(() => {
    console.warn("native host disconnected", chrome.runtime.lastError?.message);
    port = null;
  });
  port.postMessage({ type: "hello", version: chrome.runtime.getManifest().version });
}

function reply(message) {
  if (port) port.postMessage(message);
}

function note(text) {
  reply({ type: "log", text });
}

function searchUrl({ origin, destination, date, adults }) {
  const q = new URLSearchParams({
    org0: origin, dest0: destination, departureDate0: date,
    ADT: String(adults || 1), YTH: "0", CHD: "0", INF: "0", INS: "0", lang: "en-CA", tripType: "O",
  });
  return `${SEARCH_URL}?${q}`;
}

function matchesJob(search) {
  const q = new URLSearchParams(search);
  return q.get("org0") === job.params.origin && q.get("dest0") === job.params.destination
    && q.get("departureDate0") === job.params.date;
}

function finish(result) {
  if (!job) return;
  clearTimeout(job.timer);
  clearTimeout(job.settle);
  reply({ type: "result", id: job.id, ...result });
  job = null;
}

async function bridgeTab() {
  const tabs = await chrome.tabs.query({ url: "https://www.aircanada.com/*" });
  return tabs.find(t => t.url?.startsWith(SEARCH_URL)) || tabs[0] || null;
}

async function runSearch(message) {
  if (job) {
    reply({ type: "result", id: message.id, ok: false, error: "busy" });
    return;
  }
  job = { id: message.id, params: message, responses: [] };
  job.timer = setTimeout(() => finish({ ok: false, error: "timeout", responses: job.responses }), PAGE_TIMEOUT_MS);
  const url = searchUrl(message);
  const tab = await bridgeTab();
  if (tab) {
    job.tabId = tab.id;
    await chrome.tabs.update(tab.id, { url });
  } else {
    const created = await chrome.tabs.create({ url, active: false, pinned: true });
    job.tabId = created.id;
  }
}

async function status(message) {
  const tab = await bridgeTab();
  reply({ type: "status", id: message.id, busy: Boolean(job), tab: tab ? { url: tab.url, title: tab.title } : null });
}

// Sign-in types into the bridge tab through the DevTools protocol, which produces the real key and
// mouse events Aeroplan's login form requires. Credentials pass through and are never stored.
async function signIn(message) {
  note("sign-in: received");
  const tab = await bridgeTab();
  note(`sign-in: tab ${tab ? tab.url : "none"}`);
  if (!tab) return reply({ type: "result", id: message.id, ok: false, error: "no aircanada.com tab" });
  const target = { tabId: tab.id };
  const send = (method, params) => chrome.debugger.sendCommand(target, method, params);
  const pause = ms => new Promise(r => setTimeout(r, ms));
  const visible = selector => `[...document.querySelectorAll('${selector}')].find(e => e.offsetParent !== null)`;
  const centre = async selector => {
    const { result } = await send("Runtime.evaluate", { returnByValue: true, expression:
      `(() => { const e = ${visible(selector)}; if (!e) return null; const r = e.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; })()` });
    return result.value;
  };
  // The visible button, input or link whose label is exactly `label` (the page also shows "Resend").
  const centreOfLabel = async label => {
    const { result } = await send("Runtime.evaluate", { returnByValue: true, expression:
      `(() => { const e = [...document.querySelectorAll('button, input[type=submit], input[type=button], a')]
          .find(e => e.offsetParent !== null && (e.value || e.innerText || "").trim() === ${JSON.stringify(label)});
        if (!e) return null; const r = e.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; })()` });
    return result.value;
  };
  const click = async ([x, y]) => {
    for (const type of ["mousePressed", "mouseReleased"]) {
      await send("Input.dispatchMouseEvent", { type, x, y, button: "left", clickCount: 1 });
    }
  };
  const typeText = async text => {
    for (const ch of text) {
      await send("Input.dispatchKeyEvent", { type: "keyDown", text: ch, key: ch, unmodifiedText: ch });
      await send("Input.dispatchKeyEvent", { type: "keyUp", key: ch });
      await pause(40 + Math.random() * 60);
    }
  };
  try {
    note(`sign-in: attaching to tab ${tab.id} ${tab.url}`);
    await chrome.debugger.attach(target, "1.3");
    note("sign-in: attached");
    if (message.code) {
      // Second step: the one-time code Aeroplan sends by text or email after the password.
      const box = await centre('input[name="code"]') || await centre('input[placeholder="Enter Code"]');
      const go = await centreOfLabel("Continue");
      note(`sign-in: code form at ${JSON.stringify({ box, go })}`);
      if (!box || !go) throw new Error("code form not found");
      await click(box);
      // Select any earlier code so typing replaces it.
      await send("Runtime.evaluate", { expression: `${visible('input[name="code"]')}?.select()` });
      await typeText(message.code); await pause(500);
      await click(go);
      await pause(15000);
      const done = await chrome.tabs.get(tab.id);
      return reply({ type: "result", id: message.id, ok: !done.url.includes("/clogin/"), url: done.url, title: done.title });
    }
    const user = await centre('input[autocomplete="username"]');
    const password = await centre('input[autocomplete="current-password"]');
    const submit = await centre('input[type="submit"][value="Sign in"]');
    note(`sign-in: form at ${JSON.stringify({ user, password, submit })}`);
    if (!user || !password || !submit) throw new Error(`sign-in form not found on ${tab.url}`);
    await click(user); await typeText(message.user); await pause(400);
    await click(password); await typeText(message.password); await pause(600);
    note("sign-in: typed; submitting");
    await click(submit);
    await pause(15000);
    const after = await chrome.tabs.get(tab.id);
    reply({ type: "result", id: message.id, ok: !after.url.includes("/clogin/"), url: after.url, title: after.title });
  } catch (e) {
    note(`sign-in: failed ${e.message || e}`);
    reply({ type: "result", id: message.id, ok: false, error: String(e.message || e) });
  } finally {
    chrome.debugger.detach(target).catch(() => {});
  }
}

function onHostMessage(message) {
  if (message.type !== "status") note(`received ${message.type}`);
  if (message.type === "search") runSearch(message);
  else if (message.type === "status") status(message);
  else if (message.type === "signin") signIn(message);
  else if (message.type === "reload") chrome.runtime.reload();  // re-read the extension from disk
}

chrome.runtime.onMessage.addListener((message, sender) => {
  if (message.type !== "award-response" || !job || sender.tab?.id !== job.tabId || !matchesJob(message.search)) return;
  job.responses.push(message.body);
  clearTimeout(job.settle);
  job.settle = setTimeout(() => finish({ ok: true, href: message.href, responses: job.responses }), message.noFlights ? 0 : SETTLE_MS);
});

chrome.tabs.onUpdated.addListener((tabId, change) => {
  if (job && tabId === job.tabId && change.url?.includes("/clogin/")) {
    finish({ ok: false, error: "signed_out" });
  }
});

// An open native port keeps this service worker alive. If the host is missing or exits, the worker
// is eventually suspended, so a periodic alarm wakes it to reconnect.
chrome.alarms.create("reconnect", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener(() => { if (!port) connect(); });
chrome.runtime.onStartup.addListener(() => {});
chrome.runtime.onInstalled.addListener(() => {});
connect();
