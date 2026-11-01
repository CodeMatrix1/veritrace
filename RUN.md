# Running Veritrace

To try Veritrace without installing anything, open the live website: https://veritrace-iota.vercel.app

These steps run the backend, website and Chrome extension on your own computer. Commands are for PowerShell on Windows, from the `veritrace` folder.

## 1. What You Need

- Python 3.11 or later
- Node.js 20 or later
- PostgreSQL, or Docker
- A [Groq API key](https://console.groq.com)
- A [Tavily API key](https://tavily.com)
- Chrome 116 or later, for the extension

## 2. Set Up the Database

Create a UTF-8 database. Startup fails if the database isn't UTF-8.

```powershell
psql -U postgres
```

```sql
CREATE DATABASE veritrace ENCODING 'UTF8' TEMPLATE template0;
\q
```

If you use Docker, skip this and see step 7.

## 3. Add Your Settings

```powershell
Copy-Item .env.example .env
```

Open `.env`, and fill in these values:

```text
GROQ_API_KEY=your_groq_key
TAVILY_API_KEY=your_tavily_key
DATABASE_URL=postgresql://postgres:your_password@localhost:5432/veritrace
```

Never commit `.env`, or paste keys into chats or screenshots.

## 4. Start the Backend

Open a terminal:

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
uvicorn main:app --port 7860 --workers 1 --env-file ../.env
```

Database tables are created at startup. Check `http://127.0.0.1:7860/api/health`.

Keep `--workers 1`, because the audit queue is in memory.

## 5. Start the Website

Open a second terminal:

```powershell
cd frontend
npm install
Set-Content .env.local "NEXT_PUBLIC_API_URL=http://127.0.0.1:7860"
npm run dev
```

Open `http://localhost:3000`.

Don't run `npm run build` while `npm run dev` is running, because it breaks the dev server.

## 6. Install the Extension

1. Open `chrome://extensions`.
2. Turn on **Developer mode**.
3. Click **Load unpacked**, and select the `extension` folder.
4. Pin Veritrace to the toolbar.
5. Select text on any page, right-click it, and choose **Verify with Veritrace**.

This version uses the live website and backend. To use your local ones, see [Deployed Setup](#deployed-setup).

After changing extension files, click the reload icon on its card.

## 7. Optional: Run the Backend with Docker

This starts PostgreSQL and the backend together. Fill in `.env` first, and then start the website as in step 5.

```powershell
docker compose up --build
```

## 8. Optional: Save Official Pages for Ask

The Ask assistant works without this, but it answers faster with saved pages. The script reuses pages already in the search cache, then runs a list of searches at about 1 Tavily credit each.

```powershell
cd backend
.venv\Scripts\python -m scripts.seed_knowledge ../.env
```

## Everyday Commands

Run these from `backend`.

**Restart the backend.** Do this after any backend change, or the old code keeps running:

```powershell
Get-NetTCPConnection -LocalPort 7860 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
.venv\Scripts\python -m uvicorn main:app --port 7860 --workers 1 --env-file ../.env
```

**Reset today's limit:**

```powershell
.venv\Scripts\python -m scripts.reset_daily_limit ../.env
```

**Run the tests.** Use a separate database, because it gets wiped:

```powershell
$env:TEST_DATABASE_URL = "postgresql://postgres:your_password@localhost:5432/veritrace_test"
.venv\Scripts\python -m pytest -q
```

## Common Problems

| Problem | Cause | Fix |
|---|---|---|
| `WinError 1225`, connection refused | PostgreSQL isn't running | Start the PostgreSQL service |
| Port 7860 or 3000 already in use | An old server is running | Stop it with the command above, changing the port if needed |
| Database encoding error at startup | The database isn't UTF-8 | Recreate it as in step 2 |
| "The AI has reached its per-minute limit" | Too many requests at once | Wait a minute |
| "The AI has reached its daily limit" | Both Groq models are used up | Wait, or upgrade Groq. These checks don't use your daily limit |
| The website shows an old error or a missing field | The backend has old code | Restart the backend |
| `Cannot find module` errors from `.next` | `npm run build` ran during `npm run dev` | Stop the dev server, delete `frontend/.next`, and start it again |
| "Your file couldn't be accessed" in the extension | Chrome has an old version | Reload it on `chrome://extensions` |
| Your audit list is empty | Audits are saved per browser and address | Use the same address and browser where you ran them |
| "The daily audit limit has been reached" | The limit is used up | Reset it, or raise `DAILY_AUDIT_LIMIT` |
| Nothing to check | The text has no clear factual claim | Select a complete sentence that names its subject |
| "This audit is no longer available" | The audit is on another database, or is over 30 days old | Open it where it was run, or run the check again |

## Deployed Setup

Veritrace is live on these hosts:

| Part | Host | Address |
|---|---|---|
| Website | Vercel | https://veritrace-iota.vercel.app |
| Backend | Render | https://veritrace-kgjt.onrender.com |
| Database | Neon | Private |

Check the backend at https://veritrace-kgjt.onrender.com/api/health.

The extension in this repository points to these addresses. To use it with a backend on your own computer, change the addresses listed below back to your local ones.

The website, backend and database can run on separate hosts. The backend must run as a single always-on process, not as serverless functions.

| Change | Where |
|---|---|
| `DATABASE_URL`, `GROQ_API_KEY`, `TAVILY_API_KEY`, `FRONTEND_ORIGIN`, `DAILY_AUDIT_LIMIT` | Backend host's private settings |
| `NEXT_PUBLIC_API_URL` | Website host's settings |
| `API_URL`, `APP_URL` | `extension/config.js` |
| `host_permissions`, `content_scripts` address | `extension/manifest.json` |

A new database starts empty, so run step 8 against it. If you zip the extension to share it, do it after making these changes.
