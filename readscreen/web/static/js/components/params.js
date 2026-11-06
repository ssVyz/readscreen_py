// Generic editor for a stage's parameters, built from the schema the API describes.
//
// Schema entries may carry `group` (sub-heading), `only` ('short'/'long': shown only in that
// read mode) and `tool_default` (what the tool uses when the field is empty; shown as the
// placeholder, per mode when it is {short, long}).

import { fmt, h } from '../ui.js';

function quoteArg(a) {
  return /\s|"/.test(a) ? `"${a.replace(/"/g, '\\"')}"` : a;
}

function toolDefault(p, mode) {
  const td = p.tool_default;
  if (td == null) return null;
  if (typeof td === 'object') return mode ? td[mode] : `${td.short} short / ${td.long} long`;
  return td;
}

function field(p, value, labelText) {
  const id = `p-${Math.random().toString(36).slice(2)}`;
  let input;
  let read;
  let setMode = () => {};
  if (p.type === 'bool') {
    input = h('input', { type: 'checkbox', id, checked: !!value });
    read = () => input.checked;
  } else if (p.choices) {
    const unset = p.default == null ? h('option', { value: '' }, '(not set)') : null;
    input = h('select', { id }, unset, p.choices.map((c) => h('option', { value: String(c) }, String(c))));
    input.value = value == null ? '' : String(value);
    read = () => (input.value === '' ? null : input.value);
    if (unset) {
      setMode = (mode) => {
        const td = toolDefault(p, mode);
        unset.textContent = td == null ? '(not set)' : `default (${td})`;
      };
    }
  } else if (p.type === 'int' || p.type === 'float') {
    input = h('input', { type: 'number', id, step: p.type === 'int' ? '1' : 'any' });
    if (p.min != null) input.min = p.min;
    if (p.max != null) input.max = p.max;
    input.value = value == null ? '' : String(value);
    read = () => {
      if (input.value.trim() === '') return null;
      const n = Number(input.value);
      return Number.isFinite(n) ? n : input.value;
    };
  } else if (p.type === 'list') {
    input = h('input', { type: 'text', id, placeholder: 'e.g. --option value' });
    input.value = (value || []).map(quoteArg).join(' ');
    read = () => input.value.trim();
  } else {
    input = h('input', { type: 'text', id });
    input.value = value == null ? '' : String(value);
    read = () => (input.value.trim() === '' ? null : input.value.trim());
  }
  if (input.tagName === 'INPUT' && p.type !== 'bool' && p.type !== 'list') {
    setMode = (mode) => {
      const td = toolDefault(p, mode);
      input.placeholder = p.default != null ? '' : td != null ? `default ${td}` : 'not set';
    };
  }
  setMode(null);
  const label = labelText
    ? h('label', { class: 'plain-name', for: id }, labelText)
    : h('label', { class: 'name', for: id }, p.name);
  const defaultText = p.default != null && p.type !== 'bool'
    ? ` (default: ${p.type === 'list' ? (p.default.join(' ') || 'none') : p.default})` : '';
  const row = h('div', { class: 'param-row' }, label, p.type === 'bool' ? h('span', {}, input) : input,
    p.help ? h('div', { class: 'help' }, p.help, defaultText) : null);
  return { row, read, input, setMode };
}

function same(a, b, type) {
  if (type === 'list') {
    const norm = (v) => (Array.isArray(v) ? v.map(quoteArg).join(' ') : String(v || '')).trim();
    return norm(a) === norm(b);
  }
  return (a == null ? null : a) === (b == null ? null : b);
}

// schema: [{name, type, default, choices, min, max, help, group, only, tool_default}]
// options: exclude / include (parameter names), title, mode ('short'/'long' if known),
// autoMode (what a 'mode' field set to 'auto' means), labels ({name: readable label}; used
// instead of the parameter name), headings (false: no group sub-headings).
export function paramEditor(schema, values, {
  exclude = [], include = null, title = null, mode = null, autoMode = null, labels = {}, headings = true,
} = {}) {
  const fields = {};
  const sections = [];
  let current = null;
  for (const p of schema) {
    if (exclude.includes(p.name) || (include && !include.includes(p.name))) continue;
    const f = field(p, values[p.name] !== undefined ? values[p.name] : p.default, labels[p.name]);
    fields[p.name] = { ...f, param: p };
    const group = headings ? p.group || '' : '';
    if (!current || current.group !== group) {
      current = { group, rows: [], el: h('div', { class: 'param-group' }, group ? h('h4', {}, group) : null) };
      sections.push(current);
    }
    current.rows.push(fields[p.name]);
    current.el.append(f.row);
  }
  const count = Object.keys(fields).length;
  const el = h('div', { class: 'param-stage' }, title ? h('h3', {}, fmt.label(title)) : null,
    count ? sections.map((s) => s.el) : h('div', { class: 'help' }, 'This stage has no parameters.'));

  function setMode(m) {
    for (const s of sections) {
      let visible = 0;
      for (const f of s.rows) {
        const show = !f.param.only || !m || f.param.only === m;
        f.row.style.display = show ? '' : 'none';
        f.setMode(m);
        if (show) visible += 1;
      }
      s.el.style.display = visible ? '' : 'none';
    }
  }

  // a 'mode' field that switches between short and long reads drives the visibility itself
  const modeField = fields.mode;
  const switchesReads = modeField && (modeField.param.choices || []).includes('long') && schema.some((p) => p.only);
  if (switchesReads) {
    const resolve = () => (modeField.input.value === 'auto' ? autoMode : modeField.input.value);
    modeField.input.addEventListener('change', () => setMode(resolve()));
    setMode(resolve());
  } else {
    setMode(mode);
  }

  return {
    el,
    empty: count === 0,
    setMode,
    changes() {
      const out = {};
      for (const [name, f] of Object.entries(fields)) {
        const v = f.read();
        const before = values[name] !== undefined ? values[name] : f.param.default;
        if (!same(v, before, f.param.type)) out[name] = v;
      }
      return out;
    },
  };
}
