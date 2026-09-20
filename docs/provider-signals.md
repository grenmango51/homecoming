# Provider Signals and Completion Contracts

This document records the completion indicators, selectors, response markers, and challenge detection patterns for Google Flights and Skyscanner discovered in the codebase and live browser architecture.

## 1. Google Flights

### 1.1 Completion and Readiness Signals
- **DOM Result Markers**:
  - `\b\d+\s+results returned\b`, `departing flights`, `cheapest`, `no flights` in visible body text.
- **Loading State Indicators**:
  - Phrases like `loading results` or `fetching results` indicate active streaming.
- **Sort and Tab State**:
  - Cheapest tab: `[role='tab']:has-text('Cheapest')`, `button:has-text('Cheapest')`. Must verify `aria-selected="true"`.
  - Sort dropdown: default is often "Sorted by top flights". Pins cheapest sort via URL parameter `tfu=EgoIABAAGAAgAigB` or menu selection `[role='menuitem']:has-text('Price')`.
- **Known Limitations**:
  - Google Flights streams results progressively without a definitive public "all airlines checked" event.
  - If results render but provider completion cannot be verified from a terminal network event, the search enters `SearchState.COMPLETION_UNVERIFIED`.
  - Headline fare on the "Cheapest" tab can disagree with the first rendered card (e.g. headline €845 vs card €941). The rebuilt model distinguishes `headline_price` from `lowest_itinerary_price`.

### 1.2 Anti-Bot and Challenge Markers
- Interstitial keywords: `unusual traffic`, `captcha`, `verify you are human`.
- Sign-in requirements: short pages (<1000 chars) prompting for Google Account login.
- Classification: `SearchState.BLOCKED` (terminal, not eligible for price ranking).

---

## 2. Skyscanner

### 2.1 Completion Signals
- **Backend Search Responses**:
  - Intercepted on `web-unified-search` and `graphql` JSON responses.
  - Status indicators: `status`, `context.status`, `query_status`, `searchStatus` reaching `COMPLETE`, `COMPLETED`, or `FINISHED`.
- **DOM Progress Indicators**:
  - Progress bar: `[role='progressbar'], [class*='ProgressBar'], [class*='BpkProgress']`. Completion when `aria-valuenow >= aria-valuemax` or label indicates `X of X` providers.
  - Skeletons / placeholders: `[class*='TicketPlaceholder'], [class*='placeholder'], [class*='shimmer'], [class*='skeleton']`. Zero skeletons remaining confirms DOM hydration.
  - Empty results: `ei tuloksia`, `no results found`, `no flights found`, `mitään ei löytynyt`.

### 2.2 Anti-Bot Challenges (PerimeterX)
- Challenge URLs: `/sttc/px/`, containing `captcha` or `perimeterx`.
- Visible text triggers: `press & hold`, `verify you are human`, `robotti`, `oletko oikea henkilö vai robotti`, `person or a robot`.
- Resolution protocol: Bounded manual intervention window in visible browser (`SearchState.BLOCKED` / `user_action_required`).

---

## 3. Unified State Machine Mapping

All provider events map to `src.completion.CompletionReducer`:
```
NAVIGATING -> VALIDATING_QUERY -> SEARCHING -> PROVIDER_COMPLETE -> RENDERING_FINAL_RESULTS -> COMPLETE | NO_RESULTS
```
Outcomes (`is_terminal = True`):
- `COMPLETE`: Accepted fare available (only state where `can_accept_fare` is True).
- `NO_RESULTS`: Successful search, 0 flights available.
- `BLOCKED`: Anti-bot challenge or verification wall.
- `TIMEOUT`: Page load or polling budget exceeded.
- `ERROR`: Unhandled exception during navigation or parsing.
- `QUERY_MISMATCH`: Displayed parameters (dates, route, cabin) do not match requested query.
- `COMPLETION_UNVERIFIED`: Settled results rendered without confirmed terminal signal.
