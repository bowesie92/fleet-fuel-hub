/* Station map page: price-colored dots, partner stars, dimmed unverified stations, detail panel with price history. */
FFH.boot(function () {
  return Promise.all([
    FFH.loadData('stations.geojson'), FFH.loadData('price_history.json'), FFH.loadOptional('detours.json'), FFH.loadOptional('meta.json')
  ]).then(function (r) { start(r[0], r[1], r[2], r[3]); });

  function median(a) {
    if (!a.length) { return null; }
    var s = a.slice().sort(function (x, y) { return x - y; }), m = Math.floor(s.length / 2);
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  }

  /** Tucson filter box (south, west, north, east) from meta.json assumptions; null when absent. */
  function tucsonBox(meta) {
    var a = meta && meta.assumptions ? meta.assumptions.filter(function (x) { return x.key === 'fetch.overpass.tucson_metro_bbox'; })[0] : null;
    return a && a.value && a.value.length === 4 ? a.value : null;
  }

  function start(st, ph, det, meta) {
    var box = tucsonBox(meta);
    var feats = st.features || [];
    var all = feats.map(function (f) { return f.geometry.coordinates; });
    var tucson = (box ? feats.filter(function (f) {
      var c = f.geometry.coordinates;
      return c[1] >= box[0] && c[0] >= box[1] && c[1] <= box[2] && c[0] <= box[3];
    }) : [])
      .map(function (f) { return f.geometry.coordinates; });
    var panel = document.getElementById('panel');
    FFH.initSheet(panel, document.getElementById('handle'), document.getElementById('p-more'));
    var chart = null, current = null, includeUnv = false;
    var PAD = window.innerWidth > 767 ? { top: 120, bottom: 60, left: 60, right: 60 } : { top: 110, bottom: 60, left: 30, right: 30 };

    var prices = feats.map(function (f) { return f.properties.avg_price_paid; }).filter(FFH.isNum);
    var min = Math.min.apply(null, prices), max = Math.max.apply(null, prices);
    var step = Math.max((max - min) / 5, 0.001);
    var cuts = [1, 2, 3, 4].map(function (i) { return min + step * i; });
    var cols = [1, 2, 3, 4, 5].map(function (i) { return FFH.css('--price-' + i); });
    renderLegend(prices.length, min, step, cols);

    var map = FFH.createMap('map', null, 7);
    document.getElementById('corridor').addEventListener('change', function (e) {
      if (map) { FFH.fitTo(map, e.target.value === 'tucson' && tucson.length ? tucson : all, PAD, true); }
    });
    document.getElementById('close').addEventListener('click', function () { panel.hidden = true; });
    FFH.initUnverifiedToggle(det && det.include_unverified_default, function (on) {
      includeUnv = on;
      if (current) { show(current); }
    });
    if (!map) { return; }
    map.addControl(new maplibregl.GeolocateControl({ trackUserLocation: false }), 'bottom-right');

    FFH.whenReady(map, function () {
      FFH.addStarImage(map, 'ffh-partner-star');
      FFH.setSource(map, 'stations', st);
      var fill = prices.length
        ? ['case', ['==', ['typeof', ['get', 'avg_price_paid']], 'number'],
          ['step', ['get', 'avg_price_paid'], cols[0], cuts[0], cols[1], cuts[1], cols[2], cuts[2], cols[3], cuts[3], cols[4]], FFH.css('--benchmark')]
        : FFH.css('--benchmark');
      var op = ['case', ['==', ['get', 'truck_access'], 'unverified'], 0.45, 1];
      map.addLayer({ id: 'dots', type: 'circle', source: 'stations', filter: ['!', ['get', 'is_partner']],
        paint: { 'circle-radius': 7, 'circle-color': fill, 'circle-opacity': op, 'circle-stroke-opacity': op, 'circle-stroke-color': FFH.css('--card'), 'circle-stroke-width': 1.5 } });
      map.addLayer({ id: 'partner-disc', type: 'circle', source: 'stations', filter: ['get', 'is_partner'],
        paint: { 'circle-radius': 13, 'circle-color': fill, 'circle-opacity': op, 'circle-stroke-opacity': op, 'circle-stroke-color': FFH.css('--card'), 'circle-stroke-width': 2 } });
      map.addLayer({ id: 'partner-star', type: 'symbol', source: 'stations', filter: ['get', 'is_partner'],
        layout: { 'icon-image': 'ffh-partner-star', 'icon-size': 0.55, 'icon-allow-overlap': true }, paint: { 'icon-opacity': op } });
      FFH.fitTo(map, all, PAD);
      var popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 14 });
      ['dots', 'partner-disc'].forEach(function (l) {
        map.on('mouseenter', l, function (e) {
          map.getCanvas().style.cursor = 'pointer';
          var p = e.features[0].properties;
          var txt = FFH.stationLabel(p) + ': ' + (FFH.isNum(p.avg_price_paid) ? FFH.perGal(p.avg_price_paid) + ' avg paid' : 'no fills') +
            (p.truck_access === 'unverified' ? ' (truck access unverified)' : '');
          popup.setLngLat(e.features[0].geometry.coordinates).setText(txt).addTo(map);
        });
        map.on('mouseleave', l, function () { map.getCanvas().style.cursor = ''; popup.remove(); });
        map.on('click', l, function (e) { current = e.features[0].properties; show(current); });
      });
    });

    function lastWeeks(rows) {
      return rows.slice(-4).filter(function (w) { return FFH.isNum(w.avg_price_paid) && FFH.isNum(w.gallons) && w.gallons > 0; });
    }
    function wavg(rows) {
      var g = 0, s = 0;
      rows.forEach(function (w) { g += w.gallons; s += w.gallons * w.avg_price_paid; });
      return g ? s / g : null;
    }
    /** Typical detour and net saving for a station across the Tucson routes in detours.json. */
    function typical(sid) {
      var mins = [], net = [];
      if (det && det.routes) {
        Object.keys(det.routes).forEach(function (rid) {
          var r = det.routes[rid];
          (r.options || []).forEach(function (o) {
            if (o.station_id === sid && r.baseline_station_id !== sid) { mins.push(o.extra_minutes); net.push(o.net_savings_vs_baseline_usd); }
          });
        });
      }
      return { minutes: median(mins), net: median(net) };
    }

    function show(p) {
      var rows = (ph.stations || {})[p.station_id] || [];
      var eiaBy = {};
      (ph.eia || []).forEach(function (e) { eiaBy[e.week_start] = e.price_per_gal; });
      var recent = lastWeeks(rows);
      var paid4 = wavg(recent);
      var eia4 = recent.length ? recent.reduce(function (a, w) { return a + (eiaBy[w.week_start] || 0); }, 0) / recent.length : null;
      document.getElementById('p-name').textContent = FFH.stationLabel(p);
      var cls = { travel_center: 'Travel center', branded: 'Branded station', unbranded: 'Unbranded station', cardlock: 'Cardlock (fleet fueling)' }[p.station_class] || 'Station';
      var sub = document.getElementById('p-sub');
      FFH.clear(sub);
      sub.appendChild(document.createTextNode(cls + '  '));
      if (p.is_partner) {
        sub.appendChild(FFH.el('span', 'chip', 'Partner' + (FFH.isNum(p.partner_discount_cpg) ? ', ' + FFH.cents(p.partner_discount_cpg) + ' off posted' : '')));
      }
      if (p.truck_access === 'unverified') { sub.appendChild(document.createTextNode(' ')); sub.appendChild(FFH.unverifiedChip()); }
      var loc = document.getElementById('p-loc');
      if (!loc) {
        loc = FFH.el('p', 'sub');
        loc.id = 'p-loc';
        document.getElementById('p-name').insertAdjacentElement('afterend', loc);
      }
      FFH.clear(loc);
      if (p.city) { loc.appendChild(FFH.icon('pin', true)); loc.appendChild(document.createTextNode(' ' + p.city + ', AZ')); }
      loc.hidden = !p.city;
      document.getElementById('p-price').textContent = FFH.isNum(paid4) ? '$' + paid4.toFixed(2) : 'n/a';
      var chip = document.getElementById('p-chip'), chipSrc = document.getElementById('p-chip-src');
      if (FFH.isNum(paid4) && FFH.isNum(eia4) && eia4 > 0) {
        var diff = (eia4 - paid4) * 100;
        chip.className = 'chip ' + (diff >= 0 ? 'gain' : 'loss');
        chip.textContent = FFH.cents(Math.abs(diff)) + (diff >= 0 ? ' below' : ' above') + ' regional avg';
        if (chipSrc) { chipSrc.hidden = false; }
      } else {
        chip.className = 'chip'; chip.textContent = FFH.isNum(paid4) ? 'No regional average this week' : 'No recent fills';
        if (chipSrc) { chipSrc.hidden = !FFH.isNum(paid4); }
      }
      renderMetrics(p, paid4);
      var be = document.getElementById('p-be'), bes = document.getElementById('p-be-sub'), bePeak = document.getElementById('p-be-peak');
      var fillGal = det && FFH.isNum(det.typical_fill_gal) ? det.typical_fill_gal : null;
      var rushNote = det && det.rush_hour_note ? det.rush_hour_note : '';
      if (FFH.isNum(p.break_even_cpg)) {
        be.textContent = 'A detour here is only worth it if it is at least ' + p.break_even_cpg.toFixed(1) + ' cents a gallon cheaper.';
        bePeak.textContent = p.break_even_is_peak ? (rushNote || 'This figure is for a stop at weekday rush hour, when detour minutes count extra.') : '';
        bes.textContent = 'Counts extra fuel, driver time and wear' + (fillGal ? ', based on a ' + fillGal + '-gallon fill and the median Tucson route detour.' : '.');
      } else {
        be.textContent = 'No break-even yet for this station.';
        bePeak.textContent = '';
        bes.textContent = 'There are no route detours to this station to measure.';
      }
      drawChart(rows, eiaBy);
      panel.hidden = false;
    }

    function renderMetrics(p, paid4) {
      var m = document.getElementById('p-metrics');
      FFH.clear(m);
      var t = typical(p.station_id);
      var hidden = p.truck_access === 'unverified' && !includeUnv;
      var net = hidden ? 'Excluded' : (FFH.isNum(t.net) ? (t.net < 0 ? '-' : '') + FFH.usd(Math.abs(t.net)) : 'n/a');
      var mins = FFH.isNum(t.minutes) ? FFH.num(t.minutes, 0) + ' min' : 'n/a';
      [['Avg paid, 4 wks', FFH.isNum(paid4) ? FFH.perGal(paid4) : 'n/a', ''],
       ['Typical detour', mins, 'navy'],
       ['Net savings', net, '']].forEach(function (x) {
        var d = FFH.el('div');
        d.appendChild(FFH.el('div', 'l', x[0]));
        d.appendChild(FFH.el('div', 'v ' + x[2], x[1]));
        m.appendChild(d);
      });
      var note = document.getElementById('p-metric-note');
      if (!note) {
        note = FFH.el('p', 'foot m-only');
        note.id = 'p-metric-note';
        m.insertAdjacentElement('afterend', note);
      }
      note.textContent = hidden ? 'Truck access is unverified. Turn on "Include unverified truck access" to count this station.' :
        'Net savings: typical fill, median across routes, after detour costs (synthetic).';
    }

    function drawChart(rows, eiaBy) {
      if (!window.Chart) { return; }
      if (chart) { chart.destroy(); chart = null; }
      var labels = rows.map(function (w) { return FFH.dateLabel(w.week_start); });
      var paid = rows.map(function (w) { return FFH.isNum(w.avg_price_paid) ? w.avg_price_paid : null; });
      var eia = rows.map(function (w) { return FFH.isNum(eiaBy[w.week_start]) ? eiaBy[w.week_start] : null; });
      var tag = paid.filter(FFH.isNum).pop();
      chart = FFH.lineChart(document.getElementById('chart'), labels, paid, eia, FFH.isNum(tag) ? '$' + tag.toFixed(2) : '');
    }
  }

  function renderLegend(hasPrices, min, step, cols) {
    var box = document.getElementById('legend');
    box.appendChild(FFH.el('h3', null, 'Avg net price paid per gallon'));
    box.appendChild(FFH.el('div', 'foot', 'Whole period (synthetic)'));
    if (!hasPrices) { box.appendChild(FFH.el('div', 'bin', 'No fills yet')); }
    else {
      cols.forEach(function (c, i) {
        var lo = min + step * i, hi = min + step * (i + 1);
        var txt = i === 0 ? 'Under $' + hi.toFixed(2) : (i === 4 ? '$' + lo.toFixed(2) + ' and up' : '$' + lo.toFixed(2) + ' to $' + hi.toFixed(2));
        var b = FFH.el('div', 'bin');
        var sw = FFH.el('i');
        sw.style.background = c;
        b.appendChild(sw);
        b.appendChild(document.createTextNode(txt));
        box.appendChild(b);
      });
    }
    box.appendChild(FFH.eiaNote());
    var s = FFH.el('div', 'bin');
    s.appendChild(FFH.el('span', 'star', '★'));
    s.appendChild(document.createTextNode('Partner station (navy star)'));
    box.appendChild(s);
    var d = FFH.el('div', 'bin');
    var sw2 = FFH.el('i');
    sw2.style.background = FFH.css('--benchmark');
    sw2.style.opacity = '0.45';
    d.appendChild(sw2);
    d.appendChild(document.createTextNode('Dimmed: truck access unverified'));
    box.appendChild(d);
  }
});
