# Polling Dashboard — Setup &amp; Maintenance Guide

A practical, step-by-step guide to running the multi-country polling dashboard on your own machine, hosting it for free, and (optionally) automating poll updates with a built-in validation pass.

The work is split into three phases. **Phase 1 is done** — the rest is here so you can decide how far to take it. You can stop after any phase and still have something useful.

---

## The architecture in one picture

```
            ┌──────────────┐      writes      ┌────────────┐      fetches      ┌──────────────────────┐
            │  scraper.py  │  ───────────────▶ │  data.json │  ◀─────────────── │ polling_dashboard.html│
            │ (Phase 2)    │   (validated)     │ (data)     │   (on page load)  │ (the dashboard)       │
            └──────────────┘                   └────────────┘                   └──────────────────────┘
                   ▲                                                                       │
                   │ runs on a schedule (Phase 3)                                          │ served by
            ┌──────┴───────┐                                                        ┌──────┴───────┐
            │ GitHub Action│                                                        │ GitHub Pages │
            └──────────────┘                                                        └──────────────┘
```

The three pieces never talk to each other directly — they hand off through `data.json`. That decoupling is the whole point: if the scraper breaks, the dashboard keeps showing the last good data instead of going blank.

---

## Files you have now

| File | Role | Who edits it |
|---|---|---|
| `polling_dashboard.html` | The dashboard. Holds the static config (parties, colours, leaders, ideologies, seat method) plus an **embedded fallback** copy of the data. | You/me, rarely |
| `data.json` | The volatile data: poll rows, current-chamber seats, "last updated" line, per country. | The scraper (or me on a chat-refresh) |

The dashboard fetches `data.json` on load. If the fetch succeeds it uses that; if it fails (e.g. you double-clicked the file instead of serving it), it silently falls back to the embedded copy. The header shows which source is live: **"data: live (data.json)"** vs **"data: embedded fallback"**.

---

## Phase 1 — Run the split locally (≈ 10 minutes)

**Why a local server?** Browsers block a page opened as `file://...` from fetching a sibling file like `data.json` (a security rule called CORS). Serving the folder over `http://localhost` fixes this. You only need this for the *live* data path — double-clicking still works via the fallback.

### Step 1.1 — Put both files in one folder
Create a folder, e.g. `polling-dashboard/`, and drop `polling_dashboard.html` and `data.json` into it.

### Step 1.2 — Start a local server
You almost certainly already have Python. In a terminal, inside the folder:

```bash
python3 -m http.server 8000
```

