/* Receipt allocation grid. Vanilla JS; the server re-validates every amount. */
(function () {
  'use strict';
  var form = document.getElementById('receipt-form');
  if (!form) return;
  var mode = form.dataset.mode;                       // create | allocate | reallocate
  var apiTemplate = form.dataset.api;                 // .../api/flats/<id>/receivables/
  var receiptId = form.dataset.receipt || '';
  var flatSelect = document.getElementById('id_flat');
  var amountInput = document.getElementById('id_amount');
  var tbody = document.querySelector('#alloc-grid tbody');
  var hidden = document.getElementById('allocations_json');
  var sumReceipt = document.getElementById('sum-receipt');
  var sumAllocated = document.getElementById('sum-allocated');
  var sumRemaining = document.getElementById('sum-remaining');
  var cols = mode === 'reallocate' ? 9 : 8;
  var rows = [];

  // Work in paise (integers) to avoid floating-point drift.
  function paise(v) { var n = Math.round(parseFloat(String(v || '0').replace(/,/g, '')) * 100); return isNaN(n) ? 0 : n; }
  function rupees(p) {
    var s = (Math.abs(p) / 100).toFixed(2).split('.');
    var w = s[0], last3 = w.slice(-3), rest = w.slice(0, -3);
    if (rest) w = rest.replace(/\B(?=(\d{2})+(?!\d))/g, ',') + ',' + last3;
    return (p < 0 ? '-' : '') + w + '.' + s[1];
  }
  function receiptPaise() { return amountInput ? paise(amountInput.value) : paise(form.dataset.amount); }
  function cell(text, cls) { var td = document.createElement('td'); if (cls) td.className = cls; td.textContent = text; return td; }
  function message(text, cls) { tbody.innerHTML = ''; var tr = document.createElement('tr'); var td = cell(text, cls || 'muted'); td.colSpan = cols; tr.appendChild(td); tbody.appendChild(tr); }

  function recalc() {
    var total = 0, payload = [], invalid = false;
    rows.forEach(function (r) {
      var v = paise(r.input.value);
      var bad = v < 0 || v > r.max;
      r.input.classList.toggle('invalid', bad);
      invalid = invalid || bad;
      total += Math.max(v, 0);
      if (v > 0) payload.push({ bill_line_id: r.line.id, amount: (v / 100).toFixed(2) });
    });
    var receipt = receiptPaise();
    var over = total > receipt;
    sumReceipt.textContent = rupees(receipt);
    sumAllocated.textContent = rupees(total);
    sumRemaining.textContent = rupees(receipt - total);
    sumRemaining.classList.toggle('over', over);
    sumAllocated.classList.toggle('over', over);
    hidden.value = JSON.stringify(payload);
    var submit = form.querySelector('button.primary');
    if (submit) submit.disabled = over || invalid;
  }

  function render(lines) {
    rows = [];
    tbody.innerHTML = '';
    if (!lines.length) { message('No outstanding components for this flat. The full amount will be kept as advance.'); recalc(); return; }
    lines.forEach(function (line) {
      var tr = document.createElement('tr');
      var ref = cell('', 'mono');
      var strong = document.createElement('strong'); strong.textContent = line.reference; ref.appendChild(strong);
      var sub = document.createElement('div'); sub.className = 'muted small'; sub.textContent = 'Bill ' + line.bill_no + ' · #' + line.id; ref.appendChild(sub);
      tr.appendChild(ref);
      tr.appendChild(cell(line.bill_date, 'nowrap'));
      tr.appendChild(cell(line.due_date, 'nowrap'));
      tr.appendChild(cell(line.head + (line.type === 'interest' ? ' (interest)' : '')));
      tr.appendChild(cell(rupees(paise(line.payable)), 'num'));
      tr.appendChild(cell(rupees(paise(line.paid)), 'num'));
      tr.appendChild(cell(rupees(paise(line.balance)), 'num'));
      var max = mode === 'reallocate' ? paise(line.capacity) : paise(line.balance);
      if (mode === 'reallocate') tr.appendChild(cell(rupees(paise(line.receipt_current)), 'num'));
      var td = cell('', 'num');
      var input = document.createElement('input');
      input.type = 'number'; input.step = '0.01'; input.min = '0'; input.max = (max / 100).toFixed(2);
      input.className = 'alloc-input'; input.inputMode = 'decimal';
      input.setAttribute('aria-label', 'Allocate to ' + line.reference);
      if (mode === 'reallocate' && paise(line.receipt_current) > 0) input.value = (paise(line.receipt_current) / 100).toFixed(2);
      input.addEventListener('input', recalc);
      input.addEventListener('keydown', function (e) {
        // Enter moves to the next row instead of submitting; F fills this row with its balance.
        if (e.key === 'Enter') { e.preventDefault(); var i = rows.findIndex(function (r) { return r.input === input; }); if (rows[i + 1]) rows[i + 1].input.focus(); }
        if (e.key === 'f' || e.key === 'F') { e.preventDefault(); fillOne(input, max); }
      });
      td.appendChild(input);
      tr.appendChild(td);
      tbody.appendChild(tr);
      rows.push({ line: line, input: input, max: max });
    });
    recalc();
  }

  function fillOne(input, max) {
    var used = 0;
    rows.forEach(function (r) { if (r.input !== input) used += Math.max(paise(r.input.value), 0); });
    var take = Math.max(Math.min(max, receiptPaise() - used), 0);
    input.value = take ? (take / 100).toFixed(2) : '';
    recalc();
  }

  function fill(policy) {
    rows.forEach(function (r) { r.input.value = ''; });
    if (policy === 'clear') { recalc(); return; }
    var ordered = rows.slice();
    if (policy === 'oldest_due') {
      ordered.sort(function (a, b) { return a.line.due_date.localeCompare(b.line.due_date) || a.line.id - b.line.id; });
    }
    // principal_then_interest: rows already arrive ordered by period, principal before interest, head priority.
    var remaining = receiptPaise();
    ordered.forEach(function (r) {
      var take = Math.min(remaining, r.max);
      if (take > 0) { r.input.value = (take / 100).toFixed(2); remaining -= take; }
    });
    recalc();
  }

  function load() {
    var flat = flatSelect ? flatSelect.value : 'x';
    if (!flat) { rows = []; message('Select a flat to load outstanding components.'); recalc(); return; }
    var url = flatSelect ? apiTemplate.replace('/0/', '/' + encodeURIComponent(flat) + '/') : apiTemplate;
    if (mode === 'reallocate') url += '?receipt_id=' + encodeURIComponent(receiptId);
    message('Loading…');
    fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' }, credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (data) { render(data.lines); })
      .catch(function () { rows = []; message('Could not load outstanding components.', 'errorlist'); recalc(); });
  }

  // Show only the payment-reference fields relevant to the chosen mode.
  var modeSelect = document.getElementById('id_payment_mode');
  function toggleModeFields() {
    if (!modeSelect) return;
    var m = modeSelect.value;
    var show = {
      cheque_no: m === 'cheque', cheque_date: m === 'cheque', bank_name: m !== 'cash', bank_branch: m === 'cheque',
      transaction_ref: ['neft', 'rtgs', 'upi', 'bank_transfer', 'other'].indexOf(m) >= 0
    };
    Object.keys(show).forEach(function (name) {
      var box = form.querySelector('[data-field="' + name + '"]');
      if (box) box.style.display = show[name] ? '' : 'none';
    });
  }

  document.querySelectorAll('[data-fill]').forEach(function (b) { b.addEventListener('click', function () { fill(b.dataset.fill); }); });
  if (flatSelect) flatSelect.addEventListener('change', load);
  if (amountInput) amountInput.addEventListener('input', recalc);
  if (modeSelect) { modeSelect.addEventListener('change', toggleModeFields); toggleModeFields(); }
  form.addEventListener('submit', function (e) {
    recalc();
    if (paise(sumAllocated.textContent) > receiptPaise()) { e.preventDefault(); alert('Allocated amount exceeds the receipt amount.'); }
  });
  load();
})();
