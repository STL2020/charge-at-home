// eCharge@Home — Frontend-Logik
// Navigation, Theme, echte i18n-Umschaltung und Fetch-Aufrufe gegen Flask (Sprint 0)

// ── Anfragen bündeln ───────────────────────────────────────────────────────
//
// Beim Auffrischen des Dashboards rufen mehrere Funktionen dieselben
// Endpunkte auf: /api/trips sechsmal, /api/wallboxes/full fünfmal — innerhalb
// derselben Sekunde. Jede Antwort ist identisch, nur die Last nicht.
//
// hole() fasst gleiche Anfragen zusammen: Läuft bereits eine, wird deren
// Ergebnis geteilt. Kurz danach kommt es aus dem Zwischenspeicher. Die
// aufrufenden Funktionen bleiben unverändert — sie bekommen weiterhin ein
// Promise.
const _laufend = new Map();      // Adresse → laufendes Promise
const _zuletzt = new Map();      // Adresse → { zeit, daten }
const HALTBAR_MS = 2000;         // so lange gilt eine Antwort als frisch

async function hole(adresse, optionen) {
  // Schreibende Aufrufe nie bündeln — sie sollen jedes Mal ankommen.
  if (optionen && optionen.method && optionen.method !== 'GET') {
    return fetch(adresse, optionen);
  }

  const frisch = _zuletzt.get(adresse);
  if (frisch && Date.now() - frisch.zeit < HALTBAR_MS) {
    return { ok: true, json: async () => frisch.daten, _ausSpeicher: true };
  }

  if (_laufend.has(adresse)) return _laufend.get(adresse);

  const p = (async () => {
    try {
      const r = await fetch(adresse, optionen);
      const daten = await r.json();
      _zuletzt.set(adresse, { zeit: Date.now(), daten });
      return { ok: r.ok, status: r.status, json: async () => daten };
    } finally {
      _laufend.delete(adresse);
    }
  })();

  _laufend.set(adresse, p);
  return p;
}

// Nach dem Schreiben muss der Zwischenspeicher weg, sonst zeigt die
// Oberfläche noch den alten Stand.
function speicherLeeren(teil) {
  if (!teil) { _zuletzt.clear(); return; }
  for (const schluessel of _zuletzt.keys()) {
    if (schluessel.includes(teil)) _zuletzt.delete(schluessel);
  }
}

// Jeder schreibende Aufruf verwirft den Zwischenspeicher — unabhängig davon,
// welche Funktion ihn ausgelöst hat. Ohne diese Kopplung müsste an jeder
// einzelnen Stelle daran gedacht werden, und genau das geht schief.
(function () {
  const echtesFetch = window.fetch.bind(window);
  window.fetch = function (adresse, optionen) {
    const schreibend = optionen && optionen.method &&
                       optionen.method.toUpperCase() !== 'GET';
    const ergebnis = echtesFetch(adresse, optionen);
    if (schreibend) ergebnis.then(() => speicherLeeren()).catch(() => {});
    return ergebnis;
  };
})();

// Monatsnamen — hier oben, damit sie vor jedem Zugriff bereitstehen.
var MONATSNAMEN = ['Januar','Februar','März','April','Mai','Juni','Juli',
                   'August','September','Oktober','November','Dezember'];

const I18N = JSON.parse(document.getElementById('i18n-data').textContent);
const APP_STATE = JSON.parse(document.getElementById('app-state').textContent);

let currentLang = document.documentElement.lang || 'de';
let selectedFallCode = APP_STATE.user ? APP_STATE.user.abrechnungsfall : 'C';

// ---------- Navigation ----------
// ── Menü auf schmalen Bildschirmen ────────────────────────────────────────
function navUmschalten() { document.body.classList.toggle('nav-offen'); }
function navSchliessen() { document.body.classList.remove('nav-offen'); }

const navItems = document.querySelectorAll('.nav-item');
navItems.forEach(item => item.addEventListener('click', () => {
  showView(item.dataset.view);
  navSchliessen();   // nach der Auswahl zuklappen
}));

function showView(name) {
  // Protokoll-Auto-Refresh stoppen wenn man die Seite verlässt
  if (name !== 'protokoll' && window._protokollInterval) {
    clearInterval(window._protokollInterval);
    window._protokollInterval = null;
  }
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.getElementById('view-' + name).classList.add('active');
  navItems.forEach(n => n.classList.toggle('active', n.dataset.view === name));
  window.scrollTo(0, 0);
  loadNavBadges();
  if (name === 'dashboard') {
    // Beim Betreten des Dashboards: Werte zurücksetzen damit Animation neu läuft
    document.querySelectorAll('#view-dashboard .count-up').forEach(el => { el.textContent = '0'; el.dataset.animated = ''; });
    const chartSvg = document.getElementById('recent-sessions-chart-svg');
    if (chartSvg) chartSvg.dataset.rendered = '';  // Re-Render mit Animation erzwingen
    loadDashboardSummary();
    dashLeerPruefen();
  proKennzeichnungSetzen();
    loadDashboardWallboxes().then(async () => {
      const sel = document.getElementById('chart-wallbox-filter');
      if (sel && sel.options.length <= 1) {
        try {
          const r = await hole('/api/wallboxes/full');
          const d = await r.json();
          d.wallboxes.forEach(wb => {
            const opt = document.createElement('option');
            opt.value = wb.name;
            opt.textContent = wb.name;
            sel.appendChild(opt);
          });
        } catch (e) {}
      }
    });
    loadRecentSessionsChart(true);  // animiert
  } else if (name === 'protokoll') {
    loadProtokoll();
    // Auto-Refresh alle 5 Sekunden
    if (window._protokollInterval) clearInterval(window._protokollInterval);
    window._protokollInterval = setInterval(loadProtokoll, 5000);
  } else if (name === 'ladesessions') {
    pruefeBmwImportBereich();
    ladePreiseVorladen();
    loadWallboxesIntoFilter().then(loadSessions);
  } else if (name === 'fahrten') {
    pruefeBmwImportBereich();
    loadTrips();
    ladeAnlaesse();          // Auswahlliste für den Anlass füllen
  } else if (name === 'fahrzeuge') {
    loadVehiclesView();
  } else if (name === 'einstellungen') {
    loadHomeAddress();
    loadBmfReference();
    loadVehicleDescription();
    ladeAnlaesseInFeld();    // Katalog zum Bearbeiten laden
    // Allgemein-Tab als Standard aktivieren
    setTimeout(() => showSettingsTab('allgemein', document.querySelector('.settings-tab')), 0);
  } else if (name === 'wallbox') {
    loadWallboxesTable();
    _pruefeDoppelteWallbox();
    updateOcppConnectionUrl();
    loadTopologyLivePower();
    loadPollInterval();
    populateOcppClientWallboxSelect();
  } else if (name === 'belege') {
    loadPersonsIntoBelegDropdown();
    loadBelegverlauf();
    onBelegTypChange();
  } else if (name === 'auswertung') {
    loadAnalytics();
  } else if (name === 'konfigurator') {
    initKonfigurator();
  }
}

// ---------- Theme ----------
function setTheme(mode) {
  document.documentElement.setAttribute('data-theme', mode);
  document.getElementById('btn-light').classList.toggle('active', mode === 'light');
  document.getElementById('btn-dark').classList.toggle('active', mode === 'dark');
  const sl = document.getElementById('setup-theme-light');
  const sd = document.getElementById('setup-theme-dark');
  if (sl) sl.classList.toggle('on', mode === 'light');
  if (sd) sd.classList.toggle('on', mode === 'dark');
  window.currentTheme = mode;
}

// ---------- i18n (echte Umschaltung, kein reiner Deko-Toggle) ----------
function applyI18n(lang) {
  currentLang = lang;
  document.documentElement.lang = lang;
  const dict = I18N[lang] || I18N['de'];
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    if (dict[key]) {
      el.textContent = dict[key];
    }
  });
  // Sprachumschaltung entfernt (Pflichtenheft, Wunschkriterium):
  // Die Oberflaeche ist durchgaengig deutsch. Eine englische Fassung waere
  // nur zu einem kleinen Teil uebersetzbar gewesen — Begriffe wie
  // "steuerfreier Auslagenersatz nach § 3 Nr. 50 EStG" oder "Werbungskosten"
  // haben keine englische Entsprechung, weil die Sachverhalte ausserhalb
  // Deutschlands nicht existieren. Ein Umschalter, der 5 % uebersetzt und
  // den Rest deutsch laesst, ist schlechter als gar keiner.


  // Zahlen und Datumsangaben sind bereits gerendert und tragen noch das
  // Format der vorherigen Sprache — daher die aktive Ansicht neu aufbauen.
  try {
    const aktiv = document.querySelector('.view.active');
    const name = aktiv ? aktiv.id.replace('view-', '') : null;
    if (name) showView(name);
  } catch (e) { /* Umschaltung darf nie die Oberfläche blockieren */ }
}

function setLang(lang) {
  // Beibehalten fuer alte Verweise; die Anwendung ist einsprachig.
  if (lang && lang !== 'de') return;
  applyI18n(lang);
}

// ---------- Fahrten: Satz-Auswahl (rein visuell, Sprint 2) ----------
function setRate(btn) {
  btn.parentElement.querySelectorAll('button').forEach(b => b.classList.remove('on'));
  btn.classList.add('on');
}

// ---------- Ladesessions: Formular ein-/ausblenden ----------
function toggleNewSession() {
  const f = document.getElementById('new-session-form');
  const opening = f.style.display === 'none';
  f.style.display = opening ? 'block' : 'none';
  if (opening) {
    loadSessionFormSuggestions();
  } else {
    resetSessionForm();
  }
}

function resetSessionForm() {
  editingSessionId = null;
  document.getElementById('session-form-title').textContent = 'Session manuell erfassen';
  document.getElementById('ms-wallbox').value = '';
  document.getElementById('ms-rfid').value = '';
  document.getElementById('ms-start').value = '';
  document.getElementById('ms-end').value = '';
  document.getElementById('ms-meter-start').value = '';
  document.getElementById('ms-meter-end').value = '';
  document.getElementById('manual-session-message').textContent = '';
}

async function autofillLastMeter() {
  if (editingSessionId) return; // beim Bearbeiten nicht ueberschreiben
  const name = document.getElementById('ms-wallbox').value.trim();
  if (!name) return;
  try {
    const resp = await fetch('/api/wallboxes/last-meter?name=' + encodeURIComponent(name));
    const data = await resp.json();
    if (data.last_meter_wh !== null && data.last_meter_wh !== undefined) {
      document.getElementById('ms-meter-start').value = data.last_meter_wh;
    }
  } catch (e) { /* optionaler Komfort, kein Blocker */ }
}

async function loadSessionFormSuggestions() {
  try {
    const [wbResp, tagResp] = await Promise.all([
      fetch('/api/wallboxes'), fetch('/api/rfid-tags'),
    ]);
    const wbData = await wbResp.json();
    const tagData = await tagResp.json();
    const wbList = document.getElementById('wallbox-suggestions');
    const tagList = document.getElementById('rfid-suggestions');
    wbList.innerHTML = wbData.wallboxes.map(wb => `<option value="${wb.name}">`).join('');
    tagList.innerHTML = tagData.tags.map(t => `<option value="${t}">`).join('');
  } catch (e) { /* Vorschlaege sind optional, Formular bleibt nutzbar */ }
}
// Dienst/Privat-Toggle in der Ladesessions-Tabelle (rein visuell, Sprint 1)
document.querySelectorAll('.toggle-pair button').forEach(btn => {
  btn.addEventListener('click', (e) => {
    const pair = e.target.parentElement;
    if (pair.closest('td')) {
      pair.querySelectorAll('button').forEach(b => b.classList.remove('on'));
      e.target.classList.add('on');
    }
  });
});

// ---------- Setup: Fall-Auswahl ----------
function selectFall(card) {
  document.querySelectorAll('.fall-card').forEach(c => c.classList.remove('selected'));
  card.classList.add('selected');
  selectedFallCode = card.dataset.fall;
}
(function initFallSelection() {
  document.querySelectorAll('.fall-card').forEach(card => {
    if (card.dataset.fall === selectedFallCode) card.classList.add('selected');
  });
})();

// ---------- Setup: echte Anbindung an Flask (FA-SYS-04) ----------
async function submitSetup() {
  const nameInput = document.getElementById('setup-name-input');
  const name = nameInput.value.trim();
  const msgEl = document.getElementById('setup-message');

  if (!name) {
    msgEl.textContent = I18N[currentLang]['setup.error.name_required'] || 'Bitte einen Namen eingeben.';
    msgEl.style.color = 'var(--danger)';
    return;
  }

  try {
    const resp = await fetch('/api/setup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: name,
        abrechnungsfall: selectedFallCode,
        language_pref: currentLang,
        theme_pref: window.currentTheme || document.documentElement.getAttribute('data-theme'),
      }),
    });
    if (resp.ok) {
      msgEl.textContent = I18N[currentLang]['setup.success'] || 'Gespeichert.';
      msgEl.style.color = 'var(--success)';
      setTimeout(() => location.reload(), 700);
    } else {
      msgEl.textContent = 'Fehler beim Speichern (Server antwortete mit ' + resp.status + ').';
      msgEl.style.color = 'var(--danger)';
    }
  } catch (err) {
    msgEl.textContent = 'Netzwerkfehler: ' + err;
    msgEl.style.color = 'var(--danger)';
  }
}

// ---------- Einstellungen: Preis speichern (FA-LS-05-Grundgeruest) ----------
async function savePrice() {
  const raw = document.getElementById('price-input').value.replace('€', '').replace(',', '.').trim();
  const price = parseFloat(raw);
  if (isNaN(price)) { alert('Ungültiger Preis.'); return; }
  const resp = await fetch('/api/price', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ default_kwh_price: price }),
  });
  if (resp.ok) { alert('Preis gespeichert.'); } else { alert('Fehler beim Speichern.'); }
}

// ---------- Einstellungen: Lizenz aktivieren ----------



// ---------- Ladesessions: dynamisches Laden/Speichern (Sprint 1) ----------

async function loadWallboxesIntoFilter() {
  try {
    const resp = await fetch('/api/wallboxes');
    const data = await resp.json();
    const sel = document.getElementById('filter-wallbox');
    if (!sel) return;
    const current = sel.value;
    sel.innerHTML = '<option value="">Alle Wallboxen</option>';
    data.wallboxes.forEach(wb => {
      const opt = document.createElement('option');
      opt.value = wb.id;
      opt.textContent = wb.name;
      sel.appendChild(opt);
    });
    sel.value = current;
  } catch (e) { /* still fine, Filter bleibt leer */ }
}

function fmtDe(num, decimals) {
  // Sprachabhängige Zahlenformatierung (Sprint 7): Deutsch 1.234,56 —
  // Englisch 1,234.56. Der Name bleibt aus Kompatibilitätsgründen erhalten,
  // die Funktion wird an rund 90 Stellen aufgerufen.
  const n = Number(num);
  const d = decimals ?? 0;
  if (!isFinite(n)) return (0).toFixed(d).replace('.', currentLang === 'en' ? '.' : ',');
  try {
    return new Intl.NumberFormat(currentLang === 'en' ? 'en-US' : 'de-DE', {
      minimumFractionDigits: d, maximumFractionDigits: d,
    }).format(n);
  } catch (e) {
    // Fallback, falls Intl nicht verfügbar ist
    return n.toFixed(d).replace('.', currentLang === 'en' ? '.' : ',');
  }
}

// Datum sprachabhängig: TT.MM.JJJJ (de) bzw. YYYY-MM-DD (en).
// Erwartet ein ISO-Datum "YYYY-MM-DD" oder einen Zeitstempel mit führendem Datum.
function fmtDatum(iso) {
  if (!iso) return '—';
  const d = String(iso).slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) return String(iso);
  if (currentLang === 'en') return d;
  const [y, m, t] = d.split('-');
  return `${t}.${m}.${y}`;
}

async function loadSessions() {
  const tbody = document.getElementById('sessions-tbody');
  if (!tbody) return;
  const von = document.getElementById('filter-von').value;
  const bis = document.getElementById('filter-bis').value;
  const wallboxId = document.getElementById('filter-wallbox').value;

  const params = new URLSearchParams();
  if (von) params.set('von', von);
  if (bis) params.set('bis', bis);
  if (wallboxId) params.set('wallbox_id', wallboxId);

  tbody.innerHTML = '<tr><td colspan="12" class="hint">Lade Sessions …</td></tr>';
  try {
    const resp = await fetch('/api/sessions?' + params.toString());
    const data = await resp.json();
    renderSessionsTable(data.sessions, data.show_classification);
    checkDuplicates(von, bis);  // Doppelabrechnungs-Prüfung
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="12" class="hint">Fehler beim Laden.</td></tr>';
  }
}

function renderSessionsTable(sessions, showClassification) {
  const thead = document.getElementById('sessions-thead');
  const tbody = document.getElementById('sessions-tbody');
  const klassTh = thead.querySelector('.th-klass');
  if (klassTh) klassTh.style.display = showClassification ? '' : 'none';

  // 0-kWh-Sessions ausblenden (Default), Toggle "Null-Sessions anzeigen"
  const showZero = document.getElementById('filter-show-zero')?.checked;
  const zeroCount = sessions.filter(s => (s.energy_kwh || 0) < 0.05).length;
  if (!showZero) sessions = sessions.filter(s => (s.energy_kwh || 0) >= 0.05);
  const zeroInfo = document.getElementById('zero-sessions-info');
  if (zeroInfo) {
    zeroInfo.textContent = (!showZero && zeroCount > 0) ? `${zeroCount} Sessions mit 0 kWh ausgeblendet` : '';
  }

  // Quellen-Filter: Alle / Loxone / OCPP / Manuell
  const srcFilter = document.getElementById('filter-source')?.value;
  if (srcFilter) {
    sessions = sessions.filter(s => {
      const src = (s.source || '').toLowerCase();
      if (srcFilter === 'loxone') return src.includes('loxone');
      if (srcFilter === 'ocpp') return src === 'ocpp';
      if (srcFilter === 'manuell') return !src.includes('loxone') && src !== 'ocpp';
      return true;
    });
  }

  if (sessions.length === 0) {
    tbody.innerHTML = '<tr><td colspan="12" class="hint">Keine Sessions im gewählten Zeitraum.</td></tr>';
    return;
  }

  tbody.innerHTML = '';
  sessions.forEach(s => {
    const dt = s.start_timestamp || '';
    const datePart = dt.slice(0, 10).split('-').reverse().join('.');
    const timePart = dt.slice(11, 16) + (s.end_timestamp ? '–' + s.end_timestamp.slice(11, 16) : '–offen');
    const statusPill = s.status === 'open'
      ? `<span class="pill pill-amber" style="cursor:pointer;" onclick="closeStaleSession(${s.id}, this)" title="Session manuell schließen"><span class="pill-dot"></span>Offene Session – klicken zum Schließen</span>`
      : '<span class="pill pill-green"><span class="pill-dot"></span>OK</span>';

    let klassCell = '';
    if (showClassification) {
      const dOn = s.classification === 'dienstlich' ? 'on' : '';
      const pOn = s.classification === 'privat' ? 'on' : '';
      klassCell = `<td class="td-klass"><div class="toggle-pair">
        <button class="${dOn}" onclick="classifySession(${s.id}, 'dienstlich', this)">Dienst</button>
        <button class="${pOn}" onclick="classifySession(${s.id}, 'privat', this)">Privat</button>
      </div></td>`;
    } else {
      klassCell = '<td class="td-klass" style="display:none;">–</td>';
    }

    // FA-LS-BMW-02: Ladeort (zuhause/extern) -- steuert, ob eine Session in
    // den Eigenstrom-Beleg einfliesst. 'extern' meist bereits separat
    // abgerechnet (z. B. Tankkarte an einer Raststaette), siehe BMW-Import.
    const locHome = (s.charging_location || 'zuhause') === 'zuhause';
    const locTitle = s.charging_location_note ? ` title="${s.charging_location_note}"` : '';
    // Die Adresse des Ladepunkts sichtbar machen — beim BMW-Import wird sie
    // mitgeliefert und beantwortet auf einen Blick, wo geladen wurde.
    const adresse = s.charging_location_note
      ? `<div style="font-size:10px; color:var(--text-tertiary); margin-top:3px;
             max-width:150px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"
             title="${s.charging_location_note}">${s.charging_location_note}</div>`
      : '';
    const locCell = `<td><div class="toggle-pair"${locTitle}>
        <button class="${locHome ? 'on' : ''}" onclick="setChargingLocation(${s.id}, 'zuhause', this)">Zuhause</button>
        <button class="${!locHome ? 'on' : ''}" onclick="setChargingLocation(${s.id}, 'extern', this)">Extern</button>
      </div>${adresse}</td>`;

    const row = document.createElement('tr');

    // Klick auf die Zeile öffnet die Bearbeitung — wie bei den Fahrten.
    // Kästchen, Knöpfe und Umschalter behalten ihre eigene Wirkung.
    row.style.cursor = 'pointer';
    row.addEventListener('click', function (e) {
      if (e.target.closest('button, input, a, svg, .toggle-pair')) return;
      editSession(s.id);
    });

    row.innerHTML = `
      <td><input type="checkbox" class="sess-cb" data-id="${s.id}" onchange="toggleSessionCheck(${s.id}, this.checked)"></td>
      <td class="mono">#S-${s.id}</td>
      <td>${datePart}</td>
      <td class="mono">${timePart}</td>
      <td>${s.wallbox_name}</td>
      <td class="mono">${fmtDe(s.energy_kwh, 2)}</td>
      <td class="mono">${fmtDe(s.price_per_kwh, 2)} €</td>
      <td class="mono">${fmtDe(s.amount_eur, 2)} €</td>
      ${klassCell}
      ${locCell}
      <td><span class="badge-fall">${s.source.toUpperCase()}</span></td>
      <td>${statusPill}</td>
      <td style="white-space:nowrap;">
        <button class="btn btn-sm" onclick="editSession(${s.id})" title="Bearbeiten">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
        </button>
        <button class="btn btn-sm" onclick="previewSessionBeleg(${s.id})" title="Beleg-Vorschau">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
        </button>
        <a href="/api/documents/ladestrom/single/${s.id}" target="_blank" class="btn btn-sm" title="Einzelbeleg herunterladen">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
        </a>
        <button class="btn btn-sm" onclick="deleteSession(${s.id})" title="Löschen">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>
        </button>
      </td>
    `;
    if (!showClassification) row.querySelector('.td-klass').style.display = 'none';
    tbody.appendChild(row);
  });
}

async function setChargingLocation(sessionId, newValue, btn) {
  try {
    const resp = await fetch(`/api/sessions/${sessionId}/charging-location`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ charging_location: newValue }),
    });
    if (resp.ok) {
      const pair = btn.closest('.toggle-pair');
      pair.querySelectorAll('button').forEach(b => b.classList.remove('on'));
      btn.classList.add('on');
    } else {
      alert('Fehler beim Ändern des Ladeorts.');
    }
  } catch (e) {
    alert('Netzwerkfehler: ' + e);
  }
}

let editingSessionId = null;
let sessionsCache = [];

async function editSession(sessionId) {
  const resp = await hole('/api/sessions');
  const data = await resp.json();
  sessionsCache = data.sessions;
  const s = sessionsCache.find(x => x.id === sessionId);
  if (!s) return;

  editingSessionId = sessionId;
  document.getElementById('new-session-form').style.display = 'block';
  document.getElementById('session-form-title').textContent = `Session #S-${sessionId} bearbeiten`;
  document.getElementById('ms-wallbox').value = s.wallbox_name;
  document.getElementById('ms-rfid').value = s.rfid_tag || '';
  document.getElementById('ms-start').value = s.start_timestamp.replace(' ', 'T').slice(0, 16);
  document.getElementById('ms-end').value = s.end_timestamp ? s.end_timestamp.replace(' ', 'T').slice(0, 16) : '';
  // meter_start_wh/meter_stop_wh kommen nicht aus session_to_api_dict, daher neu laden:
  const rawResp = await fetch('/api/sessions/' + sessionId + '/raw');
  if (rawResp.ok) {
    const raw = await rawResp.json();
    document.getElementById('ms-meter-start').value = raw.meter_start_wh;
    document.getElementById('ms-meter-end').value = raw.meter_stop_wh || '';
  }
  loadSessionFormSuggestions();

  // Zum Formular scrollen statt an den Seitenanfang — bei langen Listen
  // hätte man sonst suchen müssen, wohin der Klick geführt hat.
  const form = document.getElementById('new-session-form');
  if (form) {
    form.scrollIntoView({ behavior: 'smooth', block: 'start' });
    form.style.transition = 'box-shadow .3s';
    form.style.boxShadow = '0 0 0 2px var(--amber)';
    setTimeout(() => { form.style.boxShadow = ''; }, 1200);
  }
}

async function deleteSession(sessionId) {
  if (!confirm(`Session #S-${sessionId} wirklich löschen? Das kann nicht rückgängig gemacht werden.`)) return;
  try {
    const resp = await fetch('/api/sessions/' + sessionId, { method: 'DELETE' });
    if (resp.ok) {
      await loadSessions();
    } else {
      const data = await resp.json();
      alert('Fehler beim Löschen: ' + (data.error || resp.status));
    }
  } catch (e) { alert('Netzwerkfehler: ' + e); }
}

async function classifySession(sessionId, value, btn) {
  btn.parentElement.querySelectorAll('button').forEach(b => b.classList.remove('on'));
  btn.classList.add('on');
  try {
    await fetch(`/api/sessions/${sessionId}/classify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ classification: value }),
    });
  } catch (e) { alert('Fehler beim Speichern der Klassifizierung.'); }
}

async function importCsv(input) {
  const file = input.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append('file', file);
  const resultEl = document.getElementById('import-result');
  resultEl.textContent = 'Importiere …';
  try {
    const resp = await fetch('/api/sessions/import', { method: 'POST', body: formData });
    const data = await resp.json();
    if (data.error === 'no_user') {
      resultEl.innerHTML = 'Bitte zuerst die <a href="#" onclick="showView(\'setup\'); return false;">Einrichtung (Setup)</a> abschließen.';
      resultEl.style.color = 'var(--warning)';
      input.value = '';
      return;
    }
    let msg = `${data.imported} Session(s) importiert.`;
    if (data.skipped && data.skipped.length > 0) {
      msg += ` ${data.skipped.length} Zeile(n) übersprungen: ` +
        data.skipped.map(s => `Zeile ${s.line} (${s.reason})`).join('; ');
      resultEl.style.color = 'var(--warning)';
    } else {
      resultEl.style.color = 'var(--success)';
    }
    resultEl.textContent = msg;
    await loadWallboxesIntoFilter();
    await loadSessions();
  } catch (e) {
    resultEl.style.color = 'var(--danger)';
    resultEl.textContent = 'Fehler beim Import: ' + e;
  }
  input.value = '';
}

async function saveManualSession() {
  const msgEl = document.getElementById('manual-session-message');
  const werte = ladeFormularWerte();

  // Das Backend erwartet weiterhin Wattstunden — die Umrechnung passiert hier,
  // damit der Anwender in kWh denken kann.
  const payload = {
    wallbox: document.getElementById('ms-wallbox').value.trim(),
    rfid: (document.getElementById('ms-rfid')?.value || '').trim(),
    start: document.getElementById('ms-start').value,
    end: document.getElementById('ms-end').value,
    meter_start: Math.round(werte.zaehlerStartWh),
    meter_end: Math.round(werte.zaehlerEndeWh),
    charging_location: werte.ort,
    price_per_kwh: werte.preis,
  };
  if (!payload.wallbox) {
    msgEl.textContent = 'Bitte eine Wallbox angeben.';
    msgEl.style.color = 'var(--danger)'; return;
  }
  if (!payload.start) {
    msgEl.textContent = 'Bitte den Ladebeginn eintragen.';
    msgEl.style.color = 'var(--danger)'; return;
  }
  if (werte.kwh <= 0) {
    msgEl.textContent = werte.modus === 'kwh'
      ? 'Bitte die geladene Menge in kWh eintragen.'
      : 'Der Zählerstand nachher muss über dem vorherigen liegen.';
    msgEl.style.color = 'var(--danger)'; return;
  }
  const isEdit = editingSessionId !== null;
  const url = isEdit ? '/api/sessions/' + editingSessionId : '/api/sessions/manual';
  const method = isEdit ? 'PUT' : 'POST';
  try {
    const resp = await fetch(url, {
      method: method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (resp.ok) {
      msgEl.textContent = isEdit ? 'Änderungen gespeichert.' : 'Ladevorgang gespeichert.';
      msgEl.style.color = 'var(--success)';
      await loadWallboxesIntoFilter();
      await loadSessions();
      setTimeout(() => { toggleNewSession(); }, 900);
    } else if (data.error === 'demo_limit_reached') {
      msgEl.textContent = 'Demo-Limit (20 Sessions) erreicht — Lizenz aktivieren, um fortzufahren.';
      msgEl.style.color = 'var(--warning)';
    } else if (data.error === 'no_user') {
      msgEl.innerHTML = 'Bitte zuerst die <a href="#" onclick="showView(\'setup\'); return false;">Einrichtung (Setup)</a> abschließen.';
      msgEl.style.color = 'var(--warning)';
    } else {
      msgEl.textContent = 'Fehler: ' + (data.error || resp.status);
      msgEl.style.color = 'var(--danger)';
    }
  } catch (e) {
    msgEl.textContent = 'Netzwerkfehler: ' + e;
    msgEl.style.color = 'var(--danger)';
  }
}

function buildPersonParams() {
  const params = new URLSearchParams();
  const personId = document.getElementById('beleg-person')?.value;
  if (personId) params.set('person_id', personId);
  ['email', 'personalnummer', 'kfz_kennzeichen', 'telefon'].forEach(field => {
    const cb = document.getElementById('beleg-include-' + field);
    if (cb && cb.checked) params.set('include_' + field, '1');
  });
  return params;
}

async function downloadLadestromBeleg(vonId, bisId) {
  const von = vonId ? document.getElementById(vonId).value : document.getElementById('filter-von').value;
  const bis = bisId ? document.getElementById(bisId).value : document.getElementById('filter-bis').value;
  const params = buildPersonParams();
  if (von) params.set('von', von);
  if (bis) params.set('bis', bis);

  try {
    const resp = await fetch('/api/documents/ladestrom?' + params.toString());
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      if (data.error === 'no_user') {
        alert('Bitte zuerst die Einrichtung (Setup) abschließen, bevor ein Beleg erzeugt werden kann.');
        showView('setup');
      } else {
        alert('Fehler beim Erzeugen des Belegs: ' + (data.error || resp.status));
      }
      return;
    }
    const blob = await resp.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `Ladestrom_Beleg_${von || 'gesamt'}_${bis || ''}.pdf`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
  } catch (e) {
    alert('Netzwerkfehler beim Erzeugen des Belegs: ' + e);
  }
}

// ---------- Fahrten (Sprint 2) ----------

let editingTripId = null;
let tripRateChosen = 0.15;

function setTripRate(btn, rate) {
  btn.parentElement.querySelectorAll('button').forEach(b => b.classList.remove('on'));
  btn.classList.add('on');
  const customInput = document.getElementById('trip-rate-custom');
  if (rate === null) {
    customInput.style.display = 'inline-block';
    customInput.focus();
    tripRateChosen = parseFloat(customInput.value) || 0;
  } else {
    customInput.style.display = 'none';
    tripRateChosen = rate;
  }
  updateTripPreview();
}

document.addEventListener('input', (e) => {
  if (e.target && (e.target.id === 'trip-rate-custom' || e.target.id === 'trip-distance')) {
    if (e.target.id === 'trip-rate-custom') tripRateChosen = parseFloat(e.target.value) || 0;
    updateTripPreview();
  }
});

function updateTripPreview() {
  const distance = parseFloat(document.getElementById('trip-distance').value) || 0;
  const isReturn = document.getElementById('trip-return')?.checked;
  const totalKm  = isReturn ? distance * 2 : distance;
  const employer = totalKm * tripRateChosen;
  const diff     = Math.max(0, totalKm * (0.30 - tripRateChosen));
  document.getElementById('trip-preview-employer').textContent = fmtDe(employer, 2) + ' €';
  document.getElementById('trip-preview-diff').textContent    = fmtDe(diff, 2) + ' €';
  const lbl = document.getElementById('trip-km-label');
  if (lbl) lbl.textContent = isReturn && distance > 0 ? `(Hin: ${fmtDe(distance,1)} km × 2 = ${fmtDe(totalKm,1)} km)` : '';
}

function onTripReturnChange() {
  updateTripPreview();
  const isReturn = document.getElementById('trip-return')?.checked;
  const hint = document.getElementById('trip-return-hint');
  if (hint) hint.textContent = isReturn ? 'Hinfahrt + Rückfahrt werden beide abgerechnet.' : '';
}

// ─── Adress-Autocomplete (Photon) ─────────────────────────────────────────
let _autocompleteTimers = {};

async function autocompleteAddress(inputId, listId) {
  const input  = document.getElementById(inputId);
  const list   = document.getElementById(listId);
  if (!input || !list) return;
  const query = input.value.trim();
  if (query.length < 2) { list.style.display = 'none'; return; }

  clearTimeout(_autocompleteTimers[inputId]);
  _autocompleteTimers[inputId] = setTimeout(async () => {
    try {
      const resp = await fetch(`/api/trips/autocomplete?q=${encodeURIComponent(query)}`);
      const data = await resp.json();
      const results = data.results || [];
      if (results.length === 0) { list.style.display = 'none'; return; }
      // Data-Attribute statt inline onclick – vermeidet Anführungszeichen-Probleme
      list.innerHTML = results.map((r, idx) =>
        `<li data-idx="${idx}" data-label="${r.label.replace(/&/g,'&amp;').replace(/"/g,'&quot;')}" tabindex="0">${r.label}</li>`
      ).join('');
      // Event-Listener direkt auf jedes LI – kein inline-onClick-Problem
      list.querySelectorAll('li').forEach(li => {
        li.addEventListener('mousedown', (e) => {
          e.preventDefault(); // verhindert blur des Inputs
          selectAddress(inputId, listId, li.dataset.label);
        });
      });
      list.style.display = 'block';
    } catch (e) { list.style.display = 'none'; }
  }, 280);
}

function selectAddress(inputId, listId, label) {
  const input = document.getElementById(inputId);
  const list  = document.getElementById(listId);
  if (input) input.value = label;
  if (list)  list.style.display = 'none';
}

// Autocomplete schließen bei Klick außerhalb
document.addEventListener('click', (e) => {
  ['trip-start-list', 'trip-end-list'].forEach(id => {
    const el = document.getElementById(id);
    if (el && !el.contains(e.target) && e.target.id !== id.replace('-list','')) {
      el.style.display = 'none';
    }
  });
});

// ─── Trip Quick-Filter ────────────────────────────────────────────────────────
function setTripQuickFilter(period, btn) {
  if (btn) {
    document.querySelectorAll('.trip-qf').forEach(b => b.classList.remove('on'));
    btn.classList.add('on');
  }
  const now = new Date();
  const y = now.getFullYear();
  const m = now.getMonth(); // 0-indexed
  const pad = n => String(n).padStart(2, '0');

  const vonEl = document.getElementById('trip-filter-von');
  const bisEl = document.getElementById('trip-filter-bis');

  if (period === 'all')       { vonEl.value = ''; bisEl.value = ''; }
  else if (period === 'thismonth') {
    vonEl.value = `${y}-${pad(m+1)}-01`;
    bisEl.value = `${y}-${pad(m+1)}-${pad(new Date(y,m+1,0).getDate())}`;
  } else if (period === 'lastmonth') {
    const lm = m === 0 ? 12 : m; const ly = m === 0 ? y-1 : y;
    vonEl.value = `${ly}-${pad(lm)}-01`;
    bisEl.value = `${ly}-${pad(lm)}-${pad(new Date(ly,lm,0).getDate())}`;
  } else if (period === 'q1') { vonEl.value=`${y}-01-01`; bisEl.value=`${y}-03-31`; }
  else if (period === 'q2')   { vonEl.value=`${y}-04-01`; bisEl.value=`${y}-06-30`; }
  else if (period === 'q3')   { vonEl.value=`${y}-07-01`; bisEl.value=`${y}-09-30`; }
  else if (period === 'q4')   { vonEl.value=`${y}-10-01`; bisEl.value=`${y}-12-31`; }
  else if (period === 'thisyear') { vonEl.value=`${y}-01-01`; bisEl.value=`${y}-12-31`; }
  else if (period === 'custom') return; // Von/Bis already set by user

  if (period !== 'custom') loadTrips();
}

async function estimateTripDistance() {
  const start = document.getElementById('trip-start').value.trim();
  const end   = document.getElementById('trip-end').value.trim();
  const hintEl = document.getElementById('trip-distance-hint');
  if (!start || !end) {
    hintEl.textContent = 'Bitte Start- und Zieladresse eingeben.';
    hintEl.style.color = 'var(--danger)';
    return;
  }
  hintEl.textContent = 'Berechne Routen …';
  hintEl.style.color = 'var(--text-tertiary)';
  // Alte Alternativen ausblenden
  const altEl = document.getElementById('trip-route-alternatives');
  if (altEl) altEl.innerHTML = '';

  try {
    const resp = await fetch('/api/trips/estimate-distance', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ start_address: start, end_address: end, alternatives: true }),
    });
    const data = await resp.json();

    if (data.distance_km !== null && data.distance_km !== undefined) {
      document.getElementById('trip-distance').value = data.distance_km;
      hintEl.textContent = `Route 1 (kürzeste): ${fmtDe(data.distance_km, 1)} km`;
      hintEl.style.color = 'var(--success)';
      updateTripPreview();
      _showTripMap(start, end);

      // Alternativen anzeigen
      const alts = data.alternatives || [];
      if (altEl && alts.length > 0) {
        altEl.innerHTML = alts.map((a, i) => `
          <button class="btn btn-sm" onclick="document.getElementById('trip-distance').value='${a.distance_km}';updateTripPreview();this.parentNode.querySelectorAll('.btn').forEach(b=>b.classList.remove('on'));this.classList.add('on');"
            style="font-size:12px;">
            Route ${i+2}: ${fmtDe(a.distance_km, 1)} km${a.duration_min ? ' · ' + Math.round(a.duration_min) + ' min' : ''}
          </button>`).join('');
      }
    } else {
      hintEl.textContent = (data.message || 'Adresse nicht gefunden') + ' — bitte km manuell eingeben.';
      hintEl.style.color = 'var(--warning)';
    }
  } catch (e) {
    hintEl.textContent = 'Dienst nicht erreichbar — bitte km manuell eingeben.';
    hintEl.style.color = 'var(--warning)';
  }
}

// Koordinaten in lesbare Adressen umwandeln. Betrifft nur Einträge, die
// wie "50.57940, 7.22690" aussehen — von Hand eingetragene Orte bleiben.
async function adressenAufloesen() {
  // Kein Confirm — der Knopf liegt direkt beim Formular, der Kontext ist klar
  _toast('Adressen werden nachgetragen …');
  try {
    const d = await (await fetch('/api/fahrten/adressen-aufloesen',
                                 { method: 'POST' })).json();
    if (d.ok) {
      _toast(d.geaendert
        ? `${d.geaendert} Fahrten ergänzt`
        : 'Keine Fahrt mit Koordinaten gefunden');
      if (d.geaendert) loadTrips();
    } else {
      _toast('Nachtragen fehlgeschlagen');
    }
  } catch (e) {
    _toast('Nachtragen fehlgeschlagen');
  }
}

// ── Anlässe für Fahrten ───────────────────────────────────────────────────
// Der Katalog steht in den Einstellungen und füllt die Auswahlliste im
// Fahrtenformular. Fest verdrahtet war er nicht brauchbar — jede Branche
// hat eigene Begriffe.

async function ladeAnlaesse() {
  const liste = document.getElementById('purpose-presets');
  if (!liste) return;
  try {
    const d = await (await hole('/api/anlaesse')).json();
    liste.innerHTML = (d.anlaesse || [])
      .map(a => `<option value="${a.replace(/"/g, '&quot;')}">`).join('');
  } catch (e) { /* Liste bleibt leer, Eintippen geht trotzdem */ }
}

async function ladeAnlaesseInFeld() {
  const feld = document.getElementById('anlass-liste');
  if (!feld) return;
  try {
    const d = await (await hole('/api/anlaesse')).json();
    feld.value = (d.anlaesse || []).join('\n');
  } catch (e) {
    feld.placeholder = 'Konnte nicht geladen werden';
  }
}

async function anlaesseSpeichern() {
  const feld = document.getElementById('anlass-liste');
  const meldung = document.getElementById('anlass-meldung');
  if (!feld) return;
  const anlaesse = feld.value.split('\n').map(z => z.trim()).filter(Boolean);
  try {
    const d = await (await fetch('/api/anlaesse', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ anlaesse })
    })).json();
    if (d.ok) {
      if (meldung) meldung.textContent = `${d.anzahl} Anlässe gespeichert`;
      _toast('Anlässe gespeichert');
      ladeAnlaesse();          // Auswahlliste sofort mitziehen
      ladeAnlaesseInFeld();    // Dubletten sind jetzt weg — Feld angleichen
    } else {
      if (meldung) meldung.textContent = d.fehler || 'Fehlgeschlagen';
    }
  } catch (e) {
    if (meldung) meldung.textContent = 'Speichern fehlgeschlagen';
  }
}

async function anlaesseZuruecksetzen() {
  if (!confirm('Die Vorschläge wiederherstellen?\n\n'
             + 'Eigene Einträge gehen dabei verloren.')) return;
  const feld = document.getElementById('anlass-liste');
  if (feld) feld.value = '';
  try {
    await fetch('/api/anlaesse', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ anlaesse: [] })
    });
  } catch (e) { /* egal — der Server liefert danach die Vorschläge */ }
  // Ohne gespeicherte Liste greifen wieder die Standardwerte
  await ladeAnlaesseInFeld();
  await ladeAnlaesse();
  _toast('Vorschläge wiederhergestellt');
}

function toggleNewTrip() {
  const f = document.getElementById('new-trip-form');
  const opening = f.style.display === 'none';
  f.style.display = opening ? 'block' : 'none';
  if (!opening) {
    resetTripForm();
  } else {
    resetTripForm();
    _initTripDatePicker();       // Custom Date Picker initialisieren
    loadPersonsIntoTripForm();
  }
}

async function prefillHomeAddressAsStart() {
  try {
    // Priorität 1: Stammadresse aus Person
    const resp1 = await fetch('/api/persons/home-address');
    const d1 = await resp1.json();
    if (d1.address) {
      document.getElementById('trip-start').value = d1.address;
      return;
    }
    // Priorität 2: Home-Adresse aus Einstellungen (Fallback)
    const resp2 = await fetch('/api/settings/home-address');
    const d2 = await resp2.json();
    if (d2.address) document.getElementById('trip-start').value = d2.address;
  } catch (e) { /* kein Vorbelegen bei Fehler */ }
}

function resetTripForm() {
  editingTripId = null;
  document.getElementById('trip-form-title').textContent = 'Neue Fahrt';
  document.getElementById('trip-date').value = '';
  document.getElementById('trip-purpose').value = '';
  document.getElementById('trip-start').value = '';
  document.getElementById('trip-end').value = '';
  document.getElementById('trip-distance').value = '';
  document.getElementById('trip-distance-hint').textContent = '';
  document.getElementById('trip-form-message').textContent = '';
  const pair = document.querySelector('#new-trip-form .toggle-pair');
  pair.querySelectorAll('button').forEach(b => b.classList.remove('on'));
  pair.querySelector('button').classList.add('on');
  tripRateChosen = 0.15;
  updateTripPreview();
}

async function loadTrips() {
  const tbody = document.getElementById('trips-tbody');
  if (!tbody) return;
  const von  = document.getElementById('trip-filter-von')?.value  || '';
  const bis  = document.getElementById('trip-filter-bis')?.value  || '';
  const dest = (document.getElementById('trip-filter-dest')?.value || '').toLowerCase().trim();
  const params = new URLSearchParams();
  if (von) params.set('von', von);
  if (bis) params.set('bis', bis);
  tbody.innerHTML = '<tr><td colspan="8" class="hint">Lade Fahrten …</td></tr>';
  try {
    const resp = await fetch('/api/trips?' + params.toString());
    const data = await resp.json();
    // Ziel-Filter client-seitig
    const filtered = dest
      ? (data.trips || []).filter(t =>
          t.end_address.toLowerCase().includes(dest) ||
          t.start_address.toLowerCase().includes(dest) ||
          t.purpose.toLowerCase().includes(dest))
      : (data.trips || []);
    renderTripsTable(filtered);
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="8" class="hint">Fehler beim Laden.</td></tr>';
  }
}

function renderTripsTable(trips) {
  const tbody = document.getElementById('trips-tbody');
  // Filter nach Fahrtart: Das Fahrtenbuch enthält alle Fahrten, für die
  // Bearbeitung ist eine gezielte Auswahl aber praktischer.
  const artFilter = document.getElementById('trip-filter-art')?.value || '';
  if (artFilter) {
    trips = trips.filter(t => (t.fahrtart || 'dienstlich') === artFilter);
  }
  // 0-km-Fahrten ausblenden (Default), gleiche Logik wie bei Ladesessions
  const showZero = document.getElementById('trip-filter-show-zero')?.checked;
  const zeroCount = trips.filter(t => (t.distance_km || 0) < 0.1).length;
  if (!showZero) trips = trips.filter(t => (t.distance_km || 0) >= 0.1);
  const zeroInfo = document.getElementById('zero-trips-info');
  if (zeroInfo) {
    zeroInfo.textContent = (!showZero && zeroCount > 0) ? `${zeroCount} Fahrten mit 0 km ausgeblendet` : '';
  }
  if (trips.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" class="hint">Keine Fahrten im gewählten Zeitraum.</td></tr>';
    return;
  }
  tbody.innerHTML = '';
  trips.forEach(t => {
    const datePart = t.trip_date.split('-').reverse().join('.');
    const rateLabel = t.rate_chosen === 0 ? 'keine' : fmtDe(t.rate_chosen, 2) + ' €';
    // Fahrtart als farbiges Kennzeichen — im Fahrtenbuch stehen alle Fahrten,
    // die Unterscheidung muss auf einen Blick erkennbar sein.
    const art = t.fahrtart || 'dienstlich';
    const artTag = art === 'privat'
      ? '<span class="trip-tag trip-tag-privat">privat</span>'
      : art === 'arbeitsweg'
        ? '<span class="trip-tag trip-tag-weg">Arbeitsweg</span>'
        : art === 'offen'
          ? '<span class="trip-tag trip-tag-offen">noch zuzuordnen</span>'
          : '<span class="trip-tag trip-tag-dienst">dienstlich</span>';
    // Erstattungssatz ebenfalls als Kennzeichen
    const satzTag = art === 'offen'
      ? '<span class="trip-tag trip-tag-neutral">Satz offen</span>'
      : art !== 'dienstlich'
      ? '<span class="trip-tag trip-tag-neutral">—</span>'
      : t.rate_chosen === 0
        ? '<span class="trip-tag trip-tag-keine">keine</span>'
        : `<span class="trip-tag trip-tag-satz">${fmtDe(t.rate_chosen, 2)} €/km</span>`;
    const row = document.createElement('tr');
    if (art === 'privat') row.style.opacity = '0.72';
    if (art === 'offen') row.style.borderLeft = '3px solid var(--warning, #eab308)';

    // Klick auf die Zeile öffnet den Bearbeitungsmodus. Ausgenommen sind
    // Kästchen und Knöpfe — dort hat der Klick bereits eine eigene Bedeutung.
    row.style.cursor = 'pointer';
    row.addEventListener('click', function (e) {
      if (e.target.closest('button, input, a, svg')) return;
      editTrip(t.id);
    });
    row.innerHTML = `
      <td><input type="checkbox" class="trip-cb" data-id="${t.id}" onchange="toggleTripCheck(${t.id}, this.checked)"></td>
      <td>${datePart}</td>
      <td>${t.start_address} → ${t.end_address}</td>
      <td>${t.purpose}<br>${artTag} ${satzTag}</td>
      <td class="mono">${fmtDe(t.distance_km, 1)}</td>
      <td class="mono">${rateLabel}</td>
      <td class="mono">${fmtDe(t.employer_amount_eur, 2)} €</td>
      <td class="mono">${fmtDe(t.diff_amount_eur, 2)} €</td>
      <td style="white-space:nowrap;">
        <button class="btn btn-sm" onclick="editTrip(${t.id})" title="Bearbeiten">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
        </button>
        <button class="btn btn-sm" onclick="duplicateTrip(${t.id})" title="Als Vorlage duplizieren">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
        </button>
        <button class="btn btn-sm" onclick="previewTripBeleg(${t.id})" title="Beleg-Vorschau">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
        </button>
        <a href="/api/documents/fahrtkosten-ag/single/${t.id}" target="_blank" class="btn btn-sm" title="Einzelbeleg herunterladen">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
        </a>
        <button class="btn btn-sm" onclick="deleteTrip(${t.id})" title="Löschen">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>
        </button>
      </td>
    `;
    tbody.appendChild(row);
  });
}

async function closeStaleSession(sessionId, pillEl) {
  if (!confirm(`Session #${sessionId} manuell schließen? Aktueller Zählerstand wird als Endwert gespeichert.`)) return;
  try {
    const resp = await fetch(`/api/sessions/${sessionId}/close`, { method: 'POST' });
    const data = await resp.json();
    if (resp.ok) {
      if (pillEl) {
        pillEl.className = 'pill pill-green';
        pillEl.innerHTML = '<span class="pill-dot"></span>Geschlossen';
        pillEl.style.cursor = '';
        pillEl.removeAttribute('onclick');
        pillEl.removeAttribute('title');
      }
      await loadSessions();
    } else {
      alert('Fehler: ' + (data.message || data.error));
    }
  } catch (e) {
    alert('Netzwerkfehler: ' + e);
  }
}

async function previewSessionBeleg(sessionId) {
  _openPdfPreview(
    `/api/documents/ladestrom/single/${sessionId}?inline=1`,
    `/api/documents/ladestrom/single/${sessionId}`,
    `Ladebeleg Session #${sessionId}`
  );
}

async function previewTripBeleg(tripId) {
  _openPdfPreview(
    `/api/documents/fahrtkosten-ag/single/${tripId}?inline=1`,
    `/api/documents/fahrtkosten-ag/single/${tripId}`,
    `Fahrtkostenbeleg Fahrt #${tripId}`
  );
}

function _openPdfPreview(inlineUrl, downloadUrl, title) {
  let overlay = document.getElementById('pdf-preview-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'pdf-preview-overlay';
    overlay.style.cssText = 'position:fixed;inset:0;z-index:9000;background:rgba(0,0,0,.72);display:flex;flex-direction:column;align-items:center;justify-content:flex-start;padding:20px;';
    overlay.innerHTML = `
      <div style="width:100%;max-width:920px;background:var(--bg-card);border-radius:var(--radius);overflow:hidden;display:flex;flex-direction:column;height:90vh;">
        <div style="display:flex;align-items:center;justify-content:space-between;padding:12px 16px;border-bottom:1px solid var(--border);flex-shrink:0;gap:10px;">
          <span id="pdf-preview-title" style="font-weight:600;font-size:14px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"></span>
          <div style="display:flex;gap:8px;flex-shrink:0;">
            <a id="pdf-preview-newtab" href="#" target="_blank" class="btn btn-sm">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
              Neuer Tab
            </a>
            <a id="pdf-preview-download" href="#" class="btn btn-sm btn-primary" download>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
              Herunterladen
            </a>
            <button class="btn btn-sm" onclick="document.getElementById('pdf-preview-overlay').style.display='none'">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
              Schließen
            </button>
          </div>
        </div>
        <!-- iframe für Browser die PDF-Vorschau unterstützen (Firefox, Edge) -->
        <iframe id="pdf-preview-frame" style="flex:1;border:none;width:100%;background:white;" type="application/pdf"></iframe>
        <!-- Fallback: falls iframe leer bleibt (Chrome/Windows) -->
        <div id="pdf-preview-fallback" style="display:none;flex:1;align-items:center;justify-content:center;flex-direction:column;gap:16px;padding:40px;text-align:center;">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="color:var(--text-tertiary);"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
          <div style="font-size:15px;font-weight:600;">PDF bereit</div>
          <div style="font-size:13px;color:var(--text-secondary);">Dein Browser zeigt PDF-Vorschauen nicht direkt an.<br>Bitte öffne das PDF in einem neuen Tab oder lade es herunter.</div>
          <div style="display:flex;gap:10px;">
            <a id="pdf-fallback-newtab" href="#" target="_blank" class="btn btn-primary">In neuem Tab öffnen</a>
            <a id="pdf-fallback-download" href="#" class="btn" download>Herunterladen</a>
          </div>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.style.display = 'none'; });
    // Erkennen ob iframe leer bleibt (Chrome blockiert PDF in iframes)
    const frame = overlay.querySelector('#pdf-preview-frame');
    frame.addEventListener('load', () => {
      try {
        // Wenn iframe leer ist (keine PDF-Unterstützung), Fallback zeigen
        if (!frame.contentDocument || frame.contentDocument.title === '') {
          // kleine Verzögerung warten
          setTimeout(() => {
            if (frame.contentWindow && frame.contentWindow.document &&
                frame.contentWindow.document.body &&
                frame.contentWindow.document.body.innerHTML === '') {
              frame.style.display = 'none';
              document.getElementById('pdf-preview-fallback').style.display = 'flex';
            }
          }, 800);
        }
      } catch(e) {
        // Cross-origin – iframe hat PDF geladen (gut)
      }
    });
  }
  document.getElementById('pdf-preview-title').textContent = title;
  document.getElementById('pdf-preview-download').href = downloadUrl;
  document.getElementById('pdf-preview-newtab').href = inlineUrl;
  const fb1 = document.getElementById('pdf-fallback-newtab');
  const fb2 = document.getElementById('pdf-fallback-download');
  if (fb1) fb1.href = inlineUrl;
  if (fb2) fb2.href = downloadUrl;
  const frame = document.getElementById('pdf-preview-frame');
  const fallback = document.getElementById('pdf-preview-fallback');
  frame.style.display = '';
  if (fallback) fallback.style.display = 'none';
  frame.src = inlineUrl;
  overlay.style.display = 'flex';
}

async function duplicateTrip(tripId) {
  // Fahrt laden und als neues Formular mit heutigem Datum öffnen
  const resp = await hole('/api/trips');
  const data = await resp.json();
  const t = data.trips.find(x => x.id === tripId);
  if (!t) return;

  editingTripId = null;  // Kein Edit-Modus → neue Fahrt
  const form = document.getElementById('new-trip-form');
  form.style.display = 'block';
  document.getElementById('trip-form-title').textContent = 'Fahrt duplizieren (als Vorlage)';

  // Heutiges Datum vorbelegen, Rest übernehmen
  const today = new Date().toISOString().split('T')[0];
  document.getElementById('trip-date').value = today;
  document.getElementById('trip-purpose').value = t.purpose;
  document.getElementById('trip-start').value = t.start_address;
  document.getElementById('trip-end').value = t.end_address;
  document.getElementById('trip-distance').value = t.distance_km;

  tripRateChosen = t.rate_chosen;
  const pair = document.querySelector('#new-trip-form .toggle-pair');
  if (pair) {
    pair.querySelectorAll('button').forEach(b => b.classList.remove('on'));
    if (t.rate_chosen === 0.15) pair.children[0].classList.add('on');
    else if (t.rate_chosen === 0.30) pair.children[1].classList.add('on');
    else if (t.rate_chosen === 0) pair.children[3].classList.add('on');
    else {
      pair.children[2].classList.add('on');
      document.getElementById('trip-rate-custom').style.display = 'inline-block';
      document.getElementById('trip-rate-custom').value = t.rate_chosen;
    }
  }
  updateTripPreview();
  document.getElementById('trip-form-message').textContent = '📋 Vorlage geladen – Datum und Angaben anpassen, dann speichern.';
  document.getElementById('trip-form-message').style.color = 'var(--accent)';
  window.scrollTo(0, document.getElementById('new-trip-form').offsetTop - 20);
}

async function editTrip(tripId) {
  const resp = await hole('/api/trips');
  const data = await resp.json();
  const t = data.trips.find(x => x.id === tripId);
  if (!t) return;

  editingTripId = tripId;
  document.getElementById('new-trip-form').style.display = 'block';
  document.getElementById('trip-form-title').textContent = `Fahrt #${tripId} bearbeiten`;

  // Fahrer und Fahrzeug befüllen — das geschah bisher nur beim Anlegen
  // einer neuen Fahrt. Wer eine bestehende öffnete, fand leere Listen und
  // musste erst eine Fahrt anlegen und verwerfen, damit sie gefüllt wurden.
  await loadPersonsIntoTripForm();
  const fzSel = document.getElementById('trip-vehicle-select');
  if (fzSel && t.vehicle_id) fzSel.value = String(t.vehicle_id);
  document.getElementById('trip-date').value = t.trip_date;
  document.getElementById('trip-purpose').value = t.purpose;
  document.getElementById('trip-start').value = t.start_address;
  document.getElementById('trip-end').value = t.end_address;
  document.getElementById('trip-distance').value = t.distance_km;
  tripRateChosen = t.rate_chosen;
  const pair = document.querySelector('#new-trip-form .toggle-pair');
  pair.querySelectorAll('button').forEach(b => b.classList.remove('on'));
  if (t.rate_chosen === 0.15) pair.children[0].classList.add('on');
  else if (t.rate_chosen === 0.30) pair.children[1].classList.add('on');
  else if (t.rate_chosen === 0) pair.children[3].classList.add('on');
  else {
    pair.children[2].classList.add('on');
    document.getElementById('trip-rate-custom').style.display = 'inline-block';
    document.getElementById('trip-rate-custom').value = t.rate_chosen;
  }
  updateTripPreview();

  // Zum Formular scrollen — nicht an den Seitenanfang. Bei einer langen
  // Fahrtenliste lag das Formular sonst außerhalb des Sichtfelds und man
  // musste erst suchen, wohin der Klick geführt hat.
  const form = document.getElementById('new-trip-form');
  if (form) {
    form.scrollIntoView({ behavior: 'smooth', block: 'start' });
    // Kurz hervorheben, damit klar ist, worauf sich das Formular bezieht
    form.style.transition = 'box-shadow .3s';
    form.style.boxShadow = '0 0 0 2px var(--amber)';
    setTimeout(() => { form.style.boxShadow = ''; }, 1200);
  }
}

async function deleteTrip(tripId) {
  if (!confirm(`Fahrt #${tripId} wirklich löschen?`)) return;
  const resp = await fetch('/api/trips/' + tripId, { method: 'DELETE' });
  if (resp.ok) await loadTrips();
  else alert('Fehler beim Löschen.');
}

async function saveTrip() {
  const msgEl    = document.getElementById('trip-form-message');
  const isReturn = document.getElementById('trip-return')?.checked;
  const baseDist = parseFloat(document.getElementById('trip-distance').value) || 0;
  const totalKm  = isReturn ? baseDist * 2 : baseDist;

  const payload = {
    trip_date:     document.getElementById('trip-date').value,
    purpose:       document.getElementById('trip-purpose').value.trim(),
    start_address: document.getElementById('trip-start').value.trim(),
    end_address:   document.getElementById('trip-end').value.trim(),
    distance_km:   totalKm,
    rate_chosen:   tripRateChosen,
    vehicle_id:    parseInt(document.getElementById('trip-vehicle-select')?.value) || null,
  };
  if (!payload.trip_date || !payload.purpose || !payload.start_address || !payload.end_address || !totalKm) {
    msgEl.textContent = 'Bitte alle Felder ausfüllen (Distanz ggf. manuell eintragen).';
    msgEl.style.color = 'var(--danger)';
    return;
  }
  const isEdit = editingTripId !== null;
  const url    = isEdit ? '/api/trips/' + editingTripId : '/api/trips';
  const method = isEdit ? 'PUT' : 'POST';
  try {
    const resp = await fetch(url, {
      method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (resp.ok) {
      const label = isReturn ? `Hin+Rück (${fmtDe(baseDist,1)}×2=${fmtDe(totalKm,1)} km)` : `${fmtDe(totalKm,1)} km`;
      msgEl.textContent = (isEdit ? 'Änderungen gespeichert' : 'Fahrt gespeichert') + ` — ${label}`;
      msgEl.style.color = 'var(--success)';
      await loadTrips();
      setTimeout(() => toggleNewTrip(), 900);
    } else if (data.error === 'no_user') {
      msgEl.innerHTML = 'Bitte zuerst die <a href="#" onclick="showView(\'setup\'); return false;">Einrichtung (Setup)</a> abschließen.';
      msgEl.style.color = 'var(--warning)';
    } else {
      msgEl.textContent = 'Fehler: ' + (data.error || resp.status);
      msgEl.style.color = 'var(--danger)';
    }
  } catch (e) {
    msgEl.textContent = 'Netzwerkfehler: ' + e;
    msgEl.style.color = 'var(--danger)';
  }
}

async function downloadPdf(url, filenameFallback) {
  try {
    const resp = await fetch(url);
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      if (data.error === 'no_user') {
        alert('Bitte zuerst die Einrichtung (Setup) abschließen.');
        showView('setup');
      } else {
        alert('Fehler beim Erzeugen des Belegs: ' + (data.error || resp.status));
      }
      return;
    }
    const blob = await resp.blob();
    const dlUrl = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = dlUrl;
    a.download = filenameFallback;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(dlUrl);
    loadBelegverlauf();
  } catch (e) {
    alert('Netzwerkfehler beim Erzeugen des Belegs: ' + e);
  }
}

async function loadBelegverlauf() {
  const tbody = document.getElementById('belegverlauf-tbody');
  if (!tbody) return;

  const jahrSelect = document.getElementById('belegverlauf-jahr');
  if (jahrSelect && jahrSelect.dataset.filled !== '1') {
    const currentYear = new Date().getFullYear();
    for (let y = currentYear; y >= currentYear - 5; y--) {
      const opt = document.createElement('option');
      opt.value = String(y); opt.textContent = String(y);
      jahrSelect.appendChild(opt);
    }
    jahrSelect.dataset.filled = '1';
  }

  const params = new URLSearchParams();
  const jahr = document.getElementById('belegverlauf-jahr').value;
  const monat = document.getElementById('belegverlauf-monat').value;
  if (jahr) params.set('year', jahr);
  if (monat) params.set('month', monat);

  try {
    const resp = await fetch('/api/documents?' + params.toString());
    const data = await resp.json();
    if (!data.documents || data.documents.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4" class="hint">Keine Belege für diesen Filter gefunden.</td></tr>';
      return;
    }
    const typLabels = { ladestrom: 'Ladestrom-Kostennachweis', fahrtkosten_ag: 'Fahrtkosten — Arbeitgeber', fahrtkosten_fa: 'Fahrtkosten — Finanzamt' };
    tbody.innerHTML = data.documents.map(d => `
      <tr>
        <td>${typLabels[d.doc_type] || d.doc_type}</td>
        <td class="mono">${d.period_start} – ${d.period_end}</td>
        <td>${d.generated_at}</td>
        <td style="white-space:nowrap;">
          <button class="icon-btn" title="Download" onclick="window.location.href='/api/documents/${d.id}/download'">⇩</button>
          <button class="icon-btn icon-btn-danger" title="Löschen" onclick="deleteBeleg(${d.id})">${ICONS.trash}</button>
        </td>
      </tr>
    `).join('');
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="4" class="hint">Fehler beim Laden des Belegverlaufs.</td></tr>';
  }
}

async function deleteBeleg(documentId) {
  if (!confirm('Beleg wirklich löschen? Das entfernt nur den gespeicherten Verlauf — die zugrundeliegenden Sessions/Fahrten bleiben erhalten und ein neuer Beleg kann jederzeit erneut erzeugt werden.')) return;
  const resp = await fetch(`/api/documents/${documentId}`, { method: 'DELETE' });
  if (resp.ok) {
    await loadBelegverlauf();
  } else {
    alert('Fehler beim Löschen.');
  }
}

function downloadFahrtkostenAG() {
  const von = document.getElementById('trip-filter-von')?.value || '';
  const bis = document.getElementById('trip-filter-bis')?.value || '';
  const params = buildPersonParams();
  if (von) params.set('von', von);
  if (bis) params.set('bis', bis);
  downloadPdf('/api/documents/fahrtkosten-ag?' + params.toString(), 'Fahrtkosten_AG_Beleg.pdf');
}

function previewFahrtkostenAG() {
  const von = document.getElementById('trip-filter-von')?.value || '';
  const bis = document.getElementById('trip-filter-bis')?.value || '';
  const params = buildPersonParams();
  if (von) params.set('von', von);
  if (bis) params.set('bis', bis);
  params.set('inline', '1');
  const dlUrl = '/api/documents/fahrtkosten-ag?' + params.toString().replace('inline=1','');
  const inlineUrl = '/api/documents/fahrtkosten-ag?' + params.toString();
  _openPdfPreview(inlineUrl, dlUrl, 'Fahrtkostenbeleg Arbeitgeber');
}


function downloadFahrtkostenFA() {
  const jahr = new Date().getFullYear();
  const params = buildPersonParams();
  params.set('jahr', jahr);
  downloadPdf('/api/documents/fahrtkosten-fa?' + params.toString(), `Reisekosten_Nachweis_${jahr}.pdf`);
}

function onBelegTypChange() {
  const typ = document.getElementById('beleg-typ').value;
  const el = document.getElementById('beleg-typ-erklaerung');
  if (!el) return;
  const texte = {
    ladestrom: 'Monatlicher Nachweis deiner Heimladekosten für den <b>Arbeitgeber</b> — Erstattung nach BMF-Pauschale (0,34 €/kWh).',
    fahrtkosten_ag: 'Beleg für die <b>Erstattung durch deinen Arbeitgeber</b>. Zeigt die Fahrten mit dem gewählten AG-Satz (z. B. 0,15 €/km). Das reichst du beim Arbeitgeber ein.',
    fahrtkosten_fa: 'Auswertung für deine <b>Steuererklärung (Werbungskosten)</b>. Rechnet je Fahrt: volle 0,30 €/km <b>minus</b> das, was der AG schon erstattet hat. Hast du für eine Tätigkeit <b>keine</b> AG-Erstattung bekommen, zählen hier die vollen 0,30 €/km.',
  };
  el.innerHTML = texte[typ] || '';
  // Override-Feld nur beim Finanzamt-Typ zeigen
  const ov = document.getElementById('beleg-fa-override');
  if (ov) ov.style.display = (typ === 'fahrtkosten_fa') ? 'block' : 'none';
}

function generateSelectedBeleg() {
  const typ = document.getElementById('beleg-typ').value;
  if (typ === 'ladestrom') {
    downloadLadestromBeleg('beleg-von', 'beleg-bis');
  } else if (typ === 'fahrtkosten_ag') {
    const von = document.getElementById('beleg-von').value;
    const bis = document.getElementById('beleg-bis').value;
    const params = buildPersonParams();
    if (von) params.set('von', von);
    if (bis) params.set('bis', bis);
    downloadPdf('/api/documents/fahrtkosten-ag?' + params.toString(), 'Fahrtkosten_AG_Beleg.pdf');
  } else if (typ === 'fahrtkosten_fa') {
    const von = document.getElementById('beleg-von').value;
    const bis = document.getElementById('beleg-bis').value;
    const jahr = von ? von.slice(0, 4) : new Date().getFullYear();
    const params = buildPersonParams();
    params.set('jahr', jahr);
    if (von) params.set('von', von);
    if (bis) params.set('bis', bis);
    const rateOv = document.getElementById('beleg-fa-rate').value;
    if (rateOv !== '') params.set('rate_override', rateOv);
    downloadPdf('/api/documents/fahrtkosten-fa?' + params.toString(), `Reisekosten_Nachweis_${jahr}.pdf`);
  }
}

// ---------- Personen-Verwaltung (Einstellungen) ----------

let editingPersonId = null;

async function loadPersons() {
  const tbody = document.getElementById('persons-tbody');
  if (!tbody) return;
  try {
    const resp = await fetch('/api/persons');
    const data = await resp.json();
    if (data.persons.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" class="hint">Noch keine Personen angelegt.</td></tr>';
    } else {
      tbody.innerHTML = '';
      data.persons.forEach(p => {
        const row = document.createElement('tr');
        row.innerHTML = `
          <td>${p.name}</td><td>${p.email || '–'}</td><td>${p.personalnummer || '–'}</td><td>${p.kfz_kennzeichen || '–'}</td>
          <td>
            <button class="btn btn-sm" onclick='editPerson(${JSON.stringify(p)})'>Bearbeiten</button>
            <button class="btn btn-sm" onclick="deletePerson(${p.id})">Löschen</button>
          </td>`;
        tbody.appendChild(row);
      });
    }
    await loadPersonsIntoBelegDropdown();
  } catch (e) { /* Einstellungen bleiben trotzdem nutzbar */ }
}

async function loadPersonsIntoBelegDropdown() {
  const sel = document.getElementById('beleg-person');
  if (!sel) return;
  try {
    const resp = await fetch('/api/persons');
    const data = await resp.json();
    const current = sel.value;
    sel.innerHTML = '<option value="">— Setup-Name verwenden —</option>';
    data.persons.forEach(p => {
      const opt = document.createElement('option');
      opt.value = p.id;
      opt.textContent = p.name;
      sel.appendChild(opt);
    });
    sel.value = current;
  } catch (e) { /* optional */ }
}

function resetPersonForm() {
  editingPersonId = null;
  ['person-name','person-email','person-personalnummer','person-kfz','person-telefon','person-home-address'].forEach(id=>{ const el=document.getElementById(id); if(el) el.value=''; });
  document.getElementById('person-form-message').textContent = '';
}

function editPerson(p) {
  editingPersonId = p.id;
  document.getElementById('person-name').value = p.name || '';
  document.getElementById('person-email').value = p.email || '';
  document.getElementById('person-personalnummer').value = p.personalnummer || '';
  document.getElementById('person-kfz').value = p.kfz_kennzeichen || '';
  document.getElementById('person-telefon').value = p.telefon || '';
  const haEl = document.getElementById('person-home-address'); if(haEl) haEl.value = p.home_address || '';
}

async function deletePerson(personId) {
  if (!confirm('Person wirklich löschen?')) return;
  await fetch('/api/persons/' + personId, { method: 'DELETE' });
  await loadPersons();
}

async function savePerson() {
  const msgEl = document.getElementById('person-form-message');
  const payload = {
    name: document.getElementById('person-name').value.trim(),
    email: document.getElementById('person-email').value.trim(),
    personalnummer: document.getElementById('person-personalnummer').value.trim(),
    kfz_kennzeichen: document.getElementById('person-kfz').value.trim(),
    telefon: document.getElementById('person-telefon').value.trim(),
    home_address: (document.getElementById('person-home-address')?.value || '').trim(),
  };
  if (!payload.name) {
    msgEl.textContent = 'Bitte einen Namen eingeben.';
    msgEl.style.color = 'var(--danger)';
    return;
  }
  const isEdit = editingPersonId !== null;
  const url = isEdit ? '/api/persons/' + editingPersonId : '/api/persons';
  const method = isEdit ? 'PUT' : 'POST';
  const resp = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  if (resp.ok) {
    msgEl.textContent = 'Gespeichert.';
    msgEl.style.color = 'var(--success)';
    resetPersonForm();
    await loadPersons();
  } else {
    msgEl.textContent = 'Fehler beim Speichern.';
    msgEl.style.color = 'var(--danger)';
  }
}

// ---------- Wallbox-Verwaltung (Sprint 3) ----------

let wbMode = 'ocpp';

// Drei Wege, eine Wallbox anzubinden. Welcher passt, hängt an der Hardware:
//
//   loxone_api    Loxone-Wallbox — direkt über den Miniserver, nichts weiter nötig
//   extern_ocpp   Fremdfabrikat mit OCPP-Dienst auf einem dauerhaft laufenden Gerät
//   ocpp          eingebauter Server — nur sinnvoll, wenn diese Anwendung durchläuft
const WB_ERKLAERUNG = {
  loxone_api: 'Die Anwendung fragt den Miniserver ab. Für Loxone-Wallboxen der '
            + 'einfachste Weg — es ist nichts weiter einzurichten.',
  extern_ocpp: 'Empfohlen für Wallboxen anderer Hersteller. Der OCPP-Dienst läuft auf '
             + 'einem Gerät, das durchgehend eingeschaltet ist (LoxBerry, NAS, Raspberry Pi). '
             + 'So gehen auch nächtliche Ladungen nicht verloren.',
  ocpp: 'Die Wallbox verbindet sich unmittelbar mit dieser Anwendung. Das funktioniert '
      + 'nur, solange sie läuft — auf einem Arbeitsplatzrechner gehen nächtliche '
      + 'Ladungen dabei verloren.',
};

// Wo die OCPP-Einstellung beim jeweiligen Hersteller zu finden ist.
// Diese Frage kostet erfahrungsgemäß die meiste Zeit bei der Einrichtung.
const WB_HERSTELLER = {
  easee:      ['Easee', 'In der Easee-App unter <b>Einstellungen → Erweitert → '
              + 'OCPP</b>. Die Ladebox muss dafür online sein.'],
  goe:        ['go-e Charger', 'Im Web-Interface unter <b>Einstellungen → '
              + 'Erweiterte Einstellungen → OCPP</b>. Ab Firmware 054.'],
  keba:       ['KEBA KeContact', 'Über das Web-Interface der Ladestation, '
              + 'Reiter <b>Configuration → OCPP</b>. Nur c- und x-series.'],
  alfen:      ['Alfen', 'Über <b>ACE Service Installer</b> (Windows) oder die '
              + 'Alfen-Eve-App unter <b>Backoffice</b>.'],
  abl:        ['ABL', 'Über die <b>ABL Configuration Software</b>, Abschnitt '
              + '<b>OCPP-Backend</b>.'],
  zaptec:     ['Zaptec', 'Im Zaptec-Portal unter <b>Installation → '
              + 'Erweiterte Einstellungen</b>. Ein Zugang wird benötigt.'],
  webasto:    ['Webasto', 'Web-Interface der Wallbox, Bereich <b>Backend</b>. '
              + 'Bei Live-Modellen ab Werk vorhanden.'],
  wallbox:    ['Wallbox Pulsar', 'In der myWallbox-App unter <b>Einstellungen → '
              + 'Verbindung → OCPP</b>.'],
  mennekes:   ['Mennekes Amtron', 'Über die <b>Amtron-App</b> oder das '
              + 'Web-Interface unter <b>Backend-Anbindung</b>.'],
  compleo:    ['Compleo', 'Über das Servicemenü der Ladestation, Bereich '
              + '<b>OCPP-Konfiguration</b>.'],
  vestel:     ['Vestel EVC04', 'Web-Interface unter <b>OCPP Settings</b>.'],
  autel:      ['Autel MaxiCharger', 'In der Autel-Charge-App unter '
              + '<b>Einstellungen → OCPP</b>.'],
  heidelberg: ['Heidelberg Energy Control', 'Benötigt ein nachgerüstetes '
              + 'OCPP-Modul. Ab Werk ist keine Netzwerkanbindung vorhanden.'],
  andere:     ['', 'Suchen Sie in der Konfiguration nach <b>OCPP</b>, '
              + '<b>Backend</b> oder <b>Lastmanagement-Server</b>. Gibt es dort '
              + 'ein Feld für eine <code>ws://</code>-Adresse, funktioniert es.'],
};

function wbHerstellerGewechselt() {
  const wahl = document.getElementById('wb-hersteller')?.value;
  const hinweis = document.getElementById('wb-hersteller-hinweis');
  const name = document.getElementById('wb-name');
  if (!wahl || !WB_HERSTELLER[wahl]) {
    if (hinweis) hinweis.innerHTML = '';
    return;
  }
  const [bez, text] = WB_HERSTELLER[wahl];
  if (hinweis) hinweis.innerHTML = text;
  // Namen vorschlagen, aber nur solange das Feld leer ist
  if (name && !name.value && bez) name.value = bez;
}

function setWbMode(btn, mode) {
  btn.parentElement.querySelectorAll('button').forEach(b => b.classList.remove('on'));
  btn.classList.add('on');
  wbMode = mode;

  const zeige = (id, sichtbar) => {
    const el = document.getElementById(id);
    if (el) el.style.display = sichtbar ? 'block' : 'none';
  };
  zeige('wb-ocpp-fields', mode === 'ocpp');
  zeige('wb-loxone-fields', mode === 'loxone_api');
  zeige('wb-extern-fields', mode === 'extern_ocpp');
  // Hersteller nur bei OCPP: Bei Loxone-API ist er bekannt.
  zeige('wb-hersteller-block', mode === 'ocpp' || mode === 'extern_ocpp');

  // Erklären, was die Wahl bedeutet — sonst rät man zwischen drei Begriffen
  const erkl = document.getElementById('wb-art-erklaerung');
  if (erkl) erkl.textContent = WB_ERKLAERUNG[mode] || '';

  // Die Vollversions-Warnung gehört nur zum eingebauten OCPP-Server
  const ocppWarn = document.getElementById('wb-ocpp-hinweis');
  if (ocppWarn && !_istVollversion) {
    ocppWarn.style.display = (mode === 'ocpp') ? 'block' : 'none';
  }

  if (mode === 'ocpp') updateOcppConnectionUrl();
  if (mode === 'extern_ocpp') wbExternVorbelegen();
}

// Bereits eingerichteten Dienst übernehmen, statt ihn erneut eintippen zu lassen
async function wbExternVorbelegen() {
  const feld = document.getElementById('wb-extern-adresse');
  if (!feld || feld.value) return;
  try {
    const d = await (await fetch('/api/extern-ocpp/konfig')).json();
    if (d.adresse) feld.value = d.adresse.replace(/^https?:\/\//, '');
    const pfad = document.getElementById('wb-extern-pfad');
    if (pfad && d.pfad) pfad.value = d.pfad;
  } catch (e) {}
}

async function wbExternTesten() {
  const out = document.getElementById('wb-extern-ergebnis');
  const adresse = document.getElementById('wb-extern-adresse').value.trim();
  const pfad = document.getElementById('wb-extern-pfad').value.trim();
  if (!adresse) { _toast('Bitte die Adresse des Dienstes eintragen'); return; }

  if (out) out.innerHTML = '<div class="hint">Prüfe die Verbindung …</div>';
  // Angaben speichern, damit der Test sie verwendet — und damit sie beim
  // Speichern der Wallbox bereits hinterlegt sind.
  await fetch('/api/extern-ocpp/konfig', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ adresse, pfad,
      wallbox_name: document.getElementById('wb-name')?.value || 'Wallbox (extern)',
      aktiv: true })
  });
  const d = await (await fetch('/api/extern-ocpp/test', { method:'POST' })).json();
  if (out) out.innerHTML = d.ok
    ? `<div style="color:var(--akz-geld); font-size:13px;">✓ ${d.meldung}</div>`
    : `<div style="color:var(--danger); font-size:13px;">✕ ${d.meldung}</div>`;
}

let _serverLanIp = null;

async function _ensureServerIp() {
  if (_serverLanIp) return _serverLanIp;
  try {
    const resp = await fetch('/api/server-info');
    const data = await resp.json();
    _serverLanIp = data.lan_ip;
  } catch (e) {
    _serverLanIp = window.location.hostname;
  }
  return _serverLanIp;
}

async function updateOcppConnectionUrl() {
  const el = document.getElementById('wb-ocpp-connection-url');
  if (!el) return;
  const id = document.getElementById('wb-ocpp-id').value.trim();
  const ocppHost = await _ensureServerIp();
  // Loxone haengt die Charge-Point-ID automatisch an die URL an —
  // in Loxone Config nur die Basis-URL ohne ID eintragen:
  // ws://10.10.40.243:9000/ocpp  →  Loxone verbindet als /ocpp/WB1
  // NICHT: ws://10.10.40.243:9000/ocpp/WB1  →  wuerde zu /ocpp/WB1/WB1
  el.textContent = `ws://${ocppHost}:9000/ocpp`;
}


// ─── Zwischenablage ────────────────────────────────────────────────────────
// navigator.clipboard steht nur in gesicherten Kontexten zur Verfuegung —
// also bei https oder localhost. Diese Anwendung laeuft im Heimnetz meist
// unter http://192.168.x.x, dort ist die Schnittstelle gesperrt und das
// Kopieren scheitert lautlos. Der Fallback ueber ein temporaeres Textfeld
// funktioniert auch dann.
function inZwischenablage(text) {
  return new Promise((erfolg) => {
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(() => erfolg(true), () => erfolg(_fallbackKopie(text)));
    } else {
      erfolg(_fallbackKopie(text));
    }
  });
}

function _fallbackKopie(text) {
  const feld = document.createElement('textarea');
  feld.value = text;
  // Ausserhalb des sichtbaren Bereichs, damit die Seite nicht springt
  feld.style.cssText = 'position:fixed;top:-1000px;left:-1000px;opacity:0;';
  feld.setAttribute('readonly', '');
  document.body.appendChild(feld);
  feld.select();
  feld.setSelectionRange(0, 99999);   // iOS braucht den Bereich ausdrücklich
  let ok = false;
  try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
  document.body.removeChild(feld);
  return ok;
}

function copyOcppUrl() {
  const el = document.getElementById('wb-ocpp-connection-url');
  const text = el.textContent;
  if (text.includes('<ID oben eingeben>')) {
    alert('Bitte zuerst eine Charge-Point-ID oben eingeben.');
    return;
  }
  inZwischenablage(text).then(ok => {
    if (ok) {
      const original = el.textContent;
      el.textContent = 'Kopiert!';
      setTimeout(() => { el.textContent = original; }, 1200);
    } else {
      window.prompt('Kopieren mit Strg+C, dann Enter:', text);
    }
  });
}

// ---------- Passwort-Persistenz fuer Loxone-API-Aktionen ----------

// Passwort-Zwischenspeicher fuer die laufende Sitzung — GLOBAL statt je
// Wallbox, da in der Praxis meist ohnehin nur ein Miniserver/Passwort im
// Spiel ist. Wird bei JEDEM Tastendruck im Passwort-Feld sofort aktualisiert
// (nicht erst nach einer erfolgreichen Aktion), damit es zwischen
// verschiedenen Aktionen (Verbindung testen, Log jetzt prüfen, ...) garantiert
// erhalten bleibt, selbst wenn das Feld zwischenzeitlich geleert wird. Bleibt
// nur im Browser-Speicher dieser Seite, wird nirgends gespeichert oder an
// Dritte uebertragen ausser an unsere eigene API.
let lastEnteredWallboxPassword = '';
// Analog fuer IP und Benutzername: so kann "Struktur laden" direkt nach der
// Eingabe ausgefuehrt werden, ohne die Wallbox vorher speichern zu muessen
// (Sprint 5, Punkt 5.2).
let lastEnteredWallboxHost = '';
let lastEnteredWallboxUser = '';

function initWallboxPasswordPersistence() {
  const passField = document.getElementById('wb-loxone-pass');
  if (passField) {
    passField.addEventListener('input', () => {
      if (passField.value) lastEnteredWallboxPassword = passField.value;
    });
  }
  const hostField = document.getElementById('wb-loxone-host');
  if (hostField) {
    hostField.addEventListener('input', () => {
      if (hostField.value.trim()) lastEnteredWallboxHost = hostField.value.trim();
    });
  }
  const userField = document.getElementById('wb-loxone-user');
  if (userField) {
    userField.addEventListener('input', () => {
      if (userField.value.trim()) lastEnteredWallboxUser = userField.value.trim();
    });
  }
}

// Stellt zwischengespeicherte Verbindungsdaten wieder her, falls Felder leer
// sind — etwa nachdem der Dialog geschlossen und erneut geoeffnet wurde.
function restoreLoxoneConnectionIfEmpty() {
  const hostEl = document.getElementById('wb-loxone-host');
  const userEl = document.getElementById('wb-loxone-user');
  if (hostEl && !hostEl.value.trim() && lastEnteredWallboxHost) hostEl.value = lastEnteredWallboxHost;
  if (userEl && !userEl.value.trim() && lastEnteredWallboxUser) userEl.value = lastEnteredWallboxUser;
  restoreWallboxPasswordIfEmpty();
}

function restoreWallboxPasswordIfEmpty() {
  const passField = document.getElementById('wb-loxone-pass');
  if (passField && !passField.value && lastEnteredWallboxPassword) {
    passField.value = lastEnteredWallboxPassword;
  }
}

function rememberWallboxPassword(wallboxId, password) {
  if (password) lastEnteredWallboxPassword = password;
}

function getRememberedPassword(wallboxId) {
  return lastEnteredWallboxPassword;
}

async function prefillWallboxFields(wallboxId) {
  // Holt IP und Benutzername der gespeicherten Wallbox und traegt sie ins
  // Formular oben ein — das Passwort wird aus Sicherheitsgruenden (NFA-11)
  // nie im Klartext zurueckgegeben, wird aber aus dem session-weiten
  // Zwischenspeicher wiederhergestellt, falls das Feld gerade leer ist.
  try {
    const resp = await hole('/api/wallboxes/full');
    const data = await resp.json();
    const wb = data.wallboxes.find(w => w.id === wallboxId);
    if (!wb) return false;
    // Nur leere Felder befuellen: sonst wuerde eine gerade geaenderte IP
    // beim Klick auf "Struktur laden" durch den gespeicherten Wert
    // ueberschrieben — die Eingabe des Nutzers hat Vorrang.
    const hostEl = document.getElementById('wb-loxone-host');
    const userEl = document.getElementById('wb-loxone-user');
    if (hostEl && !hostEl.value.trim()) hostEl.value = wb.loxone_host || '';
    if (userEl && !userEl.value.trim()) userEl.value = wb.loxone_username || '';
    restoreWallboxPasswordIfEmpty();
    return true;
  } catch (e) {
    return false;
  }
}

function ensurePasswordEntered(wallboxId, actionLabel) {
  restoreWallboxPasswordIfEmpty();
  const password = document.getElementById('wb-loxone-pass').value;
  if (!password) {
    document.getElementById('wb-loxone-pass').focus();
    alert(`IP und Benutzername wurden automatisch aus der gespeicherten Wallbox übernommen. Bitte im Formular oben noch das Passwort eingeben, dann "${actionLabel}" erneut klicken.`);
    return false;
  }
  rememberWallboxPassword(wallboxId, password);
  return true;
}

async function checkWallbox2Log(wallboxId) {
  await prefillWallboxFields(wallboxId);
  if (!ensurePasswordEntered(wallboxId, 'Wallbox2-Log prüfen')) return;

  const host = document.getElementById('wb-loxone-host').value.trim();
  const username = document.getElementById('wb-loxone-user').value.trim();
  const password = document.getElementById('wb-loxone-pass').value;
  const uuid = document.getElementById('wb-loxone-uuid').value.trim();

  if (!uuid) {
    alert('Bitte zusätzlich im Formular oben die Wallbox-UUID (des Wallbox2-Bausteins selbst) eintragen, dann erneut klicken.');
    return;
  }

  try {
    const resp = await fetch(`/api/wallboxes/${wallboxId}/check-wallbox2-log`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ loxone_host: host, loxone_username: username, loxone_password: password, loxone_uuid: uuid }),
    });
    const data = await resp.json();
    if (!data.ok) {
      alert('Fehler: ' + data.message);
      return;
    }
    const statusText = data.charging ? 'lädt gerade' : (data.connected ? 'verbunden, lädt nicht' : 'nicht verbunden');
    let msg = `Live-Status: ${statusText}, aktuelle Leistung ${data.current_power_kw ?? '?'} kW.`;
    if (data.lcl_info) msg += '\n\n' + data.lcl_info;
    alert(msg);
  } catch (e) {
    alert('Netzwerkfehler: ' + e);
  }
}

async function triggerLogReconcile(wallboxId) {
  await prefillWallboxFields(wallboxId);
  if (!ensurePasswordEntered(wallboxId, 'Log-Abgleich starten')) return;

  const host = document.getElementById('wb-loxone-host').value.trim();
  const username = document.getElementById('wb-loxone-user').value.trim();
  const password = document.getElementById('wb-loxone-pass').value;

  try {
    const resp = await fetch(`/api/wallboxes/${wallboxId}/reconcile-log`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ loxone_host: host, loxone_username: username, loxone_password: password }),
    });
    const data = await resp.json();
    if (!data.ok) {
      alert('Log-Abgleich fehlgeschlagen: ' + (data.message || data.error));
      return;
    }
    if (data.imported > 0) {
      alert(`Log-Abgleich abgeschlossen: ${data.imported} zuvor fehlende Session(en) nachgetragen.\n(${data.skipped_duplicate} bereits vorhanden, ${data.total_lines} Zeilen insgesamt geprüft.)`);
      await loadSessions();
    } else {
      alert(`Log-Abgleich abgeschlossen: keine fehlenden Sessions gefunden — alles bereits vollständig.\n(${data.skipped_duplicate} bereits vorhanden, ${data.total_lines} Zeilen insgesamt geprüft.)`);
    }
  } catch (e) {
    alert('Netzwerkfehler: ' + e);
  }
}

let editingWallboxId = null;

async function startEditWallbox(wallboxId) {
  const resp = await hole('/api/wallboxes/full');
  const data = await resp.json();
  const wb = data.wallboxes.find(w => w.id === wallboxId);
  if (!wb) return;

  editingWallboxId = wallboxId;
  document.getElementById('wb-name').value = wb.name || '';
  document.getElementById('wb-location').value = wb.location || '';
  document.getElementById('wb-ocpp-id').value = wb.ocpp_charge_point_id || '';
  document.getElementById('wb-loxone-host').value = wb.loxone_host || '';
  document.getElementById('wb-loxone-user').value = wb.loxone_username || '';
  document.getElementById('wb-loxone-pass').value = '';
  document.getElementById('wb-loxone-uuid').value = wb.loxone_uuid || '';
  restoreWallboxPasswordIfEmpty();

  const mode = wb.source_type === 'loxone_api' ? 'loxone_api' : 'ocpp';
  const toggleBtns = document.querySelectorAll('#wb-mode-toggle button');
  toggleBtns.forEach(b => b.classList.remove('on'));
  toggleBtns[mode === 'ocpp' ? 0 : 1].classList.add('on');
  setWbMode(toggleBtns[{loxone_api:0, extern_ocpp:1, ocpp:2}[mode] ?? 0], mode);

  document.getElementById('wb-edit-notice').style.display = 'block';
  document.getElementById('wb-edit-name-label').textContent = wb.name;
  const submitBtn = document.getElementById('wb-submit-btn');
  submitBtn.textContent = 'Änderungen speichern';
  submitBtn.setAttribute('onclick', 'saveEditedWallbox()');
  // Modal öffnen statt scroll
  openWbModal(wallboxId);
}

function cancelEditWallbox() {
  closeWbModal();
}

// Doppelte Wallboxen zusammenführen. Der Knopf erscheint nur, wenn es
// tatsächlich eine automatisch angelegte "BMW (zuhause)" neben einer
// echten Wallbox gibt.
// Knopf nur zeigen, wenn es wirklich eine doppelte gibt — sonst steht
// dort eine Schaltfläche, die niemand versteht.
async function _pruefeDoppelteWallbox() {
  const btn = document.getElementById('wb-merge-btn');
  if (!btn) return;
  try {
    const d = await (await fetch('/api/wallboxes')).json();
    const alle = d.wallboxes || [];
    const bmwHeim = alle.some(w => w.name === 'BMW (zuhause)');
    const echte = alle.some(w => !String(w.name).startsWith('BMW ')
                              && w.name !== 'Unterwegs geladen');
    btn.style.display = (bmwHeim && echte) ? 'inline-flex' : 'none';
  } catch (e) {
    btn.style.display = 'none';
  }
}

async function wallboxenZusammenfuehren() {
  if (!confirm('Die automatisch angelegte Wallbox "BMW (zuhause)" mit Ihrer '
             + 'echten Wallbox zusammenführen?\n\n'
             + 'Alle Ladevorgänge werden übertragen, der doppelte Eintrag '
             + 'verschwindet. "Unterwegs geladen" bleibt bestehen.')) return;
  try {
    const d = await (await fetch('/api/wallboxes/zusammenfuehren',
                                 { method: 'POST' })).json();
    if (d.ok) {
      _toast(`${d.verschoben} Ladevorgänge auf "${d.ziel}" übertragen`);
      loadWallboxesTable();
      _pruefeDoppelteWallbox();
    } else {
      _toast(d.fehler || 'Zusammenführen fehlgeschlagen');
    }
  } catch (e) {
    _toast('Zusammenführen fehlgeschlagen');
  }
}

function openWbModal(editId) {
  setTimeout(wbVerbindungsartPruefen, 100);
  const modal = document.getElementById('wb-modal');
  const title = document.getElementById('wb-modal-title');
  if (!modal) return;
  if (!editId) {
    // Neues Formular – zurücksetzen
    resetWallboxForm();
    if (title) title.textContent = 'Wallbox hinzufügen';
  } else {
    if (title) title.textContent = 'Wallbox bearbeiten';
  }
  modal.style.display = 'flex';
}

function closeWbModal() {
  const modal = document.getElementById('wb-modal');
  if (modal) modal.style.display = 'none';
  resetWallboxForm();
}

function showSettingsTab(tab, btn) {
  ['hilfe','person','ocpp','bmw','recht','lizenz','system'].forEach(t => {
    const el = document.getElementById('stab-' + t);
    if (el) el.style.display = 'none';
  });
  const active = document.getElementById('stab-' + tab);
  if (active) active.style.display = '';
  document.querySelectorAll('.settings-tab').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  if (tab === 'person') {
    loadPersons();
  }
  if (tab === 'bmw') { cardataStatusLaden(); cardataFahrzeugdatenLaden(); loadLadepreise(); loadHeimadresse(); loadBmwDuplikate(); loadBmwHeimladungen(); }
  if (tab === 'lizenz') editionAnzeigen();
  if (tab === 'system') { backupStatusLaden(); demodatenStatus(); }
  if (tab === 'hilfe') hilfeLaden();
  if (tab === 'ocpp') { ocppVerfuegbarkeitPruefen(); externOcppLaden(); }
  if (tab === 'bmw') bmwVerfuegbarkeitPruefen();
  if (tab === 'recht') loadSteuersatzIntoField();
  if (tab === 'ocpp') {
    populateOcppClientWallboxSelect();
    refreshOcppToggleUi();
    // OCPP-Server-URL anzeigen
    fetch('/api/server-info').then(r=>r.json()).then(d=>{
      const el = document.getElementById('ocpp-server-url-display');
      if (el && d.lan_ip) el.textContent = `ws://${d.lan_ip}:9000/ocpp`;
    }).catch(()=>{});
  }
}

function copyOcppServerUrl() {
  const el = document.getElementById('ocpp-server-url-display');
  if (el && el.textContent && el.textContent !== 'wird geladen …') {
    inZwischenablage(el.textContent).then(ok => {
      const btn = el.nextElementSibling;
      if (ok && btn) {
        const orig = btn.innerHTML;
        btn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--success)" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>';
        setTimeout(() => btn.innerHTML = orig, 1500);
      } else if (!ok) {
        window.prompt('Kopieren mit Strg+C, dann Enter:', el.textContent);
      }
    });
  }
}

function resetWallboxForm() {
  editingWallboxId = null;
  const notice = document.getElementById('wb-edit-notice');
  if (notice) notice.style.display = 'none';
  ['wb-name','wb-location','wb-ocpp-id','wb-loxone-host','wb-loxone-user',
   'wb-loxone-pass','wb-loxone-uuid','wb-extern-adresse'].forEach(id => {
    const el = document.getElementById(id); if (el) el.value = '';
  });
  const pfad = document.getElementById('wb-extern-pfad');
  if (pfad) pfad.value = '/api/sessions';

  // Verbindungsart ebenfalls zurücksetzen. Fehlte das, blieb die zuletzt
  // gewählte Art aktiv, während die Schaltfläche darüber „Loxone-API" anzeigte
  // — man musste erst hin- und herklicken, damit beides zusammenpasste.
  const knopf = document.querySelector('#wb-mode-toggle button');
  if (knopf) setWbMode(knopf, 'loxone_api');

  ['wb-extern-ergebnis','wb-modal-msg'].forEach(id => {
    const el = document.getElementById(id); if (el) el.innerHTML = '';
  });

  const submitBtn = document.getElementById('wb-submit-btn');
  if (submitBtn) { submitBtn.textContent = 'Speichern'; submitBtn.setAttribute('onclick', 'addWallbox()'); }
}

async function saveEditedWallbox() {
  if (!editingWallboxId) return;
  const msgEl = document.getElementById('wb-form-message');
  const name = document.getElementById('wb-name').value.trim();
  if (!name) { msgEl.textContent = 'Bitte einen Namen eingeben.'; msgEl.style.color = 'var(--danger)'; return; }

  const payload = { name: name, source_type: wbMode, location: document.getElementById('wb-location').value.trim() };
  if (wbMode === 'ocpp') {
    payload.ocpp_charge_point_id = document.getElementById('wb-ocpp-id').value.trim();
  } else if (wbMode === 'extern_ocpp') {
    // Beim externen Dienst gibt es keine Zugangsdaten — nur die Adresse,
    // unter der die gesammelten Ladevorgänge abzuholen sind.
    payload.extern_adresse = document.getElementById('wb-extern-adresse')?.value.trim() || '';
    payload.extern_pfad = document.getElementById('wb-extern-pfad')?.value.trim() || '/api/sessions';
  } else {
    payload.loxone_host = document.getElementById('wb-loxone-host').value.trim();
    payload.loxone_username = document.getElementById('wb-loxone-user').value.trim();
    payload.loxone_password = document.getElementById('wb-loxone-pass').value;
    payload.loxone_uuid = document.getElementById('wb-loxone-uuid').value.trim();
  }

  msgEl.innerHTML = '<span style="color:var(--text-tertiary);">Speichere …</span>';
  try {
    const resp = await fetch(`/api/wallboxes/${editingWallboxId}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (resp.ok) {
      // Nach dem Speichern direkt Verbindungstest anstoßen
      msgEl.innerHTML = '<span style="color:var(--text-tertiary);">Gespeichert — teste Verbindung …</span>';
      cancelEditWallbox();
      await loadWallboxesTable();

      // Verbindungstest je nach Typ
      if (wbMode === 'loxone_api') {
        const testPayload = { wallbox_id: editingWallboxId,
          loxone_host: payload.loxone_host,
          loxone_username: payload.loxone_username,
          loxone_password: payload.loxone_password,
          loxone_uuid: payload.loxone_uuid };
        try {
          const testResp = await fetch('/api/wallboxes/test-connection', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(testPayload),
          });
          const testData = await testResp.json();
          if (testData.ok) {
            msgEl.innerHTML = `<span style="color:var(--success);">✓ Gespeichert — ${testData.message}</span>`;
          } else {
            msgEl.innerHTML = `<span style="color:var(--warning);">Gespeichert — Verbindungstest: ${testData.message}</span>`;
          }
        } catch (e) {
          msgEl.innerHTML = '<span style="color:var(--success);">✓ Gespeichert</span>';
        }
      } else {
        msgEl.innerHTML = '<span style="color:var(--success);">✓ Gespeichert</span>';
      }
      // Dialog schließen und die Übersicht auffrischen. Zuvor blieb er offen
      // stehen, und man wusste nicht, ob noch etwas zu tun ist.
      setTimeout(() => {
        closeWbModal();
        loadWallboxesTable();
      }, 1100);
    } else {
      msgEl.innerHTML = `<span style="color:var(--danger);">Fehler: ${data.error || resp.status}</span>`;
    }
  } catch (e) {
    msgEl.innerHTML = `<span style="color:var(--danger);">Netzwerkfehler: ${e}</span>`;
  }
}

async function deleteAllWallboxes() {
  if (!confirm('Wirklich ALLE Wallboxen löschen? Wallboxen mit bereits zugeordneten Ladesessions werden dabei übersprungen.')) return;
  const resp = await fetch('/api/wallboxes/delete-all', { method: 'POST' });
  const data = await resp.json();
  let msg = `${data.deleted.length} Wallbox(en) gelöscht.`;
  if (data.skipped.length > 0) {
    msg += `\n${data.skipped.length} übersprungen (haben noch Sessions): ${data.skipped.map(s => s.name).join(', ')}`;
  }
  alert(msg);
  await loadWallboxesTable();
}

async function deleteAllSessions() {
  if (!confirm('Wirklich ALLE Ladesessions unwiderruflich löschen?')) return;
  const resp = await fetch('/api/sessions/delete-all', { method: 'POST' });
  const data = await resp.json();
  alert(`${data.deleted_count} Session(en) gelöscht.`);
  await loadSessions();
}

async function toggleWallboxPolling(wallboxId, pause) {
  try {
    const resp = await fetch(`/api/wallboxes/${wallboxId}/toggle-polling`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paused: pause }),
    });
    if (resp.ok) {
      await loadWallboxesTable();
    } else {
      alert('Fehler beim Umschalten des Pollings.');
    }
  } catch (e) {
    alert('Netzwerkfehler: ' + e);
  }
}

async function loadWallboxesTable() {
  const container = document.getElementById('wallboxes-tree');
  if (!container) return;
  try {
    const resp = await hole('/api/wallboxes/full');
    const data = await resp.json();
    if (data.wallboxes.length === 0) {
      container.innerHTML = '<div class="hint" style="padding:16px 4px;">Noch keine Wallboxen angelegt.</div>';
      return;
    }
    container.innerHTML = '';
    data.wallboxes.forEach(wb => {
      const idOrHost = wb.source_type === 'ocpp' ? (wb.ocpp_charge_point_id || '–') : (wb.loxone_host || '–');
      const statusLabel = wb.live_status || 'unbekannt';
      const pillClass = statusLabel === 'charging' ? 'pill-amber' : (statusLabel === 'online' || statusLabel === 'ready' ? 'pill-teal' : 'pill-neutral');
      const loxoneIcons = wb.source_type === 'loxone_api'
        ? `<button class="icon-btn" title="Live-Status jetzt aktualisieren" onclick="checkWallbox2Log(${wb.id})">${ICONS.plug}</button>`
        : '';
      const pauseToggle = wb.source_type === 'loxone_api'
        ? `<button class="btn btn-sm" style="${wb.polling_paused ? 'background:var(--danger); color:#fff; border-color:var(--danger);' : ''}"
             title="${wb.polling_paused ? 'Polling ist pausiert — keine Verbindungsversuche zum Miniserver' : 'Sofort-Stopp: keine weiteren Verbindungsversuche zum Miniserver mehr, unabhängig vom automatischen Backoff'}"
             onclick="toggleWallboxPolling(${wb.id}, ${!wb.polling_paused})">
             ${wb.polling_paused ? '▶ Polling fortsetzen' : '⏸ Polling pausieren'}
           </button>`
        : '';
      const decryptErrorHint = statusLabel.includes('entschlüsselung')
        ? `<div style="margin-top:4px;"><a href="#" onclick="startEditWallbox(${wb.id}); return false;" style="font-size:12px; color:var(--amber-strong);">Passwort neu eingeben →</a></div>`
        : '';

      const node = document.createElement('div');
      node.className = 'wallbox-node';
      node.innerHTML = `
        <div class="wallbox-node-header" onclick="toggleWallboxNode(${wb.id})">
          <svg class="wallbox-node-chevron open" id="wb-chevron-${wb.id}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
          <span class="wallbox-node-name">${wb.name}</span>
          <span class="badge-fall">${wb.source_type.toUpperCase()}</span>
          <span class="pill ${pillClass}"><span class="pill-dot"></span>${statusLabel}</span>
        </div>
        <div class="wallbox-node-body" id="wb-body-${wb.id}">
          <div class="wallbox-node-detail-row"><span class="k">ID / Host</span><span class="v mono">${idOrHost}</span></div>
          ${decryptErrorHint}
          ${wb.source_type === 'loxone_api' ? `<div class="wallbox-node-live" id="live-metrics-${wb.id}">Live-Daten werden geladen …</div>` : ''}
          <div class="wallbox-node-actions">
            <button class="icon-btn" title="Bearbeiten" onclick="startEditWallbox(${wb.id})">${ICONS.edit}</button>
            <button class="icon-btn icon-btn-danger" title="Löschen" onclick="deleteWallbox(${wb.id})">${ICONS.trash}</button>
            ${pauseToggle}
            ${loxoneIcons}
          </div>
        </div>
      `;
      container.appendChild(node);
      if (wb.source_type === 'loxone_api') {
        refreshLiveMetrics(wb.id);
      }
    });
  } catch (e) {
    container.innerHTML = '<div class="hint" style="padding:16px 4px;">Fehler beim Laden.</div>';
  }

  if (!window._liveMetricsInterval) {
    window._liveMetricsInterval = setInterval(() => {
      document.querySelectorAll('[id^="live-metrics-"]').forEach(el => {
        const wallboxId = el.id.replace('live-metrics-', '');
        refreshLiveMetrics(wallboxId);
      });
    }, 15000);
  }
}

function toggleWallboxNode(wallboxId) {
  const body = document.getElementById(`wb-body-${wallboxId}`);
  const chevron = document.getElementById(`wb-chevron-${wallboxId}`);
  if (!body || !chevron) return;
  const isOpen = body.style.display !== 'none';
  body.style.display = isOpen ? 'none' : 'block';
  chevron.classList.toggle('open', !isOpen);
}

let ocppClientEnabledState = true;

function setOcppClientEnabledToggle(btn, enabled) {
  ocppClientEnabledState = enabled;
  const toggle = document.getElementById('ocpp-client-enabled-toggle');
  toggle.querySelectorAll('button').forEach(b => b.classList.remove('on'));
  btn.classList.add('on');
}

async function populateOcppClientWallboxSelect() {
  const select = document.getElementById('ocpp-client-wallbox-select');
  if (!select) return;
  try {
    const resp = await hole('/api/wallboxes/full');
    const data = await resp.json();
    select.innerHTML = data.wallboxes.map(wb => `<option value="${wb.id}">${wb.name}</option>`).join('');
    if (data.wallboxes.length > 0) loadOcppClientConfig();
  } catch (e) { /* Auswahl bleibt leer */ }
}

async function loadOcppClientConfig() {
  const select = document.getElementById('ocpp-client-wallbox-select');
  const statusEl = document.getElementById('ocpp-client-status');
  if (!select || !select.value) return;
  try {
    const resp = await fetch(`/api/wallboxes/${select.value}/ocpp-client-config`);
    const data = await resp.json();
    if (data.configured) {
      document.getElementById('ocpp-client-url').value = data.remote_url || '';
      document.getElementById('ocpp-client-cpid').value = data.remote_charge_point_id || '';
      setOcppClientEnabledToggle(
        document.querySelectorAll('#ocpp-client-enabled-toggle button')[data.enabled ? 0 : 1],
        !!data.enabled,
      );
      let status = data.last_connect_success_at
        ? `Letzte erfolgreiche Verbindung: ${data.last_connect_success_at}`
        : 'Noch keine erfolgreiche Verbindung.';
      if (data.last_error) status += ` — Letzter Fehler: ${data.last_error}`;
      statusEl.textContent = status;
    } else {
      document.getElementById('ocpp-client-url').value = '';
      document.getElementById('ocpp-client-cpid').value = '';
      statusEl.textContent = 'Für diese Wallbox noch nicht konfiguriert.';
    }
  } catch (e) {
    statusEl.textContent = 'Fehler beim Laden.';
  }
}

async function saveOcppClientConfig() {
  const select = document.getElementById('ocpp-client-wallbox-select');
  const statusEl = document.getElementById('ocpp-client-status');
  if (!select.value) { alert('Bitte zuerst eine Wallbox auswählen.'); return; }
  const remote_url = document.getElementById('ocpp-client-url').value.trim();
  const remote_charge_point_id = document.getElementById('ocpp-client-cpid').value.trim();

  if (!remote_url || !remote_charge_point_id) {
    statusEl.innerHTML = '<span style="color:var(--warning);">⚠ Bitte URL und Charge-Point-ID ausfüllen.</span>';
    return;
  }

  // 1. Speichern
  statusEl.innerHTML = '<span style="color:var(--text-tertiary);">Speichere …</span>';
  try {
    const saveResp = await fetch(`/api/wallboxes/${select.value}/ocpp-client-config`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ remote_url, remote_charge_point_id, enabled: ocppClientEnabledState }),
    });
    const saveData = await saveResp.json();
    if (!saveResp.ok) {
      statusEl.innerHTML = `<span style="color:var(--danger);">✗ Fehler beim Speichern: ${saveData.message || saveData.error}</span>`;
      return;
    }
  } catch (e) {
    statusEl.innerHTML = `<span style="color:var(--danger);">✗ Netzwerkfehler beim Speichern: ${e}</span>`;
    return;
  }

  // 2. Sofort Verbindungstest
  statusEl.innerHTML = '<span style="color:var(--text-tertiary);">Gespeichert — teste Verbindung …</span>';
  try {
    const testResp = await fetch(`/api/wallboxes/${select.value}/ocpp-client-test`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ remote_url, remote_charge_point_id }),
    });
    const testData = await testResp.json();
    if (testData.ok) {
      statusEl.innerHTML = `<span style="color:var(--success);">✓ Gespeichert &amp; ${testData.message}</span>`;
    } else {
      statusEl.innerHTML = `<span style="color:var(--danger);">Gespeichert, aber Verbindungstest fehlgeschlagen:<br>${testData.message}</span>`;
    }
  } catch (e) {
    statusEl.innerHTML = `<span style="color:var(--warning);">Gespeichert — Verbindungstest nicht möglich: ${e}</span>`;
  }
}

async function deleteOcppClientConfig() {
  const select = document.getElementById('ocpp-client-wallbox-select');
  if (!select.value) return;
  if (!confirm('OCPP-Client-Konfiguration für diese Wallbox wirklich entfernen?')) return;
  await fetch(`/api/wallboxes/${select.value}/ocpp-client-config`, { method: 'DELETE' });
  document.getElementById('ocpp-client-url').value = '';
  document.getElementById('ocpp-client-cpid').value = '';
  document.getElementById('ocpp-client-status').textContent = 'Entfernt.';
}

let _protokollShowRoh = false;

function setProtokollRohFilter(btn, showRoh) {
  _protokollShowRoh = showRoh;
  btn.parentElement.querySelectorAll('button').forEach(b => b.classList.remove('on'));
  btn.classList.add('on');
  loadProtokoll();
}

async function clearEventLog() {
  if (!confirm('Gesamtes Ereignis-Protokoll löschen?')) return;
  try {
    const r = await fetch('/api/event-log/clear', { method: 'DELETE' });
    const d = await r.json();
    if (r.ok && d.ok) {
      const tbody = document.getElementById('protokoll-tbody');
      if (tbody) tbody.innerHTML = '<tr><td colspan="4" class="hint">Protokoll geleert.</td></tr>';
    }
  } catch(e) {
    alert('Fehler beim Löschen: ' + e);
  }
}

async function deleteOcppRawLog() {
  if (!confirm('Rohdaten-Logdatei unwiderruflich löschen?')) return;
  const resp = await fetch('/api/ocpp/raw-log', { method: 'DELETE' });
  const data = await resp.json();
  if (resp.ok) {
    const infoEl = document.getElementById('ocpp-log-file-info');
    if (infoEl) infoEl.textContent = 'Logdatei gelöscht.';
    const pre = document.getElementById('ocpp-log-tail');
    if (pre) pre.textContent = '';
    const emptyEl = document.getElementById('ocpp-log-empty');
    if (emptyEl) emptyEl.style.display = 'block';
    if (pre) pre.style.display = 'none';
  }
}

async function loadProtokoll() {
  const tbody = document.getElementById('protokoll-tbody');
  if (!tbody) return;
  const params = new URLSearchParams();
  const source = document.getElementById('protokoll-source').value;
  const level = document.getElementById('protokoll-level').value;
  if (source) params.set('source', source);
  if (level) params.set('level', level);

  try {
    const resp = await fetch('/api/events?' + params.toString());
    const data = await resp.json();
    if (!data.events || data.events.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4" class="hint">Noch keine Ereignisse protokolliert.</td></tr>';
      return;
    }
    const levelColors = { info: 'var(--text-secondary)', warning: 'var(--warning)', error: 'var(--danger)' };
    const sourceLabels = { ocpp: 'OCPP', extern_ocpp: 'OCPP extern', loxone_api: 'Loxone-API', system: 'System',
                           manual: 'Manuell', bmw: 'BMW Sync', bmw_app: 'BMW App' };

    // ROH-Filter: "ROH von" und "ROH an" ausblenden wenn gewünscht
    let events = data.events;
    if (!_protokollShowRoh) {
      events = events.filter(e => !e.message.startsWith('ROH von') && !e.message.startsWith('ROH an'));
    }

    if (events.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4" class="hint">Keine Ereignisse (ROH-Nachrichten ausgeblendet).</td></tr>';
      return;
    }

    tbody.innerHTML = events.map(e => `
      <tr>
        <td class="mono" style="font-size:12px;">${e.created_at}</td>
        <td>${sourceLabels[e.source] || e.source}</td>
        <td style="color:${levelColors[e.level] || 'inherit'}; font-weight:600;">${e.level}</td>
        <td style="max-width:700px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${e.message.replace(/"/g,'&quot;')}">${e.message}</td>
      </tr>
    `).join('');
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="4" class="hint">Fehler beim Laden des Protokolls.</td></tr>';
  }
}

let ocppLogTailData = [];

async function loadOcppDiagnose() {
  const tbody = document.getElementById('ocpp-counts-tbody');
  const notice = document.getElementById('ocpp-missing-notice');
  if (!tbody) return;
  try {
    const resp = await fetch('/api/ocpp/diagnose');
    const data = await resp.json();

    if (data.expected_but_missing && data.expected_but_missing.length > 0) {
      notice.style.display = 'block';
      notice.innerHTML = `⚠ Folgende Nachrichtentypen sind bisher <strong>nie</strong> eingegangen: `
        + `<strong>${data.expected_but_missing.join(', ')}</strong>. `
        + `Das bedeutet: die Wallbox/Loxone-Gegenstelle sendet diese Transaktionsdaten über OCPP nicht — `
        + `bestätigt bekannte Einschränkung, kein Fehler in dieser App.`;
    } else {
      notice.style.display = 'none';
    }

    if (!data.message_counts || data.message_counts.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4" class="hint">Noch keine OCPP-Nachrichten empfangen.</td></tr>';
    } else {
      const isTransactionType = t => ['StartTransaction', 'MeterValues', 'StopTransaction'].includes(t);
      tbody.innerHTML = data.message_counts.map(c => `
        <tr${isTransactionType(c.message_type) ? ' style="background:var(--amber-soft);"' : ''}>
          <td class="mono">${c.message_type}</td>
          <td class="mono">${c.charge_point_id}</td>
          <td class="mono">${c.count}</td>
          <td class="mono" style="font-size:12px;">${c.last_seen_at}</td>
        </tr>
      `).join('');
    }
    ocppLogTailData = data.log_tail || [];
    document.getElementById('ocpp-log-tail').textContent = ocppLogTailData.join('\n');
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="4" class="hint">Fehler beim Laden der OCPP-Diagnose.</td></tr>';
  }
}

let _ocppLogAutoRefreshTimer = null;

function toggleOcppLogTail() {
  const pre = document.getElementById('ocpp-log-tail');
  const btn = document.getElementById('ocpp-log-toggle-btn');
  const refreshBtn = document.getElementById('ocpp-log-refresh-btn');
  const autoLabel = document.getElementById('ocpp-log-autorefresh-label');
  const emptyEl = document.getElementById('ocpp-log-empty');
  const show = pre.style.display === 'none';

  if (show) {
    btn.textContent = 'Rohdaten-Logdatei ausblenden';
    refreshBtn.style.display = 'inline-block';
    autoLabel.style.display = 'inline';
    const exportBtn = document.getElementById('ocpp-log-export-btn');
    if (exportBtn) exportBtn.style.display = 'inline-block';
    refreshOcppRawLog();
    // Auto-Refresh alle 5 Sekunden
    _ocppLogAutoRefreshTimer = setInterval(refreshOcppRawLog, 5000);
  } else {
    pre.style.display = 'none';
    emptyEl.style.display = 'none';
    btn.textContent = 'Rohdaten-Logdatei anzeigen';
    refreshBtn.style.display = 'none';
    autoLabel.style.display = 'none';
    const exportBtn = document.getElementById('ocpp-log-export-btn');
    if (exportBtn) exportBtn.style.display = 'none';
    if (_ocppLogAutoRefreshTimer) {
      clearInterval(_ocppLogAutoRefreshTimer);
      _ocppLogAutoRefreshTimer = null;
    }
  }
}

async function refreshOcppRawLog() {
  const pre = document.getElementById('ocpp-log-tail');
  const emptyEl = document.getElementById('ocpp-log-empty');
  try {
    const resp = await fetch('/api/ocpp/raw-log?n=200');
    const data = await resp.json();
    if (data.file_info) {
      const infoEl = document.getElementById('ocpp-log-file-info');
      if (infoEl) infoEl.textContent = `Datei: ${data.file_info.path} | ${data.file_info.exists ? data.file_info.size_bytes + ' Bytes, ' + data.file_info.line_count + ' Zeilen' : 'nicht vorhanden'}`;
    }
    if (data.lines && data.lines.length > 0) {
      pre.style.display = 'block';
      emptyEl.style.display = 'none';
      const wasAtBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 20;
      pre.textContent = data.lines.join('\n');
      if (wasAtBottom) pre.scrollTop = pre.scrollHeight;
    } else {
      pre.style.display = 'none';
      emptyEl.style.display = 'block';
    }
  } catch (e) {
    pre.textContent = 'Fehler beim Laden: ' + e;
    pre.style.display = 'block';
  }
}

// Hochwertige, konsistente SVG-Icons (ersetzen Emojis wie 🚗/⚡/↻ — Rückmeldung:
// Emoji-Icons wirken unprofessionell und passen nicht zur restlichen Optik).
const ICONS = {
  bolt: '<svg class="lp-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>',
  car: '<svg class="lp-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 17h14M5 17a2 2 0 1 0 0 4 2 2 0 0 0 0-4Zm14 0a2 2 0 1 0 0 4 2 2 0 0 0 0-4ZM5 17V9.5a1 1 0 0 1 .3-.7l2.4-2.4A2 2 0 0 1 9.1 5.7l1.2-.1h3.4l1.2.1a2 2 0 0 1 1.4.7l2.4 2.4a1 1 0 0 1 .3.7V17"/><path d="M5 12h14"/></svg>',
  refresh: '<svg class="lp-icon-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
  plug: '<svg class="lp-icon-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22v-5"/><path d="M9 8V2M15 8V2"/><path d="M18 8v3a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z"/></svg>',
  chart: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="16"/></svg>',
  folder: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>',
  file: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
  edit: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4Z"/></svg>',
  trash: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
  shield: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/></svg>',
};

// ---------- FTP-Browser (wieder eingebaut, um das echte Miniserver-Dateisystem
// nach der historischen Wallbox-Aktivitaet zu durchsuchen) ----------
let ftpCurrentWallboxId = null;
let ftpCurrentPath = '/';

async function openFtpBrowser(wallboxId) {
  ftpCurrentWallboxId = wallboxId;
  ftpCurrentPath = '/';
  document.getElementById('ftp-browser-card').style.display = 'block';
  await ftpLoadPath();
}

function closeFtpBrowser() {
  document.getElementById('ftp-browser-card').style.display = 'none';
  ftpCurrentWallboxId = null;
}

async function ftpLoadPath() {
  const tbody = document.getElementById('ftp-browser-tbody');
  document.getElementById('ftp-browser-path').textContent = ftpCurrentPath;
  tbody.innerHTML = '<tr><td colspan="3" class="hint">Lade …</td></tr>';
  try {
    const resp = await fetch('/api/wallboxes/ftp-browse', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: ftpCurrentPath, wallbox_id: ftpCurrentWallboxId }),
    });
    const data = await resp.json();
    if (!data.ok) {
      tbody.innerHTML = `<tr><td colspan="3" class="hint">${data.message}</td></tr>`;
      return;
    }
    const rowsHtml = [];
    if (ftpCurrentPath !== '/') {
      rowsHtml.push('<tr><td colspan="3" style="cursor:pointer;" onclick="ftpGoUp()">.. (übergeordneter Ordner)</td></tr>');
    }
    for (const entry of data.entries) {
      const safeName = entry.name.replace(/'/g, "\\'");
      const sizeLabel = entry.is_dir ? '' : `${(entry.size / 1024).toFixed(1)} KB`;
      const icon = entry.is_dir ? ICONS.folder : ICONS.file;
      const nameCell = entry.is_dir
        ? `<td style="cursor:pointer;" onclick="ftpEnterDir('${safeName}')">${icon} ${entry.name}</td>`
        : `<td>${icon} ${entry.name}</td>`;
      const actionBtn = entry.is_dir
        ? ''
        : `<button class="icon-btn" title="Herunterladen" onclick="ftpDownload('${safeName}')">⇩</button>`;
      rowsHtml.push(`<tr>${nameCell}<td class="mono">${sizeLabel}</td><td>${actionBtn}</td></tr>`);
    }
    tbody.innerHTML = rowsHtml.join('') || '<tr><td colspan="3" class="hint">Leeres Verzeichnis.</td></tr>';
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="3" class="hint">Netzwerkfehler: ${e}</td></tr>`;
  }
}

function ftpEnterDir(name) {
  ftpCurrentPath = (ftpCurrentPath.endsWith('/') ? ftpCurrentPath : ftpCurrentPath + '/') + name;
  ftpLoadPath();
}

function ftpGoUp() {
  const parts = ftpCurrentPath.split('/').filter(Boolean);
  parts.pop();
  ftpCurrentPath = '/' + parts.join('/');
  ftpLoadPath();
}

function ftpDownload(name) {
  const fullPath = (ftpCurrentPath.endsWith('/') ? ftpCurrentPath : ftpCurrentPath + '/') + name;
  fetch('/api/wallboxes/ftp-download', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: fullPath, wallbox_id: ftpCurrentWallboxId }),
  }).then(async resp => {
    if (!resp.ok) { alert('Download fehlgeschlagen.'); return; }
    const blob = await resp.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    window.URL.revokeObjectURL(url);
  }).catch(e => alert('Netzwerkfehler: ' + e));
}

async function refreshLiveMetrics(wallboxId) {
  const cell = document.getElementById(`live-metrics-${wallboxId}`);
  if (!cell) return;
  try {
    const resp = await fetch(`/api/wallboxes/${wallboxId}/live-metrics`);
    const data = await resp.json();
    if (!data.has_data) {
      cell.innerHTML = `<div class="hint">Noch keine Live-Daten — "Wallbox2-Log jetzt prüfen" klicken oder auf den nächsten automatischen Sync warten.</div>`;
      return;
    }
    const syncTime = new Date(data.last_sync_at.replace(' ', 'T'));
    const secondsAgo = Math.round((Date.now() - syncTime.getTime()) / 1000);
    const agoLabel = secondsAgo < 90 ? `vor ${secondsAgo}s` : `vor ${Math.round(secondsAgo / 60)} min`;
    const powerLabel = data.current_power_kw !== null ? `${data.current_power_kw.toFixed(2)} kW` : '–';
    const raw = data.raw_fields || {};

    // Ca/Vc-Korrektur: Vc = "Vehicle connected" (Fahrzeug verbunden),
    // Cac = "Charging active" (laedt gerade tatsaechlich).
    const vehicleConnected = raw.Vc === '1';
    const chargingActive = raw.Cac === '1';
    const runningKwh = parseFloat(raw.Ccc || '0');

    let statusClass = 'idle';
    let statusText = 'Kein Fahrzeug verbunden';
    if (vehicleConnected && chargingActive) { statusClass = 'charging'; statusText = 'Fahrzeug verbunden, lädt aktiv'; }
    else if (vehicleConnected) { statusClass = 'connected'; statusText = 'Fahrzeug verbunden, lädt gerade nicht'; }

    let sessionHtml = '';
    if (runningKwh > 0) {
      const costLabel = raw.Cclc !== undefined ? ` · ${fmtDe(parseFloat(raw.Cclc), 2)} €` : '';
      sessionHtml = `<span class="live-panel-session">laufend: ${fmtDe(runningKwh, 3)} kWh${costLabel}</span>`;
    }

    const statLabels = [
      ['Tp', 'Max. Leistung', 'kW'], ['Mr', 'Gesamtzähler', 'kWh'],
      ['Cd', 'Heute', 'kWh'], ['Cw', 'Diese Woche', 'kWh'],
      ['Cm', 'Diesen Monat', 'kWh'], ['Cy', 'Dieses Jahr', 'kWh'],
    ];
    const statsHtml = statLabels
      .filter(([key]) => raw[key] !== undefined && raw[key] !== '')
      .map(([key, label, unit]) => `<div class="lp-item"><span class="lp-label">${label}</span><span class="lp-value">${fmtDe(parseFloat(raw[key]), key === 'Tp' ? 1 : 3)} ${unit}</span></div>`)
      .join('');

    cell.innerHTML = `
      <div class="live-panel">
        <div class="live-panel-top">
          ${ICONS.bolt}<span class="lp-power">${powerLabel}</span>
          <span class="live-panel-sync">${ICONS.refresh}<button class="icon-btn" style="width:auto; height:auto; padding:2px 6px; font-size:11px;" title="Jetzt aktualisieren" onclick="checkWallbox2Log(${wallboxId})">Sync ${agoLabel}</button></span>
        </div>
        <div class="live-panel-status ${statusClass}">${ICONS.car}<span>${statusText}</span>${sessionHtml}</div>
        ${statsHtml ? `<div class="live-panel-grid">${statsHtml}</div>` : ''}
      </div>
    `;
  } catch (e) {
    cell.innerHTML = '<div class="hint">Live-Daten nicht abrufbar.</div>';
  }
}

async function deleteWallbox(wallboxId) {
  if (!confirm('Wallbox wirklich löschen?')) return;
  const resp = await fetch('/api/wallboxes/' + wallboxId, { method: 'DELETE' });
  if (resp.ok) {
    await loadWallboxesTable();
    return;
  }
  const data = await resp.json().catch(() => ({}));
  if (data.error === 'wallbox_has_sessions') {
    if (confirm(data.message + '\n\nStattdessen Wallbox SAMT allen zugeordneten Sessions unwiderruflich löschen?')) {
      const resp2 = await fetch('/api/wallboxes/' + wallboxId + '?force=1', { method: 'DELETE' });
      if (resp2.ok) {
        await loadWallboxesTable();
        await loadSessions();
      } else {
        alert('Löschen fehlgeschlagen.');
      }
    }
  } else {
    alert(data.message || 'Fehler beim Löschen (HTTP ' + resp.status + ').');
  }
}

async function deleteAllWallboxes() {
  if (!confirm('Alle Wallboxen OHNE zugeordnete Sessions löschen? Wallboxen mit Sessions bleiben erhalten.')) return;
  const resp = await fetch('/api/wallboxes/delete-all', { method: 'POST' });
  const data = await resp.json();
  await loadWallboxesTable();
  let msg = `${data.deleted.length} Wallbox(en) gelöscht.`;
  if (data.skipped.length > 0) {
    msg += `\n\n${data.skipped.length} übersprungen (haben noch Sessions):\n` +
           data.skipped.map(s => `- ${s.name}: ${s.reason}`).join('\n');
  }
  alert(msg);
}

function onStructureSelect(select) {
  document.getElementById('wb-loxone-uuid').value = select.value;
  readLoxoneValueNow();
}

async function readLoxoneValueNow() {
  const host = document.getElementById('wb-loxone-host').value.trim();
  const username = document.getElementById('wb-loxone-user').value.trim();
  const password = document.getElementById('wb-loxone-pass').value;
  const uuid = document.getElementById('wb-loxone-uuid').value.trim();
  const valueEl = document.getElementById('wb-loxone-live-value');

  // Bei einer bereits gespeicherten Wallbox (editingWallboxId gesetzt) kann
  // der Server fehlende Angaben (insbesondere das Passwort) selbst aus der
  // gespeicherten, verschluesselten Wallbox ergaenzen — das Passwort muss
  // dann NICHT erneut eingegeben werden. Nur bei einer komplett neuen,
  // ungespeicherten Wallbox sind alle Felder zwingend erforderlich.
  if (!uuid) {
    valueEl.textContent = 'Bitte einen Baustein/UUID angeben.';
    valueEl.style.color = 'var(--warning)';
    return;
  }
  if (!editingWallboxId && (!host || !username || !password)) {
    valueEl.textContent = 'Bitte IP, Benutzername und Passwort angeben.';
    valueEl.style.color = 'var(--warning)';
    return;
  }
  valueEl.textContent = 'Lese aktuellen Wert …';
  valueEl.style.color = 'var(--text-tertiary)';

  try {
    const resp = await fetch('/api/wallboxes/read-value', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ loxone_host: host, loxone_username: username, loxone_password: password, loxone_uuid: uuid, wallbox_id: editingWallboxId }),
    });
    const data = await resp.json();
    valueEl.textContent = data.message;
    valueEl.style.color = data.ok ? 'var(--success)' : 'var(--danger)';
  } catch (e) {
    valueEl.textContent = 'Netzwerkfehler: ' + e;
    valueEl.style.color = 'var(--danger)';
  }
}

async function loadLoxoneStructure() {
  // Bei bestehender Wallbox fehlende Felder aus der DB ergaenzen (ueberschreibt
  // keine Eingaben), sonst aus dem Sitzungs-Zwischenspeicher — dadurch klappt
  // "Struktur laden" auch bei einer noch nicht gespeicherten Wallbox.
  if (editingWallboxId) {
    await prefillWallboxFields(editingWallboxId);
  } else {
    restoreLoxoneConnectionIfEmpty();
  }
  const host = document.getElementById('wb-loxone-host').value.trim();
  const username = document.getElementById('wb-loxone-user').value.trim();
  const password = document.getElementById('wb-loxone-pass').value || lastEnteredWallboxPassword;
  const msgEl = document.getElementById('wb-form-message');
  const wrap = document.getElementById('wb-loxone-structure-wrap');
  const select = document.getElementById('wb-loxone-structure-select');
  const label = document.getElementById('wb-loxone-structure-label');
  const showAll = document.getElementById('wb-loxone-show-all').checked;

  if (!host) {
    msgEl.textContent = 'Bitte zuerst die IP-Adresse des Miniservers eingeben.';
    msgEl.style.color = 'var(--danger)';
    return;
  }
  msgEl.textContent = 'Lade Struktur …';
  msgEl.style.color = 'var(--text-tertiary)';

  try {
    const resp = await fetch('/api/wallboxes/loxone-structure', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ loxone_host: host, loxone_username: username, loxone_password: password, show_all: showAll, wallbox_id: editingWallboxId }),
    });
    const data = await resp.json();
    if (data.error || !data.controls || data.controls.length === 0) {
      if (data.error === 'host_required') {
        msgEl.textContent = data.message || 'Bitte IP-Adresse, Benutzername und Passwort angeben.';
        msgEl.style.color = 'var(--danger)';
      } else if (!showAll && !data.error) {
        msgEl.textContent = 'Keine Wallbox erkannt — weder am Bausteintyp, an den '
          + 'Ausgängen noch am Namen. Setze den Haken bei "auch andere Bausteine zeigen", '
          + 'dann erscheinen alle zur Auswahl.';
        msgEl.style.color = 'var(--warning)';
      } else {
        msgEl.textContent = 'Struktur nicht erreichbar oder leer — bitte UUID manuell eintragen.';
        msgEl.style.color = 'var(--warning)';
      }
      wrap.style.display = 'none';
      return;
    }
    label.textContent = showAll ? 'Baustein auswählen (alle)' : 'Wallbox-Baustein auswählen';
    select.innerHTML = data.controls.map(c => `<option value="${c.uuid}">${c.is_wallbox ? '[Wallbox] ' : ''}${c.name} (${c.type})</option>`).join('');
    wrap.style.display = 'block';
    document.getElementById('wb-loxone-uuid').value = data.controls[0].uuid;
    const wallboxCount = data.controls.filter(c => c.is_wallbox).length;
    // Sagen, wie viele und woran erkannt — sonst bleibt unklar, ob die
    // Auswahl vollständig ist.
    msgEl.textContent = showAll
      ? `${data.controls.length} Bausteine gefunden, davon ${wallboxCount} als Wallbox erkannt (mit [Wallbox] markiert).`
      : `${data.controls.length} Wallbox${data.controls.length === 1 ? '' : 'en'} gefunden.`;
    readLoxoneValueNow();
    msgEl.style.color = 'var(--success)';
  } catch (e) {
    msgEl.textContent = 'Fehler beim Laden der Struktur: ' + e;
    msgEl.style.color = 'var(--danger)';
  }
}

function triggerStatsCsvImport(wallboxId) {
  document.getElementById('stats-csv-input-' + wallboxId).click();
}

async function importStatsCsv(wallboxId, input) {
  const file = input.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append('file', file);
  try {
    const resp = await fetch(`/api/wallboxes/${wallboxId}/import-statistics-csv`, { method: 'POST', body: formData });
    const data = await resp.json();
    if (resp.ok) {
      alert(`Import fertig: ${data.processed} Messwerte verarbeitet, ${data.started || 0} Session(n) gestartet, ${data.closed || 0} abgeschlossen.` +
            (data.skipped && data.skipped.length ? `\n${data.skipped.length} Zeile(n) übersprungen.` : ''));
      await loadSessions();
    } else {
      alert('Fehler beim Import: ' + (data.error || resp.status));
    }
  } catch (e) {
    alert('Netzwerkfehler: ' + e);
  }
  input.value = '';
}

// ─── Loxone-Log-Import (Sessions-Seite) ────────────────────────────────────
// Hierher verschoben aus der Wallbox-Seite (Rückmeldung Auftraggeber: "hat
// in der Wallbox nichts zu suchen, gehört zu den Sessions als Importoption").

function toggleLoxoneLogImport() {
  const panel = document.getElementById('loxone-log-import-panel');
  if (!panel) return;
  const isOpen = panel.style.display !== 'none';
  panel.style.display = isOpen ? 'none' : 'block';
  if (!isOpen) _populateLogImportWallboxSelect();
}

async function _populateLogImportWallboxSelect() {
  const select = document.getElementById('log-import-wallbox-select');
  if (!select) return;
  try {
    const resp = await hole('/api/wallboxes/full');
    const data = await resp.json();
    const loxoneWbs = data.wallboxes.filter(wb => wb.source_type === 'loxone_api');
    if (loxoneWbs.length === 0) {
      select.innerHTML = '<option value="">— keine Loxone-Wallboxen konfiguriert —</option>';
      return;
    }
    select.innerHTML = loxoneWbs.map(wb => `<option value="${wb.id}">${wb.name}</option>`).join('');
  } catch (e) { /* select bleibt leer */ }
}

function triggerLoxoneLogFileUpload() {
  document.getElementById('loxone-log-file-input').click();
}

async function importLoxoneLogFile(input) {
  const resultEl = document.getElementById('loxone-log-import-result');
  const wallboxId = document.getElementById('log-import-wallbox-select').value;
  if (!wallboxId) {
    resultEl.textContent = 'Bitte zuerst eine Wallbox auswählen.';
    resultEl.style.color = 'var(--warning)';
    return;
  }
  if (!input.files || !input.files[0]) return;

  resultEl.textContent = 'Wird importiert …';
  resultEl.style.color = 'var(--text-tertiary)';

  const text = await input.files[0].text();
  try {
    const resp = await fetch(`/api/wallboxes/${wallboxId}/import-log-text`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ log_text: text }),
    });
    const data = await resp.json();
    if (resp.ok) {
      resultEl.textContent = `Importiert: ${data.imported} neue Session(s). ${data.skipped_duplicate} bereits vorhanden, ${data.total_lines} Zeilen geprüft.`;
      resultEl.style.color = data.imported > 0 ? 'var(--success)' : 'var(--text-secondary)';
      if (data.imported > 0) loadSessions();
    } else {
      resultEl.textContent = 'Fehler: ' + (data.message || data.error);
      resultEl.style.color = 'var(--danger)';
    }
  } catch (e) {
    resultEl.textContent = 'Netzwerkfehler: ' + e;
    resultEl.style.color = 'var(--danger)';
  }
  input.value = '';
}

async function triggerLoxoneLogFromMiniserver() {
  const resultEl = document.getElementById('loxone-log-import-result');
  const wallboxId = document.getElementById('log-import-wallbox-select').value;
  if (!wallboxId) {
    resultEl.textContent = 'Bitte zuerst eine Wallbox auswählen.';
    resultEl.style.color = 'var(--warning)';
    return;
  }
  resultEl.textContent = 'Verbinde mit Miniserver …';
  resultEl.style.color = 'var(--text-tertiary)';
  try {
    const resp = await fetch(`/api/wallboxes/${wallboxId}/fetch-and-import-log`, {
      method: 'POST',
    });
    const data = await resp.json();
    if (resp.ok) {
      resultEl.textContent = `Importiert: ${data.imported} neue Session(s). ${data.skipped_duplicate} bereits vorhanden, ${data.total_lines} Zeilen geprüft.`;
      resultEl.style.color = data.imported > 0 ? 'var(--success)' : 'var(--text-secondary)';
      if (data.imported > 0) loadSessions();
    } else {
      resultEl.textContent = 'Fehler: ' + (data.message || data.error);
      resultEl.style.color = 'var(--danger)';
    }
  } catch (e) {
    resultEl.textContent = 'Netzwerkfehler: ' + e;
    resultEl.style.color = 'var(--danger)';
  }
}


async function testWbConnection() {
  const msgEl = document.getElementById('wb-form-message');
  if (wbMode === 'ocpp') {
    // OCPP-Server-Status prüfen
    await testOcppServer();
    return;
  }
  const payload = {
    loxone_host: document.getElementById('wb-loxone-host').value.trim(),
    loxone_username: document.getElementById('wb-loxone-user').value.trim(),
    loxone_password: document.getElementById('wb-loxone-pass').value,
    loxone_uuid: document.getElementById('wb-loxone-uuid').value.trim(),
    wallbox_id: editingWallboxId,
  };
  if (payload.loxone_password) lastEnteredWallboxPassword = payload.loxone_password;
  msgEl.textContent = 'Teste Verbindung …';
  msgEl.style.color = 'var(--text-tertiary)';
  try {
    const resp = await fetch('/api/wallboxes/test-connection', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    });
    const data = await resp.json();
    msgEl.textContent = data.message;
    msgEl.style.color = data.ok ? 'var(--success)' : 'var(--warning)';
  } catch (e) {
    msgEl.textContent = 'Netzwerkfehler: ' + e;
    msgEl.style.color = 'var(--danger)';
  }
}

async function addWallbox() {
  const msgEl = document.getElementById('wb-form-message');
  const name = document.getElementById('wb-name').value.trim();
  if (!name) { msgEl.textContent = 'Bitte einen Namen eingeben.'; msgEl.style.color = 'var(--danger)'; return; }

  const payload = { name: name, source_type: wbMode, location: document.getElementById('wb-location').value.trim() };
  if (wbMode === 'ocpp') {
    payload.ocpp_charge_point_id = document.getElementById('wb-ocpp-id').value.trim();
  } else if (wbMode === 'extern_ocpp') {
    // Beim externen Dienst gibt es keine Zugangsdaten — nur die Adresse,
    // unter der die gesammelten Ladevorgänge abzuholen sind.
    payload.extern_adresse = document.getElementById('wb-extern-adresse')?.value.trim() || '';
    payload.extern_pfad = document.getElementById('wb-extern-pfad')?.value.trim() || '/api/sessions';
  } else {
    payload.loxone_host = document.getElementById('wb-loxone-host').value.trim();
    payload.loxone_username = document.getElementById('wb-loxone-user').value.trim();
    payload.loxone_password = document.getElementById('wb-loxone-pass').value;
    payload.loxone_uuid = document.getElementById('wb-loxone-uuid').value.trim();
  }

  try {
    const resp = await fetch('/api/wallboxes', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (resp.ok) {
      msgEl.textContent = '✓ Wallbox angelegt.';
      msgEl.style.color = 'var(--success)';
      await loadWallboxesTable();
      await loadWallboxesIntoFilter();
      // Dialog schließen und Formular leeren. Zuvor blieb er offen stehen —
      // man wusste nicht, ob noch etwas zu tun ist.
      setTimeout(() => { closeWbModal(); resetWallboxForm(); }, 900);
    } else {
      msgEl.textContent = 'Fehler: ' + (data.error || resp.status);
      msgEl.style.color = 'var(--danger)';
    }
  } catch (e) {
    msgEl.textContent = 'Netzwerkfehler: ' + e;
    msgEl.style.color = 'var(--danger)';
  }
}

// ---------- Auswertung (Sprint 4) ----------

let currentPeriodKwh = 0;
const PAUSCHALE_RATE = 0.34;

async function loadAnalyticsWallboxFilter() {
  const sel = document.getElementById('ausw-wallbox');
  if (!sel) return;
  try {
    const resp = await fetch('/api/wallboxes');
    const data = await resp.json();
    const current = sel.value;
    sel.innerHTML = '<option value="">Alle Wallboxen</option>';
    data.wallboxes.forEach(wb => {
      const opt = document.createElement('option');
      opt.value = wb.id;
      opt.textContent = wb.name;
      sel.appendChild(opt);
    });
    sel.value = current;
  } catch (e) { /* optional */ }
}

async function loadAnalytics() {
  // Gespeicherten Vertragsstrompreis laden
  try {
    const rr = await fetch('/api/settings/contract-kwh-price');
    const rd = await rr.json();
    if (rd.rate) {
      const el = document.getElementById('ausw-realtarif');
      if (el && (!el.value || el.value === '0')) el.value = rd.rate;
    }
  } catch(e){}
  const klassWrap = document.getElementById('ausw-klass-wrap');
  if (klassWrap) {
    const abrechnungsfall = (APP_STATE && APP_STATE.user) ? APP_STATE.user.abrechnungsfall : null;
    klassWrap.style.display = (abrechnungsfall === 'A' || abrechnungsfall === 'B') ? 'block' : 'none';
  }
  await loadAnalyticsWallboxFilter();
  const wallboxId = document.getElementById('ausw-wallbox').value;
  const klass = document.getElementById('ausw-klass').value;
  const von = document.getElementById('ausw-von').value;
  const bis = document.getElementById('ausw-bis').value;

  const monthlyParams = new URLSearchParams({ months: '6' });
  if (wallboxId) monthlyParams.set('wallbox_id', wallboxId);
  if (klass) monthlyParams.set('classification', klass);

  try {
    const monthlyResp = await fetch('/api/analytics/monthly?' + monthlyParams.toString());
    const monthlyData = await monthlyResp.json();
    renderBarChart(monthlyData.months);
    renderLineChart(monthlyData.months);
  } catch (e) { /* Charts bleiben leer bei Fehler */ }

  const summaryParams = new URLSearchParams();
  if (von) summaryParams.set('von', von);
  if (bis) summaryParams.set('bis', bis);
  if (wallboxId) summaryParams.set('wallbox_id', wallboxId);
  if (klass) summaryParams.set('classification', klass);

  try {
    const summaryResp = await fetch('/api/analytics/summary?' + summaryParams.toString());
    const summary = await summaryResp.json();
    document.getElementById('kpi-avg-price').textContent = fmtDe(summary.avg_price_per_kwh, 3) + ' €';
    document.getElementById('kpi-total-cost').textContent = fmtDe(summary.total_cost, 2) + ' €';
    document.getElementById('kpi-session-count').textContent = summary.session_count;
    document.getElementById('kpi-trip-count').textContent = summary.trip_count;
    document.getElementById('ausw-verbrauch').value = fmtDe(summary.total_kwh, 2) + ' kWh';
    currentPeriodKwh = summary.total_kwh;
    const periodLabel = (von || bis) ? `— ${von || '…'} bis ${bis || '…'}` : '— gesamter Datenbestand';
    document.getElementById('compare-period-label').textContent = periodLabel;
    updateCompare();
  } catch (e) { /* KPIs bleiben auf "-" bei Fehler */ }
}

function renderBarChart(months) {
  const svg = document.getElementById('bar-chart-svg');
  if (!svg) return;
  const maxKwh = Math.max(...months.map(m => Math.max(0, m.kwh || 0)), 1);
  const barWidth = 30, gap = 50, startX = 20;
  let html = '';
  months.forEach((m, i) => {
    // Negative kWh koennen durch fehlerhafte Zaehlerstaende entstehen
    // (Ueberlauf, Zaehlertausch). SVG lehnt negative Hoehen ab, daher hier
    // auf 0 begrenzen — die Ursache wird in der Datenpruefung sichtbar.
    const kwh = Math.max(0, m.kwh || 0);
    const height = maxKwh > 0 ? Math.max(0, (kwh / maxKwh) * 105) : 0;
    const x = startX + i * gap;
    const y = 140 - height;
    html += `<rect class="bar-fill" x="${x}" y="${y}" width="${barWidth}" height="${height}"><title>${m.label} ${m.year}: ${m.kwh} kWh</title></rect>`;
    html += `<text class="axis-text" x="${x + 6}" y="148">${m.label}</text>`;
  });
  svg.innerHTML = html;
}

function renderLineChart(months) {
  const svg = document.getElementById('line-chart-svg');
  if (!svg) return;
  let cumulative = 0;
  const points = months.map(m => { cumulative += m.cost; return cumulative; });
  const maxCost = Math.max(...points, 1);
  const stepX = 50, startX = 20;
  const coords = points.map((c, i) => {
    const x = startX + i * stepX;
    const y = 140 - (maxCost > 0 ? (c / maxCost) * 105 : 0);
    return [x, y];
  });
  const polylinePoints = coords.map(c => c.join(',')).join(' ');
  let html = `<polyline class="line-stroke" points="${polylinePoints}"></polyline>`;
  coords.forEach((c, i) => {
    html += `<circle class="line-dot" cx="${c[0]}" cy="${c[1]}" r="3"><title>${months[i].label} ${months[i].year}: ${fmtDe(points[i],2)} €</title></circle>`;
    html += `<text class="axis-text" x="${c[0] - 6}" y="148">${months[i].label}</text>`;
  });
  svg.innerHTML = html;
}

async function updateCompare() {
  const realRate = parseFloat(document.getElementById('ausw-realtarif').value) || 0;
  // Vertragsstrompreis persistieren damit Dashboard denselben Wert zeigt
  try { await fetch('/api/settings/contract-kwh-price', { method:'POST',
    headers:{'Content-Type':'application/json'}, body:JSON.stringify({rate: realRate}) }); } catch(e){}
  try {
    const resp = await fetch('/api/analytics/compare', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ total_kwh: currentPeriodKwh, pauschale_rate: PAUSCHALE_RATE, real_rate: realRate }),
    });
    const data = await resp.json();
    const pauschaleAmt = data.pauschale_amount || 0;
    const realAmt      = data.real_amount      || 0;
    const gewinn       = pauschaleAmt - realAmt;

    document.getElementById('compare-pauschale-value').textContent = fmtDe(pauschaleAmt, 2) + ' €';
    document.getElementById('compare-real-value').textContent      = fmtDe(realAmt, 2) + ' €';
    document.getElementById('compare-real-label').textContent      = `Mein Vertragstarif (${fmtDe(realRate, 2)} €/kWh)`;

    // Reinerlös = Erstattung - Stromkosten
    const gewinnEl = document.getElementById('compare-gewinn-value');
    if (gewinnEl) {
      const sign = gewinn >= 0 ? '+' : '';
      gewinnEl.textContent = sign + fmtDe(gewinn, 2) + ' €';
      gewinnEl.style.color = gewinn >= 0 ? 'var(--success)' : 'var(--danger)';
    }
  } catch (e) { /* optional */ }
}

// ---------- Impressum-Modal ----------
function openImpressum() {
  document.getElementById('impressum-overlay').classList.add('open');
}
function closeImpressum() { document.getElementById('impressum-overlay').classList.remove('open'); }
function closeImpressumOnBg(e) { if (e.target.id === 'impressum-overlay') closeImpressum(); }

// ---------- Signatur-Gauge + Zaehler-Hochzaehlen, respektiert reduced motion ----------
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function animateGauge(targetKw, maxKw) {
  const fullLen = 204;
  const target = maxKw > 0 ? Math.min(1, targetKw / maxKw) : 0;
  const arc = document.getElementById('gauge-arc');
  const tip = document.getElementById('gauge-tip');
  const valEl = document.getElementById('gauge-val');
  if (!arc) return;
  if (reduceMotion) {
    arc.style.strokeDashoffset = fullLen * (1 - target);
    valEl.textContent = targetKw.toFixed(1);
    return;
  }
  const dur = 900, start = performance.now();
  function step(t) {
    const p = Math.min(1, (t - start) / dur);
    const eased = 1 - Math.pow(1 - p, 3);
    arc.style.strokeDashoffset = fullLen * (1 - target * eased);
    valEl.textContent = (targetKw * eased).toFixed(1);
    const angle = -105 + (210 * target * eased);
    const rad = angle * Math.PI / 180;
    tip.setAttribute('cx', 80 + 65 * Math.cos(rad));
    tip.setAttribute('cy', 90 + 65 * Math.sin(rad));
    if (p < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

function animateCounters(force = false) {
  document.querySelectorAll('.count-up').forEach(el => {
    const target = parseFloat(el.dataset.target);
    if (isNaN(target)) return;
    const decimals = parseInt(el.dataset.decimals || '0');
    const prefix = el.dataset.prefix || '';
    const suffix = el.dataset.suffix || '';
    const fmt = v => prefix + v.toFixed(decimals).replace('.', ',') + suffix;

    // Aktuellen numerischen Wert aus dem Text extrahieren (Prefix/Suffix entfernen)
    const currentNum = parseFloat(el.textContent.replace(/[^0-9,-]/g,'').replace(',','.')) || 0;
    if (!force && Math.abs(currentNum - target) < 0.001 && el.dataset.animated === '1') {
      el.textContent = fmt(target);
      return;
    }

    if (reduceMotion) {
      el.textContent = fmt(target);
      el.dataset.animated = '1';
      return;
    }
    const dur = 1100, start = performance.now();
    const from = currentNum;
    function step(t) {
      const p = Math.min(1, (t - start) / dur);
      const eased = p < 0.5 ? 2*p*p : 1 - Math.pow(-2*p+2, 2)/2; // ease-in-out
      el.textContent = fmt(from + (target - from) * eased);
      if (p < 1) requestAnimationFrame(step);
      else { el.textContent = fmt(target); el.dataset.animated = '1'; }
    }
    requestAnimationFrame(step);
  });
}

// ---------- Initialisierung ----------
window.currentTheme = document.documentElement.getAttribute('data-theme') || 'dark';
applyI18n(currentLang);
setTheme(window.currentTheme);

// FA-UX-01: Setup ist kein Dauer-Menüpunkt mehr, sondern läuft nur beim
// allerersten Start als fokussierter Assistent (Rückmeldung: "Setup" und
// "Einstellungen" nebeneinander als Menüpunkte war nicht eindeutig). Fehlt
// noch ein Nutzer (frische Installation), wird die Navigation ausgeblendet
// und direkt die Setup-Ansicht gezeigt — kein Herumklicken nötig, um die
// Einrichtung überhaupt zu finden.
if (!APP_STATE.user) {
  document.querySelector('.nav').style.display = 'none';
  showView('setup');
} else {
  loadDashboardSummary();
  loadDashboardWallboxes();
  loadRecentSessionsChart(true);  // beim ersten Laden animieren
}
checkOcppStatus();
setInterval(checkOcppStatus, 20000);
// Dashboard-Gauge und Wallbox-Liste bisher nur einmal beim Laden aktualisiert
// (Rueckmeldung: Browser braucht manuellen Refresh, aktualisiert sich nicht
// von selbst) — jetzt alle 10s automatisch neu geladen, waehrend die
// Dashboard-Seite sichtbar ist.
if (!window._dashboardRefreshInterval) {
  window._dashboardRefreshInterval = setInterval(() => {
    const dashboardView = document.getElementById('view-dashboard');
    if (dashboardView && dashboardView.classList.contains('active')) {
      loadDashboardSummary();
      loadDashboardWallboxes();
      loadRecentSessionsChart();
    }
  }, 10000);
}
loadNavBadges();

async function loadNavBadges() {
  try {
    const [sessionsResp, tripsResp] = await Promise.all([hole('/api/sessions'), hole('/api/trips')]);
    const sessionsData = await sessionsResp.json();
    const tripsData = await tripsResp.json();
    const sBadge = document.getElementById('nav-badge-sessions');
    const tBadge = document.getElementById('nav-badge-trips');
    if (sBadge) sBadge.textContent = (sessionsData.sessions || []).length;
    if (tBadge) tBadge.textContent = (tripsData.trips || []).length;
  } catch (e) { /* Badges bleiben auf "-" bei Fehler */ }
}

async function loadTopologyLivePower() {
  const el = document.getElementById('topo-live-power');
  if (!el) return;
  try {
    const resp = await hole('/api/dashboard/summary');
    const data = await resp.json();
    el.textContent = (data.live && data.live.current_power_kw !== null) ? `${data.live.current_power_kw.toFixed(1)} kW` : '–';
  } catch (e) {
    el.textContent = '–';
  }
}

async function loadPollInterval() {
  const input = document.getElementById('poll-interval-input');
  if (!input) return;
  try {
    const resp = await fetch('/api/settings/loxone-poll-interval');
    const data = await resp.json();
    input.value = data.seconds;
  } catch (e) { /* Feld bleibt leer */ }
}

async function savePollInterval() {
  const input = document.getElementById('poll-interval-input');
  const seconds = parseInt(input.value, 10);
  if (!seconds || seconds < 10) {
    alert('Bitte mindestens 10 Sekunden angeben.');
    return;
  }
  try {
    const resp = await fetch('/api/settings/loxone-poll-interval', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ seconds }),
    });
    const data = await resp.json();
    if (resp.ok) {
      alert(`Gespeichert. Wirkt ab dem nächsten Poll-Zyklus (der laufende Zyklus verwendet noch den alten Wert).`);
    } else {
      alert('Fehler: ' + (data.message || data.error));
    }
  } catch (e) {
    alert('Netzwerkfehler: ' + e);
  }
}

async function loadHomeAddress() {
  const input = document.getElementById('home-address-input');
  if (!input) return;
  try {
    const resp = await fetch('/api/settings/home-address');
    const data = await resp.json();
    input.value = data.address || '';
  } catch (e) { /* Feld bleibt leer */ }
}

async function saveHomeAddress() {
  const address = document.getElementById('home-address-input').value.trim();
  try {
    const resp = await fetch('/api/settings/home-address', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ address }),
    });
    if (resp.ok) {
      alert('Stammadresse gespeichert. Wird ab jetzt bei "Neue Fahrt" automatisch vorbelegt.');
    } else {
      alert('Fehler beim Speichern.');
    }
  } catch (e) {
    alert('Netzwerkfehler: ' + e);
  }
}

async function loadBmfReference() {
  const toggle = document.getElementById('bmf-reference-toggle');
  if (!toggle) return;
  try {
    const resp = await fetch('/api/settings/bmf-reference');
    const data = await resp.json();
    const btns = toggle.querySelectorAll('button');
    btns.forEach(b => b.classList.remove('on'));
    btns[data.enabled ? 1 : 0].classList.add('on');
  } catch (e) { /* Standard (Aus) bleibt sichtbar */ }
}

async function loadVehicleDescription() {
  const input = document.getElementById('vehicle-description-input');
  if (!input) return;
  try {
    const resp = await fetch('/api/settings/vehicle-description');
    const data = await resp.json();
    input.value = data.description || '';
  } catch (e) { /* Feld bleibt leer */ }
}

async function saveVehicleDescription() {
  const description = document.getElementById('vehicle-description-input').value.trim();
  try {
    const resp = await fetch('/api/settings/vehicle-description', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ description }),
    });
    if (resp.ok) {
      alert('Gespeichert. Erscheint ab jetzt auf dem Ladestrom-Beleg.');
    } else {
      alert('Fehler beim Speichern.');
    }
  } catch (e) {
    alert('Netzwerkfehler: ' + e);
  }
}

async function setBmfReference(btn, enabled) {
  try {
    const resp = await fetch('/api/settings/bmf-reference', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled }),
    });
    if (resp.ok) {
      const toggle = document.getElementById('bmf-reference-toggle');
      toggle.querySelectorAll('button').forEach(b => b.classList.remove('on'));
      btn.classList.add('on');
    } else {
      alert('Fehler beim Speichern.');
    }
  } catch (e) {
    alert('Netzwerkfehler: ' + e);
  }
}

async function checkOcppStatus() {
  const dot = document.getElementById('ocpp-status-dot');
  const title = document.getElementById('ocpp-status-title');
  if (!dot || !title) return;
  try {
    const resp = await hole('/api/ocpp/status');
    const data = await resp.json();
    if (!data.enabled) {
      title.textContent = 'OCPP-Server: Deaktiviert';
      dot.style.background = 'var(--warning, #eab308)';
    } else {
      title.textContent = data.online ? 'OCPP-Server: Online' : 'OCPP-Server: Offline';
      dot.style.background = data.online ? 'var(--success)' : 'var(--text-tertiary)';
    }
  } catch (e) {
    title.textContent = 'OCPP-Server: nicht erreichbar';
    dot.style.background = 'var(--text-tertiary)';
  }
}

// Port des eingebauten OCPP-Servers ändern. 9000 ist häufig belegt.
async function ocppPortSpeichern() {
  const feld = document.getElementById('ocpp-port-feld');
  const wert = parseInt((feld?.value || '').trim(), 10);
  if (!wert || wert < 1024 || wert > 65535) {
    _toast('Bitte einen Port zwischen 1024 und 65535 angeben');
    return;
  }
  try {
    const r = await fetch('/api/ocpp/port', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ port: wert })
    });
    const d = await r.json();
    if (d.ok) {
      _toast(`Port auf ${wert} gesetzt — der Dienst startet neu`);
      setTimeout(() => location.reload(), 1600);
    } else {
      _toast(d.fehler || 'Port konnte nicht gesetzt werden');
    }
  } catch (e) {
    _toast('Speichern fehlgeschlagen');
  }
}

async function setOcppServerEnabled(enabled) {
  try {
    await fetch('/api/ocpp/toggle', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ enabled }) });
    _toast(enabled ? 'OCPP-Dienst aktiviert' : 'OCPP-Dienst deaktiviert');
  } catch(e) {}
  refreshOcppToggleUi();
  checkOcppStatus();
}

async function refreshOcppToggleUi() {
  try {
    const r = await hole('/api/ocpp/status');
    const d = await r.json();
    const onBtn = document.getElementById('ocpp-toggle-on');
    const offBtn = document.getElementById('ocpp-toggle-off');
    if (onBtn && offBtn) {
      onBtn.classList.toggle('on', d.enabled);
      offBtn.classList.toggle('on', !d.enabled);
    }
    const hint = document.getElementById('ocpp-disabled-hint');
    if (hint) hint.style.display = d.enabled ? 'none' : 'block';

    // Eingestellten Port ins Feld übernehmen, damit man sieht, was gilt
    const portFeld = document.getElementById('ocpp-port-feld');
    if (portFeld && !portFeld.value) {
      try {
        const n = await (await hole('/api/server-info')).json();
        if (n.ocpp_port) portFeld.value = n.ocpp_port;
      } catch (e) { /* Feld bleibt leer, Platzhalter zeigt 9000 */ }
    }
    const statusEl = document.getElementById('ocpp-status-title-settings');
    if (statusEl) {
      statusEl.textContent = !d.enabled ? 'Deaktiviert'
        : (d.online ? 'Server läuft und wartet auf Verbindungen.' : 'Server nicht erreichbar.');
    }
  } catch(e) {}
}

let lastLiveSessionData = null;

function toggleLiveSessionDetail() {
  const panel = document.getElementById('live-session-detail');
  const chevron = document.getElementById('gauge-expand-chevron');
  const isOpen = panel.style.display !== 'none';
  panel.style.display = isOpen ? 'none' : 'block';
  chevron.textContent = isOpen ? '›' : '⌄';
  if (!isOpen && lastLiveSessionData) renderLiveSessionDetail(lastLiveSessionData);
}

function renderLiveSessionDetail(s) {
  if (!s) {
    document.getElementById('lsd-title').textContent = 'Ladesession — kein Fahrzeug verbunden';
    document.getElementById('lsd-power').textContent = '–';
    document.getElementById('lsd-target').textContent = '–';
    document.getElementById('lsd-since').textContent = '–';
    document.getElementById('lsd-duration').textContent = '–';
    document.getElementById('lsd-energy').textContent = '–';
    document.getElementById('lsd-cost').textContent = '–';
    return;
  }
  document.getElementById('lsd-title').textContent = `Ladesession — ${s.wallbox_location || s.wallbox_name}`;
  document.getElementById('lsd-power').textContent = s.current_power_kw !== null ? `${fmtDe(s.current_power_kw, 2)} kW` : '–';
  document.getElementById('lsd-target').textContent = s.target_power_kw !== null ? `${fmtDe(s.target_power_kw, 1)} kW` : '–';
  document.getElementById('lsd-since').textContent = s.connected_since || '–';
  if (s.ocpp_status_raw && !s.connected_since) {
    // Reine Statusmeldung ohne Energiedaten — keine irrefuehrenden 0,00-Werte anzeigen.
    document.getElementById('lsd-duration').textContent = '–';
    document.getElementById('lsd-energy').textContent = 'noch keine Messwerte (nur Status: ' + s.ocpp_status_raw + ')';
    document.getElementById('lsd-cost').textContent = '–';
  } else {
    document.getElementById('lsd-duration').textContent = s.duration_label || '–';
    document.getElementById('lsd-energy').textContent = `${fmtDe(s.energy_so_far_kwh, 2)} kWh`;
    document.getElementById('lsd-cost').textContent = `${fmtDe(s.cost_so_far, 2)} €`;
  }
}

async function loadDashboardSummary() {
  loadBmwTrips();
  ladeDashStatus();
  ladeDashLadevorgaenge();
  ladeMonatsabschluss();
  setTimeout(() => { ladeKosten100km(); ladeExternKachel(); }, 300);
  try {
    // Zeitraum mitgeben — die Kacheln zeigen dann den gewählten Bereich
    // statt immer den laufenden Monat.
    const _z = _zeitraum();
    const _q = _z ? `?von=${_z.von}&bis=${_z.bis}&label=${encodeURIComponent(_z.label)}` : '';
    const resp = await hole('/api/dashboard/summary' + _q);
    const d = await resp.json();

    // ── KPI-Leiste: Mengen/Aktivität ───────────────────────────────────────
    // ── 3 Kern-KPIs: Cash-Saldo · Dienstkilometer · Steuervorteil ──
    const cashEl = document.getElementById('dash-cash-saldo');
    if (cashEl) {
      const cash = d.vollkosten_cash != null ? d.vollkosten_cash : (d.gesamt_reinerloes || 0);
      cashEl.dataset.target = cash;
      cashEl.dataset.prefix = cash >= 0 ? '+' : '';
      // Farbe gehört an die Zahl, nicht an den Kasten. Ein rot umrandeter
      // Rahmen liest sich wie eine Fehlermeldung, obwohl nur ein Wert
      // negativ ist.
      cashEl.style.color = cash >= 0 ? 'var(--success)' : 'var(--danger)';
    }
    const kmEl = document.getElementById('dash-trip-km');
    if (kmEl) kmEl.dataset.target = d.trip_km || 0;
    const tcEl = document.getElementById('dash-trip-count');
    if (tcEl) tcEl.textContent = d.trip_count || '0';
    const kwhEl = document.getElementById('dash-kwh-month');
    if (kwhEl) kwhEl.textContent = fmtDe(d.kwh_this_month || 0, 1);
    const stEl = document.getElementById('dash-steuervorteil');
    if (stEl) stEl.dataset.target = d.fahrt_steuer_schaetzung || 0;

    // Zeitbezug in den Kachel-Unterzeilen nennen — sonst weiß niemand,
    // worauf sich die Zahlen beziehen.
    if (_z) {
      const cs = document.getElementById('dash-kpi-cash-label');
      if (cs) cs.textContent = _z.art === 'gesamt' ? 'Cash-Saldo gesamt'
                             : `Cash-Saldo ${_z.label}`;
    }


    // ── Reinerlös-Karte: Geld-Aufschlüsselung ──
    const setTarget = (id, val, prefix) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.dataset.target = val || 0;
      if (prefix !== undefined) el.dataset.prefix = (prefix === '+' && val < 0) ? '' : prefix;
    };
    const col = v => v >= 0 ? 'var(--success)' : 'var(--danger)';

    // Spalte 1: Ladestrom (reiner Vorteil, positiv)
    setTarget('r-strom-erstattung', d.strom_erstattung, '+');
    setTarget('r-strom-kosten', d.strom_kosten, '−');
    setTarget('r-strom-rein', d.strom_reinerloes, '+');
    const sRe = document.getElementById('r-strom-rein'); if (sRe) sRe.style.color = col(d.strom_reinerloes);

    // Spalte 2: Erstattungen gesamt (Fahrt + Allowance + Steuer)
    setTarget('r-fahrt-erstattung-d', d.fahrt_erstattung, '+');
    setTarget('r-allowance', d.allowance_netto, '+');
    setTarget('r-gesamt-steuer', d.fahrt_steuer_schaetzung, '+');
    // Erstattungen-Summe = Ladestrom-Reinerlös + Fahrt + Allowance + Steuer
    const erstattungTotal = (d.strom_reinerloes||0) + (d.fahrt_erstattung||0) + (d.allowance_netto||0) + (d.fahrt_steuer_schaetzung||0);
    setTarget('r-erstattung-total', erstattungTotal, '+');

    // Spalte 3: Vollkosten-Bilanz (maßgeblich)
    setTarget('r-vk-cash', d.gesamt_inkl_steuer, '+');  // Erstattungen inkl. Steuer + Allowance
    // genauer: Cash-Erstattungen + Allowance (vor PKW-Abzug)
    const vkEinnahmen = (d.gesamt_reinerloes||0) + (d.allowance_netto||0) + (d.fahrt_steuer_schaetzung||0);
    setTarget('r-vk-cash', vkEinnahmen, '+');
    setTarget('r-vk-ausgaben', d.pkw_ausgaben_monat, '−');
    setTarget('r-gesamt-total', d.vollkosten_inkl_steuer, '+');
    const gTot = document.getElementById('r-gesamt-total');
    if (gTot) gTot.style.color = d.vollkosten_inkl_steuer >= 0 ? 'var(--success)' : 'var(--danger)';
    // Der Kasten bleibt neutral — gefärbt wird nur der Wert darin. Ein
    // farbig hinterlegter Block neben zwei schlichten liest sich wie eine
    // Warnung, obwohl er dieselbe Art von Zahl zeigt.
    const vkWert = document.getElementById('r-gesamt-total');
    if (vkWert) vkWert.style.color = d.vollkosten_inkl_steuer >= 0
                                    ? 'var(--success)' : 'var(--danger)';

    // Hinweis wenn keine PKW-Kosten hinterlegt
    const noVkHint = document.getElementById('r-no-vollkosten-hint');
    if (noVkHint) noVkHint.style.display = d.hat_vollkosten ? 'none' : 'block';

    const gStPct = document.getElementById('r-steuersatz'); if (gStPct && d.steuersatz_pct) gStPct.textContent = d.steuersatz_pct;

    animateCounters();

    // Gauge-Elemente optional (wurden vom neuen Dashboard entfernt, aber Code bleibt kompatibel)
    const gaugeLabel = document.getElementById('gauge-label');
    const gaugePill  = document.getElementById('gauge-status-pill');
    const chevron    = document.getElementById('gauge-expand-chevron');
    lastLiveSessionData = d.live_session || null;
    if (chevron) chevron.style.display = lastLiveSessionData ? 'inline' : 'none';
    if (document.getElementById('live-session-detail')?.style.display !== 'none') {
      renderLiveSessionDetail(lastLiveSessionData);
    }
    if (d.live && d.live.current_power_kw !== null) {
      const loc = d.live.wallbox_location || d.live.wallbox_name;
      gaugeLabel.textContent = `Aktuelle Ladeleistung — ${loc}`;
      const charging = d.live.current_power_kw > 0;
      gaugePill.innerHTML = `<span class="pill-dot"></span>${charging ? 'Lädt' : (d.live.connected ? 'Bereit' : 'Nicht verbunden')}`;
      gaugePill.className = `pill ${charging ? 'pill-amber' : (d.live.connected ? 'pill-teal' : 'pill-neutral')}`;
      animateGauge(d.live.current_power_kw, Math.max(11, d.live.current_power_kw));
    } else if (lastLiveSessionData) {
      // Reine OCPP-StatusNotification ohne Leistungsmesswert (z. B. Fahrzeug
      // angesteckt, aber noch nicht autorisiert/gestartet) — bestaetigt vom
      // Auftraggeber: StatusNotification kommt an, auch ohne volle Session.
      const s = lastLiveSessionData;
      const loc = s.wallbox_location || s.wallbox_name;
      const statusLabels = { Preparing: 'Vorbereitung', Charging: 'Lädt', SuspendedEVSE: 'Pausiert (Wallbox)', SuspendedEV: 'Pausiert (Fahrzeug)', Finishing: 'Wird beendet' };
      const label = statusLabels[s.ocpp_status_raw] || s.ocpp_status_raw || 'Verbunden';
      gaugeLabel.textContent = `${loc} — ${label}`;
      gaugePill.innerHTML = `<span class="pill-dot"></span>${label}`;
      gaugePill.className = `pill ${s.charging_active ? 'pill-amber' : 'pill-teal'}`;
      animateGauge(0, 11);
    } else {
      gaugeLabel.textContent = 'Aktuelle Ladeleistung — noch keine Live-Daten';
      gaugePill.innerHTML = '<span class="pill-dot"></span>–';
      animateGauge(0, 11);
    }

    const activityList = document.getElementById('dashboard-activity-list');
    if (d.activity.length === 0) {
      activityList.innerHTML = '<div class="hint">Noch keine Aktivität.</div>';
    } else {
      activityList.innerHTML = d.activity.map(a => `
        <div class="activity-row"><span class="pill-dot activity-dot-teal"></span><div><div class="activity-text">${a.text}</div><div class="activity-time">${a.time}</div></div></div>
      `).join('');
    }
  } catch (e) {
    animateGauge(0, 11);
    animateCounters();
  }
}

async function loadDashboardWallboxes() {
  // ─── Wallbox-Karten (neues Grid, Referenz-Screenshot) ───────────────────
  const cardsContainer = document.getElementById('dashboard-wallbox-cards');
  // ─── Wallbox-Liste (alte Kleinansicht bleibt als Fallback) ───────────────
  const list = document.getElementById('dashboard-wallbox-list');

  try {
    const resp = await hole('/api/wallboxes/full');
    const data = await resp.json();

    // ── Karten-Grid ─────────────────────────────────────────────────────────
    if (cardsContainer) {
      if (data.wallboxes.length === 0) {
        cardsContainer.innerHTML = '<div class="hint">Noch keine Wallboxen angelegt — siehe Einstellungen.</div>';
      } else {
        cardsContainer.innerHTML = '';
        data.wallboxes.forEach(wb => {
          const statusRaw  = wb.live_status || '';
          const openSess   = wb.open_session;
          // "Lädt" = explizit 'charging' im live_status ODER offene Session vorhanden
          const isCharging = statusRaw === 'charging' || (openSess !== null && openSess !== undefined);
          const isOnline   = statusRaw === 'ready' || statusRaw === 'online';
          const isPaused   = statusRaw.includes('pausiert');

          const statusDot  = isCharging ? 'activity-dot-green'
            : isOnline ? 'activity-dot-teal'
            : 'activity-dot-amber';

          const statusText = isCharging ? 'Lädt gerade'
            : (wb.source_type === 'ocpp' && isOnline)     ? 'OCPP verbunden · Bereit'
            : (wb.source_type === 'loxone_api' && isOnline) ? 'Loxone API verbunden · Bereit'
            : isPaused ? 'Polling pausiert'
            : 'Kein aktiver Ladevorgang';

          const locationMeta = [wb.location, `${wb.session_count ?? 0} Sessions`].filter(Boolean).join(' · ');

          const typeSvg = wb.source_type === 'loxone_api'
            // Wallbox mit Ladekabel: Gehaeuse an der Wand, Blitz darin,
            // Kabel nach unten. Zuvor stand hier eine Aktentasche
            // beziehungsweise ein Haus — beides sagte nichts aus.
            ? `<svg class="wb-card-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="2" width="12" height="15" rx="2"/><path d="M13 6l-2.5 4H13l-2 4"/><path d="M12 17v3a2 2 0 0 0 2 2h3a2 2 0 0 0 2-2v-6"/></svg>`
            : `<svg class="wb-card-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="2" width="12" height="15" rx="2"/><path d="M13 6l-2.5 4H13l-2 4"/><path d="M12 17v3a2 2 0 0 0 2 2h3a2 2 0 0 0 2-2v-6"/></svg>`;

          // Live-Block: für BEIDE Typen einheitlich — 2 Zeilen mit Platzhalter wenn keine Daten
          let liveRow1Label = 'Lädt seit'; let liveRow1Val = '–';
          let liveRow2Label = 'Geladen';   let liveRow2Val = '–';

          if (isCharging && openSess) {
            const elapsed = openSess.elapsed_min;
            liveRow1Val = elapsed < 60
              ? `vor ${elapsed} min`
              : `vor ${Math.floor(elapsed/60)} h ${elapsed%60} min`;
            liveRow2Val = `${openSess.kwh_so_far.toFixed(2)} kWh`;
          }

          // Für Loxone API: zusätzlich Leistung (kW) nachladen
          const liveIdSuffix = `wb-card-live-${wb.id}`;

          const card = document.createElement('div');
          card.className = `wb-card${isCharging ? ' wb-card-active' : ''}`;
          card.style.display = 'flex';
          card.style.flexDirection = 'column';
          card.innerHTML = `
            <div class="wb-card-header">
              <span class="wb-card-name">${wb.name}</span>
              ${typeSvg}
            </div>
            <div class="wb-card-status${isCharging ? ' wb-card-status-charging' : ''}">
              <span class="pill-dot ${statusDot}" style="flex-shrink:0;"></span>
              ${statusText}
            </div>
            <div class="wb-card-live" id="${liveIdSuffix}" style="flex:1;">
              <div class="wb-card-live-row">
                <span>${liveRow1Label}</span>
                <span class="wb-card-live-val" id="${liveIdSuffix}-r1">${liveRow1Val}</span>
              </div>
              <div class="wb-card-live-row">
                <span>${liveRow2Label}</span>
                <span class="wb-card-live-val" id="${liveIdSuffix}-r2">${liveRow2Val}</span>
              </div>
              ${_renderLiveMetrics(wb)}
            </div>
            <div class="wb-card-meta">${locationMeta}</div>
            <button class="wb-card-open" onclick="showView('wallbox')">Öffnen</button>
          `;
          cardsContainer.appendChild(card);

          // Live-Daten nachladen: Loxone API (kW + Ccc) und OCPP falls keine open_session
          if (isCharging && wb.source_type === 'loxone_api') {
            _updateWallboxCardLive(wb.id, liveIdSuffix);
          }
        });
      }
    }

    // ── Alte Kleinliste (Sidebar-Bereich) ────────────────────────────────────
    if (list) {
      if (data.wallboxes.length === 0) {
        list.innerHTML = '<div class="hint">Noch keine Wallboxen angelegt — siehe Einstellungen.</div>';
        return;
      }
      list.innerHTML = '';
      data.wallboxes.forEach(wb => {
        const statusLabel = wb.live_status || 'unbekannt';
        const pillClass = statusLabel === 'charging' ? 'pill-amber'
          : (statusLabel === 'online' || statusLabel === 'ready' ? 'pill-teal' : 'pill-neutral');
        const meta = wb.source_type === 'ocpp'
          ? `OCPP · ${wb.ocpp_charge_point_id || ''}`
          : `Loxone-API · ${wb.loxone_host || ''}`;
        const row = document.createElement('div');
        row.className = 'wb-row';
        row.innerHTML = `
          <div class="wb-row-left">
            <div class="wb-ic">⎋</div>
            <div><div class="wb-name">${wb.name}</div><div class="wb-meta">${meta}</div></div>
          </div>
          <span class="pill ${pillClass}"><span class="pill-dot"></span>${statusLabel}</span>
        `;
        list.appendChild(row);
      });
    }
  } catch (e) {
    if (cardsContainer) cardsContainer.innerHTML = '<div class="hint">Fehler beim Laden.</div>';
    if (list) list.innerHTML = '<div class="hint">Fehler beim Laden.</div>';
  }
}

async function _updateWallboxCardLive(wallboxId, liveIdSuffix) {
  const r1 = document.getElementById(`${liveIdSuffix}-r1`);
  const r2 = document.getElementById(`${liveIdSuffix}-r2`);
  if (!r1 && !r2) return;
  try {
    const resp = await fetch(`/api/wallboxes/${wallboxId}/live-metrics`);
    const d = await resp.json();
    if (!d.has_data) return;

    // Die aktuelle Leistung steht bereits in der einheitlichen Zeile
    // "Leistung" (siehe _renderLiveMetrics) — hier nicht noch einmal
    // ausgeben, sonst erscheint derselbe Wert doppelt.

    // Energie dieser Ladesession: Loxone Ccc (Consumption current charge in kWh)
    // gehoert in die Zeile "Geladen" (r2), nicht in "Laedt seit" (r1).
    if (d.raw_fields) {
      const ccc = parseFloat(d.raw_fields.Ccc || 0);
      if (r2 && ccc > 0) r2.textContent = `${ccc.toFixed(2)} kWh`;
      // Ladezeit schätzen aus Sync-Zeitpunkt (approximativ)
      if (r1 && ccc <= 0 && d.last_sync_at) {
        const syncMs = new Date(d.last_sync_at.replace(' ', 'T')).getTime();
        const diffMin = Math.round((Date.now() - syncMs) / 60000);
        r1.textContent = diffMin < 2 ? 'gerade verbunden' : `sync vor ${diffMin} min`;
      }
    }
  } catch (e) { /* optional */ }
}

async function loadRecentSessionsChart(animate = false) {
  const svg     = document.getElementById('recent-sessions-chart-svg');
  const emptyEl = document.getElementById('recent-sessions-empty');
  const filter  = document.getElementById('chart-wallbox-filter');
  const wbName  = filter ? encodeURIComponent(filter.value) : '';
  if (!svg) return;
  if (!animate && svg.dataset.rendered === '1' && svg.dataset.filter === wbName) return;
  try {
    const resp = await hole(`/api/dashboard/recent-sessions${wbName ? '?wallbox=' + wbName : ''}`);
    const data = await resp.json();
    const sessions = data.sessions || [];

    if (sessions.length === 0) {
      svg.style.display = 'none';
      if (emptyEl) emptyEl.style.display = 'block';
      return;
    }
    if (emptyEl) emptyEl.style.display = 'none';
    svg.style.display = '';

    const W = 700, H = 190;
    const padL = 38, padR = 12, padT = 10, padB = 42;
    const chartW = W - padL - padR;
    const chartH = H - padT - padB;
    const n = sessions.length;
    const barW = Math.min(44, Math.floor(chartW / n) - 6);
    const gap  = chartW / n;
    const maxKwh = Math.max(...sessions.map(s => s.kwh), 0.1);

    // Y-Achsen-Ticks
    const ticks = 4;
    let gridLines = '';
    let axisVals = '';
    for (let i = 0; i <= ticks; i++) {
      const val = (maxKwh / ticks) * i;
      const y = padT + chartH - (val / maxKwh) * chartH;
      gridLines += `<line class="bar-grid-line" x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}"/>`;
      axisVals  += `<text class="bar-axis-val" x="${padL - 4}" y="${y + 3}" text-anchor="end">${val.toFixed(1)}</text>`;
    }

    // Balken — farblich nach Quelle: OCPP=teal, Loxone=amber, BMW=grün
    const _barColors = { ocpp: 'var(--teal)', loxone_api: 'var(--amber)', bmw_app: 'var(--success)' };
    let bars = '';
    sessions.forEach((s, i) => {
      const barH = chartH * (s.kwh / maxKwh);
      const x = padL + i * gap + (gap - barW) / 2;
      const y = padT + chartH - barH;
      const fill = _barColors[s.source] || 'var(--teal)';
      const delay = i * 60;
      if (animate && !reduceMotion) {
        // Animierte Balken: von unten hochlaufen
        bars += `<rect x="${x}" y="${padT + chartH}" width="${barW}" height="0" rx="3" fill="${fill}" opacity="0.8">
                   <animate attributeName="y" from="${padT + chartH}" to="${y}" dur="0.7s" begin="${delay}ms" fill="freeze" calcMode="spline" keySplines="0.25 0.1 0.25 1"/>
                   <animate attributeName="height" from="0" to="${barH}" dur="0.7s" begin="${delay}ms" fill="freeze" calcMode="spline" keySplines="0.25 0.1 0.25 1"/>
                   <title>${s.label}: ${s.kwh} kWh (${s.source || ''})</title></rect>`;
      } else {
        // Statisch (bei Auto-Refresh, kein Neustart der Animation)
        bars += `<rect x="${x}" y="${y}" width="${barW}" height="${barH}" rx="3" fill="${fill}" opacity="0.8">
                   <title>${s.label}: ${s.kwh} kWh (${s.source || ''})</title></rect>`;
      }
      if (barH > 18) {
        bars += `<text class="bar-recent-kwh" x="${x + barW / 2}" y="${y - 4}">${s.kwh}</text>`;
      }
      bars += `<text class="bar-recent-label" x="${x + barW / 2}" y="${padT + chartH + 14}">${s.label}</text>`;
    });

    svg.dataset.rendered = '1';
    svg.dataset.filter = wbName;

    svg.innerHTML = `
      ${gridLines}
      ${axisVals}
      <line class="bar-axis-line" x1="${padL}" y1="${padT}" x2="${padL}" y2="${padT + chartH}"/>
      <line class="bar-axis-line" x1="${padL}" y1="${padT + chartH}" x2="${W - padR}" y2="${padT + chartH}"/>
      ${bars}
    `;
  } catch (e) {
    if (svg) svg.innerHTML = '';
  }
}

// ═══════════════════════════════════════════════════════
// DISCLAIMER + SETUP WIZARD (v10.51)
// ═══════════════════════════════════════════════════════

async function checkDisclaimer() {
  try {
    const r = await fetch('/api/admin/disclaimer-accepted');
    const d = await r.json();
    if (!d.accepted) {
      showModal('disclaimer-modal');
    } else {
      await checkSetupWizard();
    }
  } catch (e) {
    // Offline / Server noch nicht bereit — App trotzdem starten
    console.warn('Disclaimer-Check fehlgeschlagen:', e);
  }
}

async function acceptDisclaimer() {
  try { await fetch('/api/admin/disclaimer-accepted', { method: 'POST' }); } catch(e){}
  hideModal('disclaimer-modal');
  await checkSetupWizard();
}

function showModal(id)  { const m=document.getElementById(id); if(m) m.style.display='flex'; }
function hideModal(id)  { const m=document.getElementById(id); if(m) m.style.display='none'; }

async function checkSetupWizard() {
  // Setup-Assistent entfaellt bewusst (Entscheidung Sprint 1): Der Nutzer legt
  // Fahrzeug, Tarif und Wallbox Schritt fuer Schritt nach der Hilfedatei an.
  // Die Installation wird direkt als eingerichtet markiert, damit keine
  // Wizard-Abfrage mehr erscheint.
  try {
    const scR = await fetch('/api/admin/setup-complete');
    const sc = await scR.json();
    if (!sc.complete) fetch('/api/admin/setup-complete', { method: 'POST' });
  } catch (e) { /* unkritisch */ }
}

function dismissWizard() {
  // Wizard jederzeit schließbar — Setup als "später" markieren
  fetch('/api/admin/setup-complete', { method: 'POST' }).catch(()=>{});
  hideModal('setup-wizard-modal');
}

// ─── Wizard State ──────────────────────────────────────
let _wizStep = 1;
let _wizWbType = 'loxone_api';
let _wizFall = 'C';

async function wizPreload() {
  // Vorhandene Einstellungen aus DB laden (falls Wizard erneut aufgerufen)
  try {
    const users = await (await fetch('/api/users/current')).json();
    if (users && users.name) {
      const el = document.getElementById('wiz-name');
      if (el && !el.value) el.value = users.name;
    }
  } catch(e){}
  try {
    const persons = await (await fetch('/api/persons')).json();
    const p = (persons.persons || [])[0];
    if (p) {
      ['wiz-name','wiz-email','wiz-pnr','wiz-kfz','wiz-tel'].forEach((id, i) => {
        const el = document.getElementById(id);
        const keys = ['name','email','personalnummer','kfz_kennzeichen','telefon'];
        if (el && !el.value && p[keys[i]]) el.value = p[keys[i]];
      });
    }
  } catch(e){}
}

function wizRender() {
  [1,2,3,4].forEach(i => {
    const el = document.getElementById(`wiz-s${i}`);
    if (el) el.style.display = (i === _wizStep) ? '' : 'none';
  });
  // Step-Zähler
  const nr = document.getElementById('wiz-step-nr');
  if (nr) nr.textContent = _wizStep;
  // Fortschritts-Dots
  document.querySelectorAll('#wiz-progress .wiz-dot').forEach(d => {
    const s = parseInt(d.dataset.step || d.dataset.s || 0);
    d.style.background = s <= _wizStep ? 'var(--accent)' : 'var(--border-strong)';
  });
  // Buttons
  const back = document.getElementById('wiz-back');
  const next = document.getElementById('wiz-next');
  if (back) back.style.display = (_wizStep > 1) ? '' : 'none';
  if (next) next.textContent = (_wizStep === 4) ? 'Fertig →' : 'Weiter →';
  // Fehlermeldung leeren
  const msg = document.getElementById('wiz-msg');
  if (msg) msg.textContent = '';
}

function wizFall(el) {
  document.querySelectorAll('#wiz-s2 .fall-card').forEach(c => c.classList.remove('selected'));
  el.classList.add('selected');
  _wizFall = el.dataset.fall;
}

function wizWbType(type, btn) {
  _wizWbType = type;
  document.getElementById('wiz-wt-lox').classList.toggle('on', type==='loxone_api');
  document.getElementById('wiz-wt-ocp').classList.toggle('on', type==='ocpp');
  document.getElementById('wiz-lox-f').style.display = type==='loxone_api' ? '' : 'none';
  document.getElementById('wiz-ocp-f').style.display = type==='ocpp' ? '' : 'none';
}

function wizBack() {
  if (_wizStep > 1) { _wizStep--; wizRender(); }
}

async function wizNext() {
  const msg = document.getElementById('wiz-msg');
  if (msg) msg.textContent = '';

  if (_wizStep === 1) {
    // Person speichern
    const name = (document.getElementById('wiz-name')?.value || '').trim();
    if (!name) { if(msg) msg.textContent = 'Bitte Namen eingeben.'; return; }
    const body = {
      name,
      email:           document.getElementById('wiz-email')?.value.trim() || '',
      personalnummer:  document.getElementById('wiz-pnr')?.value.trim()   || '',
      kfz_kennzeichen: document.getElementById('wiz-kfz')?.value.trim()   || '',
      telefon:         document.getElementById('wiz-tel')?.value.trim()   || '',
    };
    try {
      const persons = await (await fetch('/api/persons')).json();
      const existing = (persons.persons || []).find(p => p.name === name);
      if (existing) {
        await fetch(`/api/persons/${existing.id}`, {
          method: 'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)
        });
      } else {
        await fetch('/api/persons', {
          method: 'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)
        });
      }
    } catch(e) { if(msg) msg.textContent = 'Fehler beim Speichern der Person.'; return; }
    _wizStep++; wizRender();

  } else if (_wizStep === 2) {
    // Fahrzeug & Abrechnung
    const name  = document.getElementById('wiz-name')?.value.trim() || 'Benutzer';
    const vehicle = document.getElementById('wiz-vehicle')?.value.trim() || '';
    const kwh   = parseFloat(document.getElementById('wiz-kwh')?.value) || 0.34;
    const kmr   = parseFloat(document.getElementById('wiz-kmr')?.value) || 0.15;
    try {
      await fetch('/api/settings/setup', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ name, abrechnungsfall: _wizFall, default_kwh_price: kwh, vehicle_description: vehicle })
      });
    } catch(e) { if(msg) msg.textContent = 'Fehler beim Speichern der Einstellungen.'; return; }
    _wizStep++; wizRender();

  } else if (_wizStep === 3) {
    const wbName = document.getElementById('wiz-wb-name')?.value.trim() || '';
    if (!wbName) { if(msg) msg.textContent = 'Bitte Bezeichnung eingeben.'; return; }

    // Prüfen ob Wallbox mit diesem Namen bereits existiert
    try {
      const existing = await (await hole('/api/wallboxes/full')).json();
      const alreadyExists = (existing.wallboxes || []).some(w => w.name === wbName);
      if (alreadyExists) {
        // Wallbox existiert schon → nicht doppelt anlegen, einfach weitergehen
        _wizStep++; wizRender(); return;
      }
    } catch(e){}

    const body = {
      name: wbName,
      source_type: _wizWbType,
      location: document.getElementById('wiz-wb-loc')?.value.trim() || '',
    };
    if (_wizWbType === 'loxone_api') {
      body.loxone_host     = document.getElementById('wiz-lox-h')?.value.trim() || '';
      body.loxone_username = document.getElementById('wiz-lox-u')?.value.trim() || '';
      body.loxone_password = document.getElementById('wiz-lox-p')?.value || '';
      const uuid = document.getElementById('wiz-lox-id')?.value.trim() || '';
      if (uuid) body.loxone_uuid = uuid;
      if (!body.loxone_host || !body.loxone_username || !body.loxone_password) {
        if(msg) msg.textContent = 'Bitte IP, Benutzername und Passwort ausfüllen.';
        return;
      }
    } else {
      body.ocpp_charge_point_id = document.getElementById('wiz-ocp-id')?.value.trim() || 'WB1';
    }
    try {
      const r = await fetch('/api/wallboxes', {
        method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)
      });
      const d = await r.json();
      if (!r.ok) {
        if(msg) msg.textContent = `Fehler: ${d.error || r.status}`;
        return;
      }
    } catch(e) { if(msg) msg.textContent = 'Netzwerkfehler beim Anlegen der Wallbox.'; return; }
    _wizStep++; wizRender();

  } else if (_wizStep === 4) {
    try { await fetch('/api/admin/setup-complete', { method: 'POST' }); } catch(e){}
    hideModal('setup-wizard-modal');
    window.location.reload();
  }
}

// App-Start: Disclaimer prüfen
document.addEventListener('DOMContentLoaded', () => {
  setTimeout(checkDisclaimer, 5900); // erst nach dem Splash (5s + Fade-Out)
  // Zwischenspeicher fuer Loxone-Verbindungsdaten aktivieren. Der Aufruf
  // fehlte bisher komplett, wodurch auch der Passwort-Cache wirkungslos war.
  initWallboxPasswordPersistence();
});

// ─── Karten-Anzeige nach Routenberechnung (Google Maps) ─────────────────────
function _showTripMap(startAddr, endAddr) {
  const container  = document.getElementById('trip-map-container');
  const frame      = document.getElementById('trip-map-frame');
  const placeholder= document.getElementById('trip-map-placeholder');
  const gmapBtn    = document.getElementById('trip-map-gmaps-btn');
  const gmapLink   = document.getElementById('trip-map-gmaps-link');
  if (!container || !frame) return;

  // Google Maps Directions embed (kein API-Key nötig für einfache Einbettung)
  const sEnc = encodeURIComponent(startAddr);
  const eEnc = encodeURIComponent(endAddr);
  const embedUrl = `https://maps.google.com/maps?f=d&source=s_d&saddr=${sEnc}&daddr=${eEnc}&hl=de&output=embed`;
  const linkUrl  = `https://www.google.com/maps/dir/${sEnc}/${eEnc}`;

  if (placeholder) placeholder.style.display = 'none';
  frame.src = embedUrl;
  frame.style.display = 'block';

  if (gmapBtn)  gmapBtn.style.display  = 'block';
  if (gmapLink) gmapLink.href = linkUrl;
}
// ─── Sessions Quick-Filter ────────────────────────────────────────────────────
function setSessQuickFilter(period, btn) {
  if (btn) {
    document.querySelectorAll('.sess-qf').forEach(b => b.classList.remove('on'));
    btn.classList.add('on');
  }
  if (period === 'custom') return;
  const now = new Date(), y = now.getFullYear(), m = now.getMonth();
  const pad = n => String(n).padStart(2,'0');
  const von = document.getElementById('filter-von');
  const bis = document.getElementById('filter-bis');
  if (period === 'all')        { von.value=''; bis.value=''; }
  else if (period==='thismonth') { von.value=`${y}-${pad(m+1)}-01`; bis.value=`${y}-${pad(m+1)}-${pad(new Date(y,m+1,0).getDate())}`; }
  else if (period==='lastmonth') { const lm=m===0?12:m,ly=m===0?y-1:y; von.value=`${ly}-${pad(lm)}-01`; bis.value=`${ly}-${pad(lm)}-${pad(new Date(ly,lm,0).getDate())}`; }
  else if (period==='q1') { von.value=`${y}-01-01`; bis.value=`${y}-03-31`; }
  else if (period==='q2') { von.value=`${y}-04-01`; bis.value=`${y}-06-30`; }
  else if (period==='q3') { von.value=`${y}-07-01`; bis.value=`${y}-09-30`; }
  else if (period==='q4') { von.value=`${y}-10-01`; bis.value=`${y}-12-31`; }
  else if (period==='thisyear') { von.value=`${y}-01-01`; bis.value=`${y}-12-31`; }
  loadSessions();
}

// ─── Auswahl-Checkboxen ───────────────────────────────────────────────────────
let _selectedSessions = new Set();
let _selectedTrips    = new Set();

function _updateSelectionBar() {
  // Sessions-Bar (auf Ladesessions-Seite)
  const bar     = document.getElementById('selection-bar');
  const txt     = document.getElementById('selection-bar-text');
  if (bar) {
    const total = _selectedSessions.size;
    bar.style.display = total > 0 ? 'flex' : 'none';
    if (txt) txt.textContent = `${total} Session(s) ausgewählt`;
  }
  // Fahrten-Bar (auf Fahrten-Seite)
  const tripBar = document.getElementById('trips-selection-bar');
  const tripTxt = document.getElementById('trips-selection-bar-text');
  if (tripBar) {
    const total = _selectedTrips.size;
    tripBar.style.display = total > 0 ? 'flex' : 'none';
    if (tripTxt) tripTxt.textContent = `${total} Fahrt(en) ausgewählt`;
  }
}

function toggleSessionCheck(id, checked) {
  checked ? _selectedSessions.add(id) : _selectedSessions.delete(id);
  _updateSelectionBar();
}

function toggleTripCheck(id, checked) {
  checked ? _selectedTrips.add(id) : _selectedTrips.delete(id);
  _updateSelectionBar();
}

function toggleSelectAllSessions(masterCb) {
  document.querySelectorAll('.sess-cb').forEach(cb => {
    cb.checked = masterCb.checked;
    toggleSessionCheck(parseInt(cb.dataset.id), masterCb.checked);
  });
}

function toggleSelectAllTrips(masterCb) {
  document.querySelectorAll('.trip-cb').forEach(cb => {
    cb.checked = masterCb.checked;
    toggleTripCheck(parseInt(cb.dataset.id), masterCb.checked);
  });
}

function clearSelection() {
  _selectedSessions.clear();
  document.querySelectorAll('.sess-cb').forEach(cb => cb.checked = false);
  const allCb = document.getElementById('sess-select-all');
  if (allCb) allCb.checked = false;
  _updateSelectionBar();
}

function clearTripSelection() {
  _selectedTrips.clear();
  document.querySelectorAll('.trip-cb').forEach(cb => cb.checked = false);
  const allCb = document.getElementById('trip-select-all');
  if (allCb) allCb.checked = false;
  _updateSelectionBar();
}

async function generateSelectionBeleg() {
  if (_selectedSessions.size === 0) return;
  const params = new URLSearchParams();
  _selectedSessions.forEach(id => params.append('session_ids', id));
  _openPdfPreview(
    `/api/documents/selection?inline=1&${params}`,
    `/api/documents/selection?${params}`,
    `Ladeabrechnung – ${_selectedSessions.size} Session(s) ausgewählt`
  );
}

async function generateTripSelectionBeleg() {
  if (_selectedTrips.size === 0) return;
  const params = new URLSearchParams();
  _selectedTrips.forEach(id => params.append('trip_ids', id));
  _openPdfPreview(
    `/api/documents/selection?inline=1&${params}`,
    `/api/documents/selection?${params}`,
    `Fahrtkostenbeleg – ${_selectedTrips.size} Fahrt(en) ausgewählt`
  );
}

// ─── Splash Screen ────────────────────────────────────────────────────────────
(function() {
  const splash = document.getElementById('splash-screen');
  const status = document.getElementById('splash-status');
  if (!splash) return;

  const steps = [
    [400,  'Verbinde mit Server …'],
    [1200, 'Lade Wallbox-Daten …'],
    [2200, 'Prüfe Ladesessions …'],
    [3200, 'Initialisiere Dashboard …'],
    [4200, 'Bereit.'],
  ];
  steps.forEach(([delay, text]) => {
    setTimeout(() => { if (status) status.textContent = text; }, delay);
  });

  // Splash nach 5s ausblenden (Mindeststandzeit fuer die Markenwahrnehmung)
  setTimeout(() => {
    splash.style.animation = 'splash-out .7s ease forwards';
    setTimeout(() => splash.remove(), 700);
  }, 5000);
})();

// ─── Protokoll-Tabs ───────────────────────────────────────────────────────────
function onProtokollSourceChange() {
  // Eine zentrale Protokolltabelle: die Quelle steuert Filter und Sichtbarkeit
  // der quellenspezifischen Zusatzelemente (frueher getrennte Reiter).
  const src = document.getElementById('protokoll-source');
  const quelle = src ? src.value : '';

  // Rohfilter nur sinnvoll bei OCPP (nur dort gibt es ROH-Nachrichten)
  const rohWrap = document.getElementById('rohfilter-wrap');
  if (rohWrap) rohWrap.style.display = (quelle === '' || quelle === 'ocpp') ? '' : 'none';

  // OCPP-Rohdaten-Karte nur bei "Alle" oder "OCPP" zeigen
  const rawCard = document.getElementById('ocpp-rawlog-card');
  if (rawCard) rawCard.style.display = (quelle === '' || quelle === 'ocpp') ? '' : 'none';

  loadProtokoll();
}

// ─── Person-Auswahl im Fahrtformular ─────────────────────────────────────────
async function loadPersonsIntoTripForm() {
  const sel = document.getElementById('trip-person-select');
  if (!sel) return;
  try {
    const r = await fetch('/api/persons');
    const d = await r.json();
    const persons = d.persons || [];
    sel.innerHTML = '<option value="">— Person wählen oder Adresse manuell eingeben —</option>';
    persons.forEach(p => {
      const opt = document.createElement('option');
      opt.value = p.id;
      opt.textContent = p.name + (p.home_address ? ` (${p.home_address.split(',')[0]})` : '');
      opt.dataset.address = p.home_address || '';
      sel.appendChild(opt);
    });
    // Erste Person auto-vorwählen wenn sie eine Stammadresse hat
    const firstWithAddr = persons.find(p => p.home_address);
    if (firstWithAddr) {
      sel.value = firstWithAddr.id;
      const startEl = document.getElementById('trip-start');
      if (startEl && !startEl.value) startEl.value = firstWithAddr.home_address;
      _loadVehiclesIntoTripForm(firstWithAddr.id);
    } else if (persons.length > 0) {
      _loadVehiclesIntoTripForm(persons[0].id);
    }
  } catch(e) {}
}

function onTripPersonChange(sel) {
  const opt = sel.options[sel.selectedIndex];
  const addr = opt?.dataset?.address || '';
  const startEl = document.getElementById('trip-start');
  if (startEl && addr) startEl.value = addr;
  // Fahrzeuge dieser Person laden
  const pid = sel.value;
  if (pid) _loadVehiclesIntoTripForm(pid);
}

async function _loadVehiclesIntoTripForm(personId) {
  const sel = document.getElementById('trip-vehicle-select');
  if (!sel) return;
  try {
    const r = await fetch('/api/vehicles' + (personId ? '?person_id='+personId : ''));
    const d = await r.json();
    sel.innerHTML = '<option value="">— Standard-Fahrzeug —</option>' +
      (d.vehicles||[]).map(v => `<option value="${v.id}" ${v.ist_standard?'selected':''}>${v.bezeichnung}${v.antrieb==='verbrenner'?' (Verbrenner)':''}</option>`).join('');
  } catch(e){}
}

// OCPP-Server Test (prüft ob Port 9000 erreichbar ist)
async function testOcppServer() {
  const msgEl = document.getElementById('wb-form-message');
  if (msgEl) { msgEl.textContent = 'Prüfe OCPP-Server …'; msgEl.style.color = 'var(--text-tertiary)'; }
  try {
    const r = await hole('/api/ocpp/status');
    const d = await r.json();
    if (d.running || d.status === 'running') {
      if (msgEl) { msgEl.textContent = `✓ OCPP-Server läuft auf Port ${d.port || 9000}. Warte auf Wallbox-Verbindung.`; msgEl.style.color = 'var(--success)'; }
    } else {
      if (msgEl) { msgEl.textContent = 'OCPP-Server nicht erreichbar.'; msgEl.style.color = 'var(--danger)'; }
    }
  } catch(e) {
    if (msgEl) { msgEl.textContent = 'Fehler beim Prüfen des OCPP-Servers.'; msgEl.style.color = 'var(--danger)'; }
  }
}

// ─── Persönlicher Steuersatz ──────────────────────────────────────────────────
let _steuerMode = 'pauschal';

function setSteuerMode(btn, mode) {
  _steuerMode = mode;
  btn.parentNode.querySelectorAll('button').forEach(b => b.classList.remove('on'));
  btn.classList.add('on');
  document.getElementById('steuer-pauschal').style.display = mode === 'pauschal' ? '' : 'none';
  document.getElementById('steuer-zve').style.display = mode === 'zve' ? '' : 'none';
}

function onSteuerPresetChange(sel) {
  const custom = sel.value === 'custom';
  const inp = document.getElementById('steuersatz-input');
  const suf = document.getElementById('steuersatz-pct-suffix');
  if (inp) inp.style.display = custom ? '' : 'none';
  if (suf) suf.style.display = custom ? '' : 'none';
  if (custom && inp) inp.focus();
}

async function saveSteuersatz() {
  const preset = document.getElementById('steuersatz-preset');
  let pct;
  if (preset && preset.value === 'custom') {
    pct = parseFloat(document.getElementById('steuersatz-input')?.value) || 35;
  } else if (preset) {
    pct = parseFloat(preset.value) || 35;
  } else {
    pct = 35;
  }
  await fetch('/api/settings/steuersatz', { method:'POST',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify({pct}) });
  _toast(`Grenzsteuersatz ${pct} % gespeichert`);
  loadDashboardSummary();
  loadPkwVollkosten();
}

async function calcZveSteuersatz() {
  const zve = parseFloat(document.getElementById('zve-input')?.value) || 0;
  const splitting = parseInt(document.getElementById('zve-splitting')?.value) || 1;
  const resEl = document.getElementById('zve-result');
  if (zve <= 0) { if (resEl) resEl.style.display = 'none'; return; }
  try {
    const r = await fetch(`/api/tax/grenzsteuersatz?zve=${zve}&splitting=${splitting}`);
    const d = await r.json();
    if (resEl) {
      resEl.style.display = 'block';
      resEl.innerHTML = `
        <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
          <span style="color:var(--text-secondary);">Grenzsteuersatz (maßgeblich):</span>
          <b style="color:var(--amber); font-size:15px;">${fmtDe(d.grenzsteuersatz_pct,1)} %</b>
        </div>
        <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
          <span style="color:var(--text-secondary);">Durchschnittssteuersatz:</span>
          <span>${fmtDe(d.durchschnittssteuersatz_pct,1)} %</span>
        </div>
        <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
          <span style="color:var(--text-secondary);">Einkommensteuer / Jahr:</span>
          <span>${fmtDe(d.steuer_gesamt,2)} €</span>
        </div>
        <div style="font-size:11px; color:var(--text-tertiary); margin-top:6px;">${d.zone}</div>`;
      resEl.dataset.grenz = d.grenzsteuersatz_pct;
    }
  } catch(e) {}
}

async function saveSteuersatzZve() {
  const resEl = document.getElementById('zve-result');
  const pct = parseFloat(resEl?.dataset?.grenz);
  if (!pct) { alert('Bitte zuerst ein Einkommen eingeben.'); return; }
  await fetch('/api/settings/steuersatz', { method:'POST',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify({pct}) });
  _toast(`Grenzsteuersatz ${fmtDe(pct,1)} % übernommen`);
  loadDashboardSummary();
  loadPkwVollkosten();
}

async function loadSteuersatzIntoField() {
  try {
    const r = await fetch('/api/settings/steuersatz');
    const d = await r.json();
    const pct = d.pct || 35;
    const preset = document.getElementById('steuersatz-preset');
    const inp = document.getElementById('steuersatz-input');
    if (preset) {
      // Prüfen ob pct einem Preset entspricht
      const presetVals = ['30','35','42','45'];
      if (presetVals.includes(String(pct))) {
        preset.value = String(pct);
        if (inp) inp.style.display = 'none';
        document.getElementById('steuersatz-pct-suffix').style.display = 'none';
      } else {
        preset.value = 'custom';
        if (inp) { inp.style.display = ''; inp.value = pct; }
        document.getElementById('steuersatz-pct-suffix').style.display = '';
      }
    }
  } catch(e){}
}

// Kleiner Toast-Helper
function _toast(msg) {
  let t = document.getElementById('_toast');
  if (!t) {
    t = document.createElement('div');
    t.id = '_toast';
    t.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:var(--amber);color:#fff;padding:10px 20px;border-radius:8px;font-size:13px;font-weight:600;z-index:99999;box-shadow:0 4px 20px rgba(0,0,0,.3);opacity:0;transition:opacity .3s;';
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.style.opacity = '1';
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.style.opacity = '0', 2200);
}

// ─── Custom Date Picker ────────────────────────────────────────────────────────
function _initTripDatePicker() {
  // Ein natives Datumsfeld genuegt — Tag, Monat und Jahr getrennt auszuwaehlen
  // kostete drei Klicks fuer etwas, das man in zwei Sekunden tippt.
  const feld = document.getElementById('trip-date');
  if (feld && !feld.value) feld.value = new Date().toISOString().slice(0, 10);
}

// Schnellwahl unter dem Feld: heute / gestern decken die allermeisten
// Nachtraege ab.
function tripDatumSetzen(tageZurueck) {
  const feld = document.getElementById('trip-date');
  if (!feld) return;
  const d = new Date();
  d.setDate(d.getDate() + tageZurueck);
  feld.value = d.toISOString().slice(0, 10);
}

function _syncTripDate() { /* entfaellt: das Datumsfeld traegt seinen Wert selbst */ }

function _setTripDateValue(iso) {
  const feld = document.getElementById('trip-date');
  if (feld && iso) feld.value = iso.slice(0, 10);
}

// Init beim Öffnen des Formulars aufrufen

// ═══════════════════════════════════════════════════════════════════════════
// PKW-VOLLKOSTENRECHNUNG (v10.85)
// ═══════════════════════════════════════════════════════════════════════════
let _allowanceVersteuert = false;

const _pkwKatLabels = {
  leasing: 'Leasing / Finanzierung', versicherung: 'Versicherung',
  wartung: 'Wartung / Inspektion', reifen: 'Reifen', tuev: 'TÜV / HU',
  steuer: 'Kfz-Steuer', sonstige: 'Sonstige'
};
const _pkwIntLabels = { monatlich: 'monatlich', quartaerlich: 'vierteljährl.', jaehrlich: 'jährlich' };

function _pkwMonthly(betrag, intervall) {
  if (intervall === 'quartaerlich') return betrag / 3;
  if (intervall === 'jaehrlich') return betrag / 12;
  return betrag;
}

async function loadPkwCosts() {
  try {
    const r = await fetch('/api/pkw/costs');
    const d = await r.json();
    const tbody = document.getElementById('pkw-costs-tbody');
    if (!tbody) return;
    if (!d.costs || d.costs.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" class="hint">Noch keine Kosten erfasst.</td></tr>';
    } else {
      tbody.innerHTML = d.costs.map(c => {
        const monthly = _pkwMonthly(c.betrag, c.intervall);
        return `<tr>
          <td>${_pkwKatLabels[c.kategorie] || c.kategorie}</td>
          <td>${c.bezeichnung}</td>
          <td style="text-align:right;">${fmtDe(c.betrag,2)} €</td>
          <td>${_pkwIntLabels[c.intervall] || c.intervall}</td>
          <td style="text-align:right; color:var(--text-secondary);">${fmtDe(monthly,2)} €</td>
          <td style="text-align:right;">
            <button class="icon-btn" onclick="deletePkwCost(${c.id})" title="Löschen" style="color:var(--danger);">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>
            </button>
          </td>
        </tr>`;
      }).join('');
    }
    loadPkwVollkosten();
  } catch(e) {}
}

async function addPkwCost() {
  const kategorie = document.getElementById('pkw-kategorie').value;
  const bezeichnung = document.getElementById('pkw-bezeichnung').value.trim();
  const betrag = parseFloat(document.getElementById('pkw-betrag').value);
  const intervall = document.getElementById('pkw-intervall').value;
  if (!bezeichnung || isNaN(betrag) || betrag <= 0) {
    alert('Bitte Bezeichnung und einen gültigen Betrag eingeben.');
    return;
  }
  await fetch('/api/pkw/costs', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ kategorie, bezeichnung, betrag, intervall })
  });
  document.getElementById('pkw-bezeichnung').value = '';
  document.getElementById('pkw-betrag').value = '';
  loadPkwCosts();
}

async function deletePkwCost(id) {
  if (!confirm('Diesen Kostenposten löschen?')) return;
  await fetch(`/api/pkw/costs/${id}`, { method: 'DELETE' });
  loadPkwCosts();
}

function setAllowanceVersteuert(btn, val) {
  _allowanceVersteuert = val;
  btn.parentNode.querySelectorAll('button').forEach(b => b.classList.remove('on'));
  btn.classList.add('on');
}

async function loadAllowance() {
  try {
    const r = await fetch('/api/pkw/allowance');
    const d = await r.json();
    const bEl = document.getElementById('allowance-betrag');
    const lEl = document.getElementById('allowance-lstk');
    if (bEl) bEl.value = d.monatlicher_betrag || '';
    if (lEl) lEl.value = d.lohnsteuerklasse || 1;
    _allowanceVersteuert = !!d.versteuert;
    // Toggle-Zustand setzen
    const toggle = document.getElementById('allowance-versteuert-toggle');
    if (toggle) {
      toggle.querySelectorAll('button').forEach(b => b.classList.remove('on'));
      toggle.querySelectorAll('button')[_allowanceVersteuert ? 1 : 0].classList.add('on');
    }
  } catch(e) {}
}

async function saveAllowance() {
  const betrag = parseFloat(document.getElementById('allowance-betrag').value) || 0;
  const lstk = parseInt(document.getElementById('allowance-lstk').value) || 1;
  await fetch('/api/pkw/allowance', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ monatlicher_betrag: betrag, lohnsteuerklasse: lstk, versteuert: _allowanceVersteuert })
  });
  loadPkwVollkosten();
}

async function loadPkwVollkosten() {
  try {
    const r = await fetch('/api/pkw/vollkosten');
    const d = await r.json();
    const box = document.getElementById('pkw-vollkosten-box');
    if (!box) return;
    const sign = v => (v >= 0 ? '+' : '') + fmtDe(v, 2) + ' €';
    const col  = v => v >= 0 ? 'var(--success)' : 'var(--danger)';

    box.innerHTML = `
      <div style="display:grid; grid-template-columns:1fr auto; gap:6px 20px; font-size:13px;">
        <div style="color:var(--text-secondary);">Stromerstattung (${fmtDe(d.strom.kwh,1)} kWh)</div>
        <div style="text-align:right; color:var(--success);">${sign(d.strom.erstattung)}</div>
        <div style="color:var(--text-secondary);">− Stromkosten (dein Tarif)</div>
        <div style="text-align:right; color:var(--danger);">−${fmtDe(d.strom.kosten,2)} €</div>
        <div style="color:var(--text-secondary);">Fahrtkostenerstattung (${fmtDe(d.fahrt.km,1)} km)</div>
        <div style="text-align:right; color:var(--success);">${sign(d.fahrt.erstattung)}</div>
        <div style="color:var(--text-secondary);">Car Allowance (netto${d.allowance.versteuert ? ', versteuert' : ', steuerfrei'})</div>
        <div style="text-align:right; color:var(--success);">${sign(d.allowance.netto)}</div>
        <div style="grid-column:1/3; border-top:1px solid var(--border); margin:4px 0;"></div>
        <div style="color:var(--text-primary); font-weight:600;">Einnahmen / Erstattungen gesamt</div>
        <div style="text-align:right; font-weight:600;">${sign(d.einnahmen_gesamt)}</div>
        <div style="color:var(--text-secondary);">− PKW-Ausgaben (monatl. normiert)</div>
        <div style="text-align:right; color:var(--danger);">−${fmtDe(d.pkw_ausgaben_monat,2)} €</div>
        <div style="grid-column:1/3; border-top:2px solid var(--amber); margin:6px 0;"></div>
        <div style="font-weight:700; font-size:15px;">Monatliche Bilanz (Cash)</div>
        <div style="text-align:right; font-weight:700; font-size:15px; color:${col(d.bilanz)};">${sign(d.bilanz)}</div>
        <div style="color:var(--text-tertiary); font-size:11px;">+ geschätzte Steuererstattung (~${d.steuersatz_pct}%)</div>
        <div style="text-align:right; color:var(--text-tertiary); font-size:11px;">${sign(d.fahrt.steuer_schaetzung)}</div>
        <div style="font-weight:700; font-size:16px; color:var(--amber);">Vollkosten-Bilanz inkl. Steuer</div>
        <div style="text-align:right; font-weight:700; font-size:16px; color:${col(d.bilanz_inkl_steuer)};">${sign(d.bilanz_inkl_steuer)}</div>
      </div>
      <div style="font-size:10px; color:var(--text-tertiary); margin-top:10px;">
        Zeitraum: ${d.month_label} · Alle Angaben ohne Gewähr · Steuerschätzung ist unverbindlich.
      </div>`;
  } catch(e) {}
}

// ═══════════════════════════════════════════════════════════════════════════
// FAHRZEUG-VERWALTUNG (v10.87)
// ═══════════════════════════════════════════════════════════════════════════
let _editingVehicleId = null;
let _vehicleAntrieb = 'elektro';

const _antriebLabels = { elektro: 'Elektro', verbrenner: 'Verbrenner' };
const _antriebIcons = {
  elektro: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>',
  verbrenner: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 16H9m10 0h3v-3.15a1 1 0 0 0-.84-.99L16 11l-2.7-3.6a1 1 0 0 0-.8-.4H5.24a2 2 0 0 0-1.8 1.1l-.8 1.63A6 6 0 0 0 2 12.42V16h2"/><circle cx="6.5" cy="16.5" r="2.5"/><circle cx="16.5" cy="16.5" r="2.5"/></svg>'
};

async function loadVehiclesView() {
  await loadVehiclesGrid();
  await _populateVollkostenSelector();
  loadVollkostenView();
}

// Fahrzeugdaten aus dem BMW-Archiv: Kilometerstand, Wartungstermine,
// Reifen. Fällige Termine werden hervorgehoben — sonst übersieht man sie.
function _fahrzeugDaten(v) {
  const zeilen = [];
  const heute = new Date();

  if (v.km_stand) {
    const stand = v.km_stand_datum
      ? ` <span style="color:var(--text-tertiary);">(${_datumKurz(v.km_stand_datum)})</span>`
      : '';
    zeilen.push(`<div><span style="color:var(--text-tertiary);">Kilometerstand</span>
                 <b>${Number(v.km_stand).toLocaleString('de-DE')} km</b>${stand}</div>`);
  }

  [['hu_faellig', 'Hauptuntersuchung'],
   ['service_faellig', 'Service'],
   ['bremsfluessigkeit', 'Bremsflüssigkeit']].forEach(([feld, name]) => {
    if (!v[feld]) return;
    const d = new Date(v[feld]);
    const tage = Math.round((d - heute) / 86400000);
    // Ab 60 Tagen vorher wird es dringend
    const farbe = tage < 0 ? 'var(--danger)'
                : tage < 60 ? 'var(--amber)'
                : 'var(--text-secondary)';
    const zusatz = tage < 0 ? ' — überfällig'
                 : tage < 60 ? ` — in ${tage} Tagen`
                 : '';
    zeilen.push(`<div><span style="color:var(--text-tertiary);">${name}</span>
                 <b style="color:${farbe};">${_datumKurz(v[feld])}${zusatz}</b></div>`);
  });

  if (v.reifen_vorne) {
    const hinten = v.reifen_hinten && v.reifen_hinten !== v.reifen_vorne
      ? ` / ${v.reifen_hinten}` : '';
    zeilen.push(`<div><span style="color:var(--text-tertiary);">Reifen</span>
                 <span style="font-family:var(--font-mono); font-size:11.5px;">${v.reifen_vorne}${hinten}</span></div>`);
  }

  if (!zeilen.length) return '';
  return `<div style="margin-top:10px; padding-top:10px;
               border-top:1px solid var(--border); font-size:12px;
               display:flex; flex-direction:column; gap:5px;">
            ${zeilen.join('')}
          </div>`;
}

function _datumKurz(iso) {
  if (!iso) return '';
  const t = String(iso).split('-');
  return t.length === 3 ? `${t[2]}.${t[1]}.${t[0]}` : iso;
}

async function loadVehiclesGrid() {
  const grid = document.getElementById('vehicles-grid');
  if (!grid) return;
  try {
    const r = await fetch('/api/vehicles');
    const d = await r.json();
    const vehicles = d.vehicles || [];
    if (vehicles.length === 0) {
      grid.innerHTML = '<div class="hint">Noch keine Fahrzeuge. Klicke oben rechts auf „+ Fahrzeug hinzufügen".</div>';
      return;
    }
    grid.innerHTML = vehicles.map(v => `
      <div class="card" style="padding:16px; ${v.ist_standard ? 'border-left:3px solid var(--amber);' : ''}">
        <div style="display:flex; align-items:start; justify-content:space-between; margin-bottom:10px;">
          <div>
            <div style="font-size:15px; font-weight:600;">${v.bezeichnung}</div>
            <div style="font-size:12px; color:var(--text-tertiary); margin-top:2px;">${v.kennzeichen || 'Kein Kennzeichen'}</div>
          </div>
          <div style="display:flex; gap:4px;">
            <button class="icon-btn" onclick="editVehicle(${v.id})" title="Bearbeiten"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button>
            <button class="icon-btn" onclick="deleteVehicle(${v.id})" title="Löschen" style="color:var(--danger);"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg></button>
          </div>
        </div>
        <div style="display:flex; align-items:center; gap:6px; padding:6px 10px; background:${v.antrieb==='elektro'?'var(--amber-soft)':'var(--bg-input)'}; border-radius:var(--radius-sm); font-size:12px; color:${v.antrieb==='elektro'?'var(--amber)':'var(--text-secondary)'}; width:fit-content;">
          ${_antriebIcons[v.antrieb]} ${_antriebLabels[v.antrieb]}
          ${v.ist_standard ? '<span style="margin-left:6px; color:var(--text-tertiary);">· Standard</span>' : ''}
        </div>
        ${v.antrieb==='verbrenner' ? '<div class="hint" style="margin-top:8px;">Verbrenner: keine Stromerstattung, nur Fahrtkosten.</div>' : ''}
        ${_fahrzeugDaten(v)}
        <div style="margin-top:12px;">
          <button class="btn btn-sm" onclick="manageVehicleCosts(${v.id}, '${v.bezeichnung.replace(/'/g,"")}')" style="width:100%;">Kosten verwalten →</button>
        </div>
      </div>`).join('');
  } catch(e) {
    grid.innerHTML = '<div class="hint">Fehler beim Laden.</div>';
  }
}

function setVehicleAntrieb(btn, antrieb) {
  _vehicleAntrieb = antrieb;
  btn.parentNode.querySelectorAll('button').forEach(b => b.classList.remove('on'));
  btn.classList.add('on');
  const hint = document.getElementById('vehicle-antrieb-hint');
  if (hint) {
    hint.textContent = antrieb === 'verbrenner'
      ? 'Verbrenner haben keinen Einfluss auf die Wallbox/Stromerstattung. Fahrtkosten werden aber berücksichtigt, wenn dieses Fahrzeug für eine Fahrt gewählt wird.'
      : 'Elektro-Fahrzeug: Stromerstattung über die Wallbox + Fahrtkosten.';
  }
}

async // Fahrzeug aus dem BMW-CarData-Archiv anlegen. Das ZIP enthält
// Fahrgestellnummer, Kilometerstand und die Wartungstermine — die
// muss niemand abtippen.
// Fahrzeug aus der laufenden CarData-Verbindung anlegen. Nutzt die
// hinterlegte Fahrgestellnummer und die zuletzt abgerufenen Werte —
// kein Archiv, keine Wartezeit.
async function fahrzeugAusBmw() {
  _toast('Frage BMW-Daten ab …');
  try {
    const d = await (await fetch('/api/vehicles/aus-bmw',
                                 { method: 'POST' })).json();
    if (!d.ok) {
      _toast(d.fehler || 'Fahrzeugdaten nicht verfügbar');
      return;
    }
    if (d.duenn) {
      _toast('Fahrzeug angelegt — für Kilometerstand und Wartungstermine '
           + 'bitte einmal „Fahrten abrufen" ausführen');
    } else {
      const teile = [];
      if (d.km_stand) teile.push(`${Number(d.km_stand).toLocaleString('de-DE')} km`);
      if (d.hu_faellig) teile.push(`HU ${d.hu_faellig.split('-').reverse().join('.')}`);
      _toast((d.neu ? 'Fahrzeug angelegt' : 'Fahrzeug aktualisiert')
           + (teile.length ? ' — ' + teile.join(', ') : ''));
    }
    loadVehiclesGrid();
  } catch (e) {
    _toast('Abruf fehlgeschlagen');
  }
}

async function fahrzeugAusArchiv(input) {
  const datei = input.files && input.files[0];
  if (!datei) return;
  _toast('Archiv wird gelesen …');
  const daten = new FormData();
  daten.append('datei', datei);
  try {
    const d = await (await fetch('/api/vehicles/aus-archiv',
                                 { method: 'POST', body: daten })).json();
    if (d.ok) {
      const teile = [];
      if (d.km_stand) teile.push(`${Number(d.km_stand).toLocaleString('de-DE')} km`);
      if (d.hu_faellig) teile.push(`HU ${d.hu_faellig.split('-').reverse().join('.')}`);
      _toast(d.neu ? `Fahrzeug angelegt${teile.length ? ' — ' + teile.join(', ') : ''}`
                   : `Fahrzeug aktualisiert${teile.length ? ' — ' + teile.join(', ') : ''}`);
      loadVehiclesGrid();
    } else {
      _toast(d.fehler || 'Archiv konnte nicht gelesen werden');
    }
  } catch (e) {
    _toast('Import fehlgeschlagen');
  }
  input.value = '';   // damit dieselbe Datei erneut gewählt werden kann
}

async function openVehicleModal() {
  _editingVehicleId = null;
  document.getElementById('vehicle-modal-title').textContent = 'Fahrzeug hinzufügen';
  document.getElementById('vehicle-bezeichnung').value = '';
  document.getElementById('vehicle-kennzeichen').value = '';
  document.getElementById('vehicle-standard').checked = false;
  document.getElementById('vehicle-modal-message').textContent = '';
  _vehicleAntrieb = 'elektro';
  const toggle = document.getElementById('vehicle-antrieb-toggle');
  toggle.querySelectorAll('button').forEach((b,i) => b.classList.toggle('on', i===0));
  // Personen laden
  const sel = document.getElementById('vehicle-person');
  const r = await fetch('/api/persons');
  const d = await r.json();
  sel.innerHTML = (d.persons||[]).map(p => `<option value="${p.id}">${p.name}</option>`).join('');
  document.getElementById('vehicle-modal').style.display = 'flex';
}

function closeVehicleModal() {
  document.getElementById('vehicle-modal').style.display = 'none';
}

async function editVehicle(id) {
  const r = await fetch('/api/vehicles');
  const d = await r.json();
  const v = (d.vehicles||[]).find(x => x.id === id);
  if (!v) return;
  await openVehicleModal();
  _editingVehicleId = id;
  document.getElementById('vehicle-modal-title').textContent = 'Fahrzeug bearbeiten';
  document.getElementById('vehicle-person').value = v.person_id;
  document.getElementById('vehicle-bezeichnung').value = v.bezeichnung;
  document.getElementById('vehicle-kennzeichen').value = v.kennzeichen || '';
  document.getElementById('vehicle-standard').checked = !!v.ist_standard;
  _vehicleAntrieb = v.antrieb;
  const toggle = document.getElementById('vehicle-antrieb-toggle');
  toggle.querySelectorAll('button').forEach((b,i) => b.classList.toggle('on', (i===0)===(v.antrieb==='elektro')));
}

async function saveVehicle() {
  const person_id = parseInt(document.getElementById('vehicle-person').value);
  const bezeichnung = document.getElementById('vehicle-bezeichnung').value.trim();
  const kennzeichen = document.getElementById('vehicle-kennzeichen').value.trim();
  const ist_standard = document.getElementById('vehicle-standard').checked;
  const msg = document.getElementById('vehicle-modal-message');
  if (!person_id || !bezeichnung) {
    msg.textContent = 'Bitte Person und Bezeichnung angeben.';
    msg.style.color = 'var(--danger)';
    return;
  }
  const body = { person_id, bezeichnung, kennzeichen, antrieb: _vehicleAntrieb, ist_standard };
  if (_editingVehicleId) body.id = _editingVehicleId;
  await fetch('/api/vehicles', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
  closeVehicleModal();
  _toast(_editingVehicleId ? 'Fahrzeug aktualisiert' : 'Fahrzeug hinzugefügt');
  loadVehiclesView();
}

async function deleteVehicle(id) {
  if (!confirm('Dieses Fahrzeug wirklich löschen? Zugeordnete Kosten bleiben erhalten.')) return;
  await fetch(`/api/vehicles/${id}`, { method: 'DELETE' });
  _toast('Fahrzeug gelöscht');
  loadVehiclesView();
}

// Kosten-Verwaltung: wählt Fahrzeug im Selektor und scrollt zur Ansicht
async function manageVehicleCosts(vehicleId, name) {
  await _populateVollkostenSelector();
  const sel = document.getElementById('vollkosten-selector');
  if (sel) sel.value = 'v' + vehicleId;
  loadVollkostenView();
  document.getElementById('vollkosten-display').scrollIntoView({ behavior:'smooth', block:'center' });
}

async function _populateVollkostenSelector() {
  const sel = document.getElementById('vollkosten-selector');
  if (!sel) return;
  const r = await fetch('/api/vehicles');
  const d = await r.json();
  sel.innerHTML = (d.vehicles||[]).map(v =>
    `<option value="v${v.id}">${v.bezeichnung}${v.kennzeichen?' ('+v.kennzeichen+')':''}</option>`).join('')
    || '<option value="">Keine Fahrzeuge</option>';
}

async function loadVollkostenView() {
  const box = document.getElementById('vollkosten-display');
  if (!box) return;
  const sel = document.getElementById('vollkosten-selector');
  let val = sel ? sel.value : '';
  // Kein Person-Aggregat mehr: immer ein Fahrzeug. Fallback = erstes Fahrzeug.
  if (!val || !val.startsWith('v')) {
    if (sel) {
      const firstVeh = Array.from(sel.options).find(o => o.value.startsWith('v'));
      if (firstVeh) { val = firstVeh.value; sel.value = val; }
    }
  }
  if (!val || !val.startsWith('v')) {
    box.innerHTML = '<div class="hint">Lege zuerst ein Fahrzeug an.</div>';
    return;
  }
  const ctxVehicleId = val.slice(1);

  try {
    const r = await fetch('/api/pkw/vollkosten?vehicle_id=' + ctxVehicleId);
    const d = await r.json();
    const isVerbrenner = d.antrieb === 'verbrenner';

    const costsRows = (d.pkw_costs||[]).length === 0
      ? '<tr><td colspan="5" class="hint">Noch keine Kosten erfasst.</td></tr>'
      : d.pkw_costs.map(c => {
          const m = c.intervall==='quartaerlich'?c.betrag/3:c.intervall==='jaehrlich'?c.betrag/12:c.betrag;
          const katLabel = _pkwKatLabels[c.kategorie]||c.kategorie;
          const zusatz = (c.bezeichnung && c.bezeichnung !== katLabel) ? ' <span style="color:var(--text-tertiary);">('+c.bezeichnung+')</span>' : '';
          return '<tr><td>'+katLabel+zusatz+'</td>'
            +'<td style="text-align:right;">'+fmtDe(c.betrag,2)+' €</td>'
            +'<td>'+(_pkwIntLabels[c.intervall]||c.intervall)+'</td>'
            +'<td style="text-align:right; color:var(--text-secondary);">'+fmtDe(m,2)+' €</td>'
            +'<td style="text-align:right;"><button class="icon-btn" onclick="deletePkwCostV('+c.id+')" style="color:var(--danger);"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg></button></td></tr>';
        }).join('');

    const summeMonat = (d.pkw_costs||[]).reduce((s,c)=>{
      const m = c.intervall==='quartaerlich'?c.betrag/3:c.intervall==='jaehrlich'?c.betrag/12:c.betrag;
      return s+m;
    },0);

    box.innerHTML = ''
      +'<div style="display:flex; align-items:center; gap:10px; margin-bottom:16px;">'
      +'<span style="font-size:16px; font-weight:600;">'+d.label+'</span>'
      +'<span style="font-size:11px; padding:3px 10px; border-radius:12px; background:'+(isVerbrenner?'var(--bg-input)':'var(--amber-soft)')+'; color:'+(isVerbrenner?'var(--text-secondary)':'var(--amber)')+';">'+_antriebLabels[d.antrieb]+'</span>'
      +'</div>'
      +'<div style="margin-bottom:20px;">'
      +'<div class="section-title" style="font-size:13px; margin-bottom:10px;">Laufende Kosten</div>'
      +'<table style="margin-bottom:12px;"><thead><tr><th>Kostenart</th><th style="text-align:right;">Betrag</th><th>Intervall</th><th style="text-align:right;">/Monat</th><th></th></tr></thead>'
      +'<tbody>'+costsRows+'</tbody>'
      +(summeMonat>0?'<tfoot><tr style="border-top:1px solid var(--border);"><td style="font-weight:600;">Summe</td><td></td><td></td><td style="text-align:right; font-weight:600;">'+fmtDe(summeMonat,2)+' €</td><td></td></tr></tfoot>':'')
      +'</table>'
      +'<div style="display:grid; grid-template-columns:1.3fr 0.9fr 1fr auto; gap:8px; align-items:end;">'
      +'<div><label class="field-label">Kostenart</label>'
      +'<select id="vcost-kat" style="width:100%;" onchange="onVcostKatChange()">'
      +'<option value="leasing">Leasing / Finanzierung</option><option value="versicherung">Versicherung</option>'
      +'<option value="wartung">Wartung / Inspektion</option><option value="reifen">Reifen / Verschleiß</option>'
      +'<option value="tuev">TÜV/HU</option><option value="steuer">Kfz-Steuer</option>'
      +(isVerbrenner?'<option value="kraftstoff">Kraftstoff</option>':'<option value="strom">Ladestrom (Heim/unterwegs)</option>')
      +'<option value="pflege">Pflege / Betriebskosten</option>'
      +'<option value="sonstige">Sonstige …</option>'
      +'</select></div>'
      +'<div><label class="field-label">Betrag €</label><input type="number" id="vcost-betrag" step="0.01" style="width:100%"></div>'
      +'<div><label class="field-label">Intervall</label>'
      +'<select id="vcost-int" style="width:100%;"><option value="monatlich">monatl.</option><option value="quartaerlich">viertelj.</option><option value="jaehrlich">jährl.</option></select></div>'
      +'<button class="btn btn-sm btn-primary" onclick="addPkwCostV(\''+ctxVehicleId+'\')" style="height:38px;">+ Add</button>'
      +'</div>'
      +'<div id="vcost-bez-wrap" style="display:none; margin-top:8px;">'
      +'<label class="field-label">Bezeichnung (frei)</label>'
      +'<input type="text" id="vcost-bez" style="width:100%" placeholder="z. B. Garagenmiete, Parkausweis …">'
      +'</div>'
      +'</div>'
      +'<div style="margin-bottom:20px; padding-top:16px; border-top:1px solid var(--border);">'
      +'<div class="section-title" style="font-size:13px; margin-bottom:4px;">AG-Zusch\u00fcsse</div>'
      +'<div class="page-sub" style="margin:0 0 10px;">Car Allowance, Tankkarte, Jobticket \u2014 je Posten steuerpflichtig oder steuerfrei (\u00a7 3 Nr. 50 EStG).</div>'
      +'<div id="zuschuss-liste"><div class="hint">Lade \u2026</div></div>'
      +'</div>'
      +'<div style="padding:14px 16px; background:var(--bg-input); border-radius:8px; display:flex; align-items:center; justify-content:space-between; gap:16px; flex-wrap:wrap;">'
      +'<div style="font-size:13px; color:var(--text-secondary);">Diese Daten fließen in den <b>Konfigurator</b> — dort erfährst du, ob sich Pauschale oder Vollkosten lohnt und was das Auto wirklich kostet.</div>'
      +'<button class="btn btn-sm btn-primary" onclick="showView(\'konfigurator\')" style="white-space:nowrap;">Zum Konfigurator →</button>'
      +'</div>';
    loadZuschuesse(ctxVehicleId);
  } catch(e) {
    box.innerHTML = '<div class="hint">Fehler beim Laden der Fahrzeugdaten.</div>';
  }
}

function onVcostKatChange() {
  const kat = document.getElementById('vcost-kat').value;
  const wrap = document.getElementById('vcost-bez-wrap');
  if (wrap) wrap.style.display = (kat === 'sonstige') ? 'block' : 'none';
}


// ─── Profi-Modus-Block (Werbungskosten: Pauschale vs. echter km-Satz) ──────────
let _vallVer = false;

async function addPkwCostV(vehicleId) {
  const kategorie = document.getElementById('vcost-kat').value;
  const bezEl = document.getElementById('vcost-bez');
  const freitext = bezEl ? bezEl.value.trim() : '';
  const betrag = parseFloat(document.getElementById('vcost-betrag').value);
  const intervall = document.getElementById('vcost-int').value;
  if (isNaN(betrag) || betrag <= 0) { alert('Bitte einen Betrag angeben.'); return; }
  // Bei "Sonstige" ist ein Freitext nötig, sonst wird das Kategorie-Label als Bezeichnung genutzt
  if (kategorie === 'sonstige' && !freitext) { alert('Bitte für "Sonstige" eine Bezeichnung eingeben.'); return; }
  const bezeichnung = freitext || (_pkwKatLabels[kategorie] || kategorie);
  await fetch('/api/pkw/costs', { method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ vehicle_id: parseInt(vehicleId), kategorie, bezeichnung, betrag, intervall }) });
  loadVollkostenView();
}

async function deletePkwCostV(id) {
  if (!confirm('Kostenposten löschen?')) return;
  await fetch(`/api/pkw/costs/${id}`, { method:'DELETE' });
  loadVollkostenView();
}

async function saveAllowanceV(vehicleId) {
  const betrag = parseFloat(document.getElementById('vall-betrag').value) || 0;
  const lstk = parseInt(document.getElementById('vall-lstk').value) || 1;
  await fetch('/api/pkw/allowance', { method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ vehicle_id: parseInt(vehicleId), monatlicher_betrag: betrag, lohnsteuerklasse: lstk, versteuert: _vallVer }) });
  _toast('Car Allowance gespeichert');
  loadVollkostenView();
}

// ═══════════════════════════════════════════════════════════════════════════
// DOPPELABRECHNUNGS-ERKENNUNG (Compliance § 3 Nr. 50 EStG) — v10.88
// ═══════════════════════════════════════════════════════════════════════════
async function checkDuplicates(von, bis) {
  const warnBox = document.getElementById('duplicate-warning');
  if (!warnBox) return;
  try {
    const params = new URLSearchParams();
    if (von) params.set('von', von);
    if (bis) params.set('bis', bis);
    const r = await fetch('/api/sessions/duplicate-check?' + params.toString());
    const d = await r.json();
    if (!d.conflicts || d.conflicts.length === 0) {
      warnBox.style.display = 'none';
      return;
    }
    warnBox.style.display = 'block';
    const txt = document.getElementById('duplicate-warning-text');
    txt.innerHTML = `Es wurden <b>${d.conflict_count} überschneidende Ladezeiträume</b> aus unterschiedlichen Datenquellen (Wallbox MID-Sensor &amp; Fahrzeug-App) erkannt. ` +
      `Für § 3 Nr. 50 EStG darf jede Ladung nur <b>einmal</b> abgerechnet werden — behalte pro Konflikt entweder die Wallbox <b>oder</b> die App.`;

    const srcLabel = s => ({ 'loxone_api':'Wallbox MID-Sensor', 'ocpp':'Wallbox MID-Sensor', 'bmw_app':'Fahrzeug-App' }[s] || s);
    const fmtDur = m => m >= 60 ? `${Math.floor(m/60)}h ${m%60}min` : `${m}min`;

    // Bulk-Aktionen oben
    let html = `
      <div style="display:flex; gap:8px; flex-wrap:wrap; margin-bottom:14px; padding-bottom:14px; border-bottom:1px solid var(--border);">
        <button class="btn btn-sm btn-primary" onclick="resolveAllDuplicates('wallbox')">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:middle;margin-right:4px;"><polyline points="20 6 9 17 4 12"/></svg>
          Alle: Wallbox behalten (empfohlen)
        </button>
        <button class="btn btn-sm" onclick="resolveAllDuplicates('higher')">
          Alle: höheren Betrag behalten
        </button>
      </div>`;

    // Einzelkonflikte als Vergleichstabelle
    html += d.conflicts.map(c => {
      const a = c.session_a, b = c.session_b;
      const timeRange = `${(a.start||'').slice(11,16)}–${(a.end||'').slice(11,16)} Uhr`;
      const dateStr = (a.start||'').slice(0,10);
      // Karte pro Session mit Vergleichsdaten
      const card = (s, isRecommended, isHigher, keepId, removeId) => `
        <div style="flex:1; min-width:200px; border:1px solid ${isRecommended?'var(--amber)':'var(--border)'}; border-radius:8px; padding:10px 12px; background:var(--bg-input);">
          <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:8px;">
            <span style="font-size:12px; font-weight:600;">${srcLabel(s.source)}</span>
            <span style="font-size:10px; color:var(--text-tertiary);">#${s.id}</span>
          </div>
          <div style="display:flex; flex-direction:column; gap:3px; font-size:12px; margin-bottom:10px;">
            <div style="display:flex; justify-content:space-between;"><span style="color:var(--text-tertiary);">Energie</span><b>${fmtDe(s.kwh,2)} kWh</b></div>
            <div style="display:flex; justify-content:space-between;"><span style="color:var(--text-tertiary);">Betrag</span><b style="color:${isHigher?'var(--success)':'var(--text-primary)'};">${fmtDe(s.amount,2)} €${isHigher?' ▲':''}</b></div>
            <div style="display:flex; justify-content:space-between;"><span style="color:var(--text-tertiary);">Preis</span><span>${fmtDe(s.rate,3)} €/kWh</span></div>
            <div style="display:flex; justify-content:space-between;"><span style="color:var(--text-tertiary);">Dauer</span><span>${fmtDur(s.duration_min)}</span></div>
          </div>
          ${isRecommended ? '<div style="font-size:9px; color:var(--amber); margin-bottom:6px; text-align:center;">✓ §3 Nr.50 EStG-konform (MID-Messnachweis)</div>' : '<div style="height:15px;"></div>'}
          <button class="btn btn-sm" style="width:100%; ${isRecommended?'border-color:var(--amber); color:var(--amber);':''}" onclick="resolveDuplicate(${keepId}, ${removeId})">
            Diese behalten
          </button>
        </div>`;
      const aRec = c.recommended === 'a', bRec = c.recommended === 'b';
      const aHigh = c.higher_amount === 'a', bHigh = c.higher_amount === 'b';
      return `
        <div style="margin-bottom:14px;">
          <div style="font-size:12px; font-weight:600; color:var(--text-secondary); margin-bottom:8px;">${dateStr} · ${timeRange}</div>
          <div style="display:flex; gap:10px; flex-wrap:wrap; align-items:stretch;">
            ${card(a, aRec, aHigh, a.id, b.id)}
            <div style="display:flex; align-items:center; color:var(--text-tertiary); font-size:11px;">oder</div>
            ${card(b, bRec, bHigh, b.id, a.id)}
          </div>
        </div>`;
    }).join('');

    document.getElementById('duplicate-conflict-list').innerHTML = html;
  } catch(e) {
    warnBox.style.display = 'none';
  }
}

async function resolveAllDuplicates(strategy) {
  const label = strategy === 'wallbox' ? 'die Wallbox-Messung' : 'den jeweils höheren Betrag';
  if (!confirm(`Alle Konflikte automatisch auflösen und ${label} behalten?\n\nDie jeweils andere Session wird entfernt.`)) return;
  try {
    const r = await fetch('/api/sessions/duplicate-resolve', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ strategy })
    });
    const d = await r.json();
    if (d.verbleibend > 0) {
      _toast(`${d.removed} entfernt — ${d.verbleibend} Konflikte bleiben. `
           + `Bitte einzeln prüfen.`);
    } else {
      _toast(`${d.removed} doppelte Ladevorgänge entfernt`);
    }
    loadSessions();
  } catch(e) {
    alert('Fehler bei der automatischen Auflösung.');
  }
}

// Konflikt lösen: eine Session behalten, die andere als "ignoriert" markieren (löschen)
async function resolveDuplicate(keepId, removeId) {
  if (!confirm(`Session #${removeId} entfernen und #${keepId} behalten?\n\nDie entfernte Session wird gelöscht, um die Doppelabrechnung zu vermeiden.`)) return;
  try {
    await fetch(`/api/sessions/${removeId}`, { method: 'DELETE' });
    _toast(`Session #${removeId} entfernt — Doppelabrechnung vermieden`);
    loadSessions();
  } catch(e) {
    alert('Fehler beim Entfernen der Session.');
  }
}

// ─── Fahrtenbuch (Nachweis für individuellen Kilometersatz / Weg 2) ───────────
async function openFahrtenbuchDialog() {
  const heute = new Date();
  const jahr = heute.getFullYear();
  // gespeicherten Zeitraum + km-Stände laden (Fallback: Jahresanfang bis heute)
  try {
    const r = await fetch('/api/settings/fahrtenbuch-zeitraum');
    const d = await r.json();
    document.getElementById('fb-von').value = d.von || `${jahr}-01-01`;
    document.getElementById('fb-bis').value = d.bis || heute.toISOString().slice(0,10);
    document.getElementById('fb-km-start').value = d.km_start > 0 ? Math.round(d.km_start) : '';
    document.getElementById('fb-km-ende').value = d.km_ende > 0 ? Math.round(d.km_ende) : '';
  } catch(e){
    document.getElementById('fb-von').value = `${jahr}-01-01`;
    document.getElementById('fb-bis').value = heute.toISOString().slice(0,10);
  }
  document.getElementById('fahrtenbuch-modal').style.display = 'flex';
}

function closeFahrtenbuchDialog() {
  document.getElementById('fahrtenbuch-modal').style.display = 'none';
}

async function generateFahrtenbuch() {
  const von = document.getElementById('fb-von').value;
  const bis = document.getElementById('fb-bis').value;
  const kmStart = parseFloat(document.getElementById('fb-km-start').value) || 0;
  const kmEnde = parseFloat(document.getElementById('fb-km-ende').value) || 0;

  if (!von || !bis) { alert('Bitte Zeitraum (von / bis) angeben.'); return; }
  if (bis < von) { alert('Das Enddatum liegt vor dem Startdatum.'); return; }
  if (kmEnde > 0 && kmEnde < kmStart) { alert('Der End-Kilometerstand ist kleiner als der Anfangsstand.'); return; }

  // Zeitraum + km-Stände speichern
  await fetch('/api/settings/fahrtenbuch-zeitraum', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ von, bis, km_start: kmStart, km_ende: kmEnde })
  });
  closeFahrtenbuchDialog();
  // PDF herunterladen — Zeitraum steuert, welche Dienstfahrten übernommen werden
  let url = `/api/documents/fahrtenbuch?von=${von}&bis=${bis}`;
  if (kmStart > 0) url += `&km_start=${kmStart}`;
  if (kmEnde > 0) url += `&km_ende=${kmEnde}`;
  downloadPdf(url, `Fahrtenbuch_${von}_bis_${bis}.pdf`);
}


// ═══════════════════════════════════════════════════════════════════════════
// ABRECHNUNGS-KONFIGURATOR + FAHRZEUG-FINDER
// ═══════════════════════════════════════════════════════════════════════════
let _konfDefaults = null;
let _finderInit = false;

function switchKonfTab(tab) {
  const isAbr = tab === 'abrechnung';
  document.getElementById('ktab-abrechnung').classList.toggle('active', isAbr);
  document.getElementById('ktab-finder').classList.toggle('active', !isAbr);
  document.getElementById('konf-tab-abrechnung').style.display = isAbr ? 'block' : 'none';
  document.getElementById('konf-tab-finder').style.display = isAbr ? 'none' : 'block';
  if (!isAbr && !_finderInit) { initFinder(); }
}

async function initKonfigurator() {
  document.getElementById('konf-start').style.display = 'block';
  document.getElementById('konf-main').style.display = 'none';
  try {
    const r = await fetch('/api/decision/defaults');
    _konfDefaults = await r.json();
    if (!_konfDefaults.hat_echte_daten) {
      document.getElementById('konf-keine-daten').style.display = 'block';
    }
  } catch(e) { _konfDefaults = null; }
}

let _konfWeiche = 'privat';  // 'privat' oder 'firma'

function startKonfigurator(modus, weiche) {
  _konfWeiche = weiche || 'privat';
  const src = (modus === 'echt' && _konfDefaults) ? _konfDefaults.echte_werte
              : (_konfDefaults ? _konfDefaults.standard_werte : {
                  k_gesamt_privat: 9600, d_gesamt: 20000, d_dienst: 6000,
                  ag_erstattung: 0.15, steuersatz: 0.42, kwh_pv_jahr: 0,
                  ag_zuschuss_brutto: 0, ag_zuschuss_versteuert: true });

  document.getElementById('konf-start').style.display = 'none';
  document.getElementById('konf-main').style.display = 'block';

  const setVal = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
  document.getElementById('k-gesamtkm').max = 60000;
  document.getElementById('k-dienstkm').max = Math.round(src.d_gesamt);
  setVal('k-kosten', Math.round(src.k_gesamt_privat));
  setVal('k-gesamtkm', Math.round(src.d_gesamt));
  setVal('k-dienstkm', Math.round(src.d_dienst));
  setVal('k-agrate', src.ag_erstattung);
  setVal('k-steuer', Math.round(src.steuersatz * 100));
  setVal('k-arbeit', 0);
  if (src.ag_zuschuss_brutto > 0) {
    setVal('k-allowance', Math.round(src.ag_zuschuss_brutto));
    setVal('k-allowance-vst', src.ag_zuschuss_versteuert ? '1' : '0');
  }
  // Firmenwagen-Weiche: BLP vorbelegen, damit A/B sofort erscheinen
  if (_konfWeiche === 'firma') {
    setVal('k-blp', 60000);
  }
  if (_konfDefaults && _konfDefaults.antrieb) {
    const a = document.getElementById('k-antrieb');
    if (a) a.value = _konfDefaults.antrieb === 'verbrenner' ? 'verbrenner' : 'elektro';
  }
  // Kostenaufschlüsselung anzeigen (echte Posten aus Fahrzeugdaten)
  const kp = (_konfDefaults && _konfDefaults.kostenposten) || [];
  const detEl = document.getElementById('k-kosten-details');
  const listEl = document.getElementById('k-kosten-liste');
  if (kp.length > 0 && modus === 'echt') {
    const katLbl = { leasing:'Leasing/Finanzierung', versicherung:'Versicherung', wartung:'Wartung',
      reifen:'Reifen/Verschleiß', tuev:'TÜV/HU', steuer:'Kfz-Steuer', strom:'Ladestrom',
      kraftstoff:'Kraftstoff', pflege:'Pflege/Betriebskosten', sonstige:'Sonstige' };
    listEl.innerHTML = kp.map(c =>
      `<div style="display:flex; justify-content:space-between; padding:1px 0;"><span>${katLbl[c.kategorie]||c.bezeichnung||c.kategorie}</span><span>${fmtDe(c.jahr,0)} €/Jahr</span></div>`
    ).join('');
    detEl.style.display = 'block';
    document.getElementById('k-kosten-quelle').textContent = 'Aus deinen Fahrzeugdaten übernommen.';
  } else {
    detEl.style.display = 'none';
    document.getElementById('k-kosten-quelle').textContent = 'Leasing, Versicherung, Wartung, Reifen, Strom p. a.';
  }
  applyKonfWeiche();
  populateKonfVehicleSelect();
  recalcKonf();
}

// Fahrzeug-Auswahl im Konfigurator: lädt die Daten des gewählten Autos
async function populateKonfVehicleSelect() {
  const sel = document.getElementById('k-vehicle-select');
  if (!sel) return;
  try {
    const r = await fetch('/api/vehicles');
    const d = await r.json();
    const vehs = d.vehicles || [];
    sel.innerHTML = vehs.map(v => `<option value="${v.id}">${v.bezeichnung}</option>`).join('')
      || '<option value="">Kein Fahrzeug</option>';
    if (_konfDefaults && _konfDefaults.vehicle_id) sel.value = _konfDefaults.vehicle_id;
  } catch(e) {}
}

async function changeKonfVehicle() {
  const sel = document.getElementById('k-vehicle-select');
  if (!sel || !sel.value) return;
  try {
    const r = await fetch('/api/decision/defaults?vehicle_id=' + sel.value);
    _konfDefaults = await r.json();
    startKonfigurator('echt', _konfWeiche);
  } catch(e) {}
}

// Blendet je nach Weiche die relevanten Bereiche ein/aus
function applyKonfWeiche() {
  const istFirma = _konfWeiche === 'firma';
  // Privat-PKW-Karten C1/C2 nur bei Privat prominent; Firmenwagen-Block bei Firma
  const privatLabel = document.querySelector('#konf-tab-abrechnung [data-privat-label]');
  // Firmenwagen-Detailfelder (Eigenanteil) nur bei Firma
  const eaBlock = document.getElementById('konf-fw-eigenanteil');
  if (eaBlock) eaBlock.style.display = istFirma ? 'block' : 'none';
}

async function recalcKonf() {
  const num = id => parseFloat(document.getElementById(id).value) || 0;
  const kosten   = num('k-kosten');
  const gesamtKm = num('k-gesamtkm');
  let dienstKm   = num('k-dienstkm');
  const arbeitEinf = num('k-arbeit');
  const arbeitKm = arbeitEinf * 2 * 220; // hin+zurück × ~220 Arbeitstage
  const agRate   = num('k-agrate');
  const steuer   = num('k-steuer') / 100;
  const blp      = num('k-blp');
  const antrieb  = document.getElementById('k-antrieb').value;
  const allowanceBrutto = num('k-allowance');
  const allowanceVst = document.getElementById('k-allowance-vst').value === '1';
  const pvAktiv  = document.getElementById('k-pv-aktiv').checked;
  const heimAnteil = num('k-heim-anteil') / 100;             // Anteil Heimladung gesamt
  const pvDavon = pvAktiv ? num('k-pv-anteil') / 100 : 0;    // davon PV
  const preisHeim = num('k-preis-heim') || 0.30;
  const preisUnterwegs = num('k-preis-unterwegs') || 0.55;
  const pvOpp = num('k-pv-opp') || 0.08;

  document.getElementById('k-dienstkm').max = gesamtKm;
  if (dienstKm > gesamtKm) { dienstKm = gesamtKm; document.getElementById('k-dienstkm').value = gesamtKm; }

  // Labels
  document.getElementById('k-kosten-val').textContent = fmtDe(kosten,0) + ' €';
  document.getElementById('k-gesamtkm-val').textContent = fmtDe(gesamtKm,0) + ' km';
  const anteil = gesamtKm>0 ? (dienstKm/gesamtKm*100) : 0;
  document.getElementById('k-dienstkm-val').textContent = `${fmtDe(dienstKm,0)} km (${fmtDe(anteil,0)} %)`;
  document.getElementById('k-agrate-val').textContent = fmtDe(agRate,2) + ' €';
  document.getElementById('k-steuer-val').textContent = Math.round(steuer*100) + ' %';
  document.getElementById('k-arbeit-val').textContent = fmtDe(arbeitEinf,0) + ' km';
  document.getElementById('k-heim-anteil-val').textContent = Math.round(heimAnteil*100) + ' %';
  document.getElementById('k-pv-anteil-val').textContent = Math.round(pvDavon*100) + ' %';

  // AG-Zuschuss Netto-Hinweis
  const netEl = document.getElementById('k-allowance-netto');
  if (allowanceBrutto > 0) {
    const netto = allowanceVst ? allowanceBrutto * (1 - steuer) : allowanceBrutto;
    netEl.textContent = `Netto ≈ ${fmtDe(netto,0)} €/Monat (${fmtDe(netto*12,0)} €/Jahr)`;
  } else { netEl.textContent = 'Kein Zuschuss — Rechnung läuft trotzdem.'; }

  // ── Antriebsweiche: Strom/PV nur bei E-Antrieb, sonst Kraftstoff ──
  const istVerbrenner = (antrieb === 'verbrenner');
  const energieBlock = document.getElementById('konf-energie-block');
  const kraftstoffBlock = document.getElementById('konf-kraftstoff-block');
  if (energieBlock) energieBlock.style.display = istVerbrenner ? 'none' : 'block';
  if (kraftstoffBlock) kraftstoffBlock.style.display = istVerbrenner ? 'block' : 'none';
  if (istVerbrenner) {
    const vl = num('k-verbrauch-l') || 6.5;
    const pl = num('k-preis-liter') || 1.75;
    const kraftstoffJahr = gesamtKm * vl / 100 * pl;
    const kEl = document.getElementById('k-kraftstoff-gesamt');
    if (kEl) kEl.textContent = fmtDe(kraftstoffJahr, 2) + ' €';
  }

  // PV-Body toggeln
  document.getElementById('konf-pv-body').style.display = pvAktiv ? 'block' : 'none';

  // Antrieb → Verbrauch (BEV ~19 kWh/100km)
  const verbrauch100 = 19;
  const eigenanteil = num('k-fw-eigenanteil');
  // Energie-Anteile: heim (Netz) / PV / unterwegs
  const anteilPv = heimAnteil * pvDavon;
  const anteilHeimNetz = heimAnteil * (1 - pvDavon);
  const params = {
    k_gesamt_privat: kosten, d_gesamt: gesamtKm, d_dienst: dienstKm,
    d_arbeit: arbeitKm, ag_erstattung: agRate, steuersatz: steuer,
    blp: blp || null, antrieb: antrieb, entfernung_km: arbeitEinf, k_gesamt_ag: kosten,
    pv_aktiv: pvAktiv, kwh_pv_jahr: 0, verbrauch_kwh_100: verbrauch100,
    heimlade_anteil: anteilPv, pv_opportunitaet: pvOpp,
    energie_detail: (antrieb !== 'verbrenner'),
    anteil_heim: anteilHeimNetz, anteil_pv: anteilPv,
    preis_heim: preisHeim, preis_unterwegs: preisUnterwegs,
    ag_zuschuss_brutto: allowanceBrutto || null, ag_zuschuss_versteuert: allowanceVst,
    fw_eigenanteil_monat: eigenanteil || 0,
  };
  let d;
  try {
    const r = await fetch('/api/decision/calc', {
      method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(params)
    });
    d = await r.json();
  } catch(e) { return; }

  renderKonfErgebnis(d);
}

function renderKonfErgebnis(d) {
  const eur = v => fmtDe(v,2) + ' €';
  document.getElementById('k-kmsatz').textContent = fmtDe(d.echter_km_satz,3) + ' €/km';
  document.getElementById('k-kmsatz-formel').textContent =
    `${fmtDe(d.eingaben.k_gesamt_privat,0)} € ÷ ${fmtDe(d.eingaben.d_gesamt,0)} km`;

  document.getElementById('k-c1-wk').textContent = eur(d.privat.c1.werbungskosten);
  document.getElementById('k-c1-st').textContent = eur(d.privat.c1.steuererstattung);
  document.getElementById('k-c2-wk').textContent = eur(d.privat.c2.werbungskosten);
  document.getElementById('k-c2-st').textContent = eur(d.privat.c2.steuererstattung);
  const c1El = document.getElementById('konf-c1'), c2El = document.getElementById('konf-c2');
  c1El.className = 'konf-card'; c2El.className = 'konf-card';
  _setKonfBadge(['konf-c1','konf-c2'], null);
  if (d.privat.empfehlung.empfehlung === 'C2') {
    c2El.classList.add('winner');
    _setKonfBadge(['konf-c1','konf-c2'], 'konf-c2');
  } else {
    c1El.classList.add('winner-blue');
    _setKonfBadge(['konf-c1','konf-c2'], 'konf-c1');
  }

  const firmaBlock = document.getElementById('konf-firma-block');
  if (d.firmenwagen) {
    firmaBlock.style.display = 'block';
    const a = d.firmenwagen.a, b = d.firmenwagen.b;
    document.getElementById('k-a-satz').textContent = `${fmtDe(a.antrieb_satz_pct,2)} %-Regel`;
    document.getElementById('k-a-gwv').textContent = eur(a.gwv_monat);
    document.getElementById('k-a-netto').textContent = eur(a.netto_belastung_jahr);
    document.getElementById('k-b-quote').textContent = `Privatquote ${fmtDe(b.privatquote_pct,0)} %`;
    document.getElementById('k-b-gwv').textContent = eur(b.gwv_jahr);
    document.getElementById('k-b-netto').textContent = eur(b.netto_belastung_jahr);
    const aEl = document.getElementById('konf-a'), bEl = document.getElementById('konf-b');
    aEl.className = 'konf-card'; bEl.className = 'konf-card';
    _setKonfBadge(['konf-a','konf-b'], null);
    if (a.netto_belastung_jahr <= b.netto_belastung_jahr) {
      aEl.classList.add('winner-blue');
      _setKonfBadge(['konf-a','konf-b'], 'konf-a');
    } else {
      bEl.classList.add('winner');
      _setKonfBadge(['konf-a','konf-b'], 'konf-b');
    }
  } else { firmaBlock.style.display = 'none'; }

  if (d.pv) {
    document.getElementById('k-pv-marge').textContent = fmtDe(d.pv.marge_pro_kwh,2) + ' €';
    document.getElementById('k-pv-gewinn').textContent = '+' + eur(d.pv.gewinn_jahr) + '/Jahr';
  }

  // Empfehlung Hero
  const emp = d.privat.empfehlung;
  document.getElementById('konf-emp-titel').textContent = emp.titel;
  document.getElementById('konf-emp-begruendung').textContent = emp.begruendung;
  const card = document.getElementById('konf-empfehlung');
  const iconWrap = document.getElementById('konf-emp-icon');
  const vorteilEl = document.getElementById('konf-emp-vorteil');
  const ampelColor = emp.ampel === 'gruen' ? 'var(--success)' : emp.ampel === 'blau' ? 'var(--accent)' : 'var(--warning, #eab308)';
  card.style.borderLeftColor = ampelColor;
  iconWrap.style.background = emp.ampel === 'gruen' ? 'var(--success-soft)' : 'var(--accent-soft, rgba(59,130,246,.12))';

  const v = d.privat.vorteil_c2_pro_jahr;
  let txt = '';
  if (emp.empfehlung === 'C2' && v > 0) {
    vorteilEl.style.color = 'var(--success)';
    txt = `+${eur(v)} / Jahr (≈ +${eur(d.privat.vorteil_c2_pro_monat)} / Monat) durch Fahrtenbuch`;
  } else if (emp.empfehlung === 'C1') {
    vorteilEl.style.color = 'var(--text-secondary)';
    txt = `Pauschale spart Fahrtenbuch-Aufwand bei gleichem/besserem Ertrag`;
  }
  // Zusätze: AG-Zuschuss + PV
  const zusatz = [];
  if (d.allowance) zusatz.push(`Car Allowance netto ${eur(d.allowance.netto_jahr)}/Jahr`);
  if (d.pv && d.pv.gewinn_jahr > 0) zusatz.push(`PV-Bonus +${eur(d.pv.gewinn_jahr)}/Jahr`);
  if (zusatz.length) txt += '  ·  ' + zusatz.join('  ·  ');
  vorteilEl.textContent = txt;
  vorteilEl.style.display = txt ? 'block' : 'none';

  // ── Kassenbon "Was kostet mich das Auto wirklich?" ──
  const rk = d.netto_belastung_privat;
  const rkCard = document.getElementById('konf-realkosten');
  if (rk) {
    rkCard.style.display = 'block';
    document.getElementById('rk-gesamt').textContent = '− ' + eur(rk.gesamtkosten_jahr);
    document.getElementById('rk-fahrt').textContent = '+ ' + eur(rk.fahrt_erstattung_ag);
    const allowRow = document.getElementById('rk-allowance-row');
    if (rk.abzgl_allowance_netto > 0) {
      allowRow.style.display = 'flex';
      document.getElementById('rk-allowance').textContent = '+ ' + eur(rk.abzgl_allowance_netto);
    } else { allowRow.style.display = 'none'; }
    document.getElementById('rk-steuer').textContent = '+ ' + eur(rk.abzgl_steuererstattung);
    const pvRow = document.getElementById('rk-pv-row');
    if (rk.pv_bonus > 0) {
      pvRow.style.display = 'flex';
      document.getElementById('rk-pv').textContent = '+ ' + eur(rk.pv_bonus);
    } else { pvRow.style.display = 'none'; }
    const totalRow = document.querySelector('#konf-realkosten .bon-total');
    // Liquiditäts-Trennung
    const sofortEl = document.getElementById('rk-sofort');
    const spaeterEl = document.getElementById('rk-spaeter');
    if (sofortEl) {
      const ss = rk.saldo_sofort_jahr;
      sofortEl.textContent = (ss >= 0 ? '+ ' : '− ') + eur(Math.abs(ss)) + ' /Jahr';
      sofortEl.className = ss >= 0 ? 'bon-pos' : 'bon-neg';
    }
    if (spaeterEl) {
      spaeterEl.textContent = '+ ' + eur(rk.spaeter_jahr) + ' /Jahr';
      spaeterEl.className = 'bon-pos';
    }
    const saldoLbl = document.getElementById('rk-saldo-label');
    const realEl = document.getElementById('rk-real');
    const plus = rk.saldo_jahr >= 0;
    totalRow.classList.toggle('plus', plus);
    totalRow.classList.toggle('minus', !plus);
    saldoLbl.textContent = plus ? '= Überschuss pro Jahr' : '= Deine Belastung pro Jahr';
    realEl.textContent = (plus ? '+ ' : '− ') + eur(Math.abs(rk.saldo_jahr));
    document.getElementById('rk-real-monat').textContent = (plus ? '+ ' : '− ') + eur(Math.abs(rk.saldo_monat));
  } else {
    rkCard.style.display = 'none';
  }

  // ── Energiekosten-Aufschlüsselung ──
  const enBox = document.getElementById('konf-energie-detail');
  const mixBox = document.getElementById('konf-mischpreis');
  if (d.energie && enBox) {
    const e2 = d.energie;
    enBox.style.display = 'block';
    // Effektiver Mischpreis inkl. Split-Angabe
    if (mixBox) {
      mixBox.style.display = 'flex';
      document.getElementById('k-mischpreis').textContent = fmtDe(e2.mischpreis_kwh, 3) + ' €/kWh';
      const teile = [`${fmtDe(e2.anteil_heim_pct,0)} % Heim`];
      if (e2.anteil_pv_pct > 0) teile.push(`${fmtDe(e2.anteil_pv_pct,0)} % PV`);
      if (e2.anteil_unterwegs_pct > 0) teile.push(`${fmtDe(e2.anteil_unterwegs_pct,0)} % DC`);
      document.getElementById('k-mix-split').textContent = '(' + teile.join(' · ') + ')';
    }
    document.getElementById('konf-energie-liste').innerHTML =
      `<span>Heim (Netz, ${fmtDe(e2.kwh_heim,0)} kWh)</span><span style="text-align:right;">${eur(e2.kosten_heim)}</span>`
      + (e2.kwh_pv > 0 ? `<span>PV (entg. Einspeisung, ${fmtDe(e2.kwh_pv,0)} kWh)</span><span style="text-align:right;">${eur(e2.kosten_pv)}</span>` : '')
      + `<span>Unterwegs (${fmtDe(e2.kwh_unterwegs,0)} kWh)</span><span style="text-align:right;">${eur(e2.kosten_unterwegs)}</span>`
      + `<span style="font-weight:700; border-top:1px solid var(--border); padding-top:3px;">Energie gesamt</span><span style="text-align:right; font-weight:700; border-top:1px solid var(--border); padding-top:3px;">${eur(e2.energie_gesamt_jahr)}</span>`;
  } else if (enBox) {
    enBox.style.display = 'none';
    if (mixBox) mixBox.style.display = 'none';
  }
}

// ─── FAHRZEUG-FINDER ─────────────────────────────────────────────────────────
const FINDER_FARBEN = { energie:'#ef4444', verschleiss:'#f59e0b', versicherung:'#3b82f6', kfz_steuer:'#8b5cf6', afa:'#64748b' };
let _finderDefaults = null;

async function initFinder() {
  _finderInit = true;
  try {
    const r = await fetch('/api/decision/finder-defaults');
    const d = await r.json();
    _finderDefaults = d.antriebsarten;
    renderFinderOverrides();
  } catch(e) {}
  recalcFinder();
}

function renderFinderOverrides() {
  if (!_finderDefaults) return;
  const box = document.getElementById('f-overrides');
  const arten = [['diesel','Diesel'],['benzin','Benziner'],['bev','Elektro'],['phev','PHEV']];
  box.innerHTML = arten.map(([k,label]) => {
    const c = _finderDefaults[k];
    const einheit = c.einheit || 'l';
    return `<div style="font-size:11px;">
      <div style="font-weight:600; margin-bottom:4px;">${label}</div>
      <label class="field-label" style="font-size:10px;">Verbrauch (${einheit}/100km)</label>
      <input type="number" step="0.1" id="fo-${k}-verbrauch" value="${c.verbrauch}" style="width:100%; margin-bottom:4px;" oninput="recalcFinder()">
      <label class="field-label" style="font-size:10px;">Energiepreis (€/${einheit})</label>
      <input type="number" step="0.01" id="fo-${k}-preis" value="${c.energiepreis}" style="width:100%;" oninput="recalcFinder()">
    </div>`;
  }).join('');
}

async function recalcFinder() {
  const num = id => parseFloat(document.getElementById(id).value) || 0;
  document.getElementById('f-km-val').textContent = fmtDe(num('f-km'),0) + ' km';
  document.getElementById('f-tour-val').textContent = fmtDe(num('f-tour'),0) + ' km';
  document.getElementById('f-reichweite-val').textContent = fmtDe(num('f-reichweite'),0) + ' km';

  // Overrides einsammeln
  const overrides = {};
  if (_finderDefaults) {
    ['diesel','benzin','bev','phev'].forEach(k => {
      const vEl = document.getElementById(`fo-${k}-verbrauch`);
      const pEl = document.getElementById(`fo-${k}-preis`);
      if (vEl && pEl) overrides[k] = { verbrauch: parseFloat(vEl.value)||0, energiepreis: parseFloat(pEl.value)||0 };
    });
  }

  const params = {
    km_jahr: num('f-km'), tagestour_km: num('f-tour'),
    reichweite_bedarf_km: num('f-reichweite'),
    heimladung: document.getElementById('f-heimladung').checked,
    externe_ladung: document.getElementById('f-extern').checked,
    overrides: overrides,
  };
  let d;
  try {
    const r = await fetch('/api/decision/finder', {
      method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(params)
    });
    d = await r.json();
  } catch(e) { return; }
  renderFinderErgebnis(d);
}

function renderFinderErgebnis(d) {
  // Empfehlung
  if (d.empfehlung) {
    const box = document.getElementById('finder-empfehlung');
    box.style.display = 'block';
    document.getElementById('finder-emp-titel').textContent = d.empfehlung.label;
    let begr = `Günstigste geeignete Antriebsart für dein Profil: ${fmtDe(d.empfehlung.gesamt_jahr,0)} €/Jahr`;
    if (d.empfehlung.ersparnis_vs_teuerste > 0) begr += ` — bis zu ${fmtDe(d.empfehlung.ersparnis_vs_teuerste,0)} €/Jahr günstiger als die teuerste Option.`;
    if (d.empfehlung.hinweise && d.empfehlung.hinweise.length) begr += ' ' + d.empfehlung.hinweise.join(' ');
    if (d.empfehlung.guenstigste_ohne_eignung)
      begr += ` (Rechnerisch wäre ${d.empfehlung.guenstigste_ohne_eignung} günstiger, passt aber nicht zu deinem Fahrprofil.)`;
    document.getElementById('finder-emp-begruendung').textContent = begr;
    const eig = d.empfehlung.eignung;
    const col = eig === 'gut' ? 'var(--success)' : eig === 'bedingt' ? 'var(--warning, #eab308)' : 'var(--danger)';
    box.style.borderLeftColor = col;
    document.getElementById('finder-emp-icon').style.background = eig === 'gut' ? 'var(--success-soft)' : 'rgba(234,179,8,.12)';
  }

  // Balkendiagramm (gestapelt)
  const maxGesamt = Math.max(...d.arten.map(a => a.kosten.gesamt), 1);
  const teile = ['energie','verschleiss','versicherung','kfz_steuer','afa'];
  const chart = d.arten.map((a, idx) => {
    const k = a.kosten;
    const segs = teile.map(t => {
      const w = k.gesamt > 0 ? (k[t] / maxGesamt * 100) : 0;
      return w > 0 ? `<div class="finder-bar-seg" style="width:${w}%; background:${FINDER_FARBEN[t]};" title="${t}: ${fmtDe(k[t],0)} €"></div>` : '';
    }).join('');
    const badge = `<span class="finder-badge ${a.eignung}">${a.eignung}</span>`;
    return `<div class="finder-bar-row ${idx===0 && a.eignung==='gut' ? 'winner' : ''}">
      <div class="finder-bar-label">${a.label}${badge}</div>
      <div class="finder-bar-track">${segs}</div>
      <div class="finder-bar-total">${fmtDe(k.gesamt,0)} €</div>
    </div>`;
  }).join('');
  document.getElementById('finder-chart').innerHTML = chart;
}

// ═══ Bulk-Löschen: ausgewählte Sessions / Fahrten ═══
async function deleteSelectedSessions() {
  const n = _selectedSessions.size;
  if (n === 0) return;
  if (!confirm(`${n} ausgewählte Session(s) wirklich löschen?`)) return;
  const ids = Array.from(_selectedSessions);
  await Promise.all(ids.map(id => fetch(`/api/sessions/${id}`, { method:'DELETE' })));
  _selectedSessions.clear();
  _updateSelectionBar();
  _toast(`${n} Session(s) gelöscht`);
  loadSessions();
  loadDashboardSummary();
}

async function deleteSelectedTrips() {
  const n = _selectedTrips.size;
  if (n === 0) return;
  if (!confirm(`${n} ausgewählte Fahrt(en) wirklich löschen?`)) return;
  const ids = Array.from(_selectedTrips);
  await Promise.all(ids.map(id => fetch(`/api/trips/${id}`, { method:'DELETE' })));
  _selectedTrips.clear();
  _updateSelectionBar();
  _toast(`${n} Fahrt(en) gelöscht`);
  loadTrips();
  loadDashboardSummary();
}

// PDF-Auswertung Stromkosten (nutzt die aktuellen Filter der Auswertungs-Seite)
function exportStromkostenPdf() {
  const von = document.getElementById('ausw-von')?.value || '';
  const bis = document.getElementById('ausw-bis')?.value || '';
  const wallboxId = document.getElementById('ausw-wallbox')?.value || '';
  const realRate = document.getElementById('ausw-realtarif')?.value || '';
  const params = new URLSearchParams();
  if (von) params.set('von', von);
  if (bis) params.set('bis', bis);
  if (wallboxId) params.set('wallbox_id', wallboxId);
  if (realRate) params.set('real_rate', realRate);
  const label = (von && bis) ? `${von}_${bis}` : 'gesamt';
  downloadPdf('/api/documents/stromkosten-auswertung?' + params.toString(),
              `Ladestrom_Auswertung_${label}.pdf`);
}

// ═══ AG-Zuschüsse (mehrere Kategorien je Fahrzeug) ═══
async function loadZuschuesse(vehicleId) {
  const box = document.getElementById('zuschuss-liste');
  if (!box) return;
  try {
    const r = await fetch('/api/pkw/zuschuesse?vehicle_id=' + vehicleId);
    const d = await r.json();
    const kats = d.kategorien || {};
    // Sinnvolle Reihenfolge statt alphabetisch (Car Allowance ist der Regelfall)
    const katReihenfolge = ['car_allowance','tankkarte','jobticket','aufwand','sonstige'];
    const katOrdered = katReihenfolge.filter(k => kats[k]).concat(
      Object.keys(kats).filter(k => !katReihenfolge.includes(k)));
    const rows = (d.zuschuesse || []).map(z => {
      const netto = z.versteuert ? z.monatlicher_betrag * (1 - d.steuersatz) : z.monatlicher_betrag;
      const badge = z.versteuert
        ? '<span style="font-size:9px;padding:1px 6px;border-radius:8px;background:rgba(234,179,8,.15);color:var(--warning,#eab308);">versteuert</span>'
        : '<span style="font-size:9px;padding:1px 6px;border-radius:8px;background:var(--success-soft);color:var(--success);">steuerfrei</span>';
      return `<tr>
        <td>${z.bezeichnung || kats[z.kategorie] || z.kategorie} ${badge}</td>
        <td style="text-align:right;">${fmtDe(z.monatlicher_betrag,2)} €</td>
        <td style="text-align:right; color:var(--text-secondary);">${fmtDe(netto,2)} €</td>
        <td style="text-align:right;"><button class="icon-btn" onclick="deleteZuschuss(${z.id}, ${vehicleId})" style="color:var(--danger);"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg></button></td>
      </tr>`;
    }).join('');
    const s = d.summe || {};
    const katOpts = katOrdered.map(k => `<option value="${k}">${kats[k]}</option>`).join('');
    box.innerHTML = `
      <table style="margin-bottom:10px;">
        <thead><tr><th>Zuschuss</th><th style="text-align:right;">Brutto/Monat</th><th style="text-align:right;">Netto/Monat</th><th></th></tr></thead>
        <tbody>${rows || '<tr><td colspan="4" class="hint">Noch keine Zuschüsse erfasst.</td></tr>'}</tbody>
        ${(s.anzahl>0) ? `<tfoot><tr style="border-top:1px solid var(--border);">
          <td style="font-weight:600;">Summe (${s.anzahl})</td>
          <td style="text-align:right; font-weight:600;">${fmtDe(s.brutto_monat,2)} €</td>
          <td style="text-align:right; font-weight:600; color:var(--success);">${fmtDe(s.netto_monat,2)} €</td><td></td>
        </tr></tfoot>` : ''}
      </table>
      <div style="display:grid; grid-template-columns:1.4fr 0.8fr 1fr auto; gap:8px; align-items:end;">
        <div><label class="field-label">Art des Zuschusses</label>
          <select id="zus-kat" style="width:100%;" onchange="onZuschussKatChange()">${katOpts}</select></div>
        <div><label class="field-label">Brutto €/Monat</label><input type="number" id="zus-betrag" step="0.01" style="width:100%"></div>
        <div><label class="field-label">Versteuerung</label>
          <select id="zus-vst" style="width:100%;"><option value="1">steuerpflichtig</option><option value="0">steuerfrei (§ 3 Nr. 50)</option></select></div>
        <button class="btn btn-sm btn-primary" onclick="addZuschuss(${vehicleId})" style="height:38px;">+ Add</button>
      </div>
      <div id="zus-bez-wrap" style="display:none; margin-top:8px;">
        <label class="field-label">Bezeichnung (frei)</label>
        <input type="text" id="zus-bez" style="width:100%" placeholder="z. B. Mobilitätsbudget">
      </div>
      ${(s.netto_jahr>0) ? `<div class="hint" style="margin-top:8px;">Netto-Zufluss gesamt: <b>${fmtDe(s.netto_jahr,2)} €/Jahr</b> — fließt in den Konfigurator ein.</div>` : ''}`;
  } catch(e) {
    box.innerHTML = '<div class="hint">Fehler beim Laden der Zuschüsse.</div>';
  }
}

function onZuschussKatChange() {
  const kat = document.getElementById('zus-kat').value;
  const wrap = document.getElementById('zus-bez-wrap');
  if (wrap) wrap.style.display = (kat === 'sonstige') ? 'block' : 'none';
}

async function addZuschuss(vehicleId) {
  const kategorie = document.getElementById('zus-kat').value;
  const betrag = parseFloat(document.getElementById('zus-betrag').value);
  const versteuert = document.getElementById('zus-vst').value === '1';
  const bezEl = document.getElementById('zus-bez');
  const bezeichnung = bezEl ? bezEl.value.trim() : '';
  if (isNaN(betrag) || betrag <= 0) { alert('Bitte einen Betrag angeben.'); return; }
  if (kategorie === 'sonstige' && !bezeichnung) { alert('Bitte für "Sonstiger Zuschuss" eine Bezeichnung angeben.'); return; }
  await fetch('/api/pkw/zuschuesse', { method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ vehicle_id: parseInt(vehicleId), kategorie, betrag, versteuert, bezeichnung }) });
  _toast('Zuschuss hinzugefügt');
  loadZuschuesse(vehicleId);
}

async function deleteZuschuss(id, vehicleId) {
  if (!confirm('Zuschuss löschen?')) return;
  await fetch('/api/pkw/zuschuesse/' + id, { method:'DELETE' });
  loadZuschuesse(vehicleId);
}

// Loxone-Integrationsweg: A = Direktabfrage des Bausteins, B = Log-Tracker-Datei
function setLoxoneWeg(weg) {
  const isA = weg === 'a';
  document.getElementById('lox-weg-a')?.classList.toggle('on', isA);
  document.getElementById('lox-weg-b')?.classList.toggle('on', !isA);
  const aHint = document.getElementById('lox-weg-a-hint');
  const bHint = document.getElementById('lox-weg-b-hint');
  if (aHint) aHint.style.display = isA ? 'block' : 'none';
  if (bHint) bHint.style.display = isA ? 'none' : 'block';
  // Bei Weg B ist die UUID-Abfrage nicht nötig (Daten kommen aus der Logdatei)
  const uuidWrap = document.getElementById('wb-loxone-uuid')?.closest('div')?.parentElement;
  const structWrap = document.getElementById('wb-loxone-structure-wrap');
  if (structWrap && !isA) structWrap.style.display = 'none';
}

// ─── Fahrt-Erfassung gezielt öffnen (entkoppelt vom Router) ──────────────────
// Ersetzt den früheren Hack (showView + setTimeout + DOM-Klick), der ein
// Toggle auslöste und dadurch Flackern/Doppelrender verursachte.
function openTripDialog() {
  showView('fahrten');
  const f = document.getElementById('new-trip-form');
  if (!f) return;
  if (f.style.display === 'block') {           // schon offen: nur fokussieren
    f.scrollIntoView({ behavior: 'smooth', block: 'center' });
    return;
  }
  f.style.display = 'block';                    // gezielt öffnen, nicht togglen
  try {
    resetTripForm();
    _initTripDatePicker();
    loadPersonsIntoTripForm();
  } catch (e) { /* Formular bleibt nutzbar, auch wenn ein Teil fehlschlägt */ }
  f.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

// Beleg-Ansicht mit vorgewähltem Typ öffnen (ersetzt setTimeout-Hacks)
function openBelegDialog(typ) {
  showView('belege');
  const s = document.getElementById('beleg-typ');
  if (s) { s.value = typ; onBelegTypChange(); }
}

// ─── Schnellfilter "Auswertung Stromkosten" ─────────────────────────────────
// Nutzt dieselbe Zeitraumlogik wie das Fahrten-Modul, damit sich beide
// Filterleisten identisch verhalten (Sprint 2, Punkt 2.1).
function _periodRange(period) {
  const now = new Date();
  const y = now.getFullYear();
  const m = now.getMonth();                       // 0-indexiert
  const pad = n => String(n).padStart(2, '0');
  switch (period) {
    case 'all':       return ['', ''];
    case 'thismonth': return [`${y}-${pad(m+1)}-01`,
                              `${y}-${pad(m+1)}-${pad(new Date(y, m+1, 0).getDate())}`];
    case 'lastmonth': {
      const lm = m === 0 ? 12 : m, ly = m === 0 ? y - 1 : y;
      return [`${ly}-${pad(lm)}-01`, `${ly}-${pad(lm)}-${pad(new Date(ly, lm, 0).getDate())}`];
    }
    case 'q1':       return [`${y}-01-01`, `${y}-03-31`];
    case 'q2':       return [`${y}-04-01`, `${y}-06-30`];
    case 'q3':       return [`${y}-07-01`, `${y}-09-30`];
    case 'q4':       return [`${y}-10-01`, `${y}-12-31`];
    case 'thisyear': return [`${y}-01-01`, `${y}-12-31`];
    default:         return null;                 // 'custom': Nutzerwerte behalten
  }
}

function setAuswQuickFilter(period, btn) {
  if (btn) {
    document.querySelectorAll('.ausw-qf').forEach(b => b.classList.remove('on'));
    btn.classList.add('on');
  }
  if (period === 'custom') {
    // Nutzer hat Von/Bis manuell gesetzt: Schnellfilter-Markierung aufheben
    document.querySelectorAll('.ausw-qf').forEach(b => b.classList.remove('on'));
    return;
  }
  const range = _periodRange(period);
  if (!range) return;
  const vonEl = document.getElementById('ausw-von');
  const bisEl = document.getElementById('ausw-bis');
  if (!vonEl || !bisEl) return;
  [vonEl.value, bisEl.value] = range;
  loadAnalytics();
}

// Blendet das Badge "Empfohlene Option" auf genau einer Karte einer Gruppe ein
function _setKonfBadge(kartenIds, aktiveId) {
  kartenIds.forEach(id => {
    const badge = document.getElementById('badge-' + id);
    if (badge) badge.style.display = (id === aktiveId) ? 'block' : 'none';
  });
}

// ─── Aufgabenübersicht: Filter nach Status, Sprint und Stichwort ────────────
let _roadStatusFilter = 'all';

function setRoadFilter(status, btn) {
  _roadStatusFilter = status;
  if (btn) {
    document.querySelectorAll('.road-qf').forEach(b => b.classList.remove('on'));
    btn.classList.add('on');
  }
  applyRoadFilter();
}

function applyRoadFilter() {
  const rows = Array.from(document.querySelectorAll('.road-row'));
  if (!rows.length) return;
  const sprint = document.getElementById('road-sprint')?.value || '';
  const suche = (document.getElementById('road-search')?.value || '').toLowerCase().trim();

  let sichtbar = 0;
  rows.forEach(r => {
    const st = r.dataset.status;
    // "offen" umfasst auch "geplant" — beides ist noch nicht umgesetzt
    const statusOk = _roadStatusFilter === 'all'
      || (_roadStatusFilter === 'offen' && (st === 'offen' || st === 'geplant'))
      || st === _roadStatusFilter;
    const sprintOk = !sprint || r.dataset.sprint === sprint;
    const sucheOk = !suche || (r.dataset.text || '').includes(suche);
    const zeigen = statusOk && sprintOk && sucheOk;
    r.style.display = zeigen ? '' : 'none';
    if (zeigen) sichtbar++;
  });

  const cnt = document.getElementById('road-count');
  if (cnt) cnt.textContent = `${sichtbar} von ${rows.length} Aufgaben`;
  const leer = document.getElementById('road-empty');
  if (leer) leer.style.display = sichtbar === 0 ? 'block' : 'none';
}


// ─── Marktpreise laden (hinterlegte Referenzwerte, jederzeit überschreibbar) ─
async function ladeMarktpreise() {
  const btn = document.getElementById('btn-marktpreise');
  const info = document.getElementById('marktpreise-info');
  if (btn) { btn.disabled = true; }
  if (info) info.textContent = 'Lade …';
  try {
    const d = await (await fetch('/api/marktpreise')).json();
    const p = d.preise || {};
    const setzen = (id, wert) => {
      const el = document.getElementById(id);
      if (el && wert != null) el.value = wert;
    };
    // Strom: Neuvertrag als Heimtarif, DC-Preis für unterwegs
    setzen('k-preis-heim', p.strom_neuvertrag?.wert);
    setzen('k-preis-unterwegs', p.strom_dc?.wert);
    // Kraftstoff nur, wenn der Verbrenner-Block aktiv ist
    const antrieb = document.getElementById('k-antrieb')?.value;
    if (antrieb === 'verbrenner') setzen('k-preis-liter', p.benzin_e10?.wert);

    if (info) {
      info.innerHTML = `<b>Referenzwerte</b> übernommen · Strom ${fmtDe(p.strom_neuvertrag?.wert ?? 0,2)} / `
        + `DC ${fmtDe(p.strom_dc?.wert ?? 0,2)} €/kWh · Diesel ${fmtDe(p.diesel?.wert ?? 0,2)} / `
        + `E10 ${fmtDe(p.benzin_e10?.wert ?? 0,2)} €/l`;
      info.title = d.hinweis || '';
    }
    recalcKonf();
    _toast('Marktpreise übernommen');
  } catch (e) {
    if (info) info.textContent = 'Preise konnten nicht geladen werden.';
  } finally {
    if (btn) btn.disabled = false;
  }
}


// ═══════════════════════════════════════════════════════════════════════════
// STEUER-INFO-DRAWER: verbindliche Einordnung der vier Abrechnungsmodelle
// ═══════════════════════════════════════════════════════════════════════════
const STEUER_INFOS = {
  c1: {
    titel: 'C1 · Privat-Pkw mit Pauschale',
    kurz: '0,30 €/km gesetzliche Pauschale — kein Fahrtenbuch nötig',
    weg: [
      'Der Arbeitgeber erstattet dienstliche Fahrten steuerfrei (§ 3 Nr. 50 EStG), üblich sind 0,15 €/km.',
      'Die Differenz zur gesetzlichen Pauschale von 0,30 €/km machst du in der Steuererklärung als Werbungskosten geltend (Anlage N).',
      'Zahlt der Arbeitgeber nichts, sind die vollen 0,30 €/km abzugsfähig.',
    ],
    pflichten: [
      'Kein Fahrtenbuch erforderlich',
      'Formlose Aufstellung der Fahrten genügt (Datum, Ziel, Anlass, Kilometer)',
      'Belege für die Arbeitgeber-Erstattung aufbewahren',
    ],
    passt: 'Wenn dein tatsächlicher Kilometersatz unter 0,30 € liegt oder du den Fahrtenbuch-Aufwand vermeiden willst.',
    achtung: 'Werbungskosten mindern nur das zu versteuernde Einkommen — zurück kommt der Grenzsteuersatz, nicht der volle Betrag.',
    paragraf: '§ 9 Abs. 1 Satz 3 Nr. 4a EStG · § 3 Nr. 50 EStG',
  },
  c2: {
    titel: 'C2 · Privat-Pkw mit Vollkosten',
    kurz: 'Individueller Kilometersatz — Fahrtenbuch zwingend',
    weg: [
      'Du ermittelst deinen echten Kilometersatz: Jahres-Gesamtkosten geteilt durch die Jahresfahrleistung.',
      'Davon ziehst du die Arbeitgeber-Erstattung ab; der Rest zählt als Werbungskosten.',
      'Lohnt sich, sobald der echte Satz über 0,30 €/km liegt — typisch bei hohen Fixkosten und geringer Fahrleistung.',
    ],
    pflichten: [
      'Lückenloses Fahrtenbuch über mindestens 12 Monate',
      'Vollständige Belegsammlung: Leasing/AfA, Versicherung, Steuer, Wartung, Reifen, Ladestrom',
      'Nachvollziehbare Trennung dienstlich / privat / Arbeitsweg',
    ],
    passt: 'Bei teuren Fahrzeugen, hohem Dienstanteil und der Bereitschaft, ein Fahrtenbuch zu führen.',
    achtung: 'Ein unvollständiges Fahrtenbuch wird vom Finanzamt komplett verworfen — dann greift nur noch die Pauschale.',
    paragraf: 'R 9.5 LStR · § 9 Abs. 1 EStG',
  },
  a: {
    titel: 'A · Firmenwagen mit Pauschalversteuerung',
    kurz: '1 % / 0,5 % / 0,25 % des Bruttolistenpreises — kein Fahrtenbuch',
    weg: [
      'Der geldwerte Vorteil wird pauschal versteuert: 1 % des Bruttolistenpreises pro Monat.',
      'Bei reinen Elektrofahrzeugen bis 95.000 € Bruttolistenpreis nur 0,25 %, darüber 0,5 %.',
      'Zusätzlich 0,03 % des Bruttolistenpreises je Entfernungskilometer zur ersten Tätigkeitsstätte.',
      'Sämtliche Kosten inklusive Ladestrom trägt der Arbeitgeber.',
    ],
    pflichten: [
      'Kein Fahrtenbuch erforderlich',
      'Entfernung zur ersten Tätigkeitsstätte korrekt angeben',
      'Zuzahlungen zur Leasingrate mindern den geldwerten Vorteil — Belege aufbewahren',
    ],
    passt: 'Bei niedrigem Bruttolistenpreis, hoher Privatnutzung und Wunsch nach null Bürokratie.',
    achtung: 'Der geldwerte Vorteil fällt auch an, wenn du das Auto privat kaum nutzt — die Pauschale fragt nicht nach.',
    paragraf: '§ 6 Abs. 1 Nr. 4 Satz 2 EStG',
  },
  b: {
    titel: 'B · Firmenwagen mit Fahrtenbuch',
    kurz: 'Versteuerung des tatsächlichen Privatanteils — Fahrtenbuch zwingend',
    weg: [
      'Statt der Pauschale wird der echte private Nutzungsanteil versteuert.',
      'Berechnung: Gesamtkosten des Dienstwagens × Anteil der Privatfahrten (inkl. Arbeitsweg).',
      'Lohnt sich vor allem bei hohem Bruttolistenpreis und überwiegend dienstlicher Nutzung.',
    ],
    pflichten: [
      'Lückenloses Fahrtenbuch — zwingend, ohne Ausnahme',
      'Alle Fahrzeugkosten des Arbeitgebers müssen belegt sein',
      'Methodenwechsel nur zum Jahreswechsel oder Fahrzeugwechsel möglich',
    ],
    passt: 'Bei Dienstquote über etwa 60 % und hohem Bruttolistenpreis.',
    achtung: 'Wird das Fahrtenbuch verworfen, versteuert das Finanzamt rückwirkend nach der 1-%-Regel.',
    paragraf: '§ 8 Abs. 2 Satz 4 EStG · R 8.1 Abs. 9 Nr. 2 LStR',
  },
};

function openSteuerInfo(modell) {
  const info = STEUER_INFOS[modell];
  if (!info) return;
  const liste = arr => arr.map(x => `<li style="margin-bottom:5px;">${x}</li>`).join('');
  document.getElementById('drawer-titel').textContent = info.titel;
  document.getElementById('drawer-kurz').textContent = info.kurz;
  document.getElementById('drawer-body').innerHTML = `
    <div class="drawer-block">
      <div class="drawer-h">Steuerlicher Weg</div>
      <ul class="drawer-ul">${liste(info.weg)}</ul>
    </div>
    <div class="drawer-block">
      <div class="drawer-h">Deine Pflichten</div>
      <ul class="drawer-ul">${liste(info.pflichten)}</ul>
    </div>
    <div class="drawer-block">
      <div class="drawer-h">Wann passt das?</div>
      <div style="font-size:13px; line-height:1.6; color:var(--text-secondary);">${info.passt}</div>
    </div>
    <div class="drawer-block" style="background:rgba(234,179,8,.08); border-left:3px solid var(--warning,#eab308); padding:10px 12px; border-radius:6px;">
      <div class="drawer-h" style="color:var(--warning,#eab308);">Worauf du achten musst</div>
      <div style="font-size:13px; line-height:1.6; color:var(--text-secondary);">${info.achtung}</div>
    </div>
    <div style="font-size:11px; color:var(--text-tertiary); margin-top:14px; padding-top:10px; border-top:1px solid var(--border);">
      Rechtsgrundlage: ${info.paragraf}<br>
      Keine Steuerberatung — die Anerkennung obliegt im Einzelfall dem Finanzamt.
    </div>`;
  document.getElementById('steuer-drawer').classList.add('open');
}

function closeSteuerInfo() {
  document.getElementById('steuer-drawer').classList.remove('open');
}

// ─── Livewerte in der Wallbox-Karte (protokollunabhängig einheitlich) ──────
// Jede Wallbox zeigt denselben Satz Kennzahlen — egal ob die Daten per OCPP
// oder über die Loxone-API hereinkommen. Fehlt ein Wert bei einer Quelle,
// steht dort "–" statt die Zeile wegzulassen: So sind die Karten vergleichbar
// und man sieht sofort, welche Angabe die jeweilige Wallbox nicht liefert.
function _renderLiveMetrics(wb) {
  const m = wb.live_metrics || {};
  const strich = '<span style="color:var(--text-tertiary);">–</span>';

  // Leistung (+ Phasenströme, sofern die Quelle sie meldet)
  let leistung = strich;
  if (m.power_kw != null && m.power_kw > 0) {
    const phasen = [m.current_l1_a, m.current_l2_a, m.current_l3_a].filter(a => a != null && a > 0);
    const ampText = phasen.length ? ` (${phasen.map(a => fmtDe(a, 0)).join('/')} A)` : '';
    leistung = `${fmtDe(m.power_kw, 2)} kW${ampText}`;
  } else if (m.power_kw === 0) {
    leistung = '0,00 kW';
  }

  const heute = (m.tagesenergie_kwh != null)
    ? `${fmtDe(m.tagesenergie_kwh, 2)} kWh` : strich;
  const peak = (m.peak_power_kw != null && m.peak_power_kw > 0)
    ? `${fmtDe(m.peak_power_kw, 2)} kW` : strich;
  const zaehler = (m.meter_total_wh != null && m.meter_total_wh > 0)
    ? `${fmtDe(m.meter_total_wh / 1000, 2)} kWh`
      + (m.meter_total_hergeleitet ? '<span style="color:var(--text-tertiary);" title="aus der letzten Session abgeleitet"> *</span>' : '')
    : strich;

  return [
    ['Leistung', leistung],
    ['Heute geladen', heute],
    ['Peak', peak],
    ['Zählerstand', zaehler],
  ].map(([label, wert]) =>
    `<div class="wb-card-live-row"><span>${label}</span>` +
    `<span class="wb-card-live-val">${wert}</span></div>`).join('');
}

// ─── Unverarbeitete Fahrten: 1-Klick-Zuweisung ─────────────────────────────
// ─── Hinweis auf noch nicht zugeordnete Fahrten (Dashboard) ────────────────
// Die Bearbeitung erfolgt in der normalen Fahrtenliste; hier steht nur der
// Hinweis mit Direktsprung dorthin.
async function loadBmwTrips() {
  const card = document.getElementById('bmw-hinweis-card');
  if (!card) return;
  try {
    const d = await (await hole('/api/trips')).json();
    const offen = (d.trips || []).filter(t => (t.fahrtart || '') === 'offen');
    if (!offen.length) { card.style.display = 'none'; return; }
    card.style.display = 'block';
    const zaehler = document.getElementById('bmw-offen-count');
    if (zaehler) zaehler.textContent = offen.length;
    const km = offen.reduce((s, t) => s + (t.distance_km || 0), 0);
    const box = document.getElementById('bmw-trips-box');
    if (box) {
      box.innerHTML = `<div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
        <span class="hint" style="margin:0;">
          ${fmtDe(km, 0)} km warten auf die Zuordnung — dienstliche Fahrten werden dadurch abrechenbar.
        </span>
        <button class="btn btn-sm btn-primary" style="margin-left:auto;"
                onclick="zuOffenenFahrten()">Jetzt zuordnen →</button>
      </div>`;
    }
    const vor = document.getElementById('bmw-vorauswahl');
    if (vor) vor.innerHTML = '';
  } catch (e) {}
}

function zuOffenenFahrten() {
  showView('fahrten');
  // Filter direkt auf die offenen Fahrten setzen, damit der Nutzer nicht sucht
  setTimeout(() => {
    const f = document.getElementById('trip-filter-art');
    if (f) { f.value = 'offen'; loadTrips(); }
  }, 250);
}

// ─── Lizenz: Verbrauch, Aktivierung, Payhip-Konfiguration (Sprint 7) ───────



// ─── Datenprüfung: Zombie-Sessions und Zählerüberlauf (FA-COMP-02/03) ──────
async function ladeDatenpruefung() {
  const box = document.getElementById('pruefung-ergebnis');
  if (!box) return;
  box.innerHTML = '<div class="hint">Prüfe Datenbestand …</div>';
  try {
    const d = await (await fetch('/api/compliance/pruefung')).json();
    if (!d.anzahl_gesamt) {
      box.innerHTML = '<div style="color:var(--success); font-size:13px;">'
        + '✓ Keine Auffälligkeiten gefunden.</div>'
        + `<div class="hint" style="margin-top:4px;">Geprüft am ${d.geprueft_am}</div>`;
      return;
    }
    const zeilen = [];
    (d.zombies || []).forEach(z => {
      zeilen.push(`<div class="pruef-row">
        <div style="flex:1;">
          <div style="font-size:13px; font-weight:600;">Session ${z.id} · seit ${fmtDe(z.offen_stunden,0)} h offen</div>
          <div style="font-size:11px; color:var(--text-tertiary);">${z.wallbox_name || '—'} · Start ${(z.start_timestamp||'').slice(0,16)}</div>
        </div>
        <button class="btn btn-sm" onclick="pruefAktion(${z.id},'schliessen')">Schließen</button>
        <button class="btn btn-sm" onclick="pruefAktion(${z.id},'markieren')">Markieren</button>
      </div>`);
    });
    (d.zaehler_anomalien || []).forEach(a => {
      const korr = a.korrektur_vorschlag_wh
        ? `<button class="btn btn-sm btn-primary" onclick="pruefAktion(${a.id},'korrigieren',${a.korrektur_vorschlag_wh})">Auf ${fmtDe(a.korrektur_vorschlag_wh/1000,1)} kWh korrigieren</button>`
        : '';
      zeilen.push(`<div class="pruef-row">
        <div style="flex:1;">
          <div style="font-size:13px; font-weight:600;">Session ${a.id} · Zählerstand auffällig</div>
          <div style="font-size:11px; color:var(--text-tertiary);">${a.beschreibung}</div>
        </div>
        ${korr}
        <button class="btn btn-sm" onclick="pruefAktion(${a.id},'markieren')">Markieren</button>
      </div>`);
    });
    box.innerHTML = `<div style="color:var(--warning,#eab308); font-size:13px; margin-bottom:8px;">`
      + `${d.anzahl_gesamt} ${d.anzahl_gesamt===1?'auffälliger Datensatz':'auffällige Datensätze'} gefunden</div>`
      + zeilen.join('');
  } catch (e) {
    box.innerHTML = '<div class="hint">Prüfung fehlgeschlagen.</div>';
  }
}

async function pruefAktion(sessionId, aktion, korrekturWh) {
  const body = { aktion };
  if (korrekturWh) body.korrektur_wh = korrekturWh;
  try {
    await fetch(`/api/compliance/session/${sessionId}/aktion`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
    _toast('Datensatz bearbeitet');
    ladeDatenpruefung();
  } catch (e) {}
}

// ═══════════════════════════════════════════════════════════════════════════
// BMW CARDATA — Anmeldung und Fahrtenabruf (offizielle BMW-Schnittstelle)
// ═══════════════════════════════════════════════════════════════════════════
let _cardataPolling = null;

async function cardataStatusLaden() {
  try {
    const d = await (await hole('/api/cardata/status')).json();
    const box = document.getElementById('cardata-status-box');
    const anmeldung = document.getElementById('cardata-anmeldung');
    const betrieb = document.getElementById('cardata-betrieb');
    if (!box) return;

    if (d.angemeldet) {
      const warnung = d.ablauf_droht
        ? `<div style="color:var(--warning,#eab308); margin-top:6px;">Die Anmeldung läuft in Kürze ab `
          + `(seit ${d.tage_seit_erneuerung} Tagen nicht erneuert). Ein Abruf erneuert sie automatisch.</div>`
        : '';
      box.innerHTML = `<span style="color:var(--success);">●</span> <b>Verbunden</b>`
        + (d.vin ? ` · Fahrzeug ${d.vin}` : ' · noch kein Fahrzeug gewählt')
        + (d.letzter_abruf ? `<br><span style="color:var(--text-tertiary);">Letzter Abruf: ${d.letzter_abruf}</span>` : '')
        + (d.auto ? `<br><span style="color:var(--text-tertiary);">Automatik: alle ${d.intervall_min} Minuten (${d.abrufe_pro_tag} Abrufe/Tag)</span>` : '')
        + warnung;
      if (anmeldung) anmeldung.style.display = 'none';
      if (betrieb) betrieb.style.display = 'block';
      const iv = document.getElementById('cardata-intervall');
      if (iv) iv.value = d.auto ? String(d.intervall_min) : '0';
      const vinFeld = document.getElementById('cardata-vin');
      if (vinFeld && d.vin && !vinFeld.value) vinFeld.value = d.vin;
    } else {
      box.innerHTML = '<span style="color:var(--text-tertiary);">●</span> Nicht verbunden — '
        + 'bitte mit der Client-ID aus dem BMW-Portal anmelden.';
      if (anmeldung) anmeldung.style.display = 'block';
      if (betrieb) betrieb.style.display = 'none';
    }
  } catch (e) {}
}

async function cardataAnmelden() {
  const btn = document.getElementById('cardata-anmelden-btn');
  const clientId = document.getElementById('cardata-client-id').value.trim();
  if (!clientId) { _toast('Bitte die Client-ID eintragen'); return; }
  if (btn) btn.disabled = true;
  try {
    const d = await (await fetch('/api/cardata/anmelden', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ client_id: clientId,
        mit_streaming: !!document.getElementById('cardata-mit-stream')?.checked })
    })).json();
    if (!d.ok) { _toast(d.meldung || 'Anmeldung fehlgeschlagen'); return; }

    document.getElementById('cardata-bestaetigung').style.display = 'block';
    document.getElementById('cardata-usercode').textContent = d.user_code || '–';
    const link = document.getElementById('cardata-link');
    if (link) link.href = d.verification_uri_complete || d.verification_uri || '#';
    // Der Nutzer bestätigt jetzt im Browser; wir fragen in Abständen nach,
    // bis BMW die Tokens herausgibt oder der Code abläuft.
    _cardataWartenAufBestaetigung(d.interval || 5, d.expires_in || 600);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function _cardataWartenAufBestaetigung(intervallSek, gueltigSek) {
  if (_cardataPolling) clearInterval(_cardataPolling);
  const ende = Date.now() + gueltigSek * 1000;
  const text = document.getElementById('cardata-warte-text');

  _cardataPolling = setInterval(async () => {
    if (Date.now() > ende) {
      clearInterval(_cardataPolling); _cardataPolling = null;
      if (text) text.innerHTML = '<span style="color:var(--danger);">Code abgelaufen — bitte erneut anmelden.</span>';
      return;
    }
    try {
      const d = await (await fetch('/api/cardata/tokens', { method:'POST' })).json();
      if (d.ok) {
        clearInterval(_cardataPolling); _cardataPolling = null;
        document.getElementById('cardata-bestaetigung').style.display = 'none';
        _toast('Mit BMW CarData verbunden');
        cardataStatusLaden();
        cardataFahrzeugeLaden();
      } else if (!d.wartet && text) {
        text.innerHTML = `<span style="color:var(--danger);">${d.meldung || 'Fehlgeschlagen.'}</span>`;
      } else if (text) {
        const rest = Math.round((ende - Date.now()) / 1000);
        text.textContent = `Warte auf Bestätigung … (noch ${rest} Sekunden gültig)`;
      }
    } catch (e) {}
  }, Math.max(3, intervallSek) * 1000);
}

async function cardataFahrzeugeLaden() {
  // Optionaler Komfort: Fahrzeuge aus dem Konto holen. Schlaegt das fehl,
  // ist das unkritisch — die VIN lässt sich auch von Hand eintragen.
  const box = document.getElementById('cardata-vin-liste');
  try {
    const d = await (await fetch('/api/cardata/fahrzeuge')).json();
    if (!d.ok) {
      if (box) {
        box.style.display = 'block';
        box.innerHTML = `<span style="color:var(--warning,#eab308); font-size:12px;">`
          + `${d.meldung || 'Fahrzeuge konnten nicht geladen werden.'} `
          + `Trage die Fahrgestellnummer bitte von Hand ein.</span>`;
      }
      return;
    }
    const liste = d.fahrzeuge || [];
    if (!liste.length) {
      if (box) {
        box.style.display = 'block';
        box.innerHTML = '<span style="color:var(--warning,#eab308); font-size:12px;">'
          + 'Keine Fahrzeuge im Konto gefunden — bitte VIN von Hand eintragen.</span>';
      }
      return;
    }
    if (liste.length === 1) {
      document.getElementById('cardata-vin').value = liste[0].vin;
      cardataVinSpeichern();
      if (box) box.style.display = 'none';
      return;
    }
    if (box) {
      box.style.display = 'block';
      box.innerHTML = '<div style="font-size:12px; margin-bottom:4px;">Gefundene Fahrzeuge:</div>'
        + liste.map(f => `<button class="btn btn-sm" style="margin:0 6px 6px 0;"
             onclick="document.getElementById('cardata-vin').value='${f.vin}'; cardataVinSpeichern();">`
             + `${f.vin}${f.typ ? ' · ' + f.typ : ''}</button>`).join('');
    }
  } catch (e) {
    if (box) {
      box.style.display = 'block';
      box.innerHTML = '<span style="color:var(--warning,#eab308); font-size:12px;">'
        + 'Abruf fehlgeschlagen — bitte VIN von Hand eintragen.</span>';
    }
  }
}

async function cardataVinSpeichern() {
  const feld = document.getElementById('cardata-vin');
  const vin = (feld.value || '').trim().toUpperCase();
  feld.value = vin;
  if (!vin) return;
  if (vin.length !== 17) {
    _toast('Eine Fahrgestellnummer hat genau 17 Zeichen');
    return;
  }
  await fetch('/api/cardata/vin', { method:'POST',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify({ vin }) });
  _toast('Fahrzeug gespeichert');
  cardataStatusLaden();
}

async function cardataAutomatikSpeichern() {
  const wert = parseInt(document.getElementById('cardata-intervall').value, 10);
  const d = await (await fetch('/api/cardata/automatik', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ aktiv: wert > 0, intervall_min: wert || 30 })
  })).json();
  _toast(d.aktiv ? `Automatik aktiv: alle ${d.intervall_min} Minuten` : 'Automatik ausgeschaltet');
  cardataStatusLaden();
}

async function cardataAbrufen(ausFahrten = false) {
  if (bmwGesperrtHinweis('fahrten')) return;
  const btn = document.getElementById('cardata-abruf-btn');
  const out = document.getElementById(
    ausFahrten ? 'trips-import-ergebnis' : 'cardata-abruf-ergebnis');
  if (btn) btn.disabled = true;
  if (out) out.textContent = 'Rufe Fahrzeugdaten ab …';
  try {
    const d = await (await fetch('/api/cardata/abrufen', { method:'POST' })).json();
    if (!d.ok) {
      if (out) out.innerHTML = `<span style="color:var(--danger);">✕</span> ${d.meldung || 'Abruf fehlgeschlagen.'}`;
      return;
    }
    if (d.fahrt_erkannt) {
      if (out) out.innerHTML = `<span style="color:var(--success);">✓</span> Fahrt über `
        + `${fmtDe(d.distanz_km, 1)} km erkannt — im Dashboard zuordnen.`;
      _toast('Neue Fahrt erkannt');
      loadBmwTrips();
      if (ausFahrten) loadTrips();
    } else {
      if (out) out.innerHTML = `<span style="color:var(--text-tertiary);">●</span> ${d.meldung || 'Keine neue Fahrt.'}`
        + (d.km ? ` <span style="color:var(--text-tertiary);">(Kilometerstand ${fmtDe(d.km, 0)} km)</span>` : '');
    }
    cardataStatusLaden();
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function cardataAbmelden() {
  if (!confirm('Verbindung zu BMW CarData trennen?')) return;
  await fetch('/api/cardata/abmelden', { method:'POST' });
  _toast('Verbindung getrennt');
  cardataStatusLaden();
}

// ─── Fahrten aus dem BMW-Datenarchiv einlesen ──────────────────────────────
async function cardataArchivImport(ausFahrten = false) {
  const feld = document.getElementById(ausFahrten ? 'trips-archiv-datei' : 'cardata-archiv-datei');
  const btn = document.getElementById(ausFahrten ? 'trips-archiv-btn' : 'cardata-archiv-btn');
  const out = document.getElementById(ausFahrten ? 'trips-import-ergebnis' : 'cardata-archiv-ergebnis');
  if (!feld || !feld.files.length) { _toast('Bitte zuerst das ZIP-Archiv auswählen'); return; }

  const daten = new FormData();
  daten.append('file', feld.files[0]);
  if (btn) btn.disabled = true;
  if (out) out.innerHTML = '<div class="hint">Lese Archiv …</div>';
  try {
    const d = await (await fetch('/api/cardata/archiv-import', {
      method: 'POST', body: daten })).json();
    if (!d.ok) {
      if (out) out.innerHTML = `<div style="color:var(--danger); font-size:13px;">✕ ${d.meldung || 'Import fehlgeschlagen.'}</div>`;
      return;
    }
    if (!d.gefunden) {
      if (out) out.innerHTML = `<div class="hint">${d.meldung || 'Keine Fahrten gefunden.'}</div>`;
      return;
    }
    const vorschau = (d.vorschau || []).map(f =>
      `<div style="padding:6px 0; border-top:1px solid var(--border); font-size:12px;">
         <b>${fmtDatum(f.start_time)}</b> · ${fmtDe(f.distance_km, 0)} km<br>
         <span style="color:var(--text-tertiary);">${f.start_address} → ${f.end_address}</span>
       </div>`).join('');
    out.innerHTML = `
      <div style="color:var(--success); font-size:13px; font-weight:600; margin-bottom:6px;">
        ✓ ${d.neu} neue Fahrten übernommen
      </div>
      <div class="hint">
        ${d.gefunden} Fahrten aus ${d.ladevorgaenge} Ladevorgängen abgeleitet
        (${fmtDe(d.km_gesamt, 0)} km, ${d.zeitraum})${d.uebersprungen ? ` · ${d.uebersprungen} bereits bekannt` : ''}.
        ${d.neu ? 'Sie warten jetzt im Dashboard auf die Zuordnung.' : ''}
      </div>
      ${vorschau ? '<div style="margin-top:8px;">' + vorschau + '</div>' : ''}`;
    if (d.neu) {
      _toast(`${d.neu} Fahrten importiert`);
      loadBmwTrips();
      if (ausFahrten) loadTrips();
    }
  } catch (e) {
    if (out) out.innerHTML = '<div style="color:var(--danger); font-size:13px;">✕ Import fehlgeschlagen.</div>';
  } finally {
    if (btn) btn.disabled = false;
  }
}


// ─── Sammelaktionen für ausgewählte Fahrten ────────────────────────────────
async function sammelFahrtart(art) {
  const ids = Array.from(_selectedTrips);
  if (!ids.length) return;
  const d = await (await fetch('/api/trips/sammel-fahrtart', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ trip_ids: ids, fahrtart: art })
  })).json();
  if (d.ok) {
    _toast(art === 'privat'
      ? `${d.anzahl} Fahrt(en) als privat — ohne Erstattung, bleiben im Fahrtenbuch`
      : `${d.anzahl} Fahrt(en) als dienstlich eingestuft`);
    _selectedTrips.clear();
    _updateSelectionBar();
    loadTrips();
    loadDashboardSummary();
  }
}

async function sammelSatz(rate) {
  const ids = Array.from(_selectedTrips);
  if (!ids.length) return;
  const d = await (await fetch('/api/trips/sammel-satz', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ trip_ids: ids, rate })
  })).json();
  if (d.ok) {
    // Private Fahrten werden bewusst übersprungen — dort wäre ein
    // Erstattungssatz steuerlich unzulässig.
    const hinweis = d.uebersprungen
      ? ` (${d.uebersprungen} private Fahrt(en) unverändert)` : '';
    _toast(`${d.anzahl} Fahrt(en) auf ${rate === 0 ? 'keine Erstattung' : fmtDe(rate,2) + ' €/km'} gesetzt${hinweis}`);
    _selectedTrips.clear();
    _updateSelectionBar();
    loadTrips();
    loadDashboardSummary();
  }
}

// ─── Fahrzeugdaten aus dem Bordcomputer anzeigen ───────────────────────────
async function cardataFahrzeugdatenLaden() {
  const card = document.getElementById('cardata-fzg-card');
  const box = document.getElementById('cardata-fzg-werte');
  if (!card || !box) return;
  try {
    const d = await (await fetch('/api/cardata/fahrzeugdaten')).json();
    // Nur anzeigen, wenn tatsächlich Werte vorliegen
    const hatWerte = ['km','verbrauch_kwh_100','akku_max_kwh','soc_prozent',
                      'reichweite_km','service_in_km'].some(k => d[k] != null);
    if (!hatWerte) { card.style.display = 'none'; return; }
    card.style.display = 'block';

    const kachel = (label, wert, einheit, hinweis) => wert == null ? '' : `
      <div style="padding:10px 12px; background:var(--bg-input); border-radius:6px;">
        <div style="font-size:10px; color:var(--text-tertiary); text-transform:uppercase;
             letter-spacing:.04em;">${label}</div>
        <div style="font-size:17px; font-weight:700; margin-top:2px;">${wert}${einheit ? ' ' + einheit : ''}</div>
        ${hinweis ? `<div style="font-size:10px; color:var(--text-tertiary);">${hinweis}</div>` : ''}
      </div>`;

    box.innerHTML =
        kachel('Kilometerstand', d.km != null ? fmtDe(d.km, 0) : null, 'km')
      + kachel('Ø Verbrauch', d.verbrauch_kwh_100 != null ? fmtDe(d.verbrauch_kwh_100, 1) : null,
               'kWh/100 km', 'echter Wert statt Schätzung')
      + kachel('Ladestand', d.soc_prozent != null ? fmtDe(d.soc_prozent, 0) : null, '%')
      + kachel('Reichweite', d.reichweite_km != null ? fmtDe(d.reichweite_km, 0) : null, 'km')
      + kachel('Akkukapazität', d.akku_max_kwh != null ? fmtDe(d.akku_max_kwh, 1) : null, 'kWh')
      + kachel('Akkuzustand', d.akku_soh_prozent != null ? fmtDe(d.akku_soh_prozent, 0) : null,
               '%', 'State of Health')
      + kachel('Nächster Service', d.service_in_km != null ? fmtDe(d.service_in_km, 0) : null, 'km')
      + kachel('Ø pro Woche', d.woche_km != null ? fmtDe(d.woche_km, 0) : null, 'km');

    const stand = document.getElementById('cardata-fzg-stand');
    if (stand && d.stand) stand.textContent = `Stand: ${d.stand}`;
  } catch (e) {}
}

// Sichtbarkeit der BMW-Bedienelemente.
//
// Frühere Regel war: ohne Verbindung ausblenden. Das verschweigt aber, dass
// es die Funktion überhaupt gibt — in der Demo ist genau das der Punkt.
// Neue Regel:
//
//   Vollversion, verbunden      → nutzbar
//   Vollversion, nicht verbunden → sichtbar, führt zur Einrichtung
//   Demo                         → sichtbar mit Pro-Abzeichen, erklärt beim Klick
//
// Ausgeblendet wird nur noch der Dateiauswahl-Bereich für das ZIP-Archiv:
// Ein Dateifeld ohne Verbindung ist keine Werbung, sondern eine Sackgasse.
async function pruefeBmwImportBereich() {
  const knoepfe = ['bmw-lade-btn', 'bmw-fahrt-btn', 'bmw-reset-btn']
    .map(id => document.getElementById(id)).filter(Boolean);
  // Alle drei gleich behandeln: sichtbar mit Pro-Abzeichen. Zuvor war
  // 'BMW holen' bei den Fahrten ausgeblendet, die beiden anderen sichtbar.
  const bereich = document.getElementById('trips-bmw-import');

  let verbunden = false;
  try {
    const d = await (await hole('/api/cardata/status')).json();
    verbunden = !!(d.angemeldet && d.vin);
  } catch (e) { /* ohne Antwort gilt: nicht verbunden */ }

  knoepfe.forEach(el => { el.style.display = 'inline-flex'; });
  if (bereich) bereich.style.display = verbunden ? 'block' : 'none';
}

// Ladevorgänge der letzten 30 Tage übernehmen
async function cardataLadesessions() {
  if (bmwGesperrtHinweis('historie')) return;
  const btn = document.getElementById('bmw-lade-btn') || document.getElementById('cardata-lade-btn');
  const out = document.getElementById('cardata-abruf-ergebnis');
  if (btn) btn.disabled = true;
  if (out) out.textContent = 'Rufe Ladehistorie ab …';
  try {
    const d = await (await fetch('/api/cardata/ladesessions', { method:'POST' })).json();
    if (!d.ok) {
      if (out) out.innerHTML = `<span style="color:var(--danger);">✕</span> ${d.meldung || 'Abruf fehlgeschlagen.'}`;
      return;
    }
    // Aufschlüsselung zeigen: Wenn nichts übernommen wurde, ist der Grund
    // die eigentliche Information.
    const gruende = [];
    if (d.bereits_da)   gruende.push(`${d.bereits_da} bereits vorhanden`);
    if (d.doppelt)      gruende.push(`${d.doppelt} von deiner Wallbox bereits erfasst`);
    if (d.ohne_energie) gruende.push(`${d.ohne_energie} ohne Energiefluss (nur eingesteckt)`);
    if (d.ohne_zeit)    gruende.push(`${d.ohne_zeit} ohne Zeitstempel`);
    const details = gruende.length
      ? `<div class="hint" style="margin-top:4px;">${gruende.join(' · ')}</div>` : '';

    if (out) out.innerHTML = d.neu
      ? `<span style="color:var(--success);">✓</span> ${d.neu} von ${d.gefunden} `
        + `Ladevorgängen übernommen.${details}`
      : `<span style="color:var(--text-tertiary);">●</span> Keine neuen Ladevorgänge `
        + `übernommen (${d.gefunden} empfangen).${details}`;
    if (d.neu) {
      _toast(`${d.neu} Ladevorgänge importiert`);
      if (typeof loadSessions === 'function') loadSessions();
    }

    // Aus denselben Daten lassen sich die Fahrten ableiten — ohne zusätzlichen
    // Abruf vom Tageskontingent, weil die Ladehistorie bereits vorliegt.
    try {
      const f = await (await fetch('/api/cardata/fahrten-aus-ladehistorie',
                                    { method:'POST' })).json();
      if (f.ok && f.neu) {
        if (out) out.innerHTML += `<div class="hint" style="margin-top:4px; color:var(--akz-geld);">`
          + `✓ Zusätzlich ${f.neu} Fahrten abgeleitet (${fmtDe(f.km_gesamt, 0)} km) — `
          + `unter <b>Fahrten</b> zuordnen.</div>`;
        _toast(`${f.neu} Fahrten aus der Ladehistorie abgeleitet`);
      }
    } catch (e) {}

    if (!d.neu) _toast(d.meldung || 'Keine neuen Ladevorgänge');
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ─── Preise für externes Laden ─────────────────────────────────────────────
async function loadLadepreise() {
  try {
    const d = await (await fetch('/api/settings/ladepreise')).json();
    const dc = document.getElementById('lp-dc');
    const ac = document.getElementById('lp-ac');
    const sw = document.getElementById('lp-schwelle');
    if (dc) dc.value = d.dc;
    if (ac) ac.value = d.ac_extern;
    if (sw) sw.textContent = fmtDe(d.dc_schwelle_kw, 0);
  } catch (e) {}
}

async function saveLadepreise() {
  const dc = parseFloat(document.getElementById('lp-dc').value) || 0;
  const ac = parseFloat(document.getElementById('lp-ac').value) || 0;
  await fetch('/api/settings/ladepreise', { method:'POST',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify({ dc, ac_extern: ac }) });
  _toast('Ladepreise gespeichert');
}

// ─── Wohnadresse für die Ladeort-Zuordnung ─────────────────────────────────
async function loadHeimadresse() {
  try {
    const d = await (await fetch('/api/settings/heimadresse')).json();
    const feld = document.getElementById('heim-adresse');
    const info = document.getElementById('heim-erkannt');
    if (feld) feld.value = d.adresse || '';
    if (info) {
      info.textContent = (!d.adresse && d.erkannt)
        ? ` Derzeit erkannt: ${d.erkannt}` : '';
    }
  } catch (e) {}
}

async function saveHeimadresse() {
  const adresse = document.getElementById('heim-adresse').value.trim();
  await fetch('/api/settings/heimadresse', { method:'POST',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify({ adresse }) });
  _toast(adresse ? 'Wohnadresse gespeichert' : 'Wohnadresse zurückgesetzt');
  loadHeimadresse();
}

// ─── Ladeort mehrerer Sessions auf einmal korrigieren ──────────────────────
async function sammelLadeort(ort) {
  const ids = Array.from(_selectedSessions);
  if (!ids.length) return;
  // Der Preis wird beim Wechsel mitgeführt: zuhause der Vertragspreis,
  // unterwegs der eingestellte Fremdtarif. Sonst bliebe eine
  // fälschlich als extern importierte Heimladung beim teuren Satz stehen.
  await Promise.all(ids.map(id => fetch(`/api/sessions/${id}/charging-location`, {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ charging_location: ort, preis_anpassen: true })
  })));
  _toast(`${ids.length} Session(s) als ${ort === 'zuhause' ? 'zuhause' : 'unterwegs'} markiert`);
  _selectedSessions.clear();
  _updateSelectionBar();
  loadSessions();
}

// Kennzahl „Kosten je 100 km" — die ehrlichste Zahl zum Betrieb des Fahrzeugs:
// Was der gefahrene Kilometer an Strom tatsaechlich gekostet hat.
async function ladeKosten100km() {
  const wert = document.getElementById('dash-kosten-100km');
  if (!wert) return;
  try {
    const d = await (await hole('/api/dashboard/summary')).json();
    // Für den Verbrauch zählen ALLE Kilometer — der geladene Strom trägt
    // dienstliche wie private Fahrten. Nur die Dienstkilometer im Nenner
    // ergaben einen deutlich zu hohen Wert.
    const km = d.km_gesamt || d.trip_km || 0;
    const kwh = d.kwh_this_month || 0;
    const kosten = d.cost_this_month || 0;

    // Unter 100 km ist der Wert mathematisch korrekt aber irreführend —
    // wenige Kilometer mit vielen kWh ergeben absurde Zahlen. Erst ab
    // 100 km ist die Basis aussagekräftig.
    if (km >= 100 && kosten > 0) {
      wert.textContent = fmtDe(kosten / km * 100, 2) + ' €';
      const v = document.getElementById('dash-verbrauch-100km');
      if (v) v.textContent = kwh > 0 ? fmtDe(kwh / km * 100, 1) + ' kWh/100 km' : '–';
    } else if (km > 0 && km < 100) {
      wert.textContent = '–';
      const v = document.getElementById('dash-verbrauch-100km');
      if (v) v.textContent = 'noch zu wenig Fahrten';
    } else {
      wert.textContent = '–';
      const v = document.getElementById('dash-verbrauch-100km');
      // Sagen, was fehlt, statt nur einen Strich zu zeigen.
      if (v) v.textContent = km > 0 ? 'noch keine Stromkosten erfasst'
                                    : 'noch keine Dienstfahrten erfasst';
    }
  } catch (e) {}
}

// ═══════════════════════════════════════════════════════════════════════════
// DASHBOARD — Statusleiste und Ladevorgangs-Übersicht
// ═══════════════════════════════════════════════════════════════════════════

// Statusleiste: beantwortet, ob die Zahlen darunter aktuell sind.
async function ladeDashStatus() {
  const setze = (id, klasse, text) => {
    const el = document.getElementById(id);
    if (!el) return;
    const dot = el.querySelector('.sp-dot');
    if (dot) dot.className = 'sp-dot ' + klasse;
    el.lastChild.textContent = ' ' + text;
  };
  try {
    const wb = await (await hole('/api/wallboxes/full')).json();
    const laedt = (wb.wallboxes || []).find(w => w.live_metrics && w.live_metrics.power_kw > 0);
    setze('sp-wallbox', laedt ? 'aktiv' : 'ok',
      laedt ? `Wallbox lädt · ${fmtDe(laedt.live_metrics.power_kw, 1)} kW`
            : `${(wb.wallboxes || []).length} Wallbox${(wb.wallboxes||[]).length === 1 ? '' : 'en'}`);
  } catch (e) { setze('sp-wallbox', '', 'Wallbox'); }

  try {
    const cd = await (await hole('/api/cardata/status')).json();
    setze('sp-cardata', cd.angemeldet ? 'ok' : '',
      cd.angemeldet
        ? (cd.letzter_abruf ? `CarData · ${cd.letzter_abruf.slice(11,16)} Uhr` : 'CarData verbunden')
        : 'CarData nicht verbunden');
  } catch (e) {}

  try {
    const d = await (await hole('/api/trips')).json();
    const offen = (d.trips || []).filter(t => (t.fahrtart || '') === 'offen').length;
    const pill = document.getElementById('sp-offen');
    const txt = document.getElementById('sp-offen-text');
    if (pill && txt) {
      pill.style.display = offen ? 'inline-flex' : 'none';
      txt.textContent = `${offen} Fahrt${offen === 1 ? '' : 'en'} zuzuordnen`;
    }
  } catch (e) {}

  const monat = document.getElementById('sp-monat-text');
  if (monat) {
    const m = ['Januar','Februar','März','April','Mai','Juni','Juli','August',
               'September','Oktober','November','Dezember'][new Date().getMonth()];
    monat.textContent = `${m} ${new Date().getFullYear()}`;
  }
}

// Ladevorgänge im Dashboard — kompakte Fassung der Sessions-Liste mit
// Ladeort, Quelle und Summenzeile.
async function ladeDashLadevorgaenge() {
  const body = document.getElementById('dash-lv-body');
  const foot = document.getElementById('dash-lv-foot');
  if (!body) return;
  const zeitraum = document.getElementById('dash-lv-zeit')?.value || 'monat';
  const ortFilter = document.getElementById('dash-lv-ort')?.value || '';

  const heute = new Date();
  let von = null, bis = null;
  if (zeitraum === 'monat') {
    von = new Date(heute.getFullYear(), heute.getMonth(), 1);
    bis = heute;
  } else if (zeitraum === 'vormonat') {
    von = new Date(heute.getFullYear(), heute.getMonth() - 1, 1);
    bis = new Date(heute.getFullYear(), heute.getMonth(), 0);
  }
  const iso = d => d ? d.toISOString().slice(0, 10) : '';
  const params = von ? `?von=${iso(von)}&bis=${iso(bis)}` : '';

  try {
    const d = await (await fetch('/api/sessions' + params)).json();
    let liste = d.sessions || d || [];
    if (ortFilter) liste = liste.filter(s => (s.charging_location || 'zuhause') === ortFilter);
    liste = liste.slice(0, 8);

    if (!liste.length) {
      body.innerHTML = '<tr><td colspan="8" class="hint" style="padding:18px 14px;">'
        + 'Keine Ladevorgänge im gewählten Zeitraum.</td></tr>';
      foot.innerHTML = '';
      return;
    }

    body.innerHTML = liste.map(s => {
      // Die API liefert Energie und Betrag bereits berechnet — die Rohwerte
      // der Zähler sind hier gar nicht enthalten.
      const kwh = s.energy_kwh || 0;
      const betrag = s.amount_eur != null ? s.amount_eur : kwh * (s.price_per_kwh || 0);
      const zuhause = (s.charging_location || 'zuhause') === 'zuhause';
      const datum = (s.start_timestamp || '').slice(0, 10).split('-').reverse().slice(0, 2).join('.');
      const von_ = (s.start_timestamp || '').slice(11, 16);
      const bis_ = (s.end_timestamp || '').slice(11, 16);
      const adresse = s.charging_location_note || s.wallbox_name || '—';
      return `<tr${zuhause ? '' : ' style="opacity:.76"'}>
        <td>${datum}.</td>
        <td class="mono">${von_}${bis_ ? '–' + bis_ : ''}</td>
        <td>${adresse}<div class="zeilen-sub">${s.wallbox_name || ''}</div></td>
        <td><span class="zeilen-sub">${(s.source || '').replace('_', ' ')}</span></td>
        <td class="num">${fmtDe(kwh, 2)}</td>
        <td class="num">${fmtDe(s.price_per_kwh || 0, 4)}</td>
        <td class="num">${fmtDe(betrag, 2)} €</td>
        <td><span class="zuo ${zuhause ? 'zuo-ag' : 'zuo-extern'}">
          ${zuhause ? 'Arbeitgeber' : 'unterwegs'}</span></td>
      </tr>`;
    }).join('');

    // Summenzeile: nur die erstattungsfähigen Beträge — sie ist das Ergebnis.
    const heim = liste.filter(s => (s.charging_location || 'zuhause') === 'zuhause');
    const sumKwh = heim.reduce((a, s) => a + (s.energy_kwh || 0), 0);
    const sumEur = heim.reduce((a, s) => a + (s.amount_eur != null ? s.amount_eur : (s.energy_kwh || 0) * (s.price_per_kwh || 0)), 0);
    foot.innerHTML = `<tr>
      <td colspan="4">${liste.length} Ladevorgänge · davon ${heim.length} zuhause</td>
      <td class="num">${fmtDe(sumKwh, 2)}</td><td></td>
      <td class="num" style="color:var(--akz-geld);">${fmtDe(sumEur, 2)} €</td>
      <td><span class="zeilen-sub">erstattungsfähig</span></td>
    </tr>`;
  } catch (e) {
    body.innerHTML = '<tr><td colspan="8" class="hint">Ladevorgänge konnten nicht geladen werden.</td></tr>';
  }
}

// Kachel „Unterwegs geladen"
async function ladeExternKachel() {
  const wert = document.getElementById('dash-extern-kwh');
  if (!wert) return;
  try {
    const heute = new Date();
    const von = new Date(heute.getFullYear(), heute.getMonth(), 1).toISOString().slice(0, 10);
    const d = await (await fetch(`/api/sessions?von=${von}&bis=${heute.toISOString().slice(0,10)}`)).json();
    const ext = (d.sessions || d || []).filter(s => (s.charging_location || '') === 'extern');
    const kwh = ext.reduce((a, s) => a + (s.energy_kwh || 0), 0);
    const eur = ext.reduce((a, s) => a + (s.amount_eur != null ? s.amount_eur : (s.energy_kwh || 0) * (s.price_per_kwh || 0)), 0);
    wert.textContent = kwh > 0 ? fmtDe(kwh, 1) + ' kWh' : '0 kWh';
    const sub = document.getElementById('dash-extern-sub');
    if (sub) sub.textContent = ext.length
      ? `${ext.length} Ladung${ext.length === 1 ? '' : 'en'} · ${fmtDe(eur, 2)} € · nicht erstattungsfähig`
      : 'diesen Monat nur zuhause geladen';
  } catch (e) {}
}

// BMW-Import vollständig zurücksetzen — für den Fall, dass nach einem
// Löschvorgang nichts mehr importiert werden kann.
async function cardataImportZuruecksetzen() {
  if (bmwGesperrtHinweis('reset')) return;
  if (!confirm('BMW-Import vollständig zurücksetzen?\n\n'
    + 'Entfernt alle aus BMW importierten Ladevorgänge sowie die gemerkten '
    + 'Importstände. Selbst erfasste Sessions und Wallbox-Daten bleiben erhalten.\n\n'
    + 'Danach holt ein erneuter Abruf wieder alles.')) return;
  try {
    const d = await (await fetch('/api/cardata/import-zuruecksetzen', { method:'POST' })).json();
    if (d.ok) {
      _toast(`Zurückgesetzt: ${d.sessions} Ladevorgänge, ${d.referenzen} Fahrt-Referenzen`);
      loadSessions();
      loadDashboardSummary();
    }
  } catch (e) {
    _toast('Zurücksetzen fehlgeschlagen');
  }
}

// Heimladungen aus der BMW-App übernehmen — standardmäßig aus, weil die
// eigene Wallbox der belastbare Messnachweis ist.
async function loadBmwHeimladungen() {
  try {
    const d = await (await fetch('/api/settings/bmw-heimladungen')).json();
    const cb = document.getElementById('bmw-heimladungen');
    if (cb) cb.checked = !!d.uebernehmen;
    _zeigeDuplikatSchalter();
  } catch (e) {}
}

async function saveBmwHeimladungen() {
  const cb = document.getElementById('bmw-heimladungen');
  await fetch('/api/settings/bmw-heimladungen', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ uebernehmen: cb.checked })
  });
  _toast(cb.checked
    ? 'Heimladungen werden mit importiert'
    : 'Nur externe Ladungen — die Wallbox misst zuhause selbst');
  _zeigeDuplikatSchalter();
}

// Die Duplikatprüfung ist nur sinnvoll, wenn Heimladungen überhaupt
// hereinkommen — sonst steht dort eine Einstellung ohne Wirkung.
function _zeigeDuplikatSchalter() {
  const an = document.getElementById('bmw-heimladungen')?.checked;
  const zeile = document.getElementById('bmw-dupl-zeile');
  if (zeile) zeile.style.display = an ? 'flex' : 'none';
}

// Doppelerfassungs-Prüfung ein-/ausschalten
async function loadBmwDuplikate() {
  try {
    const d = await (await fetch('/api/settings/bmw-duplikate')).json();
    const cb = document.getElementById('bmw-dupl-pruefen');
    if (cb) cb.checked = !!d.pruefen;
  } catch (e) {}
}
async function saveBmwDuplikate() {
  const cb = document.getElementById('bmw-dupl-pruefen');
  await fetch('/api/settings/bmw-duplikate', { method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ pruefen: cb.checked }) });
  _toast(cb.checked ? 'Doppelte Heimladungen werden übersprungen'
                    : 'Alle Ladevorgänge werden importiert');
}

// Protokoll als Text in die Zwischenablage — für Rückfragen und Fehlersuche
// oft der schnellste Weg, den Zustand weiterzugeben.
async function protokollKopieren() {
  const tbody = document.getElementById('protokoll-tbody')
             || document.querySelector('#view-protokoll tbody');
  if (!tbody) { _toast('Kein Protokoll geladen'); return; }
  const zeilen = Array.from(tbody.querySelectorAll('tr')).map(tr =>
    Array.from(tr.querySelectorAll('td')).map(td => td.textContent.trim()).join('\t'));
  if (!zeilen.length) { _toast('Protokoll ist leer'); return; }
  const text = 'Zeitpunkt\tQuelle\tStufe\tMeldung\n' + zeilen.join('\n');
  const ok = await inZwischenablage(text);
  _toast(ok ? `${zeilen.length} Zeilen kopiert`
            : 'Kopieren nicht möglich — bitte Text markieren');
}

// ═══════════════════════════════════════════════════════════════════════════
// LADEVORGANG ERFASSEN — Eingabe in kWh, Vorschau vor dem Speichern
// ═══════════════════════════════════════════════════════════════════════════
let _msModus = 'kwh';
let _msOrt = 'zuhause';

function ladeMengeModus(modus) {
  _msModus = modus;
  document.getElementById('ms-modus-kwh')?.classList.toggle('on', modus === 'kwh');
  document.getElementById('ms-modus-zaehler')?.classList.toggle('on', modus === 'zaehler');
  const f1 = document.getElementById('ms-feld-kwh');
  const f2 = document.getElementById('ms-feld-zaehler');
  if (f1) f1.style.display = modus === 'kwh' ? 'block' : 'none';
  if (f2) f2.style.display = modus === 'zaehler' ? 'block' : 'none';
  ladeVorschau();
}

function ladeOrtSetzen(ort) {
  _msOrt = ort;
  document.getElementById('ms-ort-heim')?.classList.toggle('on', ort === 'zuhause');
  document.getElementById('ms-ort-extern')?.classList.toggle('on', ort === 'extern');
  const hinweis = document.getElementById('ms-ort-hinweis');
  if (hinweis) {
    hinweis.textContent = ort === 'zuhause'
      ? 'Zuhause geladener Strom ist nach § 3 Nr. 50 EStG erstattungsfähig.'
      : 'Unterwegs geladener Strom wird meist separat abgerechnet und ist nicht erstattungsfähig.';
  }
  ladeVorschau();
}

// Sammelt die Eingaben und rechnet sie in eine einheitliche Form um.
function ladeFormularWerte() {
  const zahl = id => parseFloat(document.getElementById(id)?.value.replace(',', '.')) || 0;
  let kwh, zStart, zEnde;

  if (_msModus === 'kwh') {
    kwh = zahl('ms-kwh');
    // Ohne echte Zählerstände beginnt die Session bei null — die Software
    // erkennt daran später, dass kein absoluter Zähler vorliegt.
    zStart = 0;
    zEnde = kwh * 1000;
  } else {
    zStart = zahl('ms-meter-start') * 1000;
    zEnde = zahl('ms-meter-end') * 1000;
    kwh = Math.max(0, (zEnde - zStart) / 1000);
  }

  // Preis: manuell gesetzt oder aus dem Ladeort abgeleitet
  let preis = zahl('ms-preis');
  if (!preis) preis = _msOrt === 'zuhause' ? (_vertragspreis || 0.28) : (_preisExtern || 0.59);

  return { kwh, zaehlerStartWh: zStart, zaehlerEndeWh: zEnde,
           ort: _msOrt, preis, modus: _msModus };
}

let _vertragspreis = null, _preisExtern = null;

function ladeVorschau() {
  const w = ladeFormularWerte();
  const setz = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };
  setz('ms-vorschau-kwh', fmtDe(w.kwh, 2) + ' kWh');
  setz('ms-vorschau-betrag', fmtDe(w.kwh * w.preis, 2) + ' €');

  const start = document.getElementById('ms-start')?.value;
  const ende = document.getElementById('ms-end')?.value;
  if (start && ende) {
    const min = Math.round((new Date(ende) - new Date(start)) / 60000);
    setz('ms-vorschau-dauer', min > 0
      ? (min >= 60 ? `${Math.floor(min/60)} h ${min%60} min` : `${min} min`)
      : '–');
  } else {
    setz('ms-vorschau-dauer', '–');
  }
}

// Preise einmalig laden, damit die Vorschau ohne Serveranfrage rechnet
async function ladePreiseVorladen() {
  try {
    const d = await (await fetch('/api/settings/ladepreise')).json();
    _vertragspreis = d.heim;
    _preisExtern = d.ac_extern;
  } catch (e) {}
}

// ═══════════════════════════════════════════════════════════════════════════
// MONATSABSCHLUSS — vom Dashboard in zwei Klicks zum fertigen Beleg
// ═══════════════════════════════════════════════════════════════════════════
// ── Zentrale Zeitraumauswahl des Dashboards ────────────────────────────────
//
// Eine Wahl steuert alles: Kacheln, Aufschlüsselung, Monatsabschluss,
// Ladevorgänge. Vier Arten stehen zur Verfügung — Monat, Quartal, Jahr und
// Gesamt. Zuvor gab es zwei Felder, die unterschiedliche Bereiche steuerten.
function _zeitwerteFuellen() {
  const art = document.getElementById('dash-zeitart')?.value || 'monat';
  const sel = document.getElementById('dash-zeitwert');
  if (!sel) return;
  const heute = new Date();
  const jahr = heute.getFullYear();
  let optionen = [];

  if (art === 'monat') {
    // Zwölf Monate rückwärts — deckt eine volle Steuerperiode ab
    for (let i = 0; i < 12; i++) {
      const d = new Date(jahr, heute.getMonth() - i, 1);
      optionen.push([`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`,
                     `${MONATSNAMEN[d.getMonth()]} ${d.getFullYear()}`]);
    }
  } else if (art === 'quartal') {
    const aktQ = Math.floor(heute.getMonth() / 3) + 1;
    for (let i = 0; i < 8; i++) {
      let q = aktQ - i, j = jahr;
      while (q < 1) { q += 4; j -= 1; }
      optionen.push([`${j}-Q${q}`, `Q${q} ${j}`]);
    }
  } else if (art === 'jahr') {
    for (let i = 0; i < 5; i++) optionen.push([`${jahr-i}`, `${jahr-i}`]);
  } else {
    optionen = [['gesamt', 'Alle Daten']];
  }

  sel.innerHTML = optionen.map(([w, t]) => `<option value="${w}">${t}</option>`).join('');
  sel.style.display = (art === 'gesamt') ? 'none' : '';
}

// Liefert Anfang, Ende und eine lesbare Beschriftung des gewählten Zeitraums.
function _zeitraum() {
  const art  = document.getElementById('dash-zeitart')?.value || 'monat';
  const wert = document.getElementById('dash-zeitwert')?.value;

  if (art === 'gesamt') {
    return { von: '2000-01-01', bis: '2099-12-31', label: 'Alle Daten',
             art: 'gesamt', monate: 0 };
  }
  if (!wert) return null;

  if (art === 'monat') {
    const [j, m] = wert.split('-').map(Number);
    const letzter = new Date(j, m, 0).getDate();
    return { von: `${j}-${String(m).padStart(2,'0')}-01`,
             bis: `${j}-${String(m).padStart(2,'0')}-${letzter}`,
             label: `${MONATSNAMEN[m-1]} ${j}`, art, monate: 1, ma_wert: wert };
  }
  if (art === 'quartal') {
    const [j, q] = [Number(wert.split('-')[0]), Number(wert.split('Q')[1])];
    const ersterM = (q - 1) * 3 + 1, letzterM = q * 3;
    const letzter = new Date(j, letzterM, 0).getDate();
    return { von: `${j}-${String(ersterM).padStart(2,'0')}-01`,
             bis: `${j}-${String(letzterM).padStart(2,'0')}-${letzter}`,
             label: `Q${q} ${j}`, art, monate: 3 };
  }
  // Jahr
  return { von: `${wert}-01-01`, bis: `${wert}-12-31`,
           label: `${wert}`, art, monate: 12 };
}

// Nur für die Belegerzeugung: Sie arbeitet monatsweise. Bei Quartal oder Jahr
// ist der Monatsabschluss nicht anwendbar — darauf weist die Oberfläche hin.
function _monatsGrenzen() {
  const z = _zeitraum();
  if (!z) return null;
  return { von: z.von, bis: z.bis, label: z.ma_wert || z.label };
}

function zeitraumArtGewechselt() {
  _zeitwerteFuellen();
  zeitraumGewechselt();
}

// Alles neu laden, was vom Zeitraum abhängt
function zeitraumGewechselt() {
  const z = _zeitraum();
  const anz = document.getElementById('ma-zeitraum-anzeige');
  if (anz && z) anz.textContent = z.label;
  loadDashboardSummary();
  ladeMonatsabschluss();
  ladeDashLadevorgaenge && ladeDashLadevorgaenge();
  loadRecentSessionsChart && loadRecentSessionsChart(true);
}

async function ladeMonatsabschluss() {
  _zeitwerteFuellen();
  const z = _monatsGrenzen();
  if (!z) return;
  const setz = (id, t) => { const el = document.getElementById(id); if (el) el.textContent = t; };
  try {
    const [sess, trips] = await Promise.all([
      (await fetch(`/api/sessions?von=${z.von}&bis=${z.bis}`)).json(),
      (await fetch(`/api/trips?von=${z.von}&bis=${z.bis}`)).json(),
    ]);
    const liste = sess.sessions || sess || [];
    const heim = liste.filter(s => (s.charging_location || 'zuhause') === 'zuhause');
    const stromKwh = heim.reduce((a, s) => a + (s.energy_kwh || 0), 0);
    const stromEur = heim.reduce((a, s) => a + (s.amount_eur || 0), 0);

    const fahrten = (trips.trips || []).filter(t => (t.fahrtart || 'dienstlich') === 'dienstlich');
    const km = fahrten.reduce((a, t) => a + (t.distance_km || 0), 0);
    const fahrtEur = fahrten.reduce((a, t) => a + (t.employer_amount_eur || 0), 0);

    setz('ma-strom', fmtDe(stromEur, 2) + ' €');
    setz('ma-strom-sub', `${fmtDe(stromKwh, 1)} kWh · ${heim.length} Ladevorgänge`);
    setz('ma-fahrten', fmtDe(fahrtEur, 2) + ' €');
    // Kilometer und Fahrtenzahl stehen bereits in der Kachel oben —
    // hier nur noch der Betrag, der auf den Beleg kommt.
    setz('ma-fahrten-sub', 'erstattungsfähig');
    setz('ma-summe', fmtDe(stromEur + fahrtEur, 2) + ' €');

    // Offene Punkte benennen, statt den Anwender in einen unvollständigen
    // Beleg laufen zu lassen.
    const offen = (trips.trips || []).filter(t => (t.fahrtart || '') === 'offen').length;
    const status = document.getElementById('ma-status');
    if (status) {
      if (offen) {
        status.innerHTML = `<span style="color:var(--akz-hinweis);">●</span> `
          + `${offen} Fahrt${offen === 1 ? '' : 'en'} noch nicht zugeordnet — `
          + `<a onclick="zuOffenenFahrten()" style="cursor:pointer; text-decoration:underline;">jetzt erledigen</a>`;
      } else if (!liste.length && !fahrten.length) {
        status.innerHTML = '<span style="color:var(--text-tertiary);">●</span> Keine Daten in diesem Monat.';
      } else {
        status.innerHTML = '<span style="color:var(--akz-geld);">●</span> Alles zugeordnet — bereit zum Abrechnen.';
      }
    }
    const btnS = document.getElementById('ma-btn-strom');
    const btnF = document.getElementById('ma-btn-fahrt');
    if (btnS) btnS.disabled = heim.length === 0;
    if (btnF) btnF.disabled = fahrten.length === 0;
  } catch (e) {
    setz('ma-status', 'Daten konnten nicht geladen werden.');
  }
}

function monatsbelegErzeugen(art) {
  const z = _monatsGrenzen();
  if (!z) return;
  const hinweis = document.getElementById('ma-hinweis');
  const ziele = {
    ladestrom:   `/api/documents/ladestrom?von=${z.von}&bis=${z.bis}`,
    fahrtkosten: `/api/documents/fahrtkosten-ag?von=${z.von}&bis=${z.bis}`,
  };
  const oeffnen = pfad => window.open(pfad, '_blank');
  if (art === 'beide') {
    oeffnen(ziele.ladestrom);
    // Kurz versetzt, damit der Browser den zweiten Tab nicht blockiert
    setTimeout(() => oeffnen(ziele.fahrtkosten), 600);
    if (hinweis) hinweis.textContent = 'Beide Belege werden erzeugt — bitte Pop-ups zulassen.';
  } else {
    oeffnen(ziele[art]);
    if (hinweis) hinweis.textContent = '';
  }
}

// Prüft eine IP-Adresse während der Eingabe. Ein Tippfehler fällt sonst erst
// beim Verbindungstest auf — nach dem Speichern und mit unklarer Meldung.
function pruefeIpFeld(feld) {
  const hinweis = document.getElementById('wb-host-hinweis');
  const wert = feld.value.trim();
  if (!hinweis) return;
  if (!wert) {
    hinweis.textContent = ''; hinweis.className = 'feld-hinweis';
    feld.classList.remove('feld-fehler');
    return;
  }
  // Erlaubt sind IPv4-Adressen und Hostnamen — beides kommt in Heimnetzen vor.
  const istIp = /^(\d{1,3}\.){3}\d{1,3}$/.test(wert);
  const istHost = /^[a-zA-Z][a-zA-Z0-9.\-]{2,}$/.test(wert);
  if (istIp) {
    const teile = wert.split('.').map(Number);
    if (teile.some(t => t > 255)) {
      hinweis.textContent = 'Ungültige IP-Adresse — jeder Block muss zwischen 0 und 255 liegen.';
      hinweis.className = 'feld-hinweis fehler';
      feld.classList.add('feld-fehler');
      return;
    }
    hinweis.textContent = 'Gültige Adresse.';
    hinweis.className = 'feld-hinweis ok';
    feld.classList.remove('feld-fehler');
  } else if (istHost) {
    hinweis.textContent = 'Wird als Hostname aufgelöst.';
    hinweis.className = 'feld-hinweis';
    feld.classList.remove('feld-fehler');
  } else {
    hinweis.textContent = 'Bitte eine IP-Adresse wie 192.168.1.99 eintragen.';
    hinweis.className = 'feld-hinweis fehler';
    feld.classList.add('feld-fehler');
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// DATENSICHERUNG
// ═══════════════════════════════════════════════════════════════════════════
async function backupStatusLaden() {
  const box = document.getElementById('backup-status');
  if (!box) return;
  try {
    const d = await (await fetch('/api/backup/status')).json();
    const eintraege = Object.entries(d.datensaetze || {})
      .filter(([, n]) => n > 0)
      .map(([name, n]) => `${n} ${name}`);
    box.textContent = eintraege.length
      ? 'Aktuell gespeichert: ' + eintraege.join(' · ')
      : 'Noch keine Daten erfasst.';
  } catch (e) {}
}

async function backupEinspielen() {
  const feld = document.getElementById('backup-datei');
  const btn = document.getElementById('backup-btn');
  const out = document.getElementById('backup-ergebnis');
  if (!feld?.files.length) { _toast('Bitte zuerst eine Sicherung auswählen'); return; }
  if (!confirm('Sicherung einspielen?\n\nDer aktuelle Stand wird vorher automatisch '
             + 'gesichert und liegt danach im Ordner data/.')) return;

  const daten = new FormData();
  daten.append('file', feld.files[0]);
  if (btn) btn.disabled = true;
  if (out) out.innerHTML = '<div class="hint">Prüfe und spiele ein …</div>';
  try {
    const d = await (await fetch('/api/backup/einspielen', { method:'POST', body: daten })).json();
    if (!d.ok) {
      out.innerHTML = `<div style="color:var(--danger); font-size:13px;">✕ ${d.meldung}</div>`;
      return;
    }
    const zahlen = Object.entries(d.datensaetze || {})
      .filter(([, n]) => n > 0).map(([k, n]) => `${n} ${k}`).join(' · ');
    out.innerHTML = `<div style="color:var(--akz-geld); font-size:13px;">`
      + `✓ Wiederhergestellt: ${zahlen}</div>`
      + `<div class="hint" style="margin-top:4px;">Vorheriger Stand gesichert als `
      + `<span class="mono">${d.notfallkopie}</span> im Ordner data/.</div>`;
    _toast('Sicherung eingespielt');
    backupStatusLaden();
    setTimeout(() => location.reload(), 1800);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function resetAllesWaehlen() {
  ['bewegungsdaten','wallboxen','fahrzeuge','personen','einstellungen']
    .forEach(n => { const el = document.getElementById('reset-' + n); if (el) el.checked = true; });
}

async function datenZuruecksetzen() {
  const bereiche = {};
  const namen = {
    bewegungsdaten: 'Ladevorgänge, Fahrten und Belege',
    wallboxen:      'Wallboxen',
    fahrzeuge:      'Fahrzeuge und Fahrzeugkosten',
    personen:       'Personen und Car Allowance',
    einstellungen:  'Einstellungen',
  };
  Object.keys(namen).forEach(n => {
    bereiche[n] = !!document.getElementById('reset-' + n)?.checked;
  });

  const gewaehlt = Object.keys(namen).filter(n => bereiche[n]);
  if (!gewaehlt.length) {
    _toast('Bitte auswählen, was gelöscht werden soll.');
    return;
  }
  const alles = gewaehlt.length === Object.keys(namen).length;

  if (!confirm('Folgendes wird unwiderruflich gelöscht:\n\n'
             + gewaehlt.map(n => '  • ' + namen[n]).join('\n') + '\n\n'
             + (alles ? 'Das ist der vollständige Auslieferungszustand — '
                      + 'die Einrichtung beginnt danach von vorn.\n\n' : '')
             + 'Vorher wird automatisch eine Sicherung im Ordner data/ abgelegt.')) return;

  const out = document.getElementById('reset-ergebnis-box');
  if (out) out.innerHTML = '<span style="color:var(--text-tertiary)">Lösche …</span>';

  try {
    const d = await (await fetch('/api/backup/zuruecksetzen', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ bestaetigung: 'ZURUECKSETZEN', bereiche })
    })).json();

    if (!d.ok) {
      if (out) out.innerHTML = `<span style="color:var(--danger)">${d.meldung || d.error || 'Fehlgeschlagen.'}</span>`;
      return;
    }

    const anzahl = d.anzahl != null ? d.anzahl
                 : Object.values(d.geloescht || {}).reduce((a, b) => a + b, 0);
    let text = `<span style="color:var(--success)">✓ ${anzahl} Datensätze entfernt.</span>`;
    if (d.probleme && d.probleme.length) {
      text += `<br><span style="color:var(--warning)">Nicht möglich bei: ${d.probleme.join(', ')}</span>`;
    }
    if (d.sicherung) text += `<br><span class="hint">Sicherung: ${d.sicherung}</span>`;
    if (out) out.innerHTML = text;

    if (d.setup_erforderlich) {
      // Auslieferungszustand: Auch der gespeicherte Zustand im Browser muss
      // weg, sonst wird der Haftungshinweis übersprungen.
      try {
        localStorage.removeItem('disclaimerAccepted');
        localStorage.removeItem('demoGefragt');
      } catch (e) {}
    }
    setTimeout(() => location.reload(), 2000);
  } catch (e) {
    if (out) out.innerHTML = '<span style="color:var(--danger)">Nicht erreichbar.</span>';
  }
}



// ─── Ausgabe anzeigen (Demo oder Vollversion) ──────────────────────────────
async function editionAnzeigen() {
  const box = document.getElementById('edition-box');
  if (!box) return;
  try {
    const d = await (await fetch('/api/license/status')).json();
    const voll = d.voll || d.pro;
    const zeile = (label, wert, farbe) =>
      `<div class="kv"><span class="k">${label}</span>` +
      `<span class="v"${farbe ? ` style="color:${farbe};"` : ''}>${wert}</span></div>`;

    let html = zeile('Ausgabe', d.bezeichnung || (voll ? 'Vollversion' : 'Demoversion'),
                     voll ? 'var(--akz-geld)' : 'var(--akz-hinweis)');
    if (voll && d.kaeufer) {
      html += zeile('Lizenziert für', d.kaeufer);
      html += `<div class="hint" style="margin-top:8px;">Dieser Name erscheint in der `
            + `Fußzeile jedes erzeugten Belegs.</div>`;
    }
    if (!voll) {
      html += zeile('Erfassen', 'unbegrenzt');
      html += zeile('Belege', 'mit Wasserzeichen', 'var(--akz-hinweis)');
      html += zeile('Wallboxen', `höchstens ${d.max_wallboxen || 1}`);
      // Gesperrte Funktionen benennen statt nur "eingeschränkt" zu schreiben
      for (const g of (d.gesperrt || [])) {
        html += zeile(g.titel, 'nur in der Vollversion', 'var(--text-tertiary)');
      }
    }
    box.innerHTML = html;
    // Kauf- und Eingabekarte nur zeigen, solange nicht freigeschaltet
    for (const id of ['upgrade-karte', 'lizenz-karte']) {
      const karte = document.getElementById(id);
      if (karte) karte.style.display = voll ? 'none' : 'block';
    }
    // Bei aktiver Lizenz die Herkunft nennen: Paket oder Schlüssel
    const liz = d.lizenz || {};
    if (voll && liz.lizenziert) {
      box.innerHTML += `<div class="hint" style="margin-top:8px;">`
        + `Freigeschaltet per Lizenzschlüssel`
        + (liz.gekauft_am ? `, gekauft am ${fmtDatum(liz.gekauft_am)}` : '')
        + `. <a onclick="lizenzEntfernen()" style="cursor:pointer; text-decoration:underline;">`
        + `Von diesem Rechner lösen</a></div>`;
    }
    if (liz.hinweis) {
      box.innerHTML += `<div class="hint" style="color:var(--akz-hinweis); margin-top:6px;">${liz.hinweis}</div>`;
    }
  } catch (e) {}
}

// ─── Lizenzschlüssel freischalten ──────────────────────────────────────────
async function lizenzAktivieren() {
  const feld = document.getElementById('lizenz-key');
  const btn = document.getElementById('lizenz-btn');
  const out = document.getElementById('lizenz-meldung');
  const key = (feld?.value || '').trim();
  if (!key) { _toast('Bitte den Lizenzschlüssel eingeben'); return; }

  if (btn) btn.disabled = true;
  if (out) out.textContent = 'Prüfe den Schlüssel …';
  try {
    const d = await (await fetch('/api/license/aktivieren', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ key })
    })).json();

    if (d.ok) {
      if (out) out.innerHTML = `<span style="color:var(--akz-geld);">✓</span> `
        + `Freigeschaltet${d.kaeufer ? ' für ' + d.kaeufer : ''}. Die Seite wird neu geladen …`;
      _toast('Vollversion freigeschaltet');
      setTimeout(() => location.reload(), 1400);
    } else {
      // Konkret sagen, woran es liegt — "ungültig" allein hilft niemandem
      if (out) out.innerHTML = `<span style="color:var(--danger);">✕</span> ${d.fehler}`;
    }
  } catch (e) {
    if (out) out.textContent = 'Prüfung fehlgeschlagen — besteht eine Internetverbindung?';
  } finally {
    if (btn) btn.disabled = false;
  }
}


async function lizenzEntfernen() {
  if (!confirm('Lizenz von diesem Rechner lösen?\n\n'
             + 'Der Schlüssel bleibt gültig und lässt sich anderswo erneut eingeben. '
             + 'Diese Installation läuft danach als Demo weiter — die Daten bleiben.')) return;
  await fetch('/api/license/entfernen', { method: 'POST' });
  _toast('Lizenz gelöst');
  setTimeout(() => location.reload(), 900);
}

// ═══════════════════════════════════════════════════════════════════════════
// BEISPIELDATEN — ein Klick zu einer vollständigen Vorführumgebung
// ═══════════════════════════════════════════════════════════════════════════
// Beispieldaten unmittelbar vom Dashboard aus anlegen — der Weg über
// Einstellungen → System war zu versteckt, um beim ersten Start zu helfen.
async function demoAnlegen() {
  const out = document.getElementById('dash-demo-status');
  if (out) out.innerHTML = '<span style="color:var(--text-tertiary)">Lege Beispieldaten an …</span>';
  try {
    const d = await (await fetch('/api/demodaten/erzeugen', { method: 'POST' })).json();
    if (d.ok) {
      if (out) out.innerHTML = `<span style="color:var(--success)">✓ ${d.fahrten} Fahrten `
        + `und ${d.sessions} Ladevorgänge angelegt (${d.zeitraum}).</span>`;
      setTimeout(() => location.reload(), 1200);
    } else {
      if (out) out.innerHTML = `<span style="color:var(--danger)">${d.error || 'Fehler'}</span>`;
    }
  } catch (e) {
    if (out) out.innerHTML = '<span style="color:var(--danger)">Nicht erreichbar.</span>';
  }
}

// Den Hinweis nur zeigen, solange nichts erfasst ist
async function dashLeerPruefen() {
  const box = document.getElementById('dash-leer-hinweis');
  if (!box) return;
  try {
    const [s, f] = await Promise.all([
      fetch('/api/sessions?limit=1').then(r => r.json()),
      fetch('/api/trips?limit=1').then(r => r.json()),
    ]);
    const leer = (!s.sessions || s.sessions.length === 0)
              && (!f.trips || f.trips.length === 0);
    box.style.display = leer ? 'block' : 'none';

    // Beim allerersten Start einmal nachfragen — aber erst, wenn die Seite
    // fertig aufgebaut ist. Ein Dialog, der mitten im Laden aufspringt,
    // wirkt wie ein Fehler und wird reflexhaft weggeklickt.
    if (leer && !localStorage.getItem('demoGefragt')) {
      setTimeout(() => {
        if (localStorage.getItem('demoGefragt')) return;   // inzwischen erledigt
        localStorage.setItem('demoGefragt', '1');
        if (confirm(
            'Die Anwendung enthält noch keine Daten.\n\n'
          + 'Sollen Beispieldaten für ein volles Jahr angelegt werden?\n'
          + 'Damit lassen sich alle Auswertungen und Belege ausprobieren, '
          + 'ohne selbst etwas erfassen zu müssen.\n\n'
          + 'Die Daten sind als Beispiel gekennzeichnet und jederzeit '
          + 'vollständig entfernbar.')) {
          demoAnlegen();
        }
      }, 2500);
    }
  } catch (e) {
    box.style.display = 'none';
  }
}

async function demodatenStatus() {
  const box = document.getElementById('demo-status');
  const wegBtn = document.getElementById('demo-weg-btn');
  if (!box) return;
  try {
    const d = await (await fetch('/api/demodaten/status')).json();
    if (d.vorhanden) {
      box.innerHTML = `<span style="color:var(--akz-hinweis);">●</span> `
        + `${d.fahrten} Fahrten, ${d.sessions} Ladevorgänge`
        + (d.wallboxen ? ` und ${d.wallboxen} Wallboxen` : '')
        + ' sind Beispieldaten.';
      if (wegBtn) wegBtn.style.display = 'inline-flex';
    } else {
      box.textContent = 'Derzeit keine Beispieldaten vorhanden.';
      if (wegBtn) wegBtn.style.display = 'none';
    }
  } catch (e) {}
}

async function demodatenErzeugen() {
  const monate = parseInt(document.getElementById('demo-monate')?.value || '12', 10);
  if (!confirm(`Beispieldaten für ${monate} Monate anlegen?\n\n`
             + 'Vorhandene Daten bleiben erhalten — die Beispiele kommen hinzu '
             + 'und lassen sich später gezielt wieder entfernen.')) return;

  const btn = document.getElementById('demo-btn');
  const out = document.getElementById('demo-ergebnis');
  if (btn) { btn.disabled = true; btn.textContent = 'Erzeuge …'; }
  if (out) out.innerHTML = '<div class="hint">Lege Fahrten und Ladevorgänge an …</div>';
  try {
    const d = await (await fetch('/api/demodaten/erzeugen', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ monate })
    })).json();

    if (!d.ok) {
      // Die Route meldet Fehler unter "error"; "meldung" gab es nie —
      // deshalb stand dort "undefined".
      const grund = d.error || d.meldung || 'Beispieldaten konnten nicht angelegt werden.';
      if (out) out.innerHTML = `<div style="color:var(--danger); font-size:13px;">${grund}</div>`;
      return;
    }
    if (out) out.innerHTML = `
      <div style="padding:12px 14px; background:var(--bg-input); border-radius:8px;
           display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:12px;">
        <div><div class="dash-kpi-label">Fahrten</div>
          <div style="font-family:var(--font-mono); font-size:17px; font-weight:700;">${d.fahrten}</div>
          <div class="hint">${fmtDe(d.km_dienstlich,0)} km dienstlich</div></div>
        <div><div class="dash-kpi-label">Ladevorgänge</div>
          <div style="font-family:var(--font-mono); font-size:17px; font-weight:700;">${d.sessions}</div>
          <div class="hint">${fmtDe(d.kwh_zuhause,0)} kWh zuhause</div></div>
        <div><div class="dash-kpi-label">Privat</div>
          <div style="font-family:var(--font-mono); font-size:17px; font-weight:700;">${fmtDe(d.km_privat,0)} km</div>
          <div class="hint">${fmtDe(d.kwh_unterwegs,0)} kWh unterwegs</div></div>
        <div><div class="dash-kpi-label">Zeitraum</div>
          <div style="font-family:var(--font-mono); font-size:15px; font-weight:700;">${d.zeitraum}</div>
          <div class="hint">Stand ${fmtDe(d.kilometerstand,0)} km</div></div>
      </div>`;
    _toast(`${d.fahrten} Fahrten und ${d.sessions} Ladevorgänge angelegt`);
    demodatenStatus();
    setTimeout(() => location.reload(), 2500);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Beispieldaten laden'; }
  }
}

async function demodatenEntfernen() {
  if (!confirm('Alle Beispieldaten entfernen?\n\n'
             + 'Selbst erfasste Fahrten und Ladevorgänge bleiben erhalten.')) return;
  const d = await (await fetch('/api/demodaten/entfernen', { method:'POST' })).json();
  _toast(`${d.fahrten} Fahrten und ${d.sessions} Ladevorgänge${d.wallboxen ? `, ${d.wallboxen} Wallboxen` : ''} entfernt`);
  setTimeout(() => location.reload(), 1200);
}


// Einheitliche Rückmeldung, wenn eine Funktion der Vollversion vorbehalten ist.
// Antworten mit HTTP 402 tragen die nötigen Angaben bereits mit.
function zeigeGesperrt(d) {
  if (!d || !d.gesperrt) return false;
  alert(`${d.funktion} ist der Vollversion vorbehalten.\n\n`
      + (d.beschreibung ? d.beschreibung + '\n\n' : '')
      + 'Ein Lizenzschlüssel schaltet die Funktion frei — alle Daten bleiben erhalten.\n'
      + 'Zu finden unter Einstellungen → Lizenz.');
  return true;
}


// ═══════════════════════════════════════════════════════════════════════════
// EINGEBETTETE HILFE
// Die Anleitung wird in den Einstellungen angezeigt statt in einem neuen
// Browser-Tab: Wer mitten in einer Einstellung eine Frage hat, verliert sonst
// den Kontext und muss anschließend zurückfinden.
// ═══════════════════════════════════════════════════════════════════════════
let _hilfeKapitel = [];
let _hilfeAktiv = null;

async function hilfeLaden() {
  const liste = document.getElementById('hilfe-liste');
  const inhalt = document.getElementById('hilfe-inhalt');
  if (!liste || _hilfeKapitel.length) {
    if (_hilfeKapitel.length && !_hilfeAktiv) hilfeKapitel(_hilfeKapitel[0].id);
    return;
  }
  try {
    const d = await (await fetch('/api/hilfe/kapitel')).json();
    _hilfeKapitel = d.kapitel || [];
    if (!_hilfeKapitel.length) {
      inhalt.innerHTML = `<div class="hint">${d.fehler || 'Keine Kapitel gefunden.'}</div>`;
      return;
    }
    liste.innerHTML = _hilfeKapitel.map(k =>
      `<div class="hilfe-kapitel-link" data-kapitel="${k.id}"
            onclick="hilfeKapitel('${k.id}')">${k.titel}</div>`).join('');
    hilfeKapitel(_hilfeKapitel[0].id);
  } catch (e) {
    if (inhalt) inhalt.innerHTML = '<div class="hint">Anleitung konnte nicht geladen werden.</div>';
  }
}

function hilfeKapitel(id) {
  const k = _hilfeKapitel.find(x => x.id === id);
  if (!k) return;
  _hilfeAktiv = id;
  const inhalt = document.getElementById('hilfe-inhalt');
  if (inhalt) {
    inhalt.innerHTML = `<h2>${k.titel}</h2>` + k.html;
    inhalt.scrollTop = 0;
  }
  document.querySelectorAll('.hilfe-kapitel-link').forEach(el =>
    el.classList.toggle('aktiv', el.dataset.kapitel === id));
}

// Suche über alle Kapitel. Trefferstellen werden hervorgehoben, damit man
// sieht, warum ein Kapitel gefunden wurde.
function hilfeSuchen() {
  const begriff = (document.getElementById('hilfe-suche')?.value || '').trim().toLowerCase();
  const liste = document.getElementById('hilfe-liste');
  const inhalt = document.getElementById('hilfe-inhalt');
  if (!liste) return;

  if (begriff.length < 2) {
    liste.innerHTML = _hilfeKapitel.map(k =>
      `<div class="hilfe-kapitel-link${k.id === _hilfeAktiv ? ' aktiv' : ''}"
            data-kapitel="${k.id}" onclick="hilfeKapitel('${k.id}')">${k.titel}</div>`).join('');
    if (_hilfeAktiv) hilfeKapitel(_hilfeAktiv);
    return;
  }

  const nurText = html => html.replace(/<[^>]+>/g, ' ').toLowerCase();
  const treffer = _hilfeKapitel.filter(k =>
    nurText(k.html).includes(begriff) || k.titel.toLowerCase().includes(begriff));

  if (!treffer.length) {
    liste.innerHTML = '<div class="hint" style="padding:8px 10px;">Kein Treffer.</div>';
    if (inhalt) inhalt.innerHTML = `<div class="hint">Nichts gefunden zu „${begriff}".</div>`;
    return;
  }
  liste.innerHTML = treffer.map(k =>
    `<div class="hilfe-kapitel-link" data-kapitel="${k.id}"
          onclick="hilfeKapitel('${k.id}')">${k.titel}</div>`).join('');

  // Erstes Ergebnis anzeigen, Fundstellen markieren
  const erster = treffer[0];
  if (inhalt) {
    const muster = new RegExp(`(${begriff.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
    // Nur außerhalb von Tags ersetzen, sonst zerbricht das Markup
    const markiert = erster.html.replace(/>([^<]+)</g,
      (treffer_, text) => '>' + text.replace(muster, '<mark>$1</mark>') + '<');
    inhalt.innerHTML = `<h2>${erster.titel}</h2>` + markiert;
  }
  document.querySelectorAll('.hilfe-kapitel-link').forEach(el =>
    el.classList.toggle('aktiv', el.dataset.kapitel === erster.id));
}

// OCPP-Bereich an die Ausgabe anpassen: In der Demo wird der Hinweis
// eingeblendet und die Bedienung gesperrt, statt Schalter anzubieten,
// die wirkungslos bleiben.
// BMW-Bereich analog zum OCPP-Bereich behandeln
async function bmwVerfuegbarkeitPruefen() {
  const hinweis = document.getElementById('bmw-gesperrt-hinweis');
  const bereich = document.getElementById('bmw-bereich');
  if (!hinweis) return;
  try {
    const d = await (await fetch('/api/license/status')).json();
    const gesperrt = !d.voll;
    hinweis.style.display = gesperrt ? 'block' : 'none';
    if (bereich) bereich.classList.toggle('gesperrt', gesperrt);
  } catch (e) {}
}

async function ocppVerfuegbarkeitPruefen() {
  const hinweis = document.getElementById('ocpp-gesperrt-hinweis');
  const karte = document.getElementById('ocpp-hauptkarte');
  if (!hinweis) return;
  try {
    const d = await (await fetch('/api/license/status')).json();
    const gesperrt = !d.voll;
    hinweis.style.display = gesperrt ? 'block' : 'none';
    if (karte) {
      karte.style.opacity = gesperrt ? '0.5' : '';
      karte.style.pointerEvents = gesperrt ? 'none' : '';
    }
  } catch (e) {}
}

// Beim Öffnen des Wallbox-Dialogs prüfen, welche Anbindung möglich ist.
async function wbVerbindungsartPruefen() {
  const hinweis = document.getElementById('wb-ocpp-hinweis');
  if (!hinweis) return;
  try {
    const d = await (await fetch('/api/license/status')).json();
    if (d.voll) { hinweis.style.display = 'none'; return; }
    // Nur zeigen, wenn OCPP auch gewählt ist. Bei Loxone-API steht dort sonst
    // eine Warnung zu einer Funktion, die gerade niemanden betrifft.
    hinweis.style.display = (wbMode === 'ocpp') ? 'block' : 'none';
    // OCPP-Schaltfläche kennzeichnen, statt sie stumm scheitern zu lassen
    document.querySelectorAll('#wb-mode-toggle button').forEach(b => {
      if ((b.getAttribute('onclick') || '').includes('ocpp')) {
        b.style.opacity = '0.45';
        b.title = 'Nur in der Vollversion verfügbar';
      }
    });
  } catch (e) {}
}

// ═══════════════════════════════════════════════════════════════════════════
// KENNZEICHNUNG KOSTENPFLICHTIGER FUNKTIONEN
// Der Anwender soll vor dem Klick erkennen, dass eine Funktion die
// Vollversion voraussetzt — nicht erst an einer Fehlermeldung danach.
// ═══════════════════════════════════════════════════════════════════════════
let _istVollversion = null;

function proAbzeichen(text = 'Vollversion') {
  return `<span class="pro-abzeichen" title="Diese Funktion setzt die Vollversion voraus">`
       + `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">`
       + `<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>`
       + `${text}</span>`;
}

// Klick auf eine gesperrte BMW-Funktion: erklären, was sie tut und wozu sie
// gehört — statt den Aufruf ins Leere laufen zu lassen.
function bmwGesperrtHinweis(was) {
  if (_istVollversion) return false;
  const texte = {
    historie: 'Die BMW-Ladehistorie holt Ladevorgänge unmittelbar aus dem '
            + 'Fahrzeug — auch solche an fremden Ladesäulen.',
    reset:    'Diese Funktion entfernt aus dem Fahrzeug importierte '
            + 'Ladevorgänge und setzt den Importstand zurück.',
    fahrten:  'Der Abruf holt neue Fahrten unmittelbar aus dem Fahrzeug — '
            + 'mit Datum, Strecke und Kilometerstand.',
  };
  alert((texte[was] || '') + '\n\n'
      + 'BMW CarData ist der Vollversion vorbehalten.\n\n'
      + 'In der Demo lassen sich Ladevorgänge über die Wallbox erfassen, '
      + 'per CSV einlesen oder von Hand eintragen.');
  return true;
}

async function proKennzeichnungSetzen() {
  try {
    const d = await (await fetch('/api/license/status')).json();
    _istVollversion = !!d.voll;
  } catch (e) {
    return;
  }
  if (_istVollversion) {
    // In der Vollversion alle Kennzeichnungen entfernen
    document.querySelectorAll('.pro-abzeichen').forEach(el => el.remove());
    document.querySelectorAll('.pro-gesperrt').forEach(el => el.classList.remove('pro-gesperrt'));
    document.querySelectorAll('.pro-bereich').forEach(el => el.classList.remove('gesperrt'));
    return;
  }

  // Menüpunkt „Wallboxen": OCPP und mehrere Ladepunkte sind gesperrt,
  // die Loxone-Anbindung nicht — deshalb nur kennzeichnen, nicht sperren.
  const markiere = (auswahl, text) => {
    document.querySelectorAll(auswahl).forEach(el => {
      if (el.querySelector('.pro-abzeichen')) return;
      el.insertAdjacentHTML('beforeend', proAbzeichen(text));
    });
  };
  markiere('[data-view="wallbox"]', 'Teilweise');

  // BMW-Knöpfe bei den Ladevorgängen: sichtbar lassen, aber kennzeichnen.
  // Ausblenden wäre ehrlicher, verschweigt aber, dass es die Funktion gibt —
  // und genau das soll die Demo zeigen. Ein Klick erklärt, was fehlt.
  document.querySelectorAll('.bmw-funktion').forEach(el => {
    if (el.querySelector('.pro-abzeichen')) return;
    el.insertAdjacentHTML('beforeend', ' ' + proAbzeichen('Pro'));
    el.classList.add('pro-gesperrt');
  });

  // Einstellungs-Reiter
  document.querySelectorAll('.settings-tab').forEach(tab => {
    const beschriftung = (tab.textContent || '').trim();
    const gesperrt = ['BMW CarData', 'OCPP'].some(n => beschriftung.startsWith(n));
    if (gesperrt && !tab.querySelector('.pro-abzeichen')) {
      tab.insertAdjacentHTML('beforeend', proAbzeichen('Pro'));
      tab.classList.add('pro-gesperrt');
    }
  });
}

// ─── Externer OCPP-Dienst ──────────────────────────────────────────────────
async function externOcppLaden() {
  try {
    const d = await (await fetch('/api/extern-ocpp/konfig')).json();
    const setz = (id, wert) => { const el = document.getElementById(id); if (el && wert) el.value = wert; };
    setz('ext-ocpp-adresse', d.adresse);
    setz('ext-ocpp-pfad', d.pfad);
    setz('ext-ocpp-wallbox', d.wallbox_name);
  } catch (e) {}
}

async function externOcppSpeichern() {
  const d = await (await fetch('/api/extern-ocpp/konfig', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      adresse: document.getElementById('ext-ocpp-adresse').value,
      pfad: document.getElementById('ext-ocpp-pfad').value,
      wallbox_name: document.getElementById('ext-ocpp-wallbox').value,
      aktiv: true })
  })).json();
  document.getElementById('ext-ocpp-adresse').value = d.adresse || '';
  _toast('Verbindungsdaten gespeichert');
}

async function externOcppTesten() {
  const out = document.getElementById('ext-ocpp-ergebnis');
  await externOcppSpeichern();
  if (out) out.innerHTML = '<div class="hint">Prüfe die Verbindung …</div>';
  const d = await (await fetch('/api/extern-ocpp/test', { method:'POST' })).json();
  if (out) out.innerHTML = d.ok
    ? `<div style="color:var(--akz-geld); font-size:13px;">✓ ${d.meldung}</div>`
    : `<div style="color:var(--danger); font-size:13px;">✕ ${d.meldung}</div>`;
}

async function externOcppImport() {
  const out = document.getElementById('ext-ocpp-ergebnis');
  await externOcppSpeichern();
  if (out) out.innerHTML = '<div class="hint">Hole Ladevorgänge …</div>';
  const d = await (await fetch('/api/extern-ocpp/import', { method:'POST' })).json();
  if (!d.ok) {
    if (out) out.innerHTML = `<div style="color:var(--danger); font-size:13px;">✕ ${d.meldung}</div>`;
    return;
  }
  const rest = [];
  if (d.uebersprungen) rest.push(`${d.uebersprungen} bereits bekannt`);
  if (d.ohne_energie) rest.push(`${d.ohne_energie} ohne Energiefluss`);
  if (out) out.innerHTML = `<div style="color:var(--akz-geld); font-size:13px;">`
    + `✓ ${d.neu} von ${d.gefunden} Ladevorgängen übernommen.</div>`
    + (rest.length ? `<div class="hint" style="margin-top:3px;">${rest.join(' · ')}</div>` : '');
  if (d.neu) _toast(`${d.neu} Ladevorgänge übernommen`);
}