(On Windows without Python, install [Node](https://nodejs.org) and run `npx serve` instead.)

### Step 1.3 — Open it
Visit **http://localhost:8000/polling_dashboard.html** in your browser. The header should read **"data: live (data.json)"**. If it says "embedded fallback", the fetch didn't resolve — check both files are in the same folder and you opened via `localhost`, not `file://`.

### Step 1.4 — Prove the split works
Open `data.json`, change one number (say, bump PRO's most recent poll), save, and refresh the browser. The standings, projection, and trend chart should all move. That confirms the dashboard is genuinely reading from `data.json` — which is exactly what the scraper will rewrite later.

**You can stop here.** To update the dashboard, just ask me in chat ("refresh the Netherlands dashboard") and I'll regenerate `data.json` for you. Everything below automates that step.

---

## Phase 2 — Host it for free on GitHub Pages (≈ 20 minutes)

This puts the dashboard on a public URL where `fetch` works without a local server.

### Step 2.1 — Create a GitHub account and repo
- Sign up at [github.com](https://github.com) if you haven't.
- Create a new **public** repository, e.g. `polling-dashboard`.

### Step 2.2 — Add your files
Either drag-and-drop `polling_dashboard.html` and `data.json` into the repo via the web UI ("Add file" → "Upload files"), or use git:

```bash
git init
git add polling_dashboard.html data.json
git commit -m "Initial dashboard"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/polling-dashboard.git
git push -u origin main
```

### Step 2.3 — Turn on Pages
In the repo: **Settings → Pages → Source: Deploy from a branch → Branch: `main` / root → Save.**
After a minute your dashboard is live at:

```
https://YOUR_USERNAME.github.io/polling-dashboard/polling_dashboard.html
```

That URL fetches `data.json` natively — no local server needed. Updating the dashboard is now just committing a new `data.json`.

**You can stop here too.** At this point updates are: I regenerate `data.json` → you commit it → the live site updates. The final phase removes you from that loop.

---

## Phase 2-alt — Host locally instead of GitHub Pages

**Short answer to "can I just do Phase 1, then jump to Phase 3?"** — yes, with one substitution. Phase 1 already gives you everything the dashboard needs locally (the server + the `data.json` split). To automate without GitHub, you swap the cloud scheduler for a local one:

| | GitHub path | Local-only path |
|---|---|---|
| Hosting | GitHub Pages (always on, public URL) | Your machine via `python3 -m http.server` (on only when you run it) |
| Scheduler | GitHub Action (cron in the cloud) | **cron** (macOS/Linux) or **Task Scheduler** (Windows) on your machine |
| `data.json` updates | Action commits it to the repo | Scraper rewrites the local file in place |
| Needs GitHub? | Yes | **No** |

So your route is **Phase 1 → Phase 3 (local variant)** — skip Phase 2 entirely. The scraper and the whole validation pass are byte-for-byte identical; only *where it runs* and *how it's triggered* change. When we build Phase 3 I'll give you both the GitHub Action YAML and the equivalent cron/Task-Scheduler setup, so you can pick.

Two things to know about local-only:
- **The dashboard is only live while your server is running.** For "always available" you'd keep the server running as a background service, or just start it when you want to look. GitHub Pages wins on always-on; local wins on privacy and zero external dependencies.
- **The scheduled scrape only fires when your machine is on.** On macOS/Linux, `anacron` will catch up runs missed while the machine was asleep; Windows Task Scheduler has a "run as soon as possible after a missed start" option. You can still keep a local git repo for `data.json` history if you want rollback without GitHub.

---

## Phase 3 — Automate updates with validation (the bigger build)

This is the scraper plus a scheduler. I'll generate the actual code when you're ready; this section explains what it does and what you'll decide.

### What the scraper does, in order
1. **Fetch** the source table (Wikipedia's "Opinion polling for the next [country] election" page is the recommended source — consistently formatted, scrape-permitted, exists for every country).
2. **Parse** the rows into the `[pollster, date, ...values]` shape the dashboard expects.
3. **Validate** each new poll (see below).
4. **Merge** anything that passes into `data.json`; quarantine anything that fails into a `review/` file.
5. **Commit** the updated `data.json` if it changed.

### The validation pass — automatic flagging, human resolution
Every newly fetched poll runs this gauntlet *before* it's allowed into `data.json`:

| Check | Rule | On failure |
|---|---|---|
| **Base consistency** | Party shares sum to 100% ± 2 | Quarantine — likely a missing column or parse error |
| **Outlier** | No party moves &gt; 4 pts from its recent rolling average | Flag for review — could be a typo or a real shock |
| **New party / column** | Schema matches the known party list | Flag — e.g. a brand-new party, or a rename (GL-PvdA → PRO) |
| **Duplicate** | Not already present (same pollster + date) | Skip silently |
| **Date sanity** | Fieldwork date is real, not in the future | Quarantine |
| **Pollster whitelist** | From a recognised house | Flag |

The key division of labour: **the scraper catches anomalies; you (or I, in chat) resolve them.** Anything flagged sits in quarantine and the dashboard keeps showing the last clean dataset — it never silently ingests a suspect figure. When something's flagged, you bring it to chat and I help adjudicate (is that 6-point jump real? should the renamed party inherit the old history?). That judgement can't be safely automated, but the *detection* fully can.

### The scheduler
A **GitHub Action** with a cron trigger (e.g. daily at 06:00) runs the scraper, and commits `data.json` if it changed. Free tier covers this comfortably — polls rarely drop more than weekly per pollster, so daily is generous. It's all version-controlled, so every poll update is a visible commit you can roll back.

### What you'll need to decide before I build it
- **Source per country** — I recommend standardising on the Wikipedia polling pages.
- **Update frequency** — daily is the sensible default.
- **Outlier threshold** — 4 points is a reasonable starting sensitivity; we can tune it.
- **What to do with flagged polls** — auto-quarantine (recommended) vs auto-accept-but-mark.

---

## Maintenance: routine tasks

### Adding a new country
This is now almost entirely data entry, thanks to the template structure. In `polling_dashboard.html`, add one entry to the `COUNTRIES` object with:
- **Identity:** `flag`, `name`, `chamber`, `totalSeats`, `majority`, `threshold`.
- **Seat method:** `'dhondt'` (NL, Spain), `'sainteLague'` + `firstDivisor` (Sweden, Norway), etc.
- **`parties`:** key, name, colour, leader, government status, two ideology labels.
- **`seatingOrder`:** left-to-right for the hemicycle.
- **`blocs`:** the coalition groupings for the majority builder and bloc row.
- **`pollKeys`:** the column order your poll rows use.

Then add that country's block to `data.json`. The rendering engine — cards, hemicycle, majority builder, trend chart, tabs — is entirely country-agnostic and needs no changes. The dropdown picks up the new country automatically — unless the country object carries `hidden: true`, which keeps it out of the menu while preserving its config and data. Belgium currently uses this flag: its national polling is too sparse to keep current (Belgian pollsters survey per-region), so it's parked until the planned v2.0 split into Flanders and Wallonia. To resurface it, simply delete the `hidden: true` line.

### When the scraper flags something
1. Check the `review/` quarantine file.
2. Bring the flagged poll to chat — I'll help you decide whether it's a real change, a typo, or a structural shift (new/renamed party).
3. Once resolved, the corrected row goes into `data.json` and the dashboard updates.

### Keeping the static config current
Leaders change, parties rename, governments fall. These live in `polling_dashboard.html` (not `data.json`), so they're a manual edit — but rare. The PRO rename and the Jetten cabinet are already in. When one happens, flag it to me and I'll patch the config.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Header says "embedded fallback" locally | Opened via `file://`, or `data.json` not in same folder | Serve over `localhost` (Step 1.2); check file location |
| Header says "embedded fallback" on GitHub Pages | `data.json` not committed, or wrong path | Confirm `data.json` is in the repo root alongside the HTML |
| Numbers look stale | Browser cached `data.json` | Hard-refresh (Ctrl/Cmd+Shift+R); the fetch already sets no-store |
| A party is missing from the projection | It fell below the threshold | Expected — sub-threshold parties win no seats; check the card for the "below threshold" flag |
| Scraper produces odd numbers | Source table restructured | Expected occasionally; the parser needs a small tweak — bring it to chat |

---

## Quick reference — the update loop at each phase

- **Phase 1 (local):** ask me to refresh → I send new `data.json` → you replace the file.
- **Phase 2 (hosted):** ask me to refresh → I send new `data.json` → you commit it → live site updates.
- **Phase 3 (automated):** scheduler runs the scraper daily → validated polls merge automatically → flagged ones come to chat.

Each phase removes a manual step. Phase 1 is the foundation and is already in place; Phases 2 and 3 are opt-in whenever you want them.
