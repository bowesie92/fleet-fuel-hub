/* Data hub page: source feeds, cleanup metrics, and the unified output table. */
FFH.boot(function () {
  var MESSY = {
    'fuel_card.csv': 'Messy names, duplicate swipes, missing odometers',
    'telematics.csv': 'Irregular pings, gaps, UTC timestamps',
    'route_plans.csv': 'Planned miles only, no actual fueling'
  };

  return FFH.loadData('hub.json').then(function (h) {
    renderSources(h.sources || []);
    renderMetrics(h.dq || [], h.accuracy || []);
    renderClean(h.clean_table);
  });

  function miniTable(columns, rows) {
    var wrap = FFH.el('div', 'tablewrap');
    var t = FFH.el('table');
    var hr = FFH.el('tr');
    columns.forEach(function (c) { hr.appendChild(FFH.el('th', null, c)); });
    t.appendChild(FFH.el('thead')).appendChild(hr);
    var b = FFH.el('tbody');
    (rows || []).slice(0, 5).forEach(function (r) {
      var tr = FFH.el('tr');
      columns.forEach(function (c) {
        var v = r[c];
        tr.appendChild(FFH.el('td', null, v === null || v === undefined || v === '' ? '(blank)' : String(v)));
      });
      b.appendChild(tr);
    });
    t.appendChild(b);
    wrap.appendChild(t);
    return wrap;
  }

  function renderSources(list) {
    var box = document.getElementById('sources');
    list.forEach(function (s, i) {
      var card = FFH.el('div', 'src s' + (i % 4));
      var head = FFH.el('div', 'card-head');
      var left = FFH.el('div');
      left.appendChild(FFH.el('h2', null, s.name));
      left.appendChild(FFH.el('p', 'sub', s.file + ', ' + FFH.num(s.rows) + ' rows'));
      head.appendChild(left);
      head.appendChild(FFH.el('span', 'chip', 'CSV'));
      card.appendChild(head);
      card.appendChild(miniTable(s.columns || [], s.sample || []));
      var m = FFH.el('div', 'messy');
      m.appendChild(FFH.icon(s.note ? 'info' : 'alert', true));
      m.appendChild(document.createTextNode(s.note || MESSY[s.file] || 'Messy and inconsistent'));
      if (s.note) { m.className = 'messy note'; }
      card.appendChild(m);
      box.appendChild(card);
    });
  }

  function human(s) { var t = s.replace(/_/g, ' '); return t.charAt(0).toUpperCase() + t.slice(1); }

  function renderMetrics(dq, acc) {
    var box = document.getElementById('metrics');
    acc.forEach(function (a) {
      var m = FFH.el('div', 'metric');
      var r = FFH.el('div', 'row1');
      r.appendChild(FFH.icon('check'));
      r.appendChild(FFH.el('span', null, human(a.metric)));
      m.appendChild(r);
      m.appendChild(FFH.el('div', 'big', FFH.pct(a.value)));
      m.appendChild(FFH.el('div', 'how', (a.notes || '') + ' Scored against the synthetic answer key.'));
      box.appendChild(m);
    });
    dq.forEach(function (q) {
      var m = FFH.el('div', 'metric');
      var r = FFH.el('div', 'row1');
      r.appendChild(FFH.icon('check'));
      r.appendChild(FFH.el('span', null, human(q.check_name)));
      m.appendChild(r);
      m.appendChild(FFH.el('div', 'big', FFH.num(q.issue_count)));
      m.appendChild(FFH.el('div', 'how', 'issues handled, ' + FFH.num(q.rows_in) + ' rows in, ' + FFH.num(q.rows_out) + ' out. ' + (q.notes || '')));
      box.appendChild(m);
    });
  }

  function renderClean(c) {
    var box = document.getElementById('clean');
    if (!c) { box.appendChild(FFH.el('p', 'sub', 'No clean table was exported.')); return; }
    var head = box.querySelector('h2');
    var wrapHead = FFH.el('div', 'card-head');
    var left = FFH.el('div');
    box.insertBefore(wrapHead, head);
    left.appendChild(head);
    left.appendChild(FFH.el('p', 'sub', c.name + ': one clean row per real fill'));
    wrapHead.appendChild(left);
    wrapHead.appendChild(FFH.el('span', 'ok-badge', 'Ready'));
    box.appendChild(FFH.el('p', 'sub', 'Times are local time, ' + FFH.MST + '. Volumes in gallons.'));
    var cols = c.columns || [], sample = (c.sample || []).slice(0, 5);
    var per = 3, page = 0;
    var tableBox = FFH.el('div');
    var pager = FFH.el('div', 'pager');
    var info = FFH.el('span');
    var prev = FFH.el('button', 'btn'), next = FFH.el('button', 'btn');
    prev.type = next.type = 'button';
    prev.setAttribute('aria-label', 'Previous page');
    next.setAttribute('aria-label', 'Next page');
    prev.appendChild(FFH.icon('left', true));
    next.appendChild(FFH.icon('right', true));
    var ctl = FFH.el('span');
    ctl.appendChild(prev);
    ctl.appendChild(document.createTextNode(' '));
    ctl.appendChild(next);
    pager.appendChild(info);
    pager.appendChild(ctl);
    function draw() {
      FFH.clear(tableBox);
      tableBox.appendChild(miniTable(cols, sample.slice(page * per, page * per + per)));
      var pages = Math.max(1, Math.ceil(sample.length / per));
      info.textContent = 'Page ' + (page + 1) + ' of ' + pages + ' (sample of ' + sample.length + ' rows)';
      prev.disabled = page === 0;
      next.disabled = page >= pages - 1;
    }
    prev.addEventListener('click', function () { if (page > 0) { page--; draw(); } });
    next.addEventListener('click', function () { page++; draw(); });
    box.appendChild(tableBox);
    box.appendChild(pager);
    draw();
    box.appendChild(FFH.el('p', 'foot', 'Showing a sample of ' + sample.length + ' rows out of ' + FFH.num(c.rows) + ' (synthetic).'));
    document.getElementById('export').addEventListener('click', function () { FFH.downloadCSV('unified_output_sample.csv', cols, sample); });
  }
});
