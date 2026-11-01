const POLL_MS = 2000;
const TOP_EVIDENCE = 3;
const content = document.getElementById("content");
const reportLink = document.getElementById("report");
const limit = document.getElementById("limit");
const linkNote = document.getElementById("link-note");
const tabCheck = document.getElementById("tab-check");
const tabHistory = document.getElementById("tab-history");

// Keep this in step with frontend/lib/verdicts.ts.
const VERDICTS = {
  supported: ["Confirmed", "Sources confirm this", "good"],
  contradicted: ["Incorrect", "Sources show this is incorrect", "bad"],
  misleading: ["Misleading", "True, but missing context", "warn"],
  unverified_insufficient: ["Unconfirmed", "Not enough evidence", "warn"],
  unverified_conflicting: ["Sources disagree", "Sources disagree", "warn"],
  partially_supported: ["Partly confirmed", "Partly confirmed", "warn"],
  assessment_incomplete: ["Incomplete", "Check incomplete", "stop"],
  no_factual_claims: ["Nothing to check", "Nothing to check", "stop"],
  error: ["Not checked", "Could not be checked", "stop"],
  cancelled: ["Cancelled", "Check cancelled", "stop"],
  failed: ["Failed", "Check failed", "bad"],
};
const COUNT_LABELS = {
  supported: "confirmed",
  contradicted: "incorrect",
  misleading: "misleading",
  unverified_conflicting: "where sources disagree",
  unverified_insufficient: "unconfirmed",
  error: "not checked",
};
const STAGES = {
  extract_text: "Reading the text",
  extract_claims: "Finding the claims",
  classify_claims: "Working out what proof is needed",
  investigate_claims: "Searching and reading sources",
  build_source_history: "Tracing the sources",
  write_report: "Writing the answer",
};
const TIERS = {
  primary: "Official source",
  secondary: "Other source",
  unknown: "Other source",
};
const RECENT_AUDITS = 5;
const NO_PROOF_OF_FALSITY = "A lack of public evidence does not prove the claim false.";
const WEAK_EVIDENCE =
  "The sources found don't meet the evidence standard for this claim. That does not prove it false.";
const SELECTION_HINT =
  "Only your selection is checked. Select a complete sentence that names its subject.";

let timer = null;
let ticker = null;
let shownState = null;
let tab = "check";

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function show(...nodes) {
  content.replaceChildren(...nodes);
}

/** Link to an audit's report, or to the website when there isn't one. */
function setReportLink(id) {
  reportLink.href = id ? `${APP_URL}/audit/${id}` : `${APP_URL}/`;
  reportLink.textContent = id ? "Open full audit report" : "Open Veritrace website";
  linkNote.hidden = Boolean(id);
}

function badge(verdict) {
  const [label, , tone] = VERDICTS[verdict] ?? ["Running", "", ""];
  return element("span", label, `badge ${tone}`);
}

function duration(seconds) {
  const total = Math.max(Math.round(seconds), 0);
  const minutes = Math.floor(total / 60);
  if (minutes === 0) return `${total} sec`;
  return total % 60 === 0 ? `${minutes} min` : `${minutes} min ${total % 60} sec`;
}

