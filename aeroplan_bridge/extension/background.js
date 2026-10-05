// Connects to the native host, which receives search jobs from aeroplan_value.py, and runs each job
// by loading Aeroplan's award search page in a dedicated tab of this signed-in browser profile.

const HOST = "ca.mihaicosma.aeroplan_bridge";
const SCRIPT_VERSION = "1.9";  // reported in hello, to confirm Chrome is running this copy of the script
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
  port.postMessage({ type: "hello", version: `${chrome.runtime.getManifest().version}/${SCRIPT_VERSION}` });
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
  // The page keeps hidden copies of its login screens, so pick the element the user can actually
  // click: scrolled into view and topmost at its own centre.
  const locate = async (filter) => {
    const { result } = await send("Runtime.evaluate", { returnByValue: true, expression: `(() => {
      for (const e of [...document.querySelectorAll('input, button, a')].filter(${filter})) {
        if (e.offsetParent === null) continue;
        e.scrollIntoView({ block: "center" });
        const r = e.getBoundingClientRect(), x = r.x + r.width / 2, y = r.y + r.height / 2;
        const hit = document.elementFromPoint(x, y);
        if (hit && (hit === e || e.contains(hit) || hit.contains(e))) return [x, y];
      }
      return null; })()` });
    return result.value;
  };
  const centre = selector => locate(`e => e.matches(${JSON.stringify(selector)})`);
  // The control whose label is exactly `label` (the code screen also shows "Resend").
  const centreOfLabel = label => locate(`e => (e.value || e.innerText || "").trim() === ${JSON.stringify(label)}`);
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
      const go = await centreOfLabel("Submit") || await centreOfLabel("Continue");  // email section, phone section
      note(`sign-in: code form at ${JSON.stringify({ box, go })}`);
      if (!box || !go) throw new Error("code form not found");
      await click(box);
      // Select any earlier code so typing replaces it.
      await send("Runtime.evaluate", { expression: "document.activeElement?.select?.()" });
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
    if (user && password && submit) {
      if (message.user && message.password) {
        await click(user); await typeText(message.user); await pause(400);
        await click(password); await typeText(message.password); await pause(600);
        note("sign-in: typed; submitting");
      } else {
        // Chrome's saved login: a real click releases the autofilled values to the page.
        await click(user); await pause(400); await click(password); await pause(600);
        note("sign-in: using saved login; submitting");
      }
      await click(submit);
      await pause(15000);
    } else if (!message.emailCode) {
      throw new Error(`sign-in form not found on ${tab.url}`);
    }
    if (message.emailCode) {
      // Ask for the one-time code by email, which aeroplan_value.py reads from Gmail.
      const sendCode = await centreOfLabel("Send Code");
      note(`sign-in: email Send Code at ${JSON.stringify(sendCode)}`);
      if (sendCode) {
        await click(sendCode);
        await pause(4000);
        return reply({ type: "result", id: message.id, ok: false, state: "email_code_sent" });
      }
    }
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
