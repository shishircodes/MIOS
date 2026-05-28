# MIOS Web — the face of MIOS

A TanStack Start (React 19) + Tailwind v4 frontend for the **Market Intelligence
Operating System**. It implements the Easy Skill design ("paper data terminal":
sunshine yellow + forest green, DM Sans / Poppins / JetBrains Mono).

The **Weekly Digest** screen reads **live** from the Python pipeline (the MIOS
LLM backend) via a FastAPI bridge. The other screens (Signal Feed, Watchlist,
Dashboard, Mode Push, Mode Publish, Sources, Tokens) render the design with
reference data.

```
repo root (Python pipeline = LLM backend)
│
├── api/            FastAPI bridge  →  http://localhost:8787
│   └── server.py   GET /api/digest, /api/health
│
└── web/            this app (TanStack Start)  →  http://localhost:3000
    └── src/
        ├── routes/        file-based routes (__root shell + screens)
        ├── components/     UI atoms (Icons, Section, TierChip, Drawer…)
        ├── lib/            typed API client + types (mirror the backend JSON)
        ├── data/           mock reference data for non-live screens
        └── styles/app.css  design system (Tailwind v4 @theme + components)
```

## Run it (two processes)

Both need to be running. From the **repo root**:

### 1. Start the Python backend (FastAPI bridge)

```bash
python -m pip install -e .[api]
python -m uvicorn api.server:app --port 8787 --reload
```

`/api/digest` serves classified signals from `data/mios.db`. If the DB has no
classified rows yet, it falls back to the labelled synthetic dataset — so the UI
always has rich data without spending Gemini quota. The Weekly Digest header
shows a **Live** or **Synthetic** badge accordingly.

### 2. Start the web app

```bash
cd web
npm install
npm run dev
```

Open http://localhost:3000 — it redirects to the Weekly Digest.

### Point the app at a different backend

```bash
VITE_API_BASE=http://localhost:9000 npm run dev
```

## How the two projects complement each other

- The **Python pipeline** (scrape → SQLite → Gemini classify → digest) is the
  source of truth. It already exists and is tested.
- `api/digest_service.py` reshapes the pipeline's SQLite rows into the JSON the
  UI expects, reusing `delivery.digest.infer_geography` and the watchlist tiers.
- The **web app** never talks to Gemini or SQLite directly — it only consumes
  the typed `/api/digest` contract. Swap the backend and the UI is unaffected.

## Tech

- TanStack Start (SSR + file-based routing) · TanStack Query (data fetching)
- Tailwind v4 via `@tailwindcss/vite` + design tokens in `@theme`
- TypeScript strict, path alias `~/*` → `src/*`
- React 19, Vite 7
