const KEY = "veritrace.audits";
const REMOVED_KEY = "veritrace.removed";
const MAX_ENTRIES = 100;

// The API has no way to list audits, by design, so each browser keeps its own list.
export type HistoryEntry = {
  id: string;
  text: string;
  createdAt: string;
  result: string | null;
};

export function loadHistory(): HistoryEntry[] {
  try {
    const entries = JSON.parse(localStorage.getItem(KEY) ?? "[]");
    return Array.isArray(entries) ? entries : [];
  } catch {
    return [];
  }
}

function loadRemoved(): string[] {
  try {
    const ids = JSON.parse(localStorage.getItem(REMOVED_KEY) ?? "[]");
    return Array.isArray(ids) ? ids : [];
  } catch {
    return [];
  }
}

export function rememberAudit(entry: HistoryEntry) {
  if (loadRemoved().includes(entry.id)) return;
  const others = loadHistory().filter((item) => item.id !== entry.id);
  localStorage.setItem(KEY, JSON.stringify([entry, ...others].slice(0, MAX_ENTRIES)));
  // Lets the extension, if installed, copy this into its own list.
  window.dispatchEvent(new Event("veritrace:history-changed"));
}

/** Remove one audit from the list. Its id is kept, so a sync can't bring it back. */
export function forgetAudit(id: string) {
  localStorage.setItem(KEY, JSON.stringify(loadHistory().filter((item) => item.id !== id)));
  localStorage.setItem(REMOVED_KEY, JSON.stringify([...loadRemoved(), id].slice(-MAX_ENTRIES * 5)));
  window.dispatchEvent(new Event("veritrace:history-changed"));
}

export function forgetAudits() {
  localStorage.removeItem(KEY);
  window.dispatchEvent(new Event("veritrace:history-cleared"));
}
