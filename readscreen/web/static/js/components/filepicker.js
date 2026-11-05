// File picker for files on the machine running readscreen (no uploads).

import { api } from '../api.js';
import { fmt, h, modal, storageGet, storageSet } from '../ui.js';

const LAST_DIR = 'readscreen.lastDir';
const KIND_LABEL = { fastq: 'FASTQ', fasta: 'FASTA' };

function parentOf(path) {
  if (!path) return null;
  const m = path.match(/^(.*)[\\/][^\\/]+$/);
  return m ? m[1] || '/' : null;
}

// Resolves to the chosen path, or null. kind: 'fastq', 'fasta' or null (any file).
export function pickFile({ title = 'Choose a file', kind = null, start = null } = {}) {
  return new Promise((resolve) => {
    let chosen = null;
    let selected = null;
    let current = null;
    let onlyKind = !!kind;
    let hidden = false;

    const pathInput = h('input', { type: 'text', placeholder: 'Folder path', 'aria-label': 'Folder path' });
    const roots = h('div', { class: 'picker-roots' });
    const list = h('div', { class: 'picker-list', tabindex: '-1' });
    const selLabel = h('span', { class: 'mono small break secondary' }, 'No file selected');
    const chooseBtn = h('button', { class: 'btn primary', type: 'button', disabled: true }, 'Choose');

    async function go(path) {
      list.replaceChildren(h('div', { class: 'empty' }, 'Loading…'));
      try {
        const data = await api.fsList(path, hidden);
        current = data.path;
        pathInput.value = data.path || '';
        if (data.path) storageSet(LAST_DIR, data.path);
        roots.replaceChildren(...data.roots.map((r) =>
          h('button', { class: 'btn small', type: 'button', onclick: () => go(r.path) }, r.name)));
        renderList(data);
      } catch (err) {
        list.replaceChildren(h('div', { class: 'empty' }, err.message));
      }
    }

    function select(entry, row) {
      selected = entry.path;
      list.querySelectorAll('tr.sel').forEach((r) => r.classList.remove('sel'));
      row.classList.add('sel');
      selLabel.textContent = entry.path;
      chooseBtn.disabled = false;
    }

    function renderList(data) {
      const rows = [];
      if (data.parent) {
        rows.push(h('tr', { onclick: () => go(data.parent) },
          h('td', {}, h('span', { class: 'ficon dir' }, '↰'), '..'), h('td', {}), h('td', {})));
      }
      let hiddenCount = 0;
      for (const e of data.entries) {
        const isDir = e.type === 'dir';
        const matches = isDir || !kind || e.kind === kind;
        if (!matches && onlyKind) {
          hiddenCount += 1;
          continue;
        }
        const row = h('tr', { class: matches ? '' : 'dim', title: e.path },
          h('td', { class: 'break' }, h('span', { class: `ficon${isDir ? ' dir' : ''}` }, isDir ? '▸' : '·'), e.name),
          h('td', { class: 'num small secondary' }, isDir ? '' : fmt.bytes(e.size)),
          h('td', { class: 'small secondary nowrap' }, e.mtime ? fmt.time(e.mtime) : ''));
        if (isDir) row.addEventListener('click', () => go(e.path));
        else {
          row.addEventListener('click', () => select(e, row));
          row.addEventListener('dblclick', () => { chosen = e.path; dlg.close(); });
        }
        rows.push(row);
      }
      const table = h('table', { class: 'data' },
        h('thead', {}, h('tr', {}, h('th', {}, 'Name'), h('th', { class: 'num' }, 'Size'), h('th', {}, 'Modified'))),
        h('tbody', {}, rows));
      const notes = [];
      if (hiddenCount) notes.push(h('div', { class: 'empty small' }, `${hiddenCount} other file(s) hidden`));
      if (data.truncated) notes.push(h('div', { class: 'empty small' }, 'Long folder: only the first entries are shown.'));
      if (!data.entries.length) notes.push(h('div', { class: 'empty' }, 'Empty folder'));
      list.replaceChildren(table, ...notes);
      list.scrollTop = 0;
    }

    pathInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        go(pathInput.value.trim());
      }
    });

    const toggles = h('div', { class: 'row' },
      kind ? h('label', { class: 'check small' },
        h('input', { type: 'checkbox', checked: onlyKind, onchange: (e) => { onlyKind = e.target.checked; go(current); } }),
        `Only ${KIND_LABEL[kind]} files`) : null,
      h('label', { class: 'check small' },
        h('input', { type: 'checkbox', checked: hidden, onchange: (e) => { hidden = e.target.checked; go(current); } }),
        'Show hidden'));

    const body = h('div', {},
      h('div', { class: 'picker-bar' },
        h('button', { class: 'btn', type: 'button', title: 'Parent folder', onclick: () => go(parentOf(current) || '') }, '↑ Up'),
        pathInput,
        h('button', { class: 'btn', type: 'button', onclick: () => go(pathInput.value.trim()) }, 'Go')),
      h('div', { class: 'row spread' }, roots, toggles),
      list);

    chooseBtn.addEventListener('click', () => { chosen = selected; dlg.close(); });
    const dlg = modal({
      title, body, wide: true,
      footerLeft: selLabel,
      actions: [{ label: 'Cancel', kind: 'ghost' }],
      onClose: () => resolve(chosen),
    });
    dlg.dialog.querySelector('footer').append(chooseBtn);
    go(start ? parentOf(start) || start : storageGet(LAST_DIR, ''));
  });
}

// A text field for a path plus a Browse button.
export function pathField({ value = '', kind = null, title, placeholder = '', onChange }) {
  const input = h('input', { type: 'text', placeholder, spellcheck: 'false' });
  input.value = value || '';
  input.addEventListener('change', () => onChange && onChange(input.value.trim()));
  const browse = h('button', {
    class: 'btn', type: 'button',
    onclick: async () => {
      const p = await pickFile({ title, kind, start: input.value.trim() || null });
      if (p) {
        input.value = p;
        if (onChange) onChange(p);
      }
    },
  }, 'Browse…');
  const el = h('div', { class: 'path-field' }, input, browse);
  return {
    el,
    get value() { return input.value.trim(); },
    set value(v) { input.value = v || ''; },
  };
}
