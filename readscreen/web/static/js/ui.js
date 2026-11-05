// DOM helpers, formatting, toasts, dialogs and badges. All text goes in through textContent.

const SVG_NS = 'http://www.w3.org/2000/svg';
const PROPS = new Set(['value', 'checked', 'disabled', 'selected', 'indeterminate']);

function apply(el, attrs) {
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.setAttribute('class', v);
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (PROPS.has(k)) el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  apply(el, attrs);
  append(el, children);
  return el;
}

export function s(tag, attrs, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  apply(el, attrs);
  append(el, children);
  return el;
}

// ------------------------------------------------------------------ formatting

export const fmt = {
  int: (n) => (n == null ? '–' : Math.round(Number(n)).toLocaleString('en-US')),
  num: (n, d = 2) =>
    n == null ? '–' : Number(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }),
  pct: (f, d = 1) => (f == null ? '–' : `${(100 * f).toFixed(d)}%`),
  pctValue: (p, d = 1) => (p == null ? '–' : `${Number(p).toFixed(d)}%`),
  bp: (n) => {
    if (n == null) return '–';
    if (n >= 1e6) return `${(n / 1e6).toFixed(2)} Mb`;
    if (n >= 1e4) return `${(n / 1e3).toFixed(1)} kb`;
    return `${Math.round(n).toLocaleString('en-US')} bp`;
  },
  bytes: (b) => {
    if (b == null) return '';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    let v = b;
    while (v >= 1024 && i < units.length - 1) {
      v /= 1024;
      i += 1;
    }
    return `${v.toFixed(i ? 1 : 0)} ${units[i]}`;
  },
  time: (t) => (t ? new Date(t * 1000).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '–'),
  ago: (t) => {
    if (!t) return '–';
    const s = Math.max(0, Date.now() / 1000 - t);
    if (s < 60) return `${Math.round(s)} s ago`;
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    return fmt.time(t);
  },
  dur: (sec) => {
    if (sec == null) return '';
    if (sec < 1) return '<1 s';
    if (sec < 60) return `${Math.round(sec)} s`;
    if (sec < 3600) return `${Math.floor(sec / 60)} min ${Math.round(sec % 60)} s`;
    return `${Math.floor(sec / 3600)} h ${Math.round((sec % 3600) / 60)} min`;
  },
  label: (name) => name.replace(/_/g, ' '),
};

// ------------------------------------------------------------------ toasts

export function toast(message, kind = 'info', timeout = 5000) {
  const dotClass = { info: 'accent', good: 'good', warn: 'warning', error: 'critical' }[kind] || 'accent';
  const el = h('div', { class: 'toast', role: kind === 'error' ? 'alert' : 'status' },
    h('span', { class: `dot ${dotClass}` }), h('div', {}, message));
  document.getElementById('toasts').append(el);
  setTimeout(() => el.remove(), kind === 'error' ? timeout * 2 : timeout);
}

// Run an async action; errors become a toast. Returns the result or undefined.
export async function act(fn, success) {
  try {
    const result = await fn();
    if (success) toast(success, 'good');
    return result;
  } catch (err) {
    toast(err.message || String(err), 'error');
    return undefined;
  }
}

// ------------------------------------------------------------------ dialogs

