// Runs on the Veritrace website, and keeps its audit list and the extension's list the same.
const KEY = "veritrace.audits";
const REMOVED_KEY = "veritrace.removed";
const MAX_ENTRIES = 100;

function readWebsite(key = KEY) {
  try {
    const entries = JSON.parse(localStorage.getItem(key) ?? "[]");
    return Array.isArray(entries) ? entries : [];
  } catch {
    return [];
  }
}

function merge(first, second) {
  const byId = new Map();
  for (const entry of [...first, ...second]) {
    const old = byId.get(entry.id);
    byId.set(entry.id, { ...old, ...entry, result: entry.result ?? old?.result ?? null });
  }
  return [...byId.values()]
    .filter((entry) => entry.text)
    // Ties need a fixed order, or the two lists would keep rewriting each other.
    .sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)) || a.id.localeCompare(b.id))
    .slice(0, MAX_ENTRIES);
}

async function sync() {
  const { audits = [], removed = [] } = await chrome.storage.local.get(["audits", "removed"]);
  const gone = [...new Set([...removed, ...readWebsite(REMOVED_KEY)])];
  const merged = merge(audits, readWebsite()).filter((entry) => !gone.includes(entry.id));
  const text = JSON.stringify(merged);
  if (text !== JSON.stringify(audits) || gone.length !== removed.length) {
    await chrome.storage.local.set({ audits: merged, removed: gone });
  }
  localStorage.setItem(REMOVED_KEY, JSON.stringify(gone));
  if (text !== localStorage.getItem(KEY)) {
    localStorage.setItem(KEY, text);
    window.dispatchEvent(new Event("veritrace:history-updated"));
  }
}

window.addEventListener("veritrace:history-changed", sync);
window.addEventListener("veritrace:history-cleared", () => chrome.storage.local.remove("audits"));
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.audits?.newValue) sync();
});
sync();
