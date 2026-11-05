// Generic editor for a stage's parameters, built from the schema the API describes.

import { fmt, h } from '../ui.js';

function quoteArg(a) {
  return /\s|"/.test(a) ? `"${a.replace(/"/g, '\\"')}"` : a;
}

function field(p, value) {
  const id = `p-${Math.random().toString(36).slice(2)}`;
  let input;
  let read;
  if (p.type === 'bool') {
    input = h('input', { type: 'checkbox', id, checked: !!value });
    read = () => input.checked;
  } else if (p.choices) {
    input = h('select', { id },
      p.default == null ? h('option', { value: '' }, '(not set)') : null,
      p.choices.map((c) => h('option', { value: String(c) }, String(c))));
    input.value = value == null ? '' : String(value);
    read = () => (input.value === '' ? null : input.value);
  } else if (p.type === 'int' || p.type === 'float') {
    input = h('input', { type: 'number', id, step: p.type === 'int' ? '1' : 'any', placeholder: p.default == null ? 'not set' : '' });
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
    input = h('input', { type: 'text', id, placeholder: p.default == null ? 'not set' : '' });
    input.value = value == null ? '' : String(value);
    read = () => (input.value.trim() === '' ? null : input.value.trim());
  }
  const label = h('label', { class: 'name', for: id }, p.name);
  const row = h('div', { class: 'param-row' }, label, p.type === 'bool' ? h('span', {}, input) : input,
    p.help ? h('div', { class: 'help' }, p.help, p.default != null && p.type !== 'bool' ? ` (default: ${p.type === 'list' ? (p.default.join(' ') || 'none') : p.default})` : '') : null);
  return { row, read, input };
}

function same(a, b, type) {
  if (type === 'list') {
    const norm = (v) => (Array.isArray(v) ? v.map(quoteArg).join(' ') : String(v || '')).trim();
    return norm(a) === norm(b);
  }
  return (a == null ? null : a) === (b == null ? null : b);
}

// schema: [{name, type, default, choices, min, max, help}], values: {name: value}
export function paramEditor(schema, values, { exclude = [], title = null } = {}) {
  const fields = {};
  const rows = [];
  for (const p of schema) {
    if (exclude.includes(p.name)) continue;
    const f = field(p, values[p.name] !== undefined ? values[p.name] : p.default);
    fields[p.name] = { ...f, param: p };
    rows.push(f.row);
  }
  const el = h('div', { class: 'param-stage' }, title ? h('h3', {}, fmt.label(title)) : null,
    rows.length ? rows : h('div', { class: 'help' }, 'This stage has no parameters.'));
  return {
    el,
    empty: rows.length === 0,
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
