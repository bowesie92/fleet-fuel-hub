/* Missed savings page: hero number, weekly bars, sortable table, route comparison map. */
FFH.boot(function () {
  return Promise.all([
    FFH.loadData('missed.json'), FFH.loadData('stations.geojson'),
    FFH.loadOptional('detour_paths.geojson'), FFH.loadOptional('routes.geojson'), FFH.loadOptional('detours.json'), FFH.loadOptional('meta.json')
  ]).then(function (r) { FFH.renderAssumptionsPanel(r[5]); start(r[0], r[1], r[2], r[3], r[4]); });

  function start(missed, st, paths, routes, det) {
    var idx = FFH.stationIndex(st);
    var rows = [], view = missed, barChart = null, hiddenCols = {};
    var sort = { key: 'net_savings_usd', dir: -1 };
    var page = 0, per = 10, selected = null;
    var map = null, mapMarkers = [];

    var COLS = [
      { key: 'date', label: 'Date', val: function (r) { return FFH.dateLabel(r.date); } },
      { key: 'truck_id', label: 'Truck' },
      { key: 'actual_station_id', label: 'Station used', val: function (r) { return stn(r.actual_station_id); } },
      { key: 'alt_station_id', label: 'Lower-cost option', val: function (r) { return stn(r.alt_station_id); }, chip: true },
      { key: 'why', label: 'Why', val: function (r) { return r.why || ''; } },
      { key: 'alt_extra_miles', label: 'Extra miles', num: true, val: function (r) { return FFH.num(r.alt_extra_miles, 1); }, dot: true },
      { key: 'net_savings_usd', label: 'Net dollars missed', num: true, val: function (r) { return FFH.usd(r.net_savings_usd); }, cls: 'bad' }
    ];

    map = FFH.createMap('map', null, 9);
    renderColMenu();
    FFH.initUnverifiedToggle(det && det.include_unverified_default, setMode);

    /** Switch between confirmed-only fields and the *_incl_unverified fields. */
    function setMode(on) {
      function pick(k) { return on && missed[k + '_incl_unverified'] !== undefined ? missed[k + '_incl_unverified'] : missed[k]; }
      view = { cpg: pick('cpg_per_card_gallon'), per_truck: pick('usd_per_truck_per_year'), total_usd: pick('total_usd'), fill_count: pick('fill_count'), weekly: pick('weekly'), rows: pick('rows') };
      rows = (view.rows || []).slice().sort(function (a, b) { return b.net_savings_usd - a.net_savings_usd; });
      page = 0; selected = null;
      renderHero(); renderBars(); renderTable();
      if (rows.length) { choose(rows[0]); } else { renderEmpty(); }
    }

    /** No purchases in the period: say so instead of leaving a stale comparison on screen. */
    function renderEmpty() {
      document.getElementById('cmp-sub').textContent = 'No purchases to compare';
      var c = document.getElementById('cmp');
      FFH.clear(c);
      c.appendChild(FFH.el('div', null, 'No fill in this period had a lower-cost, reachable station once detour costs are counted.'));
      mapMarkers.forEach(function (m) { m.remove(); });
      mapMarkers = [];
      if (map) {
        FFH.whenReady(map, function () {
          ['chosen', 'used', 'alt'].forEach(function (id) { if (map.getLayer(id)) { map.removeLayer(id); } if (map.getSource(id)) { map.removeSource(id); } });
        });
      }
    }

    function visibleCols() { return COLS.filter(function (c) { return !hiddenCols[c.key]; }); }
    function renderColMenu() {
      var btn = document.getElementById('cols'), menu = document.getElementById('colmenu');
      COLS.forEach(function (c) {
        var l = FFH.el('label');
        var cb = document.createElement('input');
        cb.type = 'checkbox'; cb.checked = true;
        cb.addEventListener('change', function () { hiddenCols[c.key] = !cb.checked; renderTable(); });
        l.appendChild(cb);
        l.appendChild(document.createTextNode(c.label));
        menu.appendChild(l);
      });
      btn.addEventListener('click', function () {
        menu.hidden = !menu.hidden;
        btn.setAttribute('aria-expanded', String(!menu.hidden));
      });
    }

    document.getElementById('per').addEventListener('change', function (e) { per = Number(e.target.value); page = 0; renderTable(); });
    document.getElementById('prev').addEventListener('click', function () { if (page > 0) { page--; renderTable(); } });
    document.getElementById('next').addEventListener('click', function () { if ((page + 1) * per < rows.length) { page++; renderTable(); } });
    document.getElementById('export').addEventListener('click', function () {
      FFH.downloadCSV('missed_savings.csv', COLS.map(function (c) { return c.key; }), sorted());
    });

    function stn(id) { return idx[id] ? idx[id].label : id; }


    function sorted() {
      var c = COLS.filter(function (x) { return x.key === sort.key; })[0];
      return rows.slice().sort(function (a, b) {
        var va = a[sort.key], vb = b[sort.key];
        if (va === null || va === undefined) { return 1; }
        if (vb === null || vb === undefined) { return -1; }
        return (va < vb ? -1 : (va > vb ? 1 : 0)) * sort.dir;
      });
    }

    function renderHero() {
      var wk = (view.weekly || []).slice().sort(function (a, b) { return a.week_start < b.week_start ? -1 : 1; });
      var last = wk[wk.length - 1], prev = wk[wk.length - 2];
      FFH.clear(document.getElementById('hero-delta'));
      document.getElementById('hero-cpg').textContent = FFH.centsRate(view.cpg);
      var wkEl = document.getElementById('hero-week');
      wkEl.textContent = last ? 'Latest week: ' + FFH.usd(last.missed_savings_usd) : '';
      wkEl.hidden = !last;
      var pt = document.getElementById('hero-truck');
      var ptxt = FFH.perTruckText(view.per_truck, missed.trucks_in_fleet);
      if (ptxt) { ptxt = 'Whole period: ' + ptxt; }
      pt.textContent = ptxt;
      pt.hidden = !ptxt;
      var d = document.getElementById('hero-delta');
      if (last && prev && prev.missed_savings_usd > 0) {
        var ch = (last.missed_savings_usd - prev.missed_savings_usd) / prev.missed_savings_usd;
        d.appendChild(FFH.el('span', ch > 0 ? 'bad' : 'good', (ch >= 0 ? '↑ ' : '↓ ') + Math.abs(ch * 100).toFixed(1) + '%'));
        d.appendChild(document.createTextNode(' vs prior week'));
      } else if (last) { d.textContent = 'Week of ' + FFH.dateLong(last.week_start); }
      document.getElementById('hero-note').textContent = 'Whole period: ' + FFH.usd(view.total_usd) + ' across ' + FFH.num(view.fill_count) +
        ' fills, ranked by net dollars after detour costs (synthetic).';
    }

    function renderBars() {
      if (!window.Chart) { return; }
      FFH.chartDefaults();
      var wk = (view.weekly || []).slice().sort(function (a, b) { return a.week_start < b.week_start ? -1 : 1; }).slice(-8);
      if (barChart) { barChart.destroy(); }
      barChart = new Chart(document.getElementById('weekly'), {
        type: 'bar',
        data: { labels: wk.map(function (w) { return FFH.dateLabel(w.week_start); }),
          datasets: [{ label: 'Missed savings', data: wk.map(function (w) { return w.missed_savings_usd; }),
            backgroundColor: wk.map(function (w, i) { return i === wk.length - 1 ? FFH.css('--navy-900') : FFH.css('--benchmark'); }), borderRadius: 4 }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false },
          tooltip: { callbacks: { label: function (c) { return FFH.usd(c.parsed.y); } } } },
          scales: { y: { beginAtZero: true, ticks: { callback: function (v) { return '$' + v; } } }, x: { grid: { display: false } } } }
      });
    }

    function renderTable() {
      var t = document.getElementById('rows');
      FFH.clear(t);
      var hr = FFH.el('tr');
      visibleCols().forEach(function (c) {
        var th = FFH.el('th', c.num ? 'num' : null);
        th.setAttribute('aria-sort', sort.key === c.key ? (sort.dir > 0 ? 'ascending' : 'descending') : 'none');
        var b = FFH.el('button', null, c.label);
        b.type = 'button';
        b.appendChild(FFH.icon('sort', true));
        b.addEventListener('click', function () {
          if (sort.key === c.key) { sort.dir = -sort.dir; } else { sort.key = c.key; sort.dir = -1; }
          page = 0; renderTable();
        });
        th.appendChild(b);
        hr.appendChild(th);
      });
      t.appendChild(FFH.el('thead')).appendChild(hr);
      var body = FFH.el('tbody');
      var all = sorted();
      all.slice(page * per, page * per + per).forEach(function (r) {
        var tr = FFH.el('tr', 'clickable' + (selected === r.txn_id ? ' selected' : ''));
        tr.tabIndex = 0;
        visibleCols().forEach(function (c) {
          var td = FFH.el('td', (c.num ? 'num ' : '') + (c.cls || ''));
          if (c.dot) { td.appendChild(FFH.el('span', 'dot')); }
          td.appendChild(document.createTextNode(c.val ? c.val(r) : r[c.key]));
          if (c.chip && r.alt_truck_access === 'unverified') { td.appendChild(document.createTextNode(' ')); td.appendChild(FFH.unverifiedChip()); }
          tr.appendChild(td);
        });
        function pick() { choose(r); }
        tr.addEventListener('click', pick);
        tr.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } });
        body.appendChild(tr);
      });
      if (!all.length) {
        var er = FFH.el('tr');
        var ec = FFH.el('td', null, 'No missed savings in this period.');
        ec.colSpan = visibleCols().length || 1;
        er.appendChild(ec);
        body.appendChild(er);
      }
      t.appendChild(body);
      var from = all.length ? page * per + 1 : 0, to = Math.min(all.length, page * per + per);
      document.getElementById('pager-info').textContent = 'Showing ' + from + ' to ' + to + ' of ' + all.length + ' results';
      document.getElementById('table-sub').textContent = 'Showing ' + all.length + ' purchases. Click a row to compare the two route options.';
    }

    function choose(r) {
      selected = r.txn_id;
      renderTable();
      document.getElementById('cmp-sub').textContent = r.truck_id + ' on ' + FFH.dateLabel(r.date) + ' (' + r.txn_id + ')';
      var c = document.getElementById('cmp');
      FFH.clear(c);
      var usedMi = FFH.isNum(r.actual_extra_miles) ? FFH.num(r.actual_extra_miles, 1) + ' extra miles, ' + FFH.num(r.actual_extra_minutes, 0) + ' extra minutes' : 'detour not given';
      c.appendChild(FFH.el('div', null, 'Station used: ' + stn(r.actual_station_id) + ' at ' + FFH.perGal(r.actual_price_per_gal) + ', ' + usedMi));
      c.appendChild(FFH.el('div', null, 'Lower-cost option: ' + stn(r.alt_station_id) + (r.alt_truck_access === 'unverified' ? ' (truck access unverified)' : '') + ' at ' + FFH.perGal(r.alt_price_per_gal) + ', ' +
        FFH.num(r.alt_extra_miles, 1) + ' extra miles, ' + FFH.num(r.alt_extra_minutes, 0) + ' extra minutes'));
      if (r.why_line) { c.appendChild(FFH.el('div', 'why-line', r.why_line)); }
      if (r.is_peak) { c.appendChild(FFH.el('div', 'foot', 'This fill was at rush hour, so detour minutes count ' + FFH.num(r.multiplier, 2) + ' times.')); }
      c.appendChild(FFH.el('div', 'good', 'Net saving after detour costs: ' + FFH.usd(r.net_savings_usd) + ' on ' + FFH.num(r.gallons, 1) + ' gal'));
      var parts = breakdown(r);
      if (parts) { c.appendChild(FFH.el('div', 'foot', parts)); }
      draw(r);
    }

    /** "Price gap x gallons $A + detour avoided at the station used $B - detour to the alternative $C = net saving $D", or '' when a part is missing. */
    function breakdown(r) {
      if (!FFH.isNum(r.price_gap_usd) || !FFH.isNum(r.actual_detour_cost_usd) || !FFH.isNum(r.alt_detour_cost_usd)) { return ''; }
      return 'Price gap x gallons ' + FFH.usd(r.price_gap_usd) + ' + detour avoided at the station used ' + FFH.usd(r.actual_detour_cost_usd) +
        ' - detour to the alternative ' + FFH.usd(r.alt_detour_cost_usd) + ' = net saving ' + FFH.usd(r.net_savings_usd) + '. ' + FFH.ROUNDING_NOTE;
    }

    /** The road path of one detour leg from detour_paths.geojson, or null. */
    function legPath(route_id, station_id, after) {
      return paths && paths.features ? (paths.features.filter(function (f) {
        var p = f.properties || {};
        return p.route_id === route_id && p.station_id === station_id && Number(p.insert_after_stop) === Number(after) && f.geometry && f.geometry.coordinates && f.geometry.coordinates.length > 1;
      })[0] || null) : null;
    }

    function stopCoords(route_id, seq) {
      var f = routes ? (routes.features || []).filter(function (x) { return x.properties.kind === 'stop' && x.properties.route_id === route_id && x.properties.seq === seq; })[0] : null;
      return f ? f.geometry.coordinates : null;
    }

    /** One detour leg: the road path when we have it, else a straight line through the station (null when nothing is known). */
    function leg(r, sid, after) {
      var road = legPath(r.route_id, sid, after);
      if (road) { return { f: { type: 'Feature', properties: {}, geometry: road.geometry }, road: true }; }
      var s = idx[sid], a = stopCoords(r.route_id, after), b = stopCoords(r.route_id, after + 1);
      if (s && a && b) { return { f: FFH.line([a, s.coords, b]), road: false }; }
      if (s) { return { f: FFH.line([s.coords, s.coords]), road: false, point: true }; }
      return null;
    }

    function draw(r) {
      if (!map) { return; }
      FFH.whenReady(map, function () {
        mapMarkers.forEach(function (m) { m.remove(); });
        mapMarkers = [];
        ['chosen', 'used', 'alt'].forEach(function (id) { if (map.getLayer(id)) { map.removeLayer(id); } if (map.getSource(id)) { map.removeSource(id); } });
        var a = idx[r.actual_station_id], b = idx[r.alt_station_id];
        var ch = routes ? (routes.features || []).filter(function (f) { return f.properties.kind === 'route' && f.properties.route_id === r.route_id; })[0] : null;
        var la = leg(r, r.actual_station_id, r.actual_insert_after_stop), lb = leg(r, r.alt_station_id, r.alt_insert_after_stop);
        var al = lb && !lb.point ? lb.f : null;
        var pts = [];
        var fallback = (la && !la.road && !la.point) || (lb && !lb.road && !lb.point);
        document.getElementById('cmp-sub').textContent += fallback ? ' (straight lines where road paths are not available)' : '';
        if (ch) {
          map.addSource('chosen', { type: 'geojson', data: ch });
          map.addLayer({ id: 'chosen', type: 'line', source: 'chosen', paint: { 'line-color': FFH.css('--teal-600'), 'line-width': 4 } });
          pts = pts.concat(ch.geometry.coordinates);
        }
        if (la && !la.point) {
          map.addSource('used', { type: 'geojson', data: la.f });
          map.addLayer({ id: 'used', type: 'line', source: 'used', layout: { 'line-cap': 'round' }, paint: { 'line-color': FFH.css('--navy-900'), 'line-width': 3, 'line-dasharray': [2, 2] } });
          pts = pts.concat(la.f.geometry.coordinates);
        }
        if (al) {
          map.addSource('alt', { type: 'geojson', data: al });
          map.addLayer({ id: 'alt', type: 'line', source: 'alt', paint: { 'line-color': FFH.css('--amber-500'), 'line-width': 3, 'line-dasharray': [2, 2] } });
          pts = pts.concat(al.geometry.coordinates);
        }
        if (a) { mapMarkers.push(FFH.marker(map, a.coords, 'used', FFH.icon('pump'), null, 'Station used: ' + a.label)); pts.push(a.coords); }
        if (b) { mapMarkers.push(FFH.marker(map, b.coords, 'alt' + (r.alt_truck_access === 'unverified' ? ' dim' : ''), FFH.icon('pump'), null, 'Lower-cost option: ' + b.label)); pts.push(b.coords); }
        FFH.fitTo(map, pts, 40, true);
      });
    }

    if (rows.length) { choose(rows[0]); }
  }
});
