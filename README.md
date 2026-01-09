# WIZZ SNOWTAM Watch (Notamify)

Static GitHub Pages dashboard that monitors **SNOWTAM-like** NOTAMs for a list of airports (from `airports.txt`) and renders:

- Grey points = unknown/not loaded
- Green = OK (no SNOWTAM-like NOTAM detected)
- Yellow / Orange / Red = winter runway condition severity
- Blinking marker = change detected since the previous run (new/updated/removed)

## How it works

1. A GitHub Actions workflow runs every ~10 minutes and on manual trigger.
2. The workflow:
   - Generates/updates `data/airports.json` (lat/lon) from OurAirports airports.csv
   - Queries Notamify `/api/v2/notams` (Active endpoint) in batches of 5 ICAOs
   - Filters NOTAMs for SNOWTAM-like winter runway content
   - Writes `data/snowtam_status.json` and commits it back to the repo
3. The frontend (`index.html`) polls the JSON files and updates the map without requiring a full page refresh.

## Setup (step-by-step)

1. Create a GitHub repository and upload all files from this project to the **repo root**.
2. Enable GitHub Pages:
   - **Settings → Pages**
   - Source: Deploy from a branch
   - Branch: `main` (or your default)
   - Folder: `/ (root)`
3. Allow Actions to commit:
   - **Settings → Actions → General → Workflow permissions**
   - Select **Read and write permissions**
4. Add your Notamify API key:
   - **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `NOTAMIFY_API_KEY`
   - Value: your key from Notamify API Manager
5. Run the workflow once:
   - **Actions → Update SNOWTAM JSON (Notamify) → Run workflow**
6. Open your GitHub Pages URL.

## Cost / rate control

Notamify Active endpoint supports **max 5 ICAO codes per call**; this project batches requests accordingly.
You can reduce credit usage by keeping `NOTAMIFY_MAX_PAGES=1` (default). If you need deeper paging, increase it.

## Safety note

This dashboard is for situational awareness. Always cross-check with your official briefing system.
