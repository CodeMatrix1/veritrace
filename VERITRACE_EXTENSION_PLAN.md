# Veritrace Chrome Extension

**Status:** Built in `veritrace/extension/`, working against the local backend. The manifest and `config.js` still point at localhost, so they must change at deployment. Real Chrome testing is still needed.

**Submission role:** A demo feature. The website stays the live prototype link.

**Describe it accurately:** The extension verifies selected text. It does not verify a whole social post with its images, comments, author or surrounding context.

## 1. User Flow

1. The user selects text, right-clicks, and chooses **Verify with Veritrace**.
2. The side panel opens, and a notification says the check has started.
3. The panel shows the exact text, the claims found, the current step, the estimated time left, and Cancel.
4. The audit runs on the server, so it continues if the panel closes.
5. One answer covering every claim appears in the panel, with the strongest evidence, and a second notification gives the result.
6. Reports open on the website in a new tab. **View all audits** is always available, and recent audits open the same way, without a new check.
7. The Ask assistant is on the website only. The panel explains what Veritrace does, and links to the website.

## 2. Design

| Part | Job |
|---|---|
| `background.js` | Menu item, opens the panel, submits text, sends notifications, cancels, keeps history, and polls every 30 seconds to set the badge |
| `panel.js` | Shows progress, a short answer and history, and polls every 2 seconds while open |
| `sync.js` | Runs on the Veritrace website, and keeps the website's and extension's audit lists the same |
| `config.js` | API and website URLs |

- **Standalone:** The extension is not standalone. It needs the backend, because that holds the Groq and Tavily keys and the database. Deploying the backend means users install only the extension.
- **Side panel instead of a popup:** It stays open, needs no injection into the page, and so works on local PDFs.
- **Permissions:** `contextMenus`, `storage`, `alarms`, `activeTab`, `sidePanel`, `notifications`, and the backend URL. The extension doesn't read pages.
- **Selected text only:** Surrounding text is not collected. Claim extraction, and every later step, sees only the selection. The page title and address, with the query removed, are shown as a link and never sent to the LLM.
- **Submit timeout:** 25 seconds, because Chrome can stop a service worker when a request runs longer. A sleeping free host may need one retry.
- **CORS:** No backend change was needed. A real `POST` from the extension to the local backend succeeded.
- **History:** Kept in `chrome.storage.local`, up to 100 audits. The API still has no way to list audits, so one person can't see another's. `sync.js` runs only on the Veritrace website, and merges the extension's list with the website's `localStorage` list. It sends nothing to the server.
- **Cancel:** `POST /api/audits/{id}/cancel` marks the audit cancelled straight away. Work already in progress finishes, and the worker stops at its next step.
- **Time estimate:** 90 seconds per claim found, starting at one claim.

## 3. What Was Tested

The tests below used headless Edge. The side panel was opened as a normal page there, because automation can't click the right-click menu.

Tested in headless Edge with the extension loaded, using a mock API with invented reports:

- Every verdict type, in the report and the side panel
- Running, failed, daily limit, unexpected reply, too-short text and retry states
- Panel layout, toolbar badge, and collapsed investigation details
- Audit list sync, removing single audits, and new-tab links

Tested in Edge against the real local backend, using a saved report, so no search credits were spent:

- Submitting from the extension, showing the result in the panel, and opening the full report

## 4. Still To Test

Do these by hand, because automation can't cover them:

- [ ] Install in real Chrome with **Load unpacked**. Automated Chrome refused to load the extension, so it has not run in Chrome yet.
- [ ] Click the real right-click menu item, and confirm the side panel opens by itself.
- [ ] Confirm both notifications appear. Windows Focus settings can hide them.
- [ ] Select text on X, Facebook and WhatsApp Web.
- [ ] Select text in Chrome's PDF viewer, online and local. PDF support stays unconfirmed until then.
- [ ] Repeat on the deployed site, in a clean Chrome profile.

## 5. Release Steps

1. Change `API_URL` and `APP_URL` in `extension/config.js`.
2. Change `host_permissions` and the `content_scripts` address in `extension/manifest.json`.
3. Reload the extension, and run the checklist above.
4. Zip the folder, and attach it to a GitHub Release.
5. Link the download and install steps from the main README.
6. Record a 15-second demo, using a complete sentence.

## 6. Context for Incomplete Selections

Selected text alone can't name a subject in fragments such as "It rose 8.2%". Surrounding text was removed from claim extraction on Oct 31, 2026, because nothing from the page should change what gets checked.

| Approach | How it works | Accuracy risk | Effort | Decision |
|---|---|---|---|---|
| Selection only | Current. Fragments are not checked | None, but many fragments fail | Done | Keep as the default |
| Page text in extraction | Removed. The AI fills in the subject | High, as it can change the claim without the user seeing | Done before | Rejected |
| Extend to the full sentence | Extension expands the selection to its sentence | Low | About 1 hour | Later |
| User adds the subject | Panel shows an edit box when a claim is not checkable | None, as the user writes the claim | About 2 hours | Next |
| Suggest, user confirms | Page text produces a suggested claim, which runs only after approval | Low, as the user sees it first | About half a day | Best long term |
| Page text for search only | Claim stays the same, and page terms widen the search | Low to medium, as it affects which sources are found | About half a day | Try later |

## 7. Known Limits

- A fragment such as "It rose 8% last year" stays `not_checkable`, because nothing outside the selection is used.
- "Yesterday" is resolved against today's date, so it can be wrong for an older post.
- Selected PDF tables can lose their row and column labels.
- Scanned PDFs and images have no text to select.
- Finished checks and answered Ask questions share the app-wide `DAILY_AUDIT_LIMIT`. A saved report counts, but failed checks and text with no claims don't.
- Selected text, page title and page address are saved like any other audit for 30 days, and anyone with the audit link can see them.
- Sources must be in English.
- History is per browser. Audits from before the lists existed can't be added, because the API never lists audits.
- Cancelling stops at the next step, so a slow search can finish first.
- Reports saved before Nov 1, 2026 can lack source labels and show older answer wording. Check again fixes this.
