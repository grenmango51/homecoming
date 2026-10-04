# Local Flight Finder

Flight Finder keeps the compact calendar and flexible-date grid UI and runs fresh searches on your own computer. No Vercel deployment or public worker is needed.

## Windows

1. Install Python 3.11 or newer from python.org. Enable **Add Python to PATH**.
2. Download this repository from GitHub and extract it (or clone it).
3. Double-click `setup-local.cmd` once. It installs the Python dependencies and Chromium browser support.
4. Double-click `start-local.cmd`. Keep its terminal open while using the app.
5. Open http://127.0.0.1:4173 if the browser does not open automatically.

This workspace is already installed; just run `start-local.cmd`.

## macOS / Linux

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m patchright install chromium
python local_app.py --open
```

Linux systems may also need Chromium system dependencies: `python -m patchright install --with-deps chromium`.

## Search

Choose airports and dates, then press **Search flights**. Both Google Flights and Skyscanner run in parallel in visible browser windows. Every click starts a new, isolated search with reuse of saved fares disabled. Results appear progressively and carry observation timestamps.

- Round trip: exact dates, a 3-by-3 grid (±1 day), or a 7-by-7 grid (±3 days), filtered by minimum stay.
- One way: exact departure or up to seven departure dates.
- Scope: one adult, economy, EUR, Finland market. No booking or payment actions.
- Flexible searches can take several minutes, up to a 30-minute scanner budget. Use **Stop search** to terminate the search and its browser processes.
- If Skyscanner asks for a human check, complete it yourself in its visible browser within the wait window. Providers can still block automation or return incomplete results; these appear as statuses, never invented prices. Google may also require consent or human interaction.
- Prices are observations, not booking guarantees. The provider links confirm your selected dates.

Only one search runs at a time. Keep the app terminal open. Closing the terminal with Ctrl+C stops active searches. Reloading the page reconnects to the current search; the files remain saved locally after completion. Restarting the app resets the in-memory job list.

## Local safety and data

The server binds only to `127.0.0.1`. It checks the Host and Origin headers, rejects cross-site requests, and requires a per-session token for search/cancel requests. There is no CORS permission for other websites and no public tunnel. Search input is limited to validated airport codes and bounded date ranges; it cannot choose shell commands, executable paths or output directories.

Browser profiles, scan results, configuration and logs are kept under ignored `var/local-ui/`. Only verified, allowlisted fare fields reach the UI. The HTTP server serves five named UI assets; it does not serve the repository, cookies or logs. Google uses temporary browser profiles; Skyscanner uses a dedicated local profile separate from the daily scan.

GitHub contains the source and setup instructions, not anyone's browser profiles, tokens or search results. Each person installs and runs their own local copy. Do not expose this server through port forwarding or a public tunnel.

## Development

```sh
python -m unittest discover -s tests -t .
ruff check src/ tests/ local_app.py
node --check web/app.js
```

Browser-free tests cover date-range bounds, request validation, cross-origin blocking, private file access, one-way exports and stale-result exclusion. Existing daily scans through `run_all.py --trip HEL_HAN.toml` remain available.

The UI was originally generated with Manus and adapted for local searches. It is independent of Turkish Airlines, Google and Skyscanner.
