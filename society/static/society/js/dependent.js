/* Dependent dropdowns.
 *
 * A select marked data-depends-on="id_parent" is narrowed to the options whose data-parent
 * contains the parent's current value. Options without data-parent (the blank choice) always stay.
 * Everything is already in the HTML, so without JavaScript the page still works as plain selects;
 * the server re-validates every choice regardless.
 */
(function () {
  'use strict';

  function setup(select) {
    var parent = document.getElementById(select.dataset.dependsOn);
    if (!parent) return;

    var options = Array.prototype.map.call(select.options, function (o) {
      return { value: o.value, label: o.text, parents: (o.dataset.parent || '').split(' ').filter(Boolean), disabled: o.disabled };
    });
    var hint = document.createElement('span');
    hint.className = 'small muted dep-hint';

    function matches(option, parentValue) {
      return !parentValue || !option.parents.length || option.parents.indexOf(parentValue) >= 0;
    }

    function apply() {
      var parentValue = parent.value || '';
      var kept = options.filter(function (o) { return matches(o, parentValue); });
      var previous = select.value;
      var stillValid = kept.some(function (o) { return o.value === previous && o.value !== ''; });

      select.textContent = '';
      kept.forEach(function (o) {
        var el = document.createElement('option');
        el.value = o.value;
        el.text = o.label;
        el.disabled = o.disabled;
        if (o.value === previous) el.selected = true;
        select.appendChild(el);
      });
      if (!stillValid) select.value = kept.length && kept[0].value === '' ? '' : select.value;

      var narrowed = parentValue && kept.length < options.length;
      hint.textContent = narrowed ? kept.filter(function (o) { return o.value; }).length + ' of ' + options.filter(function (o) { return o.value; }).length + ' shown' : '';
      if (narrowed && !hint.parentNode && select.parentNode) select.parentNode.appendChild(hint);

      // Losing the selection must reach listeners (e.g. the receipt allocation grid).
      if (!stillValid && previous) select.dispatchEvent(new Event('change', { bubbles: true }));
    }

    parent.addEventListener('change', apply);
    apply();
  }

  document.querySelectorAll('select[data-depends-on]').forEach(setup);
})();
