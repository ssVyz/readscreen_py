// Settings: global values, default stage parameters for new jobs, BLAST databases, about.

import { api } from '../api.js';
import { act, callout, fmt, h } from '../ui.js';

export function settingsView(main) {
  const root = h('div', {}, h('div', { class: 'empty' }, 'Loading…'));
  main.append(root);

  async function load() {
    const [values, schema, info, stages] = await Promise.all([api.settings(), api.settingsSchema(), api.info(), api.stages('illumina')]);
    render(values, schema, info, stages);
  }

  function globalCard(values, schema) {
    const inputs = {};
    const rows = schema.map((p) => {
      const input = h('input', { type: p.type === 'str' ? 'text' : 'number', step: p.type === 'int' ? '1' : 'any' });
      if (p.min != null) input.min = p.min;
      input.value = values[p.name] ?? '';
      inputs[p.name] = input;
      return [h('label', { class: 'mono' }, p.name), input, h('div', { class: 'help' }, p.help, ` (default: ${p.default})`)];
    });
    return h('section', { class: 'card' },
      h('h2', {}, 'General'),
      h('div', { class: 'form-grid' }, rows.flat()),
      h('div', { class: 'row', style: { marginTop: '14px' } },
        h('button', {
          class: 'btn primary', type: 'button',
          onclick: async () => {
            const changes = {};
            for (const [k, input] of Object.entries(inputs)) {
              const v = input.value.trim();
              if (String(values[k] ?? '') !== v) changes[k] = v === '' ? null : v;
            }
            if (!Object.keys(changes).length) return;
            if (await act(() => api.saveSettings(changes), 'Settings saved')) load();
          },
        }, 'Save'),
        h('span', { class: 'help' }, 'Empty a field to go back to the default. Changes apply to the next stage the worker starts.')));
  }

  function defaultsCard(values, stages) {
    const overrides = Object.entries(values).filter(([k]) => k.startsWith('default.'));
    const stageSel = h('select', {}, stages.filter((s) => s.params.length).map((s) => h('option', { value: s.name }, s.name)));
    const paramSel = h('select', {});
    const valueInput = h('input', { type: 'text', placeholder: 'value' });
    const hint = h('div', { class: 'help' });
    const fillParams = () => {
      const st = stages.find((s) => s.name === stageSel.value);
      paramSel.replaceChildren(...st.params.map((p) => h('option', { value: p.name }, p.name)));
      fillHint();
    };
    const fillHint = () => {
      const p = stages.find((s) => s.name === stageSel.value).params.find((x) => x.name === paramSel.value);
      hint.textContent = p ? `${p.help} — ${p.type}${p.choices ? `: ${p.choices.join(' | ')}` : ''}, code default ${JSON.stringify(p.default)}` : '';
    };
    stageSel.addEventListener('change', fillParams);
    paramSel.addEventListener('change', fillHint);
    fillParams();
    return h('section', { class: 'card' },
      h('h2', {}, 'Defaults for new jobs'),
      h('p', { class: 'secondary small' }, 'Override the default of a stage parameter. Existing jobs keep their parameters.'),
      overrides.length
        ? h('table', { class: 'data' }, h('tbody', {}, overrides.map(([k, v]) => h('tr', {},
          h('td', { class: 'mono' }, k.slice(8)), h('td', { class: 'mono' }, JSON.stringify(v)),
          h('td', { class: 'num' }, h('button', {
            class: 'btn small ghost', type: 'button',
            onclick: async () => { if (await act(() => api.saveSettings({ [k]: null }), 'Default removed')) load(); },
          }, 'Remove'))))))
        : h('p', { class: 'muted small' }, 'No overrides: all stages use the built-in defaults.'),
      h('div', { class: 'row', style: { marginTop: '12px' } }, stageSel, paramSel, valueInput,
        h('button', {
          class: 'btn', type: 'button',
          onclick: async () => {
            const key = `default.${stageSel.value}.${paramSel.value}`;
            if (await act(() => api.saveSettings({ [key]: valueInput.value }), 'Default saved')) load();
          },
        }, 'Add')),
      hint);
  }

  function dbCard(values) {
    const box = h('div', {}, h('div', { class: 'empty' }, 'Loading…'));
    const fill = async (refresh) => {
      try {
        const dbs = await api.dbs(refresh);
        box.replaceChildren(dbs.length
          ? h('table', { class: 'data' },
            h('thead', {}, h('tr', {}, h('th', {}, 'Name'), h('th', {}, 'Title'), h('th', { class: 'num' }, 'Sequences'), h('th', { class: 'num' }, 'Size'))),
            h('tbody', {}, dbs.map((d) => h('tr', {}, h('td', { class: 'mono' }, d.name), h('td', {}, d.title),
              h('td', { class: 'num' }, fmt.int(d.sequences)), h('td', { class: 'num' }, fmt.bp(d.bases))))))
          : callout('warn', 'No BLAST database found.', `Put nucleotide databases into the folder “${values.blast_db_dir}”.`));
      } catch (err) {
        box.replaceChildren(callout('error', 'Could not list the databases', err.message));
      }
    };
    fill(false);
    return h('section', { class: 'card' },
      h('div', { class: 'card-head' }, h('h2', {}, 'BLAST databases'),
        h('button', { class: 'btn small', type: 'button', onclick: () => fill(true) }, 'Refresh')),
      h('p', { class: 'secondary small' }, 'Folder: ', h('span', { class: 'mono' }, values.blast_db_dir)),
      box);
  }

  function render(values, schema, info, stages) {
    root.replaceChildren(
      h('div', { class: 'page-head' }, h('h1', {}, 'Settings')),
      h('div', { class: 'stack' },
        globalCard(values, schema),
        defaultsCard(values, stages),
        dbCard(values),
        h('section', { class: 'card' }, h('h2', {}, 'About'),
          h('div', { class: 'kv' },
            h('div', {}, 'Version'), h('div', {}, info.version),
            h('div', {}, 'Data folder'), h('div', { class: 'mono break' }, info.data_dir)))));
  }

  load().catch((err) => root.replaceChildren(callout('error', 'Could not load the settings', err.message)));
  return null;
}
