<!-- LOVABLE:BEGIN -->
> [!IMPORTANT]
> This project is connected to [Lovable](https://lovable.dev). Avoid rewriting
> published git history — force pushing, or rebasing/amending/squashing commits
> that are already pushed — as it rewrites history on Lovable's side and the
> user will likely lose their project history.
>
> Commits you push to the connected branch sync back to Lovable and show up in
> the editor, so keep the branch in a working state.
<!-- LOVABLE:END -->

## Architecture rules
- UI is the original homecoming `web/` page: markup in `src/flight/markup.ts`, CSS in `src/flight/flight.css`, behavior in `src/flight/legacy-app.js`; only its data layer is adapted. Why: the approved UI must stay identical.
- Owner access is decided server-side by `public.is_flight_owner()` plus RLS; browser checks are cosmetic. Why: owner-only enforcement must survive direct API calls.
- Clients never write tables directly; writes go through owner RPCs (`flight_submit`, `flight_cancel`) or service-only worker RPCs behind `/api/public/worker/*` with `WORKER_TOKEN`. Why: keeps lease/queue invariants atomic.
- Searching runs only in the Python worker (`worker/hosted_worker.py`) using the unchanged scrapers; never port scraping to TypeScript. Why: completion checks and challenge recovery live there.
- Provider links are rebuilt server-side from validated route/dates (`src/lib/flight-query.ts`). Why: never trust worker-supplied URLs.
- `public.flight_submit` validates the search and computes date pairs itself (`flight_validate_search`); TypeScript/Python validation is defense in depth. Why: direct RPC calls must be as strict as the UI.

