/* Test route page: pick a route, click a station, see the detour and net cost. */
FFH.boot(function () {
  return Promise.all([
    FFH.loadData('routes.geojson'), FFH.loadData('detours.json'), FFH.loadData('stations.geojson'), FFH.loadOptional('meta.json'), FFH.loadOptional('overview.json'), FFH.loadOptional('detour_paths.geojson')
  ]).then(function (r) { FFH.renderHos(document.getElementById('hos'), r[4] && r[4].hos_check); start(r[0], r[1], r[2], r[3], r[5]); });

  /** The longest detour allowed, in minutes, from meta.json (null when the file does not say). */
  function maxDetour(meta) {
    var a = meta && meta.assumptions ? meta.assumptions.filter(function (x) { return x.key === 'time_cost.max_detour_minutes'; })[0] : null;
    return a && FFH.isNum(a.value) ? a.value : null;
  }

  /** "17:00" becomes "5 pm". */
  function clock(t) {
    var h = parseInt(String(t).split(':')[0], 10);
    return (h % 12 === 0 ? 12 : h % 12) + (h >= 12 ? ' pm' : ' am');
  }

  function start(routes, det, st, meta, paths) {
    var limit = maxDetour(meta);
    var we = det.worked_example || null;
    var idx = FFH.stationIndex(st);
    var routeFeats = routes.features.filter(function (f) { return f.properties.kind === 'route' && String(f.properties.route_id).indexOf('TUC-R') === 0; });
    // Linehaul runs (PHX-L..) stay in routes.geojson but are not selectable here.
    var sel = document.getElementById('route-select');
    routeFeats.forEach(function (f) {
      var o = FFH.el('option', null, f.properties.label || f.properties.route_id);
      o.value = f.properties.route_id;
      sel.appendChild(o);
    });
    if (det.default_route_id && routeFeats.some(function (f) { return f.properties.route_id === det.default_route_id; })) { sel.value = det.default_route_id; }
    renderNotes();
    var panel = document.getElementById('panel');
    var setOpen = FFH.initSheet(panel, document.getElementById('handle'), document.getElementById('p-more'));
    var map = FFH.createMap('map', null, 10);
    var markers = [], stopMarkers = [], popup = null;
    var cur = { rid: null, sid: null };
    var ready = false, includeUnv = false, noRoad = false;
    function bestId(ri) {
      if (!ri) { return null; }
      return includeUnv && ri.best_station_id_incl_unverified ? ri.best_station_id_incl_unverified : ri.best_station_id;
    }
    function bestReason(ri) {
      return includeUnv && ri.best_reason_incl_unverified ? ri.best_reason_incl_unverified : ri.best_reason;
    }

    if (!routeFeats.length) { FFH.setStatus('No routes were found in the data.', true); return; }
    sel.addEventListener('change', function () { chooseRoute(sel.value); });
    if (map) {
      FFH.whenReady(map, function () {
        FFH.setSource(map, 'route', FFH.emptyFC());
        FFH.setSource(map, 'detour', FFH.emptyFC());
        map.addLayer({ id: 'route-line', type: 'line', source: 'route', layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': FFH.css('--teal-600'), 'line-width': 4 } });
        map.addLayer({ id: 'detour-line', type: 'line', source: 'detour', layout: { 'line-cap': 'round' }, paint: { 'line-color': FFH.css('--amber-500'), 'line-width': 3, 'line-dasharray': [2, 2] } });
        ready = true;
        chooseRoute(sel.value);
      });
    }
    FFH.initUnverifiedToggle(det.include_unverified_default, function (on) { includeUnv = on; chooseRoute(sel.value); });

    function routeInfo(rid) { return det.routes ? det.routes[rid] : null; }
    function optionFor(rid, sid) {
      var ri = routeInfo(rid);
      if (!ri) { return null; }
      return (ri.options || []).filter(function (o) { return o.station_id === sid; })[0] || null;
    }
    function stopsOf(rid) {
      return routes.features.filter(function (f) { return f.properties.kind === 'stop' && f.properties.route_id === rid; })
        .sort(function (a, b) { return a.properties.seq - b.properties.seq; });
    }

    function chooseRoute(rid) {
      cur.rid = rid;
      var rf = routeFeats.filter(function (f) { return f.properties.route_id === rid; })[0];
      var stops = stopsOf(rid);
      if (map) {
        if (ready) { FFH.setSource(map, 'route', { type: 'FeatureCollection', features: [rf] }); }
        stopMarkers.forEach(function (m) { m.remove(); });
        stopMarkers = stops.map(function (s) {
          return FFH.marker(map, s.geometry.coordinates, '', String(s.properties.seq + 1), null, s.properties.label);
        });
        drawStations();
        var pad = window.innerWidth > 767 ? { top: 70, bottom: 70, right: 70, left: 430 } : { top: 70, bottom: 140, right: 40, left: 40 };
        FFH.fitTo(map, rf.geometry.coordinates.concat(markers.map(function (m) { return idx[m._sid].coords; })), pad);
      }
      var ri = routeInfo(rid);
      var best = ri && bestId(ri) && optionFor(rid, bestId(ri)) ? bestId(ri) : null;
      renderWorked();
      renderBest(ri, best);
      renderOptions(ri);
      document.getElementById('basis').textContent = (det.price_basis ? 'Prices: ' + det.price_basis + '. ' : '') +
        'Typical fill: ' + (det.typical_fill_gal || 'n/a') + ' gallons. Each cost is for the time that stop would happen on the route (synthetic).';
      select(best || (ri && ri.baseline_station_id) || null);
    }

    /** The rush hour note and the ranking note, each shown once at the top of the panel. */
    function renderNotes() {
      var box = document.getElementById('notes');
      if (!box) { return; }
      FFH.clear(box);
      if (det.rush_hour_note) { box.appendChild(FFH.el('p', 'foot', det.rush_hour_note)); }
      if (det.ranking_note) { box.appendChild(FFH.el('p', 'foot', det.ranking_note)); }
      box.hidden = !box.firstChild;
    }

    /** The road path of one detour from detour_paths.geojson, or null when it is not there. */
    function pathFor(rid, sid, after) {
      var f = paths && paths.features ? paths.features.filter(function (x) {
        var p = x.properties || {};
        return p.route_id === rid && p.station_id === sid && Number(p.insert_after_stop) === Number(after) && x.geometry && x.geometry.coordinates && x.geometry.coordinates.length > 1;
      })[0] : null;
      return f || null;
    }

    /** An option is shown when its station has confirmed truck access, the toggle is on, or it is the usual stop. */
    function allowed(o, ri) { return o.truck_access !== 'unverified' || includeUnv || (ri && ri.baseline_station_id === o.station_id); }

    function access(sid) { return idx[sid] ? idx[sid].props.truck_access : 'confirmed'; }

    function drawStations() {
      markers.forEach(function (m) { m.remove(); });
      markers = [];
      var ri = routeInfo(cur.rid);
      Object.keys(idx).forEach(function (sid) {
        var opt = optionFor(cur.rid, sid);
        if (!opt && ri) { return; }
        if (opt && !allowed(opt, ri)) { return; }
        var cls = 'stn' + (ri && bestId(ri) === sid ? ' best' : '') + ((access(sid) === 'unverified' || (opt && opt.over_max_detour)) ? ' dim' : '') + (cur.sid === sid ? ' sel' : '');
        var m = FFH.marker(map, idx[sid].coords, cls, FFH.icon('pump'), function () { select(sid); }, idx[sid].label);
        m._sid = sid;
        markers.push(m);
      });
    }

    /** The "Worked example" callout: the sentence plus the numbers behind it. */
    function renderWorked() {
      var box = document.getElementById('worked');
      if (!box) { return; }
      if (!we || !we.sentence) { box.hidden = true; return; }
      var inner = box.lastChild;
      FFH.clear(inner);
      inner.appendChild(FFH.el('b', null, 'Worked example'));
      inner.appendChild(FFH.el('span', null, we.sentence));
      var nums = ['Detour ' + FFH.num(we.detour_minutes, 0) + ' minutes',
        FFH.num(we.effective_minutes, 1) + ' effective minutes' + (we.is_peak && FFH.isNum(we.multiplier) ? ' (rush hour factor ' + we.multiplier + '×)' : ''),
        'time cost ' + FFH.usd(we.time_cost_usd), 'fuel cost ' + FFH.usd(we.fuel_cost_usd), 'wear ' + FFH.usd(we.wear_cost_usd),
        'needs ' + FFH.num(we.cpg_needed, 1) + ' cents a gallon'];
      inner.appendChild(FFH.el('small', 'worked-nums', nums.join(' · ') + '.'));
      box.hidden = false;
    }

    function renderBest(ri, best) {
      var box = document.getElementById('best').lastChild;
      FFH.clear(box);
      if (!ri || !best) {
        box.appendChild(FFH.el('b', null, 'No stop beats the usual one'));
        box.appendChild(FFH.el('small', null, 'Either no stops were tried for this route, or none wins after detour costs.'));
        return;
      }
      box.appendChild(FFH.el('b', null, 'Best stop: ' + idx[best].label));
      box.appendChild(FFH.el('small', null, bestReason(ri) || ''));
    }

    function renderOptions(ri) {
      var box = document.getElementById('optlist');
      FFH.clear(box);
      var hiddenCount = 0, lastTime = null;
      // Sort by stop time (ties keep their current order), then put one heading above each distinct time.
      var sorted = ((ri && ri.options) || []).map(function (o, i) { return { o: o, i: i }; }).sort(function (a, b) {
        var ta = a.o.stop_time_local || '99:99', tb = b.o.stop_time_local || '99:99';
        return ta < tb ? -1 : (ta > tb ? 1 : a.i - b.i);
      });
      sorted.forEach(function (e) {
        var o = e.o;
        if (!allowed(o, ri)) { hiddenCount += 1; return; }
        if (o.stop_time_local && o.stop_time_local !== lastTime) {
          box.appendChild(FFH.el('h4', 'opt-time', whenText(o)));
        }
        lastTime = o.stop_time_local || null;
        var wrap = FFH.el('div', 'opt' + (o.over_max_detour ? ' over' : ''));
        var b = FFH.el('button');
        b.type = 'button';
        if (o.over_max_detour) { b.setAttribute('aria-disabled', 'true'); }
        var name = idx[o.station_id] ? idx[o.station_id].label : o.station_id;
        var lab = FFH.el('span', null, name + (ri.baseline_station_id === o.station_id ? ' (usual stop)' : '') + ' ');
        if (o.truck_access === 'unverified') { lab.appendChild(FFH.unverifiedChip()); }
        b.appendChild(lab);
        b.appendChild(FFH.el('span', netClass(o), netText(o, ri)));
        b.addEventListener('click', function () { select(o.station_id); });
        wrap.appendChild(b);
        var chips = FFH.el('div', 'opt-chips');
        if (o.over_max_detour) { chips.appendChild(FFH.el('span', 'chip loss', 'Over the ' + (limit !== null ? limit + '-minute ' : '') + 'detour limit')); }
        if (chips.firstChild) { wrap.appendChild(chips); }
        box.appendChild(wrap);
      });
      if (hiddenCount) {
        box.appendChild(FFH.el('p', 'foot', hiddenCount + (hiddenCount === 1 ? ' station' : ' stations') + ' with unverified truck access ' + (hiddenCount === 1 ? 'is' : 'are') +
          ' hidden. Turn on "Include unverified truck access" to see them.'));
      }
    }

    /** "Costed at 7:00 am" for the time this stop would happen, with a rush hour tag when it falls in a peak window. */
    function whenText(o) {
      if (!o.stop_time_local) { return ''; }
      return 'Costed at ' + clockExact(o.stop_time_local) + (o.is_peak ? ' (rush hour)' : '');
    }
    /** "07:05" becomes "7:05 am". */
    function clockExact(t) {
      var p = String(t).split(':'), h = parseInt(p[0], 10);
      return (h % 12 === 0 ? 12 : h % 12) + ':' + (p[1] || '00') + (h >= 12 ? ' pm' : ' am');
    }
    function isBaseline(o, ri) { return ri && o && ri.baseline_station_id === o.station_id; }
    function netClass(o) { return o.net_savings_vs_baseline_usd > 0 ? 'good' : (o.net_savings_vs_baseline_usd < 0 ? 'bad' : 'neutral'); }
    function netText(o, ri) {
      if (isBaseline(o, ri)) { return 'Usual stop'; }
      var v = o.net_savings_vs_baseline_usd;
      return v >= 0 ? 'Saves ' + FFH.usd(v) : 'Costs ' + FFH.usd(-v) + ' more';
    }

    /** Detour minutes as costed: rush-hour minutes when the stop happens in rush hour, otherwise the road minutes. */
    function drive(o) { return FFH.isNum(o.extra_minutes_effective) ? o.extra_minutes_effective : o.extra_minutes; }

    function select(sid) {
      cur.sid = sid;
      noRoad = false;
      var ri = routeInfo(cur.rid), o = sid ? optionFor(cur.rid, sid) : null;
      markers.forEach(function (m) {
        var b = m.getElement();
        b.classList.toggle('sel', m._sid === sid);
      });
      if (popup) { popup.remove(); popup = null; }
      if (map) {
        var stops = stopsOf(cur.rid);
        var src = FFH.emptyFC();
        if (o && idx[sid] && !isBaseline(o, ri)) {
          var road = pathFor(cur.rid, sid, o.insert_after_stop);
          var a = stops[o.insert_after_stop], b = stops[o.insert_after_stop + 1];
          if (road) {
            src.features.push({ type: 'Feature', properties: {}, geometry: road.geometry });
          } else if (a && b) {
            src.features.push(FFH.line([a.geometry.coordinates, idx[sid].coords, b.geometry.coordinates]));
          }
          noRoad = !road;
          popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 22 })
            .setLngLat(idx[sid].coords).setText(FFH.num(o.extra_miles, 1) + ' mi · ' + FFH.num(drive(o), 0) + ' min').addTo(map);
        }
        if (ready && map.getSource('detour')) { FFH.setSource(map, 'detour', src); }
      }
      var rn = document.getElementById('road-note');
      if (rn) { rn.hidden = !(noRoad && o && !isBaseline(o, ri)); }
      renderStops(o, ri, sid);
      renderMetrics(o, ri);
    }

    function renderMetrics(o, ri) {
      var m = document.getElementById('p-metrics');
      FFH.clear(m);
      var cells = o
        ? [['Price per gallon', FFH.perGal(o.price_per_gal), ''],
           ['Detour', isBaseline(o, ri) ? 'None' : FFH.num(drive(o), 0) + ' min', 'navy'],
           ['Net savings', isBaseline(o, ri) ? 'Usual stop' : (o.net_savings_vs_baseline_usd >= 0 ? '' : '-') + FFH.usd(Math.abs(o.net_savings_vs_baseline_usd)), '']]
        : [['Price per gallon', 'n/a', ''], ['Detour', 'n/a', 'navy'], ['Net savings', 'n/a', '']];
      cells.forEach(function (x) {
        var d = FFH.el('div');
        d.appendChild(FFH.el('div', 'l', x[0]));
        d.appendChild(FFH.el('div', 'v ' + x[2], x[1]));
        m.appendChild(d);
      });
    }

    function renderStops(o, ri, sid) {
      var ol = document.getElementById('stops');
      FFH.clear(ol);
      var stops = stopsOf(cur.rid);
      var insertAfter = o && !isBaseline(o, ri) ? o.insert_after_stop : -1;
      stops.forEach(function (s, i) {
        var li = FFH.el('li');
        li.appendChild(FFH.el('span', 'num-circle', String(s.properties.seq + 1)));
        li.appendChild(FFH.el('span', null, s.properties.label));
        ol.appendChild(li);
        if (i === insertAfter) {
          var li2 = FFH.el('li');
          var card = FFH.el('div', 'inserted' + (o.net_savings_vs_baseline_usd < 0 ? ' neg' : ''));
          card.style.width = '100%';
          var hd = FFH.el('b', null, 'Added: ' + idx[sid].label + ' ');
          if (o.truck_access === 'unverified') { hd.appendChild(FFH.unverifiedChip()); }
          card.appendChild(hd);
          card.appendChild(FFH.el('div', 'sub', 'Inserted between stop ' + (i + 1) + ' and stop ' + (i + 2)));
          var line = FFH.el('div', null, FFH.num(o.extra_miles, 1) + ' miles, ' + FFH.num(drive(o), 0) + ' minutes, ');
          line.appendChild(FFH.el('span', netClass(o), o.net_savings_vs_baseline_usd >= 0 ? 'net saving ' + FFH.usd(o.net_savings_vs_baseline_usd) : 'net cost ' + FFH.usd(-o.net_savings_vs_baseline_usd)));
          card.appendChild(line);
          var wt = whenText(o);
          if (wt) { card.appendChild(FFH.el('div', 'foot', wt + '.')); }
          if (o.over_max_detour) { card.appendChild(FFH.el('div', 'foot', 'Over the ' + (limit !== null ? limit + '-minute ' : '') + 'detour limit, so it is never recommended.')); }
          card.appendChild(FFH.el('div', 'foot', 'Fill of ' + det.typical_fill_gal + ' gal at ' + FFH.perGal(o.price_per_gal) + '. Fuel ' + FFH.usd(o.fuel_cost_usd) + ' plus detour ' + FFH.usd(o.detour_cost_usd) + ' = ' + FFH.usd(o.total_cost_usd) + '.'));
          li2.appendChild(card);
          ol.appendChild(li2);
        }
      });
      if (sid && !o) {
        var n = FFH.el('li', 'foot', (idx[sid] ? idx[sid].label : sid) + ' has no precomputed detour on this route.');
        ol.appendChild(n);
      }
      renderTotals(o, ri, insertAfter >= 0);
    }

    function renderTotals(o, ri, inserted) {
      var rf = routeFeats.filter(function (f) { return f.properties.route_id === cur.rid; })[0].properties;
      var mi = rf.base_miles + (inserted ? o.extra_miles : 0);
      var mn = rf.base_minutes + (inserted ? o.extra_minutes : 0);
      var box = document.getElementById('totals');
      FFH.clear(box);
      var whole = Math.round(mn);
      box.appendChild(FFH.el('span', null, 'Total route ' + FFH.num(mi, 1) + ' miles, ' + Math.floor(whole / 60) + ' h ' + (whole % 60) + ' min, including stops'));
    }
  }
});
