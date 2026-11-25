// tidytabs helper: runs commands from the tidytabs command line tool.
// The browser starts the native host (com.tidytabs.host) when this worker connects.
// The open connection keeps this service worker alive.

const HOST = "com.tidytabs.host";
let port = null;

function browserName() {
  const brands = (navigator.userAgentData && navigator.userAgentData.brands) || [];
  return navigator.brave || brands.some((b) => /Brave/i.test(b.brand)) ? "brave" : "chrome";
}

function connect() {
  if (port) return;
  port = chrome.runtime.connectNative(HOST);
  port.onMessage.addListener(handle);
  port.onDisconnect.addListener(() => {
    console.warn("tidytabs host disconnected:", chrome.runtime.lastError && chrome.runtime.lastError.message);
    port = null;
    setTimeout(connect, 5000);
  });
  port.postMessage({ type: "hello", browser: browserName(), version: chrome.runtime.getManifest().version });
}

async function handle(msg) {
  try {
    const result = await run(msg.cmd, msg.args || {});
    port.postMessage({ id: msg.id, result });
  } catch (e) {
    port.postMessage({ id: msg.id, error: String((e && e.message) || e) });
  }
}

async function run(cmd, args) {
  if (cmd === "ping") return { ok: true, version: chrome.runtime.getManifest().version };

  if (cmd === "list") {
    const windows = await chrome.windows.getAll({ populate: true, windowTypes: ["normal"] });
    return windows.map((w) => ({
      id: w.id,
      tabs: w.tabs.map((t) => ({
        id: t.id,
        title: t.title || "",
        url: t.url || t.pendingUrl || "",
        lastAccessed: t.lastAccessed || null,
      })),
    }));
  }

  if (cmd === "move") {
    // chrome.tabs.move keeps the page: no reload, no lost scroll position or history.
    let ids = args.tabIds;
    let windowId = args.windowId;
    const missing = [];
    let moved = 0;
    if (windowId == null) {
      while (ids.length && windowId == null) {
        const first = ids[0];
        ids = ids.slice(1);
        try {
          const w = await chrome.windows.create({ tabId: first, focused: false });
          windowId = w.id;
          moved++;
        } catch (e) {
          missing.push(first);
        }
      }
    }
    for (const id of ids) {
      try {
        await chrome.tabs.move(id, { windowId, index: -1 });
        moved++;
      } catch (e) {
        missing.push(id);
      }
    }
    return { moved, missing, windowId };
  }

  if (cmd === "focus") {
    if (args.tabId != null) await chrome.tabs.update(args.tabId, { active: true });
    await chrome.windows.update(args.windowId, { focused: true });
    return { ok: true };
  }

  throw new Error("unknown command: " + cmd);
}

connect();
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
