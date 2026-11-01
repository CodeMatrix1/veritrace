importScripts("config.js");

const POLL_ALARM = "poll-audit";
// Chrome can stop a service worker when a request takes over 30 seconds.
const SUBMIT_TIMEOUT_MS = 25000;
const MAX_HISTORY = 100;
const WARNING_COLOR = "#b45309";
const BADGES = {
  supported: ["OK", "#047857"],
  contradicted: ["NO", "#b91c1c"],
};
const RESULT_TITLES = {
  supported: "Confirmed by sources",
  contradicted: "Incorrect, according to sources",
  misleading: "Misleading",
  unverified_insufficient: "Not enough evidence",
  unverified_conflicting: "Sources disagree",
  partially_supported: "Partly confirmed",
  assessment_incomplete: "Check incomplete",
  no_factual_claims: "Nothing to check",
};

chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});

async function saveState(state) {
  await chrome.storage.session.set({ current: state });
}

async function loadState() {
  return (await chrome.storage.session.get("current")).current ?? null;
}

async function setBadge(text, color) {
  await chrome.action.setBadgeText({ text });
  await chrome.action.setBadgeBackgroundColor({ color });
}

function notify(id, title, message) {
  chrome.notifications.create(id, {
    type: "basic",
    iconUrl: "icons/128.png",
    title,
    message: message.slice(0, 160),
  });
}

async function remember(entry) {
  const { audits = [] } = await chrome.storage.local.get("audits");
  const old = audits.find((item) => item.id === entry.id);
  const others = audits.filter((item) => item.id !== entry.id);
  await chrome.storage.local.set({
    audits: [{ ...old, ...entry }, ...others].slice(0, MAX_HISTORY),
  });
}

async function fail(state, error) {
  await chrome.alarms.clear(POLL_ALARM);
  await saveState({ ...state, status: "failed", error });
  await setBadge("!", WARNING_COLOR);
  if (state.auditId) await remember({ id: state.auditId, result: "failed" });
}

function failureMessage(error) {
  return error.name === "TimeoutError"
    ? "The service took too long to respond. It may be waking up, so please try again."
    : "The verification service could not be reached.";
}

/** Return the page's title and address, without its query, for the report's link. */
function pageDetails(info, tab) {
  try {
    const url = new URL(info.pageUrl);
    if (!["http:", "https:"].includes(url.protocol)) return null;
    const pageUrl = url.origin + url.pathname;
    return { page_url: pageUrl, page_title: tab?.title?.slice(0, 300) || null };
  } catch {
    return null;
  }
}

async function postAudit(text, context, fresh) {
  const response = await fetch(`${API_URL}/api/audits`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type: "text", content: text, fresh, ...(context ? { context } : {}) }),
    signal: AbortSignal.timeout(SUBMIT_TIMEOUT_MS),
  });
  const body = await response.json().catch(() => null);
  if (response.ok && body?.audit_id) return { auditId: body.audit_id };
  if (response.status === 429 && typeof body?.detail === "string") return { error: body.detail };
  return { error: "The check could not be started. Please try again." };
}

async function submit(selected, context = null, fresh = false) {
  const text = selected?.trim() ?? "";
  const state = {
    id: crypto.randomUUID(),
    status: "submitting",
    text,
    context,
    auditId: null,
    error: null,
  };
  if (text.length < MIN_CHARS) return fail(state, "Please select more text.");
  if (text.length > MAX_CHARS) {
    return fail(state, `Please select fewer than ${MAX_CHARS} characters.`);
  }

  await chrome.alarms.clear(POLL_ALARM);
  await saveState(state);
  await setBadge("...", "#57534e");

  let result;
  try {
    result = await postAudit(text, context, fresh);
  } catch (error) {
    result = { error: failureMessage(error) };
  }
  // A newer selection replaces this one, so its result no longer matters.
  if ((await loadState())?.id !== state.id) return;
  if (result.error) return fail(state, result.error);

  await saveState({ ...state, status: "running", auditId: result.auditId });
  await remember({
    id: result.auditId,
    text,
    createdAt: new Date().toISOString(),
    result: null,
  });
  notify(`started-${state.id}`, "Veritrace is checking this", text);
  await chrome.alarms.create(POLL_ALARM, { periodInMinutes: 0.5 });
}

async function refresh() {
  const state = await loadState();
  if (state?.status !== "running") return;
  const response = await fetch(`${API_URL}/api/audits/${state.auditId}`);
  if (!response.ok) return;
  const audit = await response.json();
  if ((await loadState())?.id !== state.id) return;

  if (audit.status === "failed") {
    return fail(state, audit.public_error ?? "The check could not be completed.");
  }
  if (audit.status === "cancelled") {
    await chrome.alarms.clear(POLL_ALARM);
    await saveState({ ...state, status: "cancelled" });
    await remember({ id: state.auditId, result: "cancelled" });
    await chrome.action.setBadgeText({ text: "" });
  }
  if (audit.status === "completed") {
    await chrome.alarms.clear(POLL_ALARM);
    await saveState({ ...state, status: "finished" });
    await remember({ id: state.auditId, result: audit.overall_result });
    await setBadge(...(BADGES[audit.overall_result] ?? ["!", WARNING_COLOR]));
    notify(
      `audit-${state.auditId}`,
      RESULT_TITLES[audit.overall_result] ?? "Check finished",
      state.text,
    );
  }
}

async function cancel() {
  const state = await loadState();
  if (!state?.auditId) return;
  await fetch(`${API_URL}/api/audits/${state.auditId}/cancel`, { method: "POST" });
  await refresh();
}

chrome.runtime.onInstalled.addListener(async () => {
  // Updates also fire this event, and a duplicate ID would raise an error.
  await chrome.contextMenus.removeAll();
  chrome.contextMenus.create({
    id: "verify",
    title: "Verify with Veritrace",
    contexts: ["selection"],
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== "verify") return;
  // Chrome only allows this straight after the click, so it must come before any wait.
  if (tab?.id >= 0) chrome.sidePanel.open({ tabId: tab.id }).catch(() => {});
  submit(info.selectionText, pageDetails(info, tab));
});

chrome.notifications.onClicked.addListener((id) => {
  if (id.startsWith("audit-")) {
    chrome.tabs.create({ url: `${APP_URL}/audit/${id.slice("audit-".length)}` });
  }
  chrome.notifications.clear(id);
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === POLL_ALARM) refresh().catch(() => {});
});

chrome.runtime.onMessage.addListener((message) => {
  if (message.type === "refresh") refresh().catch(() => {});
  if (message.type === "cancel") cancel().catch(() => {});
  if (message.type === "retry") {
    loadState().then((state) => {
      const same = state?.text === message.text;
      // Checking again must search again, not repeat a saved result.
      submit(message.text ?? state?.text, same || !message.text ? state?.context : null, true);
    });
  }
});
