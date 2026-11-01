# Veritrace Chrome Extension

Select text on a page, right-click it, and choose **Verify with Veritrace**. A side panel opens beside the page, and a notification confirms the check has started. The panel shows the exact text being verified, the claims found, the time left and a Cancel button. The answer then appears in the panel, with no need to open anything. The toolbar badge shows `...`, then `OK`, `NO` or `!`.

**View all audits** and **Open full audit report** open the website in a new tab, with or without a check running. Recent audits on the Check tab open the same way. **Check again** appears after every check, and searches again instead of reusing a saved result. The **Ask** tab explains Indian schemes, policies, rights and public data. It tries official sources first, then a labelled web search, and it isn't a fact-check or legal advice. The **History** tab lists past audits, and any of them can be reopened. The check runs on the Veritrace server, so it continues if the panel closes.

## It needs the Veritrace server

The extension is only the screen. Searching, reading sources and the AI all run on the Veritrace backend, so that backend must be reachable. Once it is deployed, a user only installs the extension.

## Install

1. Download and unzip the extension.
2. Open `chrome://extensions`.
3. Turn on **Developer mode**.
4. Click **Load unpacked**, and select the unzipped folder.
5. Pin Veritrace to the toolbar. Chrome 116 or later is required.

## Point it at a deployed app

Change `API_URL` and `APP_URL` in `config.js`. In `manifest.json`, change the host permission and the `content_scripts` address. Then reload the extension.

## Good to know

- Select a complete statement. Only the selected text is used, so a fragment such as "It rose 8% last year" can't be checked.
- It checks selected text, not a whole post with its images, comments or author.
- The extension never reads the page. When you choose **Verify with Veritrace**, it sends your selection, plus the page title and address without its query so the report can link back. The AI sees only your selection.
- All of this is saved like any other audit and removed after 30 days, and anyone with the audit link can open it. Avoid using it on private pages, such as email.
- It works the same way in a PDF.
- History is kept in this browser only, for your latest 100 audits. The extension and the website share one list, which syncs when the website is open. Audits are removed from the server after 30 days.
- The time shown is an estimate based on recent checks.
- Sources must be in English.
- Checks and Ask questions share the app's daily limit, and the number left is shown in the panel.
