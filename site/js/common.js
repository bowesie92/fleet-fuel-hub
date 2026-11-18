/* Shared helpers for every page. Plain JavaScript, no build step. Colors are read from tokens.css at run time. */
(function () {
  'use strict';

  var EIA_CAPTION = 'Source: U.S. Energy Information Administration (EIA)';
  var SYN_CAPTION = 'Synthetic prices built from the EIA weekly diesel average (benchmark source: U.S. Energy Information Administration)';
  var MAP_STYLE = 'https://tiles.openfreemap.org/styles/liberty';
  var CORRIDOR_CENTER = [-111.3, 32.5];
  var MST = 'MST (Arizona, no daylight saving time)';
  var ROUNDING_NOTE = 'Parts are rounded to the cent, so one part can show a cent off its own rounding to make the parts add up to the net saving.';

  /** Read a CSS variable from tokens.css, for Chart.js and MapLibre. */
  function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  /** The build version from <meta name="build-version">, or '' when the page has none. */
  function buildVersion() {
    var m = document.querySelector('meta[name="build-version"]');
    return m ? (m.getAttribute('content') || '') : '';
  }
  /** The URL of one data file, with ?v=<build version> added when the page has a version (cache busting). */
  function dataUrl(name) {
    var v = buildVersion();
    return 'data/' + name + (v ? '?v=' + encodeURIComponent(v) : '');
  }

  /** Fetch one file from site/data/ and parse it as JSON. */
  function loadData(name) {
    return fetch(dataUrl(name), { cache: 'no-cache' }).then(function (r) {
      if (!r.ok) { throw new Error(name + ' could not be loaded (' + r.status + ')'); }
      return r.json();
    });
  }
  /** Like loadData, but returns null when the file is missing (optional files). */
  function loadOptional(name) {
    return loadData(name).catch(function () { return null; });
  }

  function isNum(n) { return typeof n === 'number' && isFinite(n); }
  function usd(n) {
    if (!isNum(n)) { return 'n/a'; }
    return (n < 0 ? '-' : '') + '$' + Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function usd0(n) {
    if (!isNum(n)) { return 'n/a'; }
    return '$' + Math.round(n).toLocaleString('en-US');
  }
  function perGal(n) { return isNum(n) ? '$' + n.toFixed(2) + '/gal' : 'n/a'; }
  function price3(n) { return isNum(n) ? '$' + n.toFixed(3) + '/gal' : 'n/a'; }
  function num(n, d) {
    if (!isNum(n)) { return 'n/a'; }
    return n.toLocaleString('en-US', { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 });
  }
  function pct(f) { return isNum(f) ? (f * 100).toFixed(1) + '%' : 'n/a'; }
  function cents(n) { return Math.round(n) + '¢'; }
  /** Headline rate in cents with one decimal, e.g. 2.4¢ (chips keep whole cents). */
  function centsRate(n) { return isNum(n) ? (Math.round(n * 10) / 10).toFixed(1) + '¢' : 'n/a'; }
  function dateLabel(iso) {
    var p = String(iso).split('-');
    if (p.length < 3) { return iso; }
    var m = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    return m[Number(p[1]) - 1] + ' ' + Number(p[2]);
  }
  function dateLong(iso) {
    var p = String(iso).split('-');
    return p.length < 3 ? iso : dateLabel(iso) + ', ' + p[0];
  }

  /** Display name for a station: the OSM name, else the brand, else "Unnamed fuel station \u00b7 STN-001". */
  function stationLabel(p) {
    if (p.display_name) { return p.display_name; }
    if (p.name) { return p.name; }
    if (p.brand) { return p.brand; }
    return 'Unnamed fuel station · ' + (p.station_id || '');
  }

  /** Saved choice for "Include unverified truck access". Browser storage may be blocked, so every access is guarded. */
  var UNV_KEY = 'ffh.includeUnverified';
  function getUnverified(defaultValue) {
    try {
      var v = window.localStorage.getItem(UNV_KEY);
      if (v === '1') { return true; }
      if (v === '0') { return false; }
    } catch (e) { /* storage unavailable, use the default */ }
    return !!defaultValue;
  }
  /** Wire the page's #unv checkbox. Calls onChange(value) now and whenever it changes. */
  function initUnverifiedToggle(defaultValue, onChange) {
    var box = document.getElementById('unv');
    var on = getUnverified(defaultValue);
    if (box) {
      box.checked = on;
      box.addEventListener('change', function () {
        try { window.localStorage.setItem(UNV_KEY, box.checked ? '1' : '0'); } catch (e) { /* ignore */ }
        onChange(box.checked);
      });
    }
    onChange(on);
  }
  /** A small amber chip for options at stations whose truck access is unverified. */
  function unverifiedChip() { return el('span', 'chip unv', 'Truck access unverified'); }

  /** Draw a navy star on a canvas and register it as a map icon (partner stations). */
  function addStarImage(m, name) {
    var s = 48, c = document.createElement('canvas');
    c.width = c.height = s;
    var g = c.getContext('2d');
    g.fillStyle = css('--navy-900');
    g.strokeStyle = css('--card');
    g.lineWidth = 3;
    g.beginPath();
    for (var i = 0; i < 10; i++) {
      var ang = -Math.PI / 2 + i * Math.PI / 5, rad = i % 2 ? 9 : 22;
      g.lineTo(s / 2 + rad * Math.cos(ang), s / 2 + rad * Math.sin(ang));
    }
    g.closePath(); g.stroke(); g.fill();
    if (!m.hasImage(name)) { m.addImage(name, g.getImageData(0, 0, s, s), { pixelRatio: 2 }); }
  }
  /** Lookup from station_id to {props, coords, label}. */
  function stationIndex(fc) {
    var idx = {};
    (fc.features || []).forEach(function (f) {
      idx[f.properties.station_id] = { props: f.properties, coords: f.geometry.coordinates, label: stationLabel(f.properties) };
    });
    return idx;
  }

  /** Create an element. Text is never parsed as HTML. */
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) { e.className = cls; }
    if (text !== undefined && text !== null) { e.textContent = text; }
    return e;
  }
  /** An outline icon from the sprite at the top of each page. */
  function icon(name, small) {
    var s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    s.setAttribute('class', 'ic' + (small ? ' sm' : ''));
    s.setAttribute('aria-hidden', 'true');
    var u = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    u.setAttribute('href', '#i-' + name);
    s.appendChild(u);
    return s;
  }
  function clear(node) { while (node.firstChild) { node.removeChild(node.firstChild); } }

  function setStatus(msg, isError) {
    var s = document.getElementById('status');
    if (!s) { return; }
    if (!msg) { s.hidden = true; return; }
    s.hidden = false;
    s.textContent = msg;
  }

  /** Run a page start-up function and show a plain-English message if it fails. */
  function boot(fn) {
    function go() {
      Promise.resolve().then(fn).catch(function (err) {
        setStatus('This page could not load its data. ' + (err && err.message ? err.message : ''), true);
        if (window.console) { console.warn(err); }
      });
    }
    if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', go); } else { go(); }
  }

  /** Fill the "Data as of" card in the sidebar from meta.json. */
  function fillAsOf() {
    loadOptional('meta.json').then(function (m) {
      var a = document.getElementById('asof');
      if (a && m && m.generated_on) { a.textContent = dateLong(m.generated_on); }
    });
  }
  document.addEventListener('DOMContentLoaded', fillAsOf);

  /** Create a MapLibre map. Returns null if maps are not available. */
  function createMap(id, center, zoom, interactive) {
    if (!window.maplibregl) { setStatus('The map library could not be loaded, so the map is hidden.', true); return null; }
    try {
      var map = new maplibregl.Map({ container: id, style: MAP_STYLE, center: center || CORRIDOR_CENTER, zoom: zoom || 7, interactive: interactive !== false, attributionControl: { compact: true } });
      if (interactive !== false) { map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right'); }
      return map;
    } catch (e) {
      setStatus('This browser could not draw the map. The tables on this page still work.', true);
      return null;
    }
  }
  /** Run fn once the map style has loaded. */
  function whenReady(map, fn) {
    if (!map) { return; }
    if (map.loaded() || map.isStyleLoaded()) { fn(); } else { map.once('load', fn); }
  }
  /** Zoom the map to fit [lon, lat] points. */
  function fitTo(map, coords, padding, animate) {
    if (!map || !coords.length) { return; }
    var b = new maplibregl.LngLatBounds(coords[0], coords[0]);
    coords.forEach(function (c) { b.extend(c); });
    map.fitBounds(b, { padding: padding || 60, maxZoom: 13, duration: animate ? 400 : 0 });
  }
  /** Add or update a GeoJSON source. */
  function setSource(map, id, data) {
    var s = map.getSource(id);
    if (s) { s.setData(data); } else { map.addSource(id, { type: 'geojson', data: data }); }
  }
  function emptyFC() { return { type: 'FeatureCollection', features: [] }; }
  function line(coords) { return { type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: coords } }; }

  /** A round HTML map marker. */
  function marker(map, coords, cls, content, onClick, label) {
    var b = el('button', 'gm ' + (cls || ''));
    b.type = 'button';
    if (label) { b.setAttribute('aria-label', label); b.title = label; }
    if (typeof content === 'string') { b.textContent = content; } else if (content) { b.appendChild(content); }
    if (onClick) { b.addEventListener('click', function (e) { e.stopPropagation(); onClick(); }); }
    return new maplibregl.Marker({ element: b }).setLngLat(coords).addTo(map);
  }

  /** Bottom-sheet behavior on phones: tap or drag the handle, or press the button, to open. */
  function initSheet(sheet, handle, moreBtn) {
    function set(open) { sheet.classList.toggle('open', open); }
    if (handle) {
      var y0 = null;
      handle.addEventListener('click', function () { set(!sheet.classList.contains('open')); });
      handle.addEventListener('pointerdown', function (e) { y0 = e.clientY; });
      handle.addEventListener('pointerup', function (e) {
        if (y0 !== null && Math.abs(e.clientY - y0) > 30) { set(e.clientY < y0); }
        y0 = null;
      });
    }
    if (moreBtn) { moreBtn.addEventListener('click', function () { set(true); }); }
    return set;
  }

  /** Download rows as a CSV file in the browser. Nothing is sent anywhere. */
  function downloadCSV(filename, columns, rows) {
    function cell(v) {
      var s = v === null || v === undefined ? '' : String(v);
      return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    }
    var out = [columns.map(cell).join(',')];
    rows.forEach(function (r) { out.push(columns.map(function (c) { return cell(r[c]); }).join(',')); });
    var blob = new Blob([out.join('\n') + '\n'], { type: 'text/csv' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
  }

  /** Weekly fleet totals from price_history.json and the stations file. */
  function fleetWeeks(ph, idx) {
    var by = {};
    Object.keys(ph.stations || {}).forEach(function (sid) {
      var partner = idx[sid] && idx[sid].props.is_partner;
      ph.stations[sid].forEach(function (w) {
        var o = by[w.week_start] || (by[w.week_start] = { week: w.week_start, gal: 0, spend: 0, partnerGal: 0 });
        if (isNum(w.avg_price_paid) && isNum(w.gallons)) {
          o.gal += w.gallons; o.spend += w.gallons * w.avg_price_paid;
          if (partner) { o.partnerGal += w.gallons; }
        }
      });
    });
    return Object.keys(by).sort().map(function (k) {
      var o = by[k];
      o.price = o.gal > 0 ? o.spend / o.gal : null;
      return o;
    });
  }

  /** Chart.js defaults and a plugin that draws the latest value in a teal tag at the end of a line. */
  function chartDefaults() {
    if (!window.Chart) { return; }
    Chart.defaults.font.family = css('--font');
    Chart.defaults.font.size = 12;
    Chart.defaults.color = css('--text-600');
    Chart.defaults.borderColor = css('--border');
  }
  var endTag = {
    id: 'endTag',
    afterDatasetsDraw: function (chart, args, opts) {
      if (!opts || !opts.text) { return; }
      var meta = chart.getDatasetMeta(0);
      var pts = meta.data;
      var last = null;
      for (var i = pts.length - 1; i >= 0; i--) { if (pts[i] && !pts[i].skip && isNum(chart.data.datasets[0].data[i])) { last = pts[i]; break; } }
      if (!last) { return; }
      var ctx = chart.ctx;
      ctx.save();
      ctx.font = '600 12px ' + css('--font');
      var w = ctx.measureText(opts.text).width + 16;
      var x = Math.min(last.x - w / 2, chart.chartArea.right - w);
      var y = last.y - 30;
      ctx.fillStyle = css('--teal-600');
      ctx.beginPath();
      if (ctx.roundRect) { ctx.roundRect(x, y, w, 22, 6); } else { ctx.rect(x, y, w, 22); }
      ctx.fill();
      ctx.fillStyle = css('--card');
      ctx.textBaseline = 'middle';
      ctx.fillText(opts.text, x + 8, y + 11);
      ctx.restore();
    }
  };

  /** Standard teal line options for a paid-vs-regional chart. */
  function lineChart(canvas, labels, paid, eia, tagText) {
    chartDefaults();
    return new Chart(canvas, {
      type: 'line',
      plugins: [endTag],
      data: {
        labels: labels,
        datasets: [
          { label: 'Price paid', data: paid, borderColor: css('--teal-600'), backgroundColor: css('--teal-50'), fill: true, tension: 0.3, pointRadius: 3, pointBackgroundColor: css('--teal-600'), spanGaps: false },
          { label: 'Regional average (EIA)', data: eia, borderColor: css('--benchmark'), borderDash: [6, 4], borderWidth: 2, pointRadius: 0, fill: false, tension: 0.3 }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        layout: { padding: { top: 28, right: 8 } },
        plugins: { legend: { display: false }, endTag: { text: tagText }, tooltip: { callbacks: { label: function (c) { return c.dataset.label + ': ' + (isNum(c.parsed.y) ? '$' + c.parsed.y.toFixed(3) + '/gal' : 'no fills'); } } } },
        scales: { y: { ticks: { callback: function (v) { return '$' + Number(v).toFixed(2); } } }, x: { grid: { display: false } } }
      }
    });
  }


  /** The EIA caption line for cards that build their own markup. */
  function eiaNote() {
    return el('p', 'foot eia-note', SYN_CAPTION);
  }

  /** "$X per truck per year (20 trucks)", or '' when the figure is missing. */
  function perTruckText(usdPerTruck, trucks) {
    if (!isNum(usdPerTruck)) { return ''; }
    return usd0(usdPerTruck) + ' per truck per year' + (isNum(trucks) && trucks > 0 ? ' (' + num(trucks) + ' trucks)' : '');
  }

  var KEY_LABELS = { 'derived.tucson_local_route_miles_per_day': 'Tucson local route length', 'prices.partner_set_description': 'Fuel partner program' };

  /** One route over a limit: "TUC-R01: 11.6 h driving, 13.2 h on duty (over the driving limit)". Missing fields are left out. */
  function hosRouteText(r) {
    if (typeof r === 'string') { return r; }
    var parts = [];
    if (isNum(r.driving_hours)) { parts.push(num(r.driving_hours, 1) + ' h driving'); }
    if (isNum(r.on_duty_hours)) { parts.push(num(r.on_duty_hours, 1) + ' h on duty'); }
    var which = { driving: 'over the driving limit', on_duty: 'over the on-duty limit', both: 'over both limits' }[r.limit];
    return (r.route_id ? String(r.route_id) : 'A route') + (parts.length ? ': ' + parts.join(', ') : '') + (which ? ' (' + which + ')' : '');
  }

  /** Hours-of-service check card: the message, the longest route day, and any routes over a limit. */
  function renderHos(box, hos) {
    if (!box || !hos || !hos.message) { return; }
    clear(box);
    var over = hos.routes_over || [];
    box.className = 'hos' + (over.length ? ' warn' : '');
    box.appendChild(el('b', null, hos.message));
    if (isNum(hos.max_driving_hours) && isNum(hos.max_on_duty_hours)) {
      var lim = isNum(hos.limit_driving_hours) && isNum(hos.limit_on_duty_hours) ? ' (limits ' + hos.limit_driving_hours + ' h and ' + hos.limit_on_duty_hours + ' h, FMCSA)' : '';
      box.appendChild(el('small', null, 'Longest route day: ' + num(hos.max_driving_hours, 1) + ' h driving, ' + num(hos.max_on_duty_hours, 1) + ' h on duty' + lim));
    }
    if (over.length) {
      var ul = el('ul', 'hos-list');
      over.forEach(function (r) { ul.appendChild(el('li', null, hosRouteText(r))); });
      box.appendChild(ul);
    }
    box.hidden = false;
  }

  /** A derived entry's note, shown in parentheses after the value (any leading 'Derived:' tag is dropped). */
  function derivedNote(n) { return String(n).replace(/^Derived:\s*/, ''); }

  /** Plain label from an assumption key: drops the section name and spells out the rest. */
  function keyLabel(key) {
    if (KEY_LABELS[key]) { return KEY_LABELS[key]; }
    var parts = String(key).split('.');
    if (parts.length > 1) { parts.shift(); }
    var words = { gal: 'gallon', cpg: '', usd: '', eia: 'EIA', vs: 'versus', mi: '' };
    var t = parts.join(', ').split('_').join(' ').split(' ').map(function (w) { return Object.prototype.hasOwnProperty.call(words, w) ? words[w] : w; }).join(' ');
    t = t.split(' ').filter(function (w) { return w; }).join(' ');
    return t.charAt(0).toUpperCase() + t.slice(1);
  }
  /** "06:00-09:00", "16:00-19:00" becomes "6 to 9 am, 4 to 7 pm (weekdays)". */
  function peakWindows(list) {
    function hr(t) { var h = parseInt(String(t).split(':')[0], 10); return { n: h % 12 === 0 ? 12 : h % 12, ap: h >= 12 && h < 24 ? 'pm' : 'am' }; }
    return list.map(function (w) {
      var p = String(w).split('-'), a = hr(p[0]), b = hr(p[1]);
      return a.n + (a.ap === b.ap ? '' : ' ' + a.ap) + ' to ' + b.n + ' ' + b.ap;
    }).join(', ') + ' (weekdays)';
  }

  var SOURCE_NAMES = {
    'ops.fhwa.dot.gov': 'FHWA Urban Congestion Report', 'static.tti.tamu.edu': 'Texas A&M Urban Mobility Report',
    'bls.gov': 'BLS Occupational Outlook', 'nacfe.org': 'NACFE Run on Less Regional',
    'truckingresearch.org': 'ATRI Operational Costs of Trucking 2026', 'ecfr.gov': 'eCFR 49 CFR 395'
  };

  /** Text with any web address turned into a link named for its source (a plain host name when the source is not known). */
  function linkify(text) {
    var out = document.createDocumentFragment();
    String(text).split(/(https?:\/\/[^\s;)]+)/).forEach(function (part, i) {
      if (i % 2 === 0) { if (part) { out.appendChild(document.createTextNode(part)); } return; }
      var a = document.createElement('a');
      var trail = (part.match(/[.,]+$/) || [''])[0];
      part = part.slice(0, part.length - trail.length);
      var host = part.replace(/^https?:\/\//, '').split('/')[0].replace(/^www\./, '');
      a.href = part; a.rel = 'noopener'; a.target = '_blank';
      a.textContent = SOURCE_NAMES[host] || host;
      out.appendChild(a);
      if (trail) { out.appendChild(document.createTextNode(trail)); }
    });
    return out;
  }

  /** Show a setting with its unit, read from the key (per_gal, cpg, rate, mi, usd, per_hour, per_mile). */
  function assumptionValue(v, key) {
    if (Array.isArray(v) && /peak_windows$/.test(String(key || ''))) { return peakWindows(v); }
    if (v === true) { return 'Yes'; }
    if (v === false) { return 'No'; }
    if (isNum(v)) {
      var k = String(key || ''), money = (v < 0 ? '-' : '') + '$' + Math.abs(v).toFixed(2);
      if (/_time_index$/.test(k)) { return Number(v.toFixed(2)) + '×'; }
      if (/_mpg$/.test(k)) { return v.toFixed(1) + ' mpg'; }
      if (/_usd_per_minute$/.test(k)) { return '$' + v.toFixed(2) + '/minute'; }
      if (/_minutes$/.test(k)) { return v + ' minutes'; }
      if (/_hours$/.test(k)) { return v + ' hours'; }
      if (/_miles$/.test(k)) { return v + ' miles'; }
      if (k.indexOf('miles_per_day') >= 0) { return Number(v.toFixed(1)) + ' mi/day'; }
      if (k.indexOf('per_hour') >= 0) { return money + '/hour'; }
      if (k.indexOf('per_mile') >= 0) { return money + '/mile'; }
      if (k.indexOf('_per_gal') >= 0) { return money + '/gal'; }
      if (/_cpg(\.|$)/.test(k)) { return v + '¢/gal'; }
      if (/_rate(\.|$)/.test(k)) { return Number((v * 100).toFixed(1)) + '%'; }
      if (/_mi(\.|$)/.test(k)) { return v + ' miles'; }
      if (/_usd(\.|$)/.test(k)) { return money; }
      return String(v);
    }
    return typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v);
  }

  /** Fill the "Assumptions behind these numbers" panel from meta.json headline_assumptions. Stays hidden when the field is missing. */
  function renderAssumptionsPanel(meta) {
    var panel = document.getElementById('assump-panel');
    var list = meta && meta.headline_assumptions;
    if (!panel || !list || !list.length) { return; }
    var body = document.getElementById('assump-list');
    clear(body);
    list.forEach(function (a) {
      var row = el('li', 'assump-row');
      var head = el('div', 'assump-head');
      head.appendChild(el('span', 'assump-label', keyLabel(a.key)));
      var right = el('span', 'assump-val');
      if (a.source === 'derived') { right.appendChild(el('span', 'chip amber', 'derived from the data')); right.appendChild(document.createTextNode(' ')); }
      right.appendChild(el('span', 'chip', assumptionValue(a.value, a.key)));
      if (a.source === 'derived' && a.note) { right.appendChild(document.createTextNode(' (' + derivedNote(a.note) + ')')); }
      head.appendChild(right);
      row.appendChild(head);
      if (a.note && a.source !== 'derived') {
        var nt = el('small', 'assump-note');
        nt.appendChild(linkify(String(a.note).replace(/^(Assumption|Setting|Fact|Derived):\s*/, '')));
        row.appendChild(nt);
      }
      body.appendChild(row);
    });
    try { panel.open = window.matchMedia('(min-width: 768px)').matches; } catch (e) { panel.open = true; }
    panel.hidden = false;
  }

  window.FFH = {
    eiaNote: eiaNote, perTruckText: perTruckText, keyLabel: keyLabel, renderAssumptionsPanel: renderAssumptionsPanel,
    EIA_CAPTION: EIA_CAPTION, SYN_CAPTION: SYN_CAPTION, MST: MST, ROUNDING_NOTE: ROUNDING_NOTE, css: css, loadData: loadData, dataUrl: dataUrl, loadOptional: loadOptional,
    isNum: isNum, centsRate: centsRate, assumptionValue: assumptionValue, derivedNote: derivedNote, renderHos: renderHos, linkify: linkify, peakWindows: peakWindows, usd: usd, usd0: usd0, perGal: perGal, price3: price3, num: num, pct: pct, cents: cents,
    dateLabel: dateLabel, dateLong: dateLong, stationLabel: stationLabel, stationIndex: stationIndex,
    el: el, icon: icon, clear: clear, setStatus: setStatus, boot: boot, createMap: createMap, whenReady: whenReady,
    fitTo: fitTo, setSource: setSource, emptyFC: emptyFC, line: line, marker: marker, initSheet: initSheet,
    getUnverified: getUnverified, initUnverifiedToggle: initUnverifiedToggle, unverifiedChip: unverifiedChip, addStarImage: addStarImage,
    downloadCSV: downloadCSV, fleetWeeks: fleetWeeks, chartDefaults: chartDefaults, endTag: endTag, lineChart: lineChart
  };
})();