export function modal({ title, body, actions = [], wide = false, footerLeft = null, onClose = null }) {
  const previous = document.activeElement;
  const backdrop = h('div', { class: 'modal-backdrop' });
  const close = () => {
    backdrop.remove();
    document.removeEventListener('keydown', onKey);
    if (onClose) onClose();
    if (previous && previous.focus) previous.focus();
  };
  const onKey = (e) => {
    if (e.key === 'Escape') close();
  };
  const buttons = actions.map((a) =>
    h('button', {
      class: `btn ${a.kind || ''}`,
      type: 'button',
      onclick: async (e) => {
        const btn = e.currentTarget;
        btn.disabled = true;
        try {
          const keep = a.onClick ? await a.onClick() : undefined;
          if (keep !== false) close();
        } finally {
          btn.disabled = false;
        }
      },
    }, a.label));
  const dialog = h('div', { class: `modal${wide ? ' wide' : ''}`, role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
    h('header', {}, h('h2', {}, title),
      h('button', { class: 'btn ghost small', type: 'button', 'aria-label': 'Close', onclick: close }, '✕')),
    h('div', { class: 'content' }, body),
    actions.length || footerLeft ? h('footer', {}, footerLeft ? h('div', { class: 'left' }, footerLeft) : null, buttons) : null);
  backdrop.append(dialog);
  backdrop.addEventListener('mousedown', (e) => {
    if (e.target === backdrop) close();
  });
  document.addEventListener('keydown', onKey);
  document.body.append(backdrop);
  const focusable = dialog.querySelector('input, select, textarea, button.primary') || dialog.querySelector('button');
  if (focusable) focusable.focus();
  return { close, dialog };
}

// Resolves to the value of the chosen action, or null when dismissed.
export function choose({ title, message, choices }) {
  return new Promise((resolve) => {
    let result = null;
    modal({
      title,
      body: typeof message === 'string' ? h('p', {}, message) : message,
      actions: [
        { label: 'Cancel', kind: 'ghost' },
        ...choices.map((c) => ({ label: c.label, kind: c.kind, onClick: () => { result = c.value; } })),
      ],
      onClose: () => resolve(result),
    });
  });
}

export function confirmDialog({ title, message, confirmLabel = 'OK', danger = false }) {
  return choose({ title, message, choices: [{ label: confirmLabel, kind: danger ? 'danger' : 'primary', value: true }] })
    .then((v) => v === true);
}

export function promptDialog({ title, label, value = '', multiline = false, confirmLabel = 'Save' }) {
  return new Promise((resolve) => {
    let result = null;
    const input = multiline ? h('textarea', { rows: 5 }) : h('input', { type: 'text' });
    input.value = value;
    modal({
      title,
      body: h('div', { class: 'stack' }, label ? h('label', { class: 'secondary small' }, label) : null, input),
      actions: [
        { label: 'Cancel', kind: 'ghost' },
        { label: confirmLabel, kind: 'primary', onClick: () => { result = input.value; } },
      ],
      onClose: () => resolve(result),
    });
  });
}

// ------------------------------------------------------------------ badges

const STATUS = {
  queued: ['dot', 'queued'],
  running: ['dot accent pulse', 'running'],
  paused: ['dot warning', 'paused'],
  stopped: ['dot hollow', 'stopped'],
  done: ['dot good', 'done'],
  error: ['dot critical', 'error'],
};

export function statusBadge(status) {
  const [cls, label] = STATUS[status] || ['dot', status];
  return h('span', { class: 'badge' }, h('span', { class: cls }), label);
}

export function stateBadge(state, running = false) {
  if (running) return h('span', { class: 'badge' }, h('span', { class: 'dot accent pulse' }), 'running');
  const map = { pending: ['dot hollow', 'pending'], done: ['dot good', 'done'], skipped: ['dot', 'skipped'] };
  const [cls, label] = map[state] || ['dot', state];
  return h('span', { class: 'badge' }, h('span', { class: cls }), label);
}

export const FLAG_HELP = {
  divergent:
    'Mean read identity is below the threshold (default 90%): the closest reference is distant, so read counts and breadth are likely underestimated.',
  uneven:
    'Observed breadth is less than half of what evenly spread reads would give: reads cluster in a few regions (conserved regions of a distant reference, repeats or artifacts).',
  ambiguous:
    'More than half of the reads have MAPQ 0: they fit another selected reference, or a repeat within the genome, equally well.',
  weak: 'Only one supporting contig or read, and less than 5% of the reference covered.',
};

export function flagBadge(flag) {
  return h('span', { class: `flag${flag === 'weak' ? ' info' : ''}`, title: FLAG_HELP[flag] || flag },
    h('span', { class: 'icon', 'aria-hidden': 'true' }, flag === 'weak' ? '◌' : '⚠'), flag === 'weak' ? 'weak evidence' : flag);
}

export function pauseText(job) {
  if (job.status !== 'paused' || !job.pause_reason) return '';
  if (job.pause_reason === 'select') return 'waiting for hit selection';
  if (job.pause_reason.startsWith('after:')) return `paused after ${job.pause_reason.slice(6)}`;
  return job.pause_reason;
}

export function callout(kind, title, ...body) {
  const icon = { warn: '⚠', error: '✕', info: 'ℹ' }[kind] || 'ℹ';
  return h('div', { class: `callout ${kind}` }, h('span', { class: 'icon', 'aria-hidden': 'true' }, icon),
    h('div', { class: 'body' }, title ? h('strong', {}, title) : null, body.length ? h('div', {}, body) : null));
}

export function meter(fraction, wide = false) {
  const pct = Math.max(0, Math.min(1, fraction || 0)) * 100;
  return h('span', { class: wide ? 'meter wide' : 'meter', 'aria-hidden': 'true' }, h('span', { style: { width: `${pct}%` } }));
}

// ------------------------------------------------------------------ live views

function busy(container) {
  if (document.querySelector('.modal-backdrop')) return true;
  const a = document.activeElement;
  return !!(a && container.contains(a) && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName));
}

// Load data periodically; re-render only when it changed and the user is not typing.
export function live(container, { load, render, interval = 5000, onError }) {
  let last = null;
  let timer = null;
  let gen = 0;
  let disposed = false;
  async function tick(force) {
    const mine = ++gen;
    clearTimeout(timer);
    let data;
    try {
      data = await load();
      if (disposed || mine !== gen) return;
      const key = JSON.stringify(data);
      if (force || (key !== last && !busy(container))) {
        last = key;
        render(data);
      }
    } catch (err) {
      if (disposed || mine !== gen) return;
      if (onError) onError(err);
      else if (last === null) container.replaceChildren(callout('error', 'Could not load data', err.message));
    }
    if (!disposed && mine === gen) {
      const wait = typeof interval === 'function' ? interval(data) : interval;
      if (wait) timer = setTimeout(() => tick(false), wait);
    }
  }
  tick(true);
  return {
    refresh: () => tick(true),
    dispose: () => {
      disposed = true;
      clearTimeout(timer);
    },
  };
}

export function storageGet(key, fallback = null) {
  try {
    const v = localStorage.getItem(key);
    return v == null ? fallback : JSON.parse(v);
  } catch {
    return fallback;
  }
}

export function storageSet(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage unavailable: ignore */
  }
}
