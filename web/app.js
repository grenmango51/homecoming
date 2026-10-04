(() => {
  'use strict';

  const AIRPORTS = [
    { code: 'HEL', city: 'Helsinki', name: 'Helsinki Airport' },
    { code: 'HAN', city: 'Hanoi', name: 'Noi Bai International Airport' },
    { code: 'SGN', city: 'Ho Chi Minh City', name: 'Tan Son Nhat International Airport' },
    { code: 'BKK', city: 'Bangkok', name: 'Suvarnabhumi Airport' },
    { code: 'SIN', city: 'Singapore', name: 'Changi Airport' },
    { code: 'NRT', city: 'Tokyo', name: 'Narita International Airport' },
    { code: 'HND', city: 'Tokyo', name: 'Haneda Airport' },
    { code: 'ICN', city: 'Seoul', name: 'Incheon International Airport' },
    { code: 'DEL', city: 'Delhi', name: 'Indira Gandhi International Airport' },
    { code: 'LHR', city: 'London', name: 'Heathrow Airport' },
    { code: 'CDG', city: 'Paris', name: 'Charles de Gaulle Airport' },
    { code: 'FRA', city: 'Frankfurt', name: 'Frankfurt Airport' },
    { code: 'AMS', city: 'Amsterdam', name: 'Schiphol Airport' },
    { code: 'JFK', city: 'New York', name: 'John F. Kennedy International Airport' }
  ];

  const LOCALE = 'en-GB';

  const state = {
    tripType: 'roundtrip',
    origin: airportFromCode('HEL'),
    destination: airportFromCode('HAN'),
    departure: '2026-12-09',
    returnDate: '2027-01-09',
    flexible: true,
    flexDays: 3,
    minStay: 21,
    activeQuery: null,
    selectedMatrix: { departure: '2026-12-09', returnDate: '2027-01-09' },
    picker: {
      target: 'departure',
      displayMonth: parseDate('2026-12-01'),
      opener: null,
      error: ''
    },
    archive: {
      status: 'ready',
      fares: [],
      generatedAt: null,
      invalidCount: 0,
      error: ''
    }
  };
  let sessionToken = null;
  let currentJob = null;
  let pollTimer = null;

  const el = {
    form: document.getElementById('search-card'),
    originInput: document.getElementById('origin-input'),
    destinationInput: document.getElementById('destination-input'),
    originSuggestions: document.getElementById('origin-suggestions'),
    destinationSuggestions: document.getElementById('destination-suggestions'),
    swapButton: document.getElementById('swap-button'),
    departureButton: document.getElementById('departure-button'),
    returnButton: document.getElementById('return-button'),
    departureValue: document.getElementById('departure-value'),
    returnValue: document.getElementById('return-value'),
    departureSubtle: document.getElementById('departure-subtle'),
    returnSubtle: document.getElementById('return-subtle'),
    passengerButton: document.getElementById('passenger-button'),
    flexibleToggle: document.getElementById('flexible-toggle'),
    flexDays: document.getElementById('flex-days'),
    minStay: document.getElementById('min-stay'),
    formMessage: document.getElementById('form-message'),
    archiveStatus: document.getElementById('archive-status'),
    archiveStatusText: document.getElementById('archive-status-text'),
    results: document.getElementById('results'),
    resultsTitle: document.getElementById('results-title'),
    resultDescription: document.getElementById('result-description'),
    resultsType: document.getElementById('results-type'),
    resultSummary: document.getElementById('result-summary'),
    googleLink: document.getElementById('google-link'),
    skyscannerLink: document.getElementById('skyscanner-link'),
    matrixSection: document.querySelector('.matrix-section'),
    fareMatrix: document.getElementById('fare-matrix'),
    datePicker: document.getElementById('date-picker'),
    calendarMonths: document.getElementById('calendar-months'),
    pickerTargetLabel: document.getElementById('picker-target-label'),
    pickerRangeLabel: document.getElementById('picker-range-label'),
    pickerError: document.getElementById('picker-error'),
    toast: document.getElementById('toast')
  };

  function airportFromCode(code) {
    const normalized = String(code || '').toUpperCase();
    return AIRPORTS.find((airport) => airport.code === normalized) || {
      code: normalized,
      city: normalized,
      name: 'Custom airport code'
    };
  }

  function isIata(value) {
    return /^[A-Z]{3}$/.test(String(value || '').toUpperCase());
  }

  function parseDate(value) {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
    const [year, month, day] = value.split('-').map(Number);
    const date = new Date(Date.UTC(year, month - 1, day));
    if (Number.isNaN(date.getTime()) || date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return null;
    return date;
  }

  function toISODate(date) {
    return date.toISOString().slice(0, 10);
  }

  function addDays(isoDate, amount) {
    const date = parseDate(isoDate);
    if (!date) return null;
    date.setUTCDate(date.getUTCDate() + amount);
    return toISODate(date);
  }

  function addMonths(date, amount) {
    return new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + amount, 1));
  }

  function daysBetween(earlier, later) {
    const start = parseDate(earlier);
    const end = parseDate(later);
    if (!start || !end) return NaN;
    return Math.round((end.getTime() - start.getTime()) / 86400000);
  }

  function dateIsAfter(later, earlier) {
    return daysBetween(earlier, later) > 0;
  }

  function formatFullDate(isoDate) {
    const date = parseDate(isoDate);
    if (!date) return 'Choose a date';
    return new Intl.DateTimeFormat(LOCALE, {
      weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC'
    }).format(date);
  }

  function formatHeaderDate(isoDate) {
    const date = parseDate(isoDate);
    if (!date) return '—';
    return new Intl.DateTimeFormat(LOCALE, {
      weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC'
    }).format(date);
  }

  function formatMonth(date) {
    return new Intl.DateTimeFormat(LOCALE, { month: 'long', year: 'numeric', timeZone: 'UTC' }).format(date);
  }

  function formatEuro(value) {
    return new Intl.NumberFormat('en-IE', {
      style: 'currency', currency: 'EUR', minimumFractionDigits: 0, maximumFractionDigits: 2
    }).format(value);
  }

  function formatObservedAt(value, compact = false) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return 'recorded time unavailable';
    return new Intl.DateTimeFormat(LOCALE, compact ? {
      day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC'
    } : {
      day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'UTC', timeZoneName: 'short'
    }).format(date);
  }

  function normalizeAirportInput(value) {
    const text = String(value || '').trim().toUpperCase();
    if (isIata(text)) return text;
    const bracketed = text.match(/\(([A-Z]{3})\)\s*$/);
    if (bracketed && isIata(bracketed[1])) return bracketed[1];
    const suffix = text.match(/(?:^|\s)([A-Z]{3})\s*$/);
    return suffix && isIata(suffix[1]) ? suffix[1] : null;
  }

  function airportDisplay(airport) {
    return airport.city === airport.code ? airport.code : `${airport.city} (${airport.code})`;
  }

  function clearFormMessage() {
    el.formMessage.textContent = '';
  }

  function showFormMessage(message) {
    el.formMessage.textContent = message;
  }

  function showToast(message) {
    window.clearTimeout(showToast.timeoutId);
    el.toast.textContent = message;
    el.toast.hidden = false;
    showToast.timeoutId = window.setTimeout(() => {
      el.toast.hidden = true;
      el.toast.textContent = '';
    }, 4200);
  }

  function escapeHTML(value) {
    return String(value).replace(/[&<>'"]/g, (character) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
    }[character]));
  }

  function safeHttpUrl(value) {
    try {
      const url = new URL(value);
      return url.protocol === 'https:' && /(^|\.)(google\.com|skyscanner\.(net|fi))$/.test(url.hostname) ? url.href : null;
    } catch (_) {
      return null;
    }
  }

  function syncAirportInputs() {
    el.originInput.value = airportDisplay(state.origin);
    el.destinationInput.value = airportDisplay(state.destination);
  }

  function syncDateFields() {
    const oneWay = state.tripType === 'oneway';
    el.departureValue.textContent = formatFullDate(state.departure);
    el.departureSubtle.textContent = state.departure ? 'Departure date' : 'Choose a date';
    el.returnValue.textContent = oneWay ? 'No return selected' : formatFullDate(state.returnDate);
    el.returnSubtle.textContent = oneWay ? 'One way' : (state.returnDate ? `${daysBetween(state.departure, state.returnDate)} nights` : 'Choose a date');
    el.returnButton.disabled = oneWay;
    el.returnButton.closest('.return-field-group').hidden = false;
    el.returnButton.setAttribute('aria-label', oneWay ? 'Return date unavailable for one-way search' : `Return date, ${formatFullDate(state.returnDate)}`);
    el.departureButton.setAttribute('aria-label', `Departure date, ${formatFullDate(state.departure)}`);
  }

  function syncOptionControls() {
    el.flexibleToggle.checked = state.flexible;
    el.flexDays.value = String(state.flexDays);
    el.flexDays.disabled = !state.flexible;
    el.minStay.value = String(state.minStay);
    document.body.dataset.tripType = state.tripType;
  }

  function syncForm() {
    syncAirportInputs();
    syncDateFields();
    syncOptionControls();
  }

  function hideSuggestions() {
    el.originSuggestions.hidden = true;
    el.destinationSuggestions.hidden = true;
    el.originInput.setAttribute('aria-expanded', 'false');
    el.destinationInput.setAttribute('aria-expanded', 'false');
  }

  function airportElements(kind) {
    return kind === 'origin'
      ? { input: el.originInput, list: el.originSuggestions }
      : { input: el.destinationInput, list: el.destinationSuggestions };
  }

  function renderSuggestions(kind) {
    const { input, list } = airportElements(kind);
    const query = input.value.trim().toLowerCase();
    const matches = AIRPORTS.filter((airport) => [airport.code, airport.city, airport.name]
      .some((value) => value.toLowerCase().includes(query))).slice(0, 6);
    const enteredCode = normalizeAirportInput(input.value);

    list.replaceChildren();
    const codes = new Set(matches.map((airport) => airport.code));
    if (enteredCode && !codes.has(enteredCode)) {
      matches.unshift(airportFromCode(enteredCode));
    }

    if (!matches.length) {
      const empty = document.createElement('p');
      empty.className = 'suggestion-subtitle';
      empty.textContent = 'Enter a three-letter IATA code, for example HEL.';
      empty.style.padding = '8px 10px';
      empty.style.margin = '0';
      list.append(empty);
    } else {
      matches.forEach((airport) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'suggestion';
        button.dataset.airportCode = airport.code;
        button.dataset.airportKind = kind;
        button.setAttribute('role', 'option');
        button.setAttribute('aria-selected', 'false');
        const left = document.createElement('span');
        const title = document.createElement('span');
        title.className = 'suggestion-title';
        title.textContent = airport.city === airport.code ? `${airport.code} — custom airport code` : airport.city;
        const subtitle = document.createElement('span');
        subtitle.className = 'suggestion-subtitle';
        subtitle.textContent = airport.name;
        left.append(title, subtitle);
        const code = document.createElement('span');
        code.className = 'suggestion-code';
        code.textContent = airport.code;
        button.append(left, code);
        list.append(button);
      });
    }
    list.hidden = false;
    input.setAttribute('aria-expanded', 'true');
  }

  function chooseAirport(kind, code) {
    if (!isIata(code)) return;
    state[kind] = airportFromCode(code);
    const { input } = airportElements(kind);
    input.value = airportDisplay(state[kind]);
    hideSuggestions();
    clearFormMessage();
  }

  function commitAirport(kind, announceError = true) {
    const { input } = airportElements(kind);
    const code = normalizeAirportInput(input.value);
    if (!code) {
      if (announceError) showFormMessage(`Enter a valid three-letter IATA code for ${kind === 'origin' ? 'the origin' : 'the destination'}.`);
      return false;
    }
    state[kind] = airportFromCode(code);
    input.value = airportDisplay(state[kind]);
    return true;
  }

  function openDatePicker(target) {
    clearFormMessage();
    state.picker.target = target;
    state.picker.error = '';
    state.picker.opener = target === 'departure' ? el.departureButton : el.returnButton;
    const selectedDate = target === 'return' && state.returnDate ? state.returnDate : state.departure;
    state.picker.displayMonth = selectedDate ? new Date(Date.UTC(parseDate(selectedDate).getUTCFullYear(), parseDate(selectedDate).getUTCMonth(), 1)) : new Date();
    el.datePicker.hidden = false;
    document.body.style.overflow = 'hidden';
    el.departureButton.setAttribute('aria-expanded', target === 'departure' ? 'true' : 'false');
    el.returnButton.setAttribute('aria-expanded', target === 'return' ? 'true' : 'false');
    renderDatePicker();
    window.setTimeout(() => el.datePicker.querySelector('[data-picker-action="close"]').focus(), 0);
  }

  function closeDatePicker() {
    if (el.datePicker.hidden) return;
    el.datePicker.hidden = true;
    document.body.style.overflow = '';
    el.departureButton.setAttribute('aria-expanded', 'false');
    el.returnButton.setAttribute('aria-expanded', 'false');
    if (state.picker.opener) state.picker.opener.focus();
  }

  function renderDatePicker() {
    const targetIsReturn = state.picker.target === 'return';
    el.pickerTargetLabel.textContent = targetIsReturn ? 'Choose return' : 'Choose departure';
    el.pickerRangeLabel.textContent = targetIsReturn
      ? `Return after ${formatFullDate(state.departure)} · minimum ${state.minStay} nights`
      : 'Select a departure, then a return';
    el.pickerError.textContent = state.picker.error;
    el.calendarMonths.replaceChildren(renderCalendarMonth(state.picker.displayMonth), renderCalendarMonth(addMonths(state.picker.displayMonth, 1)));
  }

  function renderCalendarMonth(monthDate) {
    const month = document.createElement('section');
    month.className = 'calendar-month';
    const title = document.createElement('h3');
    title.className = 'month-title';
    title.textContent = formatMonth(monthDate);
    const weekdayRow = document.createElement('div');
    weekdayRow.className = 'weekdays';
    ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].forEach((day) => {
      const cell = document.createElement('span');
      cell.textContent = day;
      weekdayRow.append(cell);
    });
    const grid = document.createElement('div');
    grid.className = 'calendar-grid';
    const first = new Date(Date.UTC(monthDate.getUTCFullYear(), monthDate.getUTCMonth(), 1));
    const weekdayOffset = (first.getUTCDay() + 6) % 7;
    for (let index = 0; index < weekdayOffset; index += 1) {
      const spacer = document.createElement('span');
      spacer.className = 'calendar-spacer';
      spacer.setAttribute('aria-hidden', 'true');
      grid.append(spacer);
    }
    const lastDay = new Date(Date.UTC(monthDate.getUTCFullYear(), monthDate.getUTCMonth() + 1, 0)).getUTCDate();
    const today = toISODate(new Date());
    for (let day = 1; day <= lastDay; day += 1) {
      const date = new Date(Date.UTC(monthDate.getUTCFullYear(), monthDate.getUTCMonth(), day));
      const iso = toISODate(date);
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'calendar-day';
      button.dataset.date = iso;
      button.textContent = String(day);
      button.setAttribute('aria-label', formatFullDate(iso));
      if (iso === today) button.classList.add('is-today');
      if (iso === state.departure) button.classList.add('is-departure');
      if (state.tripType === 'roundtrip' && iso === state.returnDate) button.classList.add('is-return');
      if (state.tripType === 'roundtrip' && state.departure && state.returnDate && iso > state.departure && iso < state.returnDate) button.classList.add('is-in-range');
      grid.append(button);
    }
    month.append(title, weekdayRow, grid);
    return month;
  }

  function selectCalendarDate(isoDate) {
    if (!parseDate(isoDate)) return;
    state.picker.error = '';
    if (state.tripType === 'oneway' || state.picker.target === 'departure') {
      state.departure = isoDate;
      if (state.tripType === 'oneway') {
        state.selectedMatrix.departure = isoDate;
        syncDateFields();
        closeDatePicker();
        return;
      }
      if (!state.returnDate || !dateIsAfter(state.returnDate, state.departure) || daysBetween(state.departure, state.returnDate) < state.minStay) {
        state.returnDate = addDays(state.departure, state.minStay);
      }
      state.picker.target = 'return';
      state.selectedMatrix = { departure: state.departure, returnDate: state.returnDate };
      syncDateFields();
      renderDatePicker();
      return;
    }

    if (!dateIsAfter(isoDate, state.departure)) {
      state.picker.error = 'Return date must be after the departure date.';
      renderDatePicker();
      return;
    }
    if (daysBetween(state.departure, isoDate) < state.minStay) {
      state.picker.error = `Choose a return at least ${state.minStay} nights after departure.`;
      renderDatePicker();
      return;
    }
    state.returnDate = isoDate;
    state.selectedMatrix = { departure: state.departure, returnDate: state.returnDate };
    syncDateFields();
    closeDatePicker();
  }

  function clearDates() {
    state.departure = null;
    state.returnDate = null;
    state.selectedMatrix = { departure: null, returnDate: null };
    state.picker.target = 'departure';
    state.picker.error = '';
    syncDateFields();
    renderDatePicker();
  }

  function validFare(rawFare) {
    if (!rawFare || typeof rawFare !== 'object') return null;
    const origin = String(rawFare.origin || '').toUpperCase();
    const destination = String(rawFare.destination || '').toUpperCase();
    const departure = String(rawFare.departure || '');
    const returnDate = rawFare.return_date ? String(rawFare.return_date) : null;
    const provider = String(rawFare.provider || '').toLowerCase();
    const price = rawFare.price_eur;
    const observedAt = String(rawFare.observed_at || '');
    const searchUrl = safeHttpUrl(rawFare.search_url);
    if (!isIata(origin) || !isIata(destination) || origin === destination) return null;
    if (!parseDate(departure) || (returnDate && (!parseDate(returnDate) || !dateIsAfter(returnDate, departure)))) return null;
    if (!Number.isFinite(price) || price <= 0) return null;
    if (!['google_flights', 'skyscanner'].includes(provider)) return null;
    if (String(rawFare.status) !== 'complete' || Number.isNaN(new Date(observedAt).getTime()) || !searchUrl) return null;
    return {
      origin,
      destination,
      departure,
      return_date: returnDate,
      provider,
      price_eur: price,
      observed_at: observedAt,
      status: 'complete',
      search_url: searchUrl
    };
  }

  function allFares() {
    return state.archive.fares;
  }

  function findLowestFare(origin, destination, departure, returnDate) {
    return allFares()
      .filter((fare) => fare.origin === origin && fare.destination === destination && fare.departure === departure && fare.return_date === returnDate)
      .sort((first, second) => first.price_eur - second.price_eur || new Date(second.observed_at) - new Date(first.observed_at))[0] || null;
  }

  function getSearchQuery() {
    clearFormMessage();
    const originIsValid = commitAirport('origin');
    const destinationIsValid = commitAirport('destination');
    if (!originIsValid || !destinationIsValid) return null;
    if (state.origin.code === state.destination.code) {
      showFormMessage('Choose different origin and destination airport codes.');
      return null;
    }
    if (!parseDate(state.departure)) {
      showFormMessage('Choose a departure date before searching.');
      return null;
    }
    if (state.tripType === 'roundtrip') {
      if (!parseDate(state.returnDate)) {
        showFormMessage('Choose a return date before searching.');
        return null;
      }
      if (!dateIsAfter(state.returnDate, state.departure)) {
        showFormMessage('Return must be after departure.');
        return null;
      }
      if (daysBetween(state.departure, state.returnDate) < state.minStay) {
        showFormMessage(`Choose a stay of at least ${state.minStay} nights.`);
        return null;
      }
    }
    return {
      origin: state.origin.code,
      destination: state.destination.code,
      departure: state.departure,
      return_date: state.tripType === 'roundtrip' ? state.returnDate : null,
      trip_type: state.tripType,
      flex_days: state.flexible ? state.flexDays : 0,
      min_stay_nights: state.minStay
    };
  }

  function eligiblePairs(query) {
    const offsets = query.flex_days > 0
      ? Array.from({ length: query.flex_days * 2 + 1 }, (_, index) => index - query.flex_days)
      : [0];
    const pairs = [];
    offsets.forEach((departureOffset) => {
      if (query.trip_type === 'oneway') {
        pairs.push({ departure: addDays(query.departure, departureOffset), returnDate: null });
        return;
      }
      offsets.forEach((returnOffset) => {
        const departure = addDays(query.departure, departureOffset);
        const returnDate = addDays(query.return_date, returnOffset);
        if (dateIsAfter(returnDate, departure) && daysBetween(departure, returnDate) >= query.min_stay_nights) {
          pairs.push({ departure, returnDate });
        }
      });
    });
    return pairs;
  }

  function buildGoogleUrl(query) {
    const terms = query.trip_type === 'roundtrip'
      ? `Flights from ${query.origin} to ${query.destination} on ${query.departure} return ${query.return_date}`
      : `Flights from ${query.origin} to ${query.destination} on ${query.departure} one way`;
    return `https://www.google.com/travel/flights?q=${encodeURIComponent(terms)}&hl=en&gl=FI&curr=EUR`;
  }

  function compactDate(isoDate) {
    const date = parseDate(isoDate);
    return date ? `${String(date.getUTCFullYear()).slice(-2)}${String(date.getUTCMonth() + 1).padStart(2, '0')}${String(date.getUTCDate()).padStart(2, '0')}` : '';
  }

  function buildSkyscannerUrl(query) {
    const origin = encodeURIComponent(query.origin.toLowerCase());
    const destination = encodeURIComponent(query.destination.toLowerCase());
    const departure = compactDate(query.departure);
    const returnPart = query.trip_type === 'roundtrip' && query.return_date ? `/${compactDate(query.return_date)}` : '';
    return `https://www.skyscanner.fi/transport/flights/${origin}/${destination}/${departure}${returnPart}/?adultsv2=1&cabinclass=economy&rtn=${query.trip_type === 'roundtrip' ? 1 : 0}&currency=EUR&sortby=cheapest&outboundaltsenabled=false&inboundaltsenabled=false`;
  }

  function providerName(provider) {
    return provider === 'google_flights' ? 'Google Flights' : 'Skyscanner';
  }

  function renderArchiveStatus() {
    el.archiveStatus.classList.remove('is-ready', 'is-error');
    if (state.archive.status === 'error') {
      el.archiveStatus.classList.add('is-error');
      el.archiveStatusText.textContent = state.archive.error;
      return;
    }
    el.archiveStatus.classList.add('is-ready');
    if (!currentJob) {
      el.archiveStatusText.textContent = 'Local app ready · Search fetches fresh prices from both providers';
      return;
    }
    const progress = Object.entries(currentJob.providers || {}).map(([provider, info]) => {
      const warnings = Object.entries(info.statuses).filter(([status]) => status !== 'observed')
        .map(([status, count]) => `${count} ${status.replaceAll('_', ' ')}`).join(', ');
      return `${providerName(provider)} ${info.verified}/${info.expected}${warnings ? ` (${warnings})` : ''}`;
    }).join(' · ');
    el.archiveStatusText.textContent = `${currentJob.message} ${progress} · ${Math.floor((currentJob.elapsed_seconds || 0) / 60)}m elapsed`;
  }

  function renderResults() {
    const query = state.activeQuery;
    if (!query) return;
    el.results.hidden = false;
    el.resultsTitle.innerHTML = `${escapeHTML(query.origin)} <span aria-hidden="true">→</span> ${escapeHTML(query.destination)}`;
    el.resultsType.textContent = `${query.trip_type === 'roundtrip' ? 'Round trip' : 'One way'} · 1 adult · Economy`;
    el.resultDescription.textContent = `Fresh search centred on ${formatFullDate(query.matrix_departure || query.departure)}${query.flex_days ? ` · ±${query.flex_days} days` : ''}. Prices appear as each provider finishes.`;
    const recordedSource = (provider) => allFares().find((fare) => fare.provider === provider && fare.origin === query.origin && fare.destination === query.destination && fare.departure === query.departure && fare.return_date === query.return_date);
    el.googleLink.href = recordedSource('google_flights')?.search_url || buildGoogleUrl(query);
    el.skyscannerLink.href = recordedSource('skyscanner')?.search_url || buildSkyscannerUrl(query);
    renderSummary(query);
    el.matrixSection.hidden = false;
    renderMatrix(query);
  }

  function renderSummary(query) {
    const selectedFare = findLowestFare(query.origin, query.destination, query.departure, query.return_date);
    if (currentJob?.status === 'running' && !selectedFare) {
      el.resultSummary.innerHTML = `
        <div class="summary-card is-empty">
          <span class="summary-icon empty" aria-hidden="true">…</span>
          <div><p class="summary-title">Searching both providers…</p><p class="summary-copy">Keep this app open. The grid fills with verified prices as searches finish. If a browser asks for a human check, complete it there.</p></div>
        </div>`;
      return;
    }
    if (state.archive.status === 'error') {
      el.resultSummary.innerHTML = `
        <div class="summary-card is-empty">
          <span class="summary-icon empty" aria-hidden="true">!</span>
          <div><p class="summary-title">Could not reach the local app</p><p class="summary-copy">Keep the Flight Finder terminal open, then reload this page.</p></div>
        </div>`;
      return;
    }
    if (!selectedFare) {
      const pairs = eligiblePairs(query);
      const scanned = pairs.filter((pair) => findLowestFare(query.origin, query.destination, pair.departure, pair.returnDate)).length;
      el.resultSummary.innerHTML = `
        <div class="summary-card is-empty">
          <span class="summary-icon empty" aria-hidden="true">○</span>
          <div><p class="summary-title">No verified price for these dates</p><p class="summary-copy">${scanned ? `${scanned} nearby date combination${scanned === 1 ? '' : 's'} returned prices.` : 'The providers did not return a complete, verified price.'} Check the status above or try the provider links.</p></div>
        </div>`;
      return;
    }
    const sourceUrl = safeHttpUrl(selectedFare.search_url);
    const sourceLink = sourceUrl ? `<a class="recorded-label" href="${escapeHTML(sourceUrl)}" target="_blank" rel="noopener noreferrer">Recorded source ↗</a>` : '<span class="recorded-label">Recorded archive</span>';
    el.resultSummary.innerHTML = `
      <div class="summary-card">
        <span class="summary-icon" aria-hidden="true">✓</span>
        <div>
          <p class="summary-overline">Lowest complete observation for this pair</p>
          <p class="summary-title">${escapeHTML(providerName(selectedFare.provider))} · <span class="recorded-label">Fetched in this search</span></p>
          <p class="summary-copy">Observed ${escapeHTML(formatObservedAt(selectedFare.observed_at))} · ${sourceLink}</p>
        </div>
        <div class="summary-price-wrap"><div class="summary-price">${escapeHTML(formatEuro(selectedFare.price_eur))}</div><p class="summary-source">Not an availability guarantee</p></div>
      </div>`;
    const comparisons = ['google_flights', 'skyscanner'].map((provider) => {
      const fare = allFares().find((item) => item.provider === provider && item.origin === query.origin && item.destination === query.destination && item.departure === query.departure && item.return_date === query.return_date);
      return `<div class="provider-record"><strong>${escapeHTML(providerName(provider))}</strong><span>${fare ? escapeHTML(formatEuro(fare.price_eur)) : 'Not scanned'}</span><small>${fare ? 'Recorded ' + escapeHTML(formatObservedAt(fare.observed_at)) : 'Use the provider link to check these dates.'}</small></div>`;
    });
    el.resultSummary.insertAdjacentHTML('beforeend', `<div class="provider-records">${comparisons.join('')}</div>`);
  }

  function renderMatrix(query) {
    const radius = query.flex_days;
    const departures = Array.from({ length: radius * 2 + 1 }, (_, index) => addDays(query.matrix_departure || query.departure, index - radius));
    const returns = query.trip_type === 'oneway' ? [null] : Array.from({ length: radius * 2 + 1 }, (_, index) => addDays(query.matrix_return || query.return_date, index - radius));
    document.getElementById('matrix-title').textContent = radius ? `${radius * 2 + 1}-day flexible fare grid` : 'Your selected dates';
    const matrixFares = [];
    departures.forEach((departure) => returns.forEach((returnDate) => {
      if (returnDate === null || (dateIsAfter(returnDate, departure) && daysBetween(departure, returnDate) >= query.min_stay_nights)) {
        const fare = findLowestFare(query.origin, query.destination, departure, returnDate);
        if (fare) matrixFares.push({ fare, departure, returnDate });
      }
    }));
    const best = matrixFares.sort((first, second) => first.fare.price_eur - second.fare.price_eur)[0] || null;
    const tableHead = document.createElement('thead');
    const headRow = document.createElement('tr');
    const corner = document.createElement('th');
    corner.className = 'matrix-corner';
    corner.scope = 'col';
    corner.textContent = 'Dep. \ Ret.';
    headRow.append(corner);
    returns.forEach((returnDate) => {
      const header = document.createElement('th');
      header.className = 'matrix-header';
      header.scope = 'col';
      if (returnDate === state.selectedMatrix.returnDate) header.classList.add('is-selected');
      header.textContent = returnDate ? formatHeaderDate(returnDate) : 'One way';
      headRow.append(header);
    });
    tableHead.append(headRow);
    const body = document.createElement('tbody');
    departures.forEach((departure) => {
      const row = document.createElement('tr');
      const rowHeader = document.createElement('th');
      rowHeader.className = 'matrix-row-header';
      rowHeader.scope = 'row';
      if (departure === state.selectedMatrix.departure) rowHeader.classList.add('is-selected');
      rowHeader.textContent = formatHeaderDate(departure);
      row.append(rowHeader);
      returns.forEach((returnDate) => {
        const cell = document.createElement('td');
        cell.className = 'matrix-cell';
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'fare-cell';
        const pairIsValid = returnDate === null || (dateIsAfter(returnDate, departure) && daysBetween(departure, returnDate) >= query.min_stay_nights);
        const fare = pairIsValid ? findLowestFare(query.origin, query.destination, departure, returnDate) : null;
        const selected = departure === state.selectedMatrix.departure && returnDate === state.selectedMatrix.returnDate;
        const rowOrColumn = departure === state.selectedMatrix.departure || returnDate === state.selectedMatrix.returnDate;
        const bestMatch = best && best.departure === departure && best.returnDate === returnDate && best.fare === fare;
        button.dataset.departure = departure;
        button.dataset.returnDate = returnDate || '';
        if (selected) button.classList.add('is-selected');
        else if (rowOrColumn) button.classList.add('is-row-or-column');
        if (bestMatch) button.classList.add('is-best');
        if (!pairIsValid) {
          button.disabled = true;
          button.setAttribute('aria-label', `${formatHeaderDate(departure)} to ${formatHeaderDate(returnDate)} is below the ${query.min_stay_nights}-night minimum`);
          appendFareCellText(button, `Min. ${query.min_stay_nights} nights`, 'fare-cell-muted');
        } else if (fare) {
          button.setAttribute('aria-label', `${formatHeaderDate(departure)} to ${formatHeaderDate(returnDate)}, ${formatEuro(fare.price_eur)}, recorded from ${providerName(fare.provider)} on ${formatObservedAt(fare.observed_at)}`);
          appendFareCellText(button, formatEuro(fare.price_eur), 'fare-cell-price');
          appendFareCellText(button, providerName(fare.provider), 'fare-cell-muted');
          appendFareCellText(button, `Recorded · ${formatObservedAt(fare.observed_at, true)}`, 'fare-cell-recorded');
          if (bestMatch) {
            const badge = document.createElement('span');
            badge.className = 'best-badge';
            badge.textContent = 'Best';
            button.append(badge);
          }
        } else {
          button.setAttribute('aria-label', `${formatHeaderDate(departure)} to ${formatHeaderDate(returnDate)}, not scanned`);
          appendFareCellText(button, currentJob?.status === 'running' ? 'Searching…' : 'No verified price', 'fare-cell-muted');
        }
        cell.append(button);
        row.append(cell);
      });
      body.append(row);
    });
    el.fareMatrix.replaceChildren(tableHead, body);
  }

  function appendFareCellText(button, text, className) {
    const span = document.createElement('span');
    span.className = className;
    span.textContent = text;
    button.append(span);
  }

  async function startLiveSearch() {
    const query = getSearchQuery();
    if (!query || !sessionToken || currentJob?.status === 'running') return;
    setBusy(true);
    try {
      const payload = await api('/api/jobs', query);
      state.activeQuery = { ...query, matrix_departure: query.departure, matrix_return: query.return_date };
      state.selectedMatrix = { departure: query.departure, returnDate: query.return_date };
      applyJob(payload);
      pollTimer = window.setTimeout(pollJob, 2000);
      el.results.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (error) {
      setBusy(false);
      showFormMessage(error.message);
    }
  }

  function setBusy(busy) {
    document.getElementById('search-button').disabled = busy || !sessionToken;
    document.getElementById('search-button').querySelector('span').textContent = busy ? 'Searching…' : 'Search flights';
    document.getElementById('stop-search').hidden = !busy;
  }

  async function api(path, body) {
    const response = await fetch(path, {
      method: body === undefined ? 'GET' : 'POST',
      headers: body === undefined ? { Accept: 'application/json' } : { 'Content-Type': 'application/json', 'X-Flight-Token': sessionToken },
      body: body === undefined ? undefined : JSON.stringify(body), cache: 'no-store'
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || 'Could not reach the local app.');
    return payload;
  }

  function applyJob(payload) {
    currentJob = payload;
    state.archive.fares = payload.fares.map(validFare).filter(Boolean);
    state.archive.status = 'ready';
    setBusy(payload.status === 'running');
    renderArchiveStatus();
    renderResults();
  }

  async function pollJob() {
    if (!currentJob || currentJob.status !== 'running') return;
    const pollingId = currentJob.id;
    try {
      const payload = await api(`/api/jobs/${pollingId}`);
      if (currentJob.id !== pollingId || currentJob.status !== 'running') return;
      applyJob(payload);
      if (currentJob.status === 'running') pollTimer = window.setTimeout(pollJob, 2000);
      else showToast('Search finished. Your results are ready.');
    } catch (error) {
      if (currentJob.id !== pollingId || currentJob.status !== 'running') return;
      state.archive.status = 'error';
      state.archive.error = `${error.message} Retrying…`;
      renderArchiveStatus();
      pollTimer = window.setTimeout(pollJob, 5000);
    }
  }

  async function connectLocalApp() {
    try {
      const session = await api('/api/session');
      sessionToken = session.token;
      setBusy(false);
      if (session.job) {
        const query = session.job.query;
        state.origin = airportFromCode(query.origin);
        state.destination = airportFromCode(query.destination);
        state.departure = query.departure;
        state.returnDate = query.return_date;
        state.tripType = query.trip_type;
        state.flexible = query.flex_days > 0;
        state.flexDays = query.flex_days || 3;
        state.minStay = query.min_stay_nights;
        document.querySelector(`input[name="tripType"][value="${state.tripType}"]`).checked = true;
        state.activeQuery = { ...query, matrix_departure: query.departure, matrix_return: query.return_date };
        state.selectedMatrix = { departure: query.departure, returnDate: query.return_date };
        syncForm();
        applyJob(session.job);
        if (currentJob.status === 'running') pollTimer = window.setTimeout(pollJob, 2000);
      }
    } catch (_) {
      state.archive.status = 'error';
      state.archive.error = 'Start Flight Finder with the local launcher, then open http://127.0.0.1:4173.';
      setBusy(false);
    }
    renderArchiveStatus();
  }

  function bindEvents() {
    document.querySelectorAll('input[name="tripType"]').forEach((input) => {
      input.addEventListener('change', () => {
        state.tripType = input.value;
        clearFormMessage();
        syncDateFields();
        if (!el.datePicker.hidden) renderDatePicker();
      });
    });

    [['origin', el.originInput, el.originSuggestions], ['destination', el.destinationInput, el.destinationSuggestions]].forEach(([kind, input, list]) => {
      input.addEventListener('focus', () => renderSuggestions(kind));
      input.addEventListener('input', () => renderSuggestions(kind));
      input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          if (commitAirport(kind)) hideSuggestions();
        }
        if (event.key === 'ArrowDown' && !list.hidden) {
          event.preventDefault();
          list.querySelector('button')?.focus();
        }
      });
      input.addEventListener('blur', () => window.setTimeout(hideSuggestions, 150));
      list.addEventListener('mousedown', (event) => event.preventDefault());
      list.addEventListener('click', (event) => {
        const button = event.target.closest('button[data-airport-code]');
        if (button) chooseAirport(kind, button.dataset.airportCode);
      });
    });

    el.swapButton.addEventListener('click', () => {
      if (!commitAirport('origin') || !commitAirport('destination')) return;
      [state.origin, state.destination] = [state.destination, state.origin];
      syncAirportInputs();
      clearFormMessage();
      showToast('Origin and destination swapped.');
    });

    el.departureButton.addEventListener('click', () => openDatePicker('departure'));
    el.returnButton.addEventListener('click', () => { if (state.tripType === 'roundtrip') openDatePicker('return'); });
    el.passengerButton.addEventListener('click', () => showToast('Searches use 1 adult in Economy, EUR, Finland market.'));
    el.flexibleToggle.addEventListener('change', () => {
      state.flexible = el.flexibleToggle.checked;
      syncOptionControls();
    });
    el.flexDays.addEventListener('change', () => { state.flexDays = Number(el.flexDays.value); });
    el.minStay.addEventListener('change', () => {
      state.minStay = Number(el.minStay.value);
      if (!el.datePicker.hidden) renderDatePicker();
    });
    el.form.addEventListener('submit', (event) => {
      event.preventDefault();
      startLiveSearch();
    });
    document.getElementById('stop-search').addEventListener('click', async () => {
      if (!currentJob) return;
      try {
        window.clearTimeout(pollTimer);
        applyJob(await api(`/api/jobs/${currentJob.id}/cancel`, {}));
      } catch (error) {
        showFormMessage(error.message);
        pollTimer = window.setTimeout(pollJob, 2000);
      }
    });

    el.datePicker.addEventListener('click', (event) => {
      if (event.target === el.datePicker) {
        closeDatePicker();
        return;
      }
      const action = event.target.closest('[data-picker-action]')?.dataset.pickerAction;
      if (action === 'close') closeDatePicker();
      if (action === 'previous') {
        state.picker.displayMonth = addMonths(state.picker.displayMonth, -1);
        renderDatePicker();
      }
      if (action === 'next') {
        state.picker.displayMonth = addMonths(state.picker.displayMonth, 1);
        renderDatePicker();
      }
      if (action === 'clear') clearDates();
      const day = event.target.closest('button[data-date]');
      if (day) selectCalendarDate(day.dataset.date);
    });

    el.fareMatrix.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-departure][data-return-date]');
      if (!button || button.disabled || !state.activeQuery) return;
      state.departure = button.dataset.departure;
      state.returnDate = button.dataset.returnDate || null;
      state.selectedMatrix = { departure: state.departure, returnDate: state.returnDate };
      state.activeQuery = {
        ...state.activeQuery,
        departure: state.departure,
        return_date: state.returnDate
      };
      syncDateFields();
      renderResults();
      showToast(`Selected ${formatFullDate(state.departure)}${state.returnDate ? ` to ${formatFullDate(state.returnDate)}` : ' one way'}.`);
    });

    document.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape') return;
      if (!el.datePicker.hidden) {
        event.preventDefault();
        closeDatePicker();
      } else {
        hideSuggestions();
      }
    });
  }

  function initialize() {
    const today = new Date();
    if (state.departure < toISODate(today)) {
      state.departure = addDays(toISODate(today), 60);
      state.returnDate = addDays(state.departure, 31);
      state.picker.displayMonth = parseDate(state.departure);
    }
    syncForm();
    bindEvents();
    renderArchiveStatus();
    setBusy(false);
    connectLocalApp();
  }

  initialize();
})();
