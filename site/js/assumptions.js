/* Assumptions page: every assumption from meta.json. */
FFH.boot(function () {
  FFH.loadOptional('detours.json').then(renderNotes);
  FFH.loadOptional('missed.json').then(renderMissed);
  FFH.loadOptional('partner_scout.json').then(renderScout);

  /** Rush hour and ranking notes, quoted from detours.json. */
  function renderNotes(det) {
    var box = document.getElementById('how-notes');
    if (!box || !det) { return; }
    if (det.rush_hour_note) { box.appendChild(FFH.el('p', null, det.rush_hour_note)); }
    if (det.ranking_note) { box.appendChild(FFH.el('p', null, det.ranking_note)); }
  }

  /** The top missed-savings row, taken apart into its pieces. */
  function renderMissed(ms) {
    var box = document.getElementById('how-missed');
    var r = ms && ms.rows && ms.rows.slice().sort(function (a, b) { return b.net_savings_usd - a.net_savings_usd; })[0];
    if (!r) { return; }
    FFH.clear(box);
    box.appendChild(FFH.el('p', null, 'Take the biggest row on the Missed savings page: ' + r.truck_id + ' on ' + FFH.dateLabel(r.date) + ', ' + FFH.num(r.gallons, 1) + ' gallons. The station used charged ' +
      FFH.perGal(r.actual_price_per_gal) + ' and the lower-cost option ' + FFH.perGal(r.alt_price_per_gal) + '.'));
    if (FFH.isNum(r.price_gap_usd) && FFH.isNum(r.actual_detour_cost_usd) && FFH.isNum(r.alt_detour_cost_usd)) {
      box.appendChild(FFH.el('p', 'how-eq', 'Price gap x gallons ' + FFH.usd(r.price_gap_usd) + ' + detour avoided at the station used ' + FFH.usd(r.actual_detour_cost_usd) +
        ' - detour to the alternative ' + FFH.usd(r.alt_detour_cost_usd) + ' = net saving ' + FFH.usd(r.net_savings_usd)));
      box.appendChild(FFH.el('p', 'foot', FFH.ROUNDING_NOTE));
      box.appendChild(FFH.el('p', null, 'The station used was ' + FFH.num(r.actual_extra_miles, 1) + ' extra miles and ' + FFH.num(r.actual_extra_minutes, 0) +
        ' extra minutes off the route, and the lower-cost option ' + FFH.num(r.alt_extra_miles, 1) + ' extra miles and ' + FFH.num(r.alt_extra_minutes, 0) +
        ' extra minutes. Each detour cost is extra fuel, driver time and wear.'));
    } else {
      box.appendChild(FFH.el('p', null, 'Net saving after detour costs: ' + FFH.usd(r.net_savings_usd) + '.'));
    }
  }

  /** The scout method and the top station's already-available figure. */
  function renderScout(sc) {
    var box = document.getElementById('how-scout');
    if (!sc) { return; }
    FFH.clear(box);
    if (sc.method) { box.appendChild(FFH.el('p', null, sc.method)); }
    var top = (sc.stations || []).filter(function (s) { return s.station_id === sc.best_station_id_at_default; })[0];
    if (top && FFH.isNum(top.annual_savings_without_deal_usd)) {
      box.appendChild(FFH.el('p', null, 'Already available at posted prices: ' + FFH.usd0(top.annual_savings_without_deal_usd) + ' a year at ' + (top.display_name || top.station_id) +
        ', the top station in the scout. This is what the fleet could already save there with no partner deal, after detour costs.'));
    }
  }

  return FFH.loadData('meta.json').then(function (m) {
    document.getElementById('period').textContent =
      'Synthetic period: ' + m.period_start + ' to ' + m.period_end + '. Data as of ' + m.generated_on +
      '. Lines marked Fact are public facts, and lines marked Setting are technical knobs.';
    var t = document.getElementById('assumptions');
    var hr = FFH.el('tr');
    ['Setting', 'Value', 'What it means'].forEach(function (h, i) { hr.appendChild(FFH.el('th', i === 1 ? 'num' : null, h)); });
    t.appendChild(FFH.el('thead')).appendChild(hr);
    var body = FFH.el('tbody');
    var all = (m.assumptions || []).slice();
    var have = {};
    all.forEach(function (a) { have[a.key] = true; });
    (m.headline_assumptions || []).forEach(function (a) { if (a.source === 'derived' && !have[a.key]) { all.push(a); } });
    all.forEach(function (a) {
      var tr = FFH.el('tr');
      tr.appendChild(FFH.el('td', null, a.source === 'derived' ? FFH.keyLabel(a.key) : a.key));
      var shown = typeof a.value === 'object' ? (/peak_windows$/.test(a.key) ? FFH.assumptionValue(a.value, a.key) : JSON.stringify(a.value)) :
        (String(a.key).indexOf('miles_per_day') >= 0 || (String(a.key).indexOf('time_cost.') === 0 || String(a.key).indexOf('hos.') === 0 || /_mpg$/.test(a.key)) ? FFH.assumptionValue(a.value, a.key) : String(a.value));
      var fits = shown.length <= FFH.BADGE_MAX;
      tr.appendChild(FFH.el('td', 'num', fits ? shown : 'See description'));
      var note = FFH.el('td', 'what');
      var noteText = a.source === 'derived' ? FFH.derivedNote(a.note || '') : (a.note || '');
      if (!fits) {
        var lv = FFH.el('div', 'assump-longval');
        lv.appendChild(FFH.linkify(shown));
        note.appendChild(lv);
      }
      note.appendChild(FFH.linkify(noteText));
      tr.appendChild(note);
      body.appendChild(tr);
    });
    t.appendChild(body);
  });
});