function safeUrl(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function strongest(evidence) {
  const rank = (item) =>
    (item.used_in_verdict ? 0 : 4) +
    (item.source_tier === "primary" ? 0 : 2) +
    (item.validation_status === "valid" ? 0 : 1);
  return [...evidence].sort((a, b) => rank(a) - rank(b));
}

function evidenceCard(item) {
  const card = element("div", "", "card evidence");
  const tier = TIERS[item.source_tier];
  card.append(element("strong", tier ? `${item.publisher} — ${tier}` : item.publisher));
  card.append(element("blockquote", `"${item.quote}"`));
  const url = safeUrl(item.source_url);
  if (url) {
    const link = element("a", "Open source", "muted");
    link.href = url;
    link.target = "_blank";
    link.rel = "noreferrer";
    card.append(link);
  }
  return card;
}

function runAgainButton(text) {
  const button = element("button", "Check again", "quiet");
  button.addEventListener("click", () => {
    chrome.runtime.sendMessage({ type: "retry", text });
    setTab("check");
  });
  return button;
}

function showReport(audit, state) {
  const report = audit.report;
  const result = report.overall_result;
  const head = element("section", "", "");
  head.append(badge(result), element("h2", VERDICTS[result]?.[1] ?? "Audit complete"));
  head.append(element("p", `Finished in ${duration(audit.elapsed_seconds)}.`, "muted"));
  if (report.reused && report.checked_at) {
    const date = new Date(report.checked_at).toLocaleDateString();
    head.append(element("p", `Saved result, checked on ${date}.`, "muted"));
  }
  const nodes = [head];
  const answer = element("section", "", `card verdict ${VERDICTS[result]?.[2] ?? ""}`);
  const many = report.claims.length > 1;
  if (many) {
    const counts = new Map();
    report.claims.forEach((claim) => {
      const label = COUNT_LABELS[claim.verdict ?? "error"];
      counts.set(label, (counts.get(label) ?? 0) + 1);
    });
    const parts = [...counts].map(([label, count]) => `${count} ${label}`);
    answer.append(element("p", `Of ${report.claims.length} claims: ${parts.join(", ")}.`));
  }
  (report.answer ?? [])
    .filter((sentence) => !many || !sentence.fallback)
    .forEach((sentence) => answer.append(element("p", sentence.text)));
  const all = report.claims.flatMap((claim) => claim.evidence);
  const unique = all.filter((item, i) => all.findIndex((other) => other.quote === item.quote) === i);
  if (result === "unverified_insufficient") {
    answer.append(element("p", unique.length > 0 ? WEAK_EVIDENCE : NO_PROOF_OF_FALSITY));
  }
  const gap = report.claims.flatMap((claim) => (claim.verdict === "error" ? claim.gaps : []))[0];
  if (gap) answer.append(element("p", gap, "muted"));
  if (answer.childElementCount > 0) nodes.push(answer);
  if (many) {
    nodes.push(element("h3", "Each claim"));
    report.claims.forEach((claim) => {
      const row = element("div", "", "card");
      row.append(badge(claim.verdict ?? "error"), element("p", claim.text));
      if (claim.confidence) row.append(element("p", `${claim.confidence} confidence`, "muted"));
      nodes.push(row);
    });
  }
  if (unique.length > 0) {
    nodes.push(element("h3", "Evidence"), ...strongest(unique).slice(0, TOP_EVIDENCE).map(evidenceCard));
  }
  if (report.claims.length === 0) {
    nodes.push(element("p", "We found no factual claims that sources could prove true or false."));
    nodes.push(element("p", SELECTION_HINT, "muted"));
  }
  // Claims with no evidence get fixed wording, so nothing here is AI-written.
  const aiWritten = report.claims.some((claim) => claim.evidence.some((item) => item.used_in_verdict));
  if (aiWritten && report.summary_label) nodes.push(element("p", report.summary_label, "muted"));
  nodes.push(runAgainButton(audit.input_text));
  show(...nodes);
}

function showError(message, text) {
  const nodes = [element("p", message, "error")];
  if (text && text.length >= MIN_CHARS && text.length <= MAX_CHARS) nodes.push(runAgainButton(text));
  show(...nodes);
}

function showProgress(audit) {
  const started = Date.now() - audit.elapsed_seconds * 1000;
  const fill = element("div", "", "fill");
  const track = element("div", "", "track");
  track.append(fill);
  const spent = element("span", "", "muted");
  const left = element("span", "", "muted");
  const times = element("div", "", "row");
  times.append(spent, left);

  function tick() {
    const elapsed = (Date.now() - started) / 1000;
    const remaining = audit.estimate_seconds - elapsed;
    fill.style.width = `${Math.min((elapsed / audit.estimate_seconds) * 100, 95)}%`;
    spent.textContent = `${duration(elapsed)} so far`;
    left.textContent = remaining > 5 ? `About ${duration(remaining)} left` : "Taking a little longer";
  }
  tick();
  clearInterval(ticker);
  ticker = setInterval(tick, 1000);

  const nodes = [
    element("h2", STAGES[audit.current_stage] ?? "Waiting to start"),
    track,
    times,
    element("h3", "Text being verified"),
    element("p", audit.input_text, "quote"),
  ];
  if (audit.claims.length > 0) {
    nodes.push(element("h3", audit.claims.length === 1 ? "Claim being checked" : "Claims being checked"));
    audit.claims.forEach((claim) => nodes.push(element("p", claim, "card")));
  }
  const cancel = element("button", "Cancel", "quiet");
  cancel.addEventListener("click", () => {
    cancel.disabled = true;
    cancel.textContent = "Cancelling...";
    chrome.runtime.sendMessage({ type: "cancel" });
  });
  nodes.push(cancel);
  nodes.push(element("p", "You can close this. The check keeps running.", "muted"));
  show(...nodes);
}

function showCancelled(text) {
  show(badge("cancelled"), element("h2", "Check cancelled"), element("p", text, "quote"), runAgainButton(text));
}

async function loadAudit(id, state) {
  clearTimeout(timer);
  setReportLink(id);
  try {
    const response = await fetch(`${API_URL}/api/audits/${id}`);
    if (!response.ok) throw new Error(String(response.status));
    const audit = await response.json();
    if (tab !== "check" || shownState?.auditId !== id) return;

    if (["completed", "failed", "cancelled"].includes(audit.status)) {
      clearInterval(ticker);
      if (state) chrome.runtime.sendMessage({ type: "refresh" });
    }
    if (audit.status === "failed") {
      showError(audit.public_error ?? "The check could not be completed.", audit.input_text);
    } else if (audit.status === "cancelled") {
      showCancelled(audit.input_text);
    } else if (audit.status === "completed" && audit.report) {
      showReport(audit, state);
    } else {
      showProgress(audit);
      timer = setTimeout(() => loadAudit(id, state), POLL_MS);
    }
  } catch (error) {
    if (error.message === "404") {
      show(element("p", "This audit has expired. Audits are kept for 30 days.", "error"));
      setReportLink(null);
    } else {
      show(element("p", "Reconnecting to the verification service...", "muted"));
      timer = setTimeout(() => loadAudit(id, state), POLL_MS);
    }
  }
}

async function removeAudit(id) {
  const { audits = [], removed = [] } = await chrome.storage.local.get(["audits", "removed"]);
  await chrome.storage.local.set({
    audits: audits.filter((audit) => audit.id !== id),
    removed: [...removed, id],
  });
  setTab(tab);
}

function historyItem(audit) {
  const row = element("div", "", "history-row");
  const item = element("button", "", "history");
  item.append(element("span", audit.text, "clip"), badge(audit.result));
  if (audit.createdAt) {
    item.append(element("span", `  ${new Date(audit.createdAt).toLocaleString()}`, "muted"));
  }
  item.addEventListener("click", () => {
    chrome.tabs.create({ url: `${APP_URL}/audit/${audit.id}` });
  });
  const remove = element("button", "×", "remove");
  remove.title = "Remove from this list";
  remove.setAttribute("aria-label", "Remove from this list");
  remove.addEventListener("click", () => removeAudit(audit.id));
  row.append(item, remove);
  return row;
}

function feature(title, text) {
  const card = element("div", "", "card feature");
  card.append(element("strong", title), element("p", text));
  return card;
}

async function showWelcome() {
  const { audits = [] } = await chrome.storage.local.get("audits");
  if (tab !== "check" || shownState) return;
  const nodes = [
    element("h2", "Check claims with evidence"),
    feature("Verify with Veritrace", "Select a statement on any page, right-click it, and choose Verify with Veritrace. The result appears here."),
    feature("Check a claim", "On the website, paste any claim. Veritrace searches both sides and quotes the sources."),
    feature("Ask about government information", "On the website, ask about schemes, rights, rules and public services."),
  ];
  if (audits.length > 0) {
    nodes.push(element("h3", "Recent audits"), ...audits.slice(0, RECENT_AUDITS).map(historyItem));
  } else {
    nodes.push(element("p", "Your result will appear here.", "muted"));
  }
  show(...nodes);
}

function render(state) {
  shownState = state;
  if (tab !== "check") return;
  clearTimeout(timer);
  clearInterval(ticker);
  setReportLink(state?.auditId);

  if (!state) {
    showWelcome();
  } else if (state.status === "submitting") {
    show(
      element("h2", "Starting your check..."),
      element("h3", "Text being verified"),
      element("p", state.text, "quote"),
    );
  } else if (state.status === "failed" && !state.auditId) {
    showError(state.error, state.text);
  } else {
    loadAudit(state.auditId, state);
  }
}

async function renderHistory() {
  clearTimeout(timer);
  clearInterval(ticker);
  setReportLink(null);
  const { audits = [] } = await chrome.storage.local.get("audits");
  if (tab !== "history") return;
  if (audits.length === 0) {
    show(element("p", "Your past audits will appear here. They are saved in this browser only.", "muted"));
    return;
  }
  show(...audits.map(historyItem));
}

async function showRemaining() {
  try {
    const health = await (await fetch(`${API_URL}/api/health`)).json();
    limit.textContent = `${health.daily_audits_remaining} left today, shared by checks and questions`;
  } catch {
    limit.textContent = "";
  }
}

function setTab(next) {
  tab = next;
  tabCheck.classList.toggle("active", next === "check");
  tabHistory.classList.toggle("active", next === "history");
  if (next === "history") {
    renderHistory();
  } else {
    render(shownState);
  }
}

tabCheck.addEventListener("click", () => setTab("check"));
tabHistory.addEventListener("click", () => setTab("history"));

showRemaining();
chrome.storage.session.get("current").then(({ current }) => render(current ?? null));
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.audits && tab === "history") renderHistory();
  const next = area === "session" ? changes.current?.newValue : null;
  if (next && (next.id !== shownState?.id || next.status !== shownState?.status)) {
    showRemaining();
    // A new check takes over the panel, even from a past audit or the history list.
    if (next.id !== shownState?.id) {
      tab = "check";
      tabCheck.classList.add("active");
      tabHistory.classList.remove("active");
    }
    render(next);
  }
});
