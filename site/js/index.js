/* Overview page: KPI tiles, weekly price trend, corridor map. */
FFH.boot(function () {
  return Promise.all([
    FFH.loadData('overview.json'), FFH.loadData('price_history.json'),
    FFH.loadData('stations.geojson'), FFH.loadOptional('missed.json'), FFH.loadOptional('meta.json')
  ]).then(function (r) {
    var ov = r[0], ph = r[1], st = r[2], missed = r[3], meta0 = r[4];
    var idx = FFH.stationIndex(st);
    var weeks = FFH.fleetWeeks(ph, idx);
    renderKpis(ov.kpis || {}, weeks, missed, (meta0 && meta0.trucks_in_fleet) || (missed && missed.trucks_in_fleet), countPartners(st, meta0));
    FFH.renderHos(document.getElementById('hos'), ov.hos_check);
    FFH.renderAssumptionsPanel(meta0);
    var trendChart = null;
    var rangeSel = document.getElementById('range');
    function draw() { trendChart = renderTrend(ph, weeks, Number(rangeSel.value), trendChart); }
    rangeSel.addEventListener('change', draw);
    draw();
    renderMap(st);
  }).then(function () {
    return Promise.all([FFH.loadOptional('partner_scout.json'), FFH.loadOptional('meta.json')])
      .then(function (r) { renderScout(r[0], r[1]); })
      .catch(function () { /* the scout card is optional: it stays hidden */ });
  });

  /** Find an assumption value by key in meta.json (null when missing). */
  function assumption(meta, key) {
    var a = meta && meta.assumptions ? meta.assumptions.filter(function (x) { return x.key === key; })[0] : null;
    return a ? a.value : null;
  }

  /** Curve point of a station at discount x (null when the station has no such step). */
  function pointAt(stn, x) {
    return (stn.curve || []).filter(function (c) { return c.discount_cpg === x; })[0] || null;
  }

  /** Partnership scout: slider for the discount X, best station, top 5 list and the one-sentence callout. */
  function renderScout(sc, meta) {
    var card = document.getElementById('scout');
    if (!card || !sc || !sc.stations || !sc.stations.length || !sc.discount_steps_cpg || !sc.discount_steps_cpg.length) { return; }
    var steps = sc.discount_steps_cpg.slice().sort(function (a, b) { return a - b; });
    var slider = document.getElementById('scout-slider');
    slider.min = 0; slider.max = steps.length - 1; slider.step = 1;
    var start = steps.indexOf(sc.default_discount_cpg);
    slider.value = start >= 0 ? start : 0;
    document.getElementById('scout-chip').textContent = sc.scenario_label || 'Synthetic scenario';
    var ticks = document.getElementById('scout-ticks');
    var every = Math.ceil(steps.length / 8);
    steps.forEach(function (x, i) {
      ticks.appendChild(FFH.el('span', null, (i % every === 0 || i === steps.length - 1) ? FFH.cents(x) : ''));
    });
    var wage = assumption(meta, 'savings.driver_cost_per_hour');
    var lead = 'Based on the fleet\'s actual fills over the period' + (FFH.isNum(wage) ? ', with driver time at ' + FFH.usd(wage) + ' an hour' : '') + '. ';
    document.getElementById('scout-assume').textContent = lead + 'Savings are net of detour costs.';


    function ranked(x) {
      return sc.stations.map(function (s) { return { s: s, p: pointAt(s, x) }; })
        .filter(function (e) { return e.p; })
        .sort(function (a, b) { return b.p.annual_net_savings_usd - a.p.annual_net_savings_usd; });
    }
    function draw() {
      var x = steps[Number(slider.value)];
      var list = ranked(x);
      document.getElementById('scout-x').textContent = FFH.cents(x);
      slider.setAttribute('aria-valuetext', FFH.cents(x) + ' a gallon');
      var tb = document.getElementById('scout-rows');
      tb.textContent = '';
      if (!list.length) { return; }
      var best = list[0], nm = best.s.display_name || best.s.station_id;
      var creates = best.p.annual_net_savings_usd > 0;
      document.getElementById('scout-cpg').textContent = creates ? FFH.centsRate(best.p.net_savings_cpg_captured) : FFH.centsRate(0);
      var an = document.getElementById('scout-annual');
      an.textContent = creates ? FFH.usd0(best.p.annual_net_savings_usd) + ' a year in net savings' : '';
      an.hidden = !creates;
      var pt = document.getElementById('scout-truck');
      var ptxt = creates ? FFH.perTruckText(best.p.annual_net_savings_per_truck_usd, sc.trucks_in_fleet) : '';
      pt.textContent = ptxt;
      pt.hidden = !ptxt;
      document.getElementById('scout-name').textContent = creates ? nm : 'No station stands out yet';
      document.getElementById('scout-gal').textContent = creates ? FFH.num(best.p.gallons_per_month) + ' gallons a month would come here' : '';
      list.slice(0, 5).forEach(function (e, i) {
        var tr = FFH.el('tr', i === 0 && creates ? 'lead' : null);
        tr.appendChild(FFH.el('td', null, e.s.display_name || e.s.station_id));
        tr.appendChild(FFH.el('td', 'num', FFH.num(e.p.gallons_per_month)));
        tr.appendChild(FFH.el('td', 'num', FFH.centsRate(e.p.net_savings_cpg_captured)));
        tr.appendChild(FFH.el('td', 'num', FFH.usd0(e.p.annual_net_savings_usd)));
        tb.appendChild(tr);
      });
      document.getElementById('scout-line').textContent = creates
        ? 'At ' + FFH.cents(x) + ' off, routing Tucson fills to ' + nm + ' would give ' + FFH.centsRate(best.p.net_savings_cpg_captured) + ' saved per gallon moved to this station, ' + FFH.usd0(best.p.annual_net_savings_usd) + ' a year after detour costs.'
        : 'With no discount there is nothing to save yet: a partner discount is what creates the saving.';
    }
    slider.addEventListener('input', draw);
    draw();
    card.hidden = false;
  }

  /** Compare the last 4 weeks with the 4 before them. Returns null when there is not enough history. */
  function delta(values, higherIsGood, mean) {
    if (values.length < 8) { return null; }
    var last = values.slice(-4), prev = values.slice(-8, -4);
    function agg(a) { var s = a.reduce(function (x, y) { return x + y; }, 0); return mean ? s / a.length : s; }
    var a = agg(last), b = agg(prev);
    if (!b) { return null; }
    var ch = (a - b) / b;
    var cls = higherIsGood === null ? 'neutral' : ((ch >= 0) === higherIsGood ? 'good' : 'bad');
    return { text: (ch >= 0 ? '↑ ' : '↓ ') + Math.abs(ch * 100).toFixed(1) + '%', cls: cls };
  }

  /** Counts of partner stations, and of those in Tucson, read from stations.geojson (no hard-coded claim). */
  function countPartners(st, meta) {
    var p = st.features.filter(function (f) { return f.properties.is_partner; });
    var b = assumption(meta, 'fetch.overpass.tucson_metro_bbox');
    var has = b && b.length === 4;
    function inTucson(f) {
      var c = f.geometry.coordinates;
      return has && c[1] >= b[0] && c[0] >= b[1] && c[1] <= b[2] && c[0] <= b[3];
    }
    return { total: p.length, tucson: p.filter(inTucson).length };
  }

  /** "Neither of the 2 partner stations is in Tucson." and its 1 and 3-or-more forms. */
  function noneInTucson(n) {
    if (n === 1) { return 'The 1 partner station is not in Tucson,'; }
    if (n === 2) { return 'Neither of the 2 partner stations is in Tucson,'; }
    return 'None of the ' + n + ' partner stations is in Tucson,';
  }

  /** One plain sentence under the partner tile, derived from the data. */
  function partnerNote(share, c) {
    if (!FFH.isNum(share) || share > 0) { return ''; }
    if (c.total === 0) { return 'No station is a partner yet.'; }
    if (c.tucson === 0) { return noneInTucson(c.total) + ' so no Tucson card gallons get partner pricing.'; }
    return c.tucson + ' of the ' + c.total + ' partner stations are in Tucson, but no Tucson card gallons were bought there.';
  }

  /** "Card 41,200 gal · Phoenix DC tank 39,300 gal", or '' when the split is not in the data. */
  function gallonSplit(k) {
    if (!FFH.isNum(k.card_gallons) || !FFH.isNum(k.yard_gallons)) { return ''; }
    return 'Card ' + FFH.num(k.card_gallons) + ' gal · Phoenix DC tank ' + FFH.num(k.yard_gallons) + ' gal';
  }

  function renderKpis(k, weeks, missed, trucks, partnerCounts) {
    var priced = weeks.filter(function (w) { return FFH.isNum(w.price); });
    var gal = weeks.map(function (w) { return w.gal; });
    var partnerLink = { href: '#scout', text: 'See the partner scout' };
    var miss = missed && missed.weekly ? missed.weekly.map(function (w) { return w.missed_savings_usd; }) : [];
    var tiles = [
      { icon: 'pump', amber: false, label: 'Total gallons', value: FFH.num(k.total_gallons), d: delta(gal, null, false), dlabel: 'card gallons vs prior 4 weeks ', cls: 'v', split: gallonSplit(k) },
      { icon: 'dollar', amber: true, label: 'Avg net price per gallon', value: FFH.perGal(k.avg_net_price_per_gal), d: delta(priced.map(function (w) { return w.price; }), false, true), cls: 'v teal', split: 'Card fills only.' + (FFH.isNum(k.avg_yard_price_per_gal) ? ' Phoenix DC tank: ' + FFH.price3(k.avg_yard_price_per_gal) : '') },
      { icon: 'alert', amber: false, label: 'Missed savings', value: FFH.centsRate(k.missed_savings_cpg_card), unit: ' per card gallon', d: delta(miss, false, false), cls: 'v amber', sub: 'after detour costs',
        split: [FFH.usd0(k.missed_savings_usd) + ' over the period, after detour costs', FFH.perTruckText(k.missed_usd_per_truck_per_year, trucks)] },
      { icon: 'pie', amber: true, label: 'Tucson card gallons at partner pricing', value: FFH.pct(k.tucson_card_gallons_partner_share), d: null, cls: 'v teal',
        split: partnerNote(k.tucson_card_gallons_partner_share, partnerCounts), link: partnerLink }
    ];
    var box = document.getElementById('kpis');
    tiles.forEach(function (t) {
      var c = FFH.el('div', 'kpi');
      var top = FFH.el('div', 'top');
      var b = FFH.el('span', 'badge' + (t.amber ? ' amber' : ''));
      b.appendChild(FFH.icon(t.icon));
      top.appendChild(b);
      top.appendChild(FFH.el('span', null, t.label));
      c.appendChild(top);
      var v = FFH.el('div', t.cls, t.value);
      if (t.unit) { v.appendChild(FFH.el('span', 'unit', t.unit)); }
      c.appendChild(v);
      [].concat(t.split || []).filter(function (x) { return x; }).forEach(function (line) { c.appendChild(FFH.el('div', 'split', line)); });
      if (t.link) {
        var lk = FFH.el('div', 'split');
        var a = FFH.el('a', null, t.link.text);
        a.href = t.link.href;
        lk.appendChild(a);
        c.appendChild(lk);
      }
      var dl = FFH.el('div', 'delta');
      if (t.d) {
        dl.appendChild(document.createTextNode(t.dlabel || 'vs prior 4 weeks '));
        dl.appendChild(FFH.el('span', t.d.cls, t.d.text));
      } else {
        dl.textContent = t.sub || 'Whole synthetic period';
      }
      c.appendChild(dl);
      box.appendChild(c);
    });
  }

  /** Draw the weekly trend for the last N weeks (0 means all). Returns the chart so it can be replaced. */
  function renderTrend(ph, allWeeks, n, old) {
    var weeks = n > 0 ? allWeeks.slice(-n) : allWeeks;
    if (old) { old.destroy(); }
    var eiaBy = {};
    (ph.eia || []).forEach(function (e) { eiaBy[e.week_start] = e.price_per_gal; });
    var labels = weeks.map(function (w) { return FFH.dateLabel(w.week); });
    var paid = weeks.map(function (w) { return FFH.isNum(w.price) ? +w.price.toFixed(3) : null; });
    var eia = weeks.map(function (w) { return FFH.isNum(eiaBy[w.week]) ? eiaBy[w.week] : null; });
    var latest = paid.filter(FFH.isNum).pop();
    if (!window.Chart) { return null; }
    return FFH.lineChart(document.getElementById('trend'), labels, paid, eia, FFH.isNum(latest) ? '$' + latest.toFixed(2) : '');
  }

  function renderMap(st) {
    var map = FFH.createMap('corridor-map', null, 6, false);
    if (!map) { return; }
    FFH.whenReady(map, function () {
      FFH.setSource(map, 'stn', st);
      FFH.addStarImage(map, 'ffh-partner-star');
      var op = ['case', ['==', ['get', 'truck_access'], 'unverified'], 0.45, 1];
      map.addLayer({
        id: 'stn-dots', type: 'circle', source: 'stn', filter: ['!', ['get', 'is_partner']],
        paint: { 'circle-radius': 4, 'circle-color': FFH.css('--teal-600'), 'circle-opacity': op, 'circle-stroke-opacity': op,
          'circle-stroke-color': FFH.css('--card'), 'circle-stroke-width': 1.5 }
      });
      map.addLayer({
        id: 'stn-stars', type: 'symbol', source: 'stn', filter: ['get', 'is_partner'],
        layout: { 'icon-image': 'ffh-partner-star', 'icon-size': 0.5, 'icon-allow-overlap': true }, paint: { 'icon-opacity': op }
      });
      FFH.fitTo(map, st.features.map(function (f) { return f.geometry.coordinates; }), 24);
    });
  }
});
