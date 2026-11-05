// Settings: global values, default stage parameters for new jobs, BLAST databases and their
// download from NCBI, about.

import { api } from '../api.js';
import { act, callout, confirmDialog, fmt, h, live, meter } from '../ui.js';

const mb = (n) => (n == null ? '–' : `${Math.round(n / 1e6).toLocaleString('en-US')} MB`);

const PHASES = [
  ['check', 'Checking NCBI'],
  ['download', 'Downloading'],
  ['unpack', 'Unpacking'],
  ['test', 'Testing'],
  ['wait', 'Waiting for running jobs'],
  ['install', 'Installing'],
];

// Download/update card. Polls the download status (every second while one runs) and reloads the
// database list when a download ends. NCBI's version is checked once when the page opens.
function downloadCard(onFinished) {
  const box = h('div', {}, h('div', { class: 'empty' }, 'Loading…'));
  let remote = { loading: true, byName: {}, error: null };
  let wasRunning = null;

  async function checkRemote(refresh) {
    remote = { ...remote, loading: true };
    poller.refresh();
    try {
      const list = await api.dbSources(true, refresh);
      remote = { loading: false, byName: Object.fromEntries(list.map((s) => [s.name, s])), error: null };
    } catch (err) {
      remote = { loading: false, byName: {}, error: err.message };
    }
    poller.refresh();
  }

  async function start(src, info) {
    if (src.installed) {
      const when = info && info.remote ? `NCBI's version of ${info.remote.updated}` : 'the current NCBI version';
      const ok = await confirmDialog({
        title: 'Replace the installed database?',
        message: `The folder ${src.installed.path} is replaced with ${when}`
          + (info && info.remote ? ` (${mb(info.remote.download_bytes)} download)` : '')
          + '. Running jobs are not interrupted: the switch waits until no job is reading the database.',
        confirmLabel: 'Download and replace',
      });
      if (!ok) return;
    }
    if (await act(() => api.startDownload(src.name), 'Download started')) poller.refresh();
  }

  function progress(st) {
    const idx = PHASES.findIndex(([p]) => p === st.phase);
    const label = idx >= 0 ? `Step ${idx + 1} of ${PHASES.length}: ${PHASES[idx][1]}` : 'Starting';
    const frac = st.bytes_total ? (st.bytes_done || 0) / st.bytes_total : null;
    return h('div', { class: 'dl-progress' },
      h('div', { class: 'row spread' },
        h('strong', { class: 'small' }, label),
        st.phase === 'install' ? null : h('button', {  // the folder swap itself cannot be cancelled
          class: 'btn small', type: 'button',
          onclick: async () => { if (await act(() => api.cancelDownload(), 'Cancel requested')) poller.refresh(); },
        }, 'Cancel')),
      frac != null
        ? h('div', { class: 'row' }, meter(frac, true),
          h('span', { class: 'small secondary num' }, `${fmt.pct(frac, 0)} · ${mb(st.bytes_done)} of ${mb(st.bytes_total)}`))
        : null,
      h('div', { class: 'small muted' }, st.message || ''));
  }

  function sourceBlock(src, status) {
    const info = remote.byName[src.name];
    const mine = status.db === src.name;
    const running = status.running && mine;
    // compared here, not taken from the NCBI check: the installed version changes after a download
    const newer = info && info.remote
      ? (!src.installed || !src.installed.updated || info.remote.updated > src.installed.updated) : null;
    let badge;
    if (!src.installed) badge = h('span', { class: 'badge' }, h('span', { class: 'dot hollow' }), 'not installed');
    else if (newer) badge = h('span', { class: 'badge' }, h('span', { class: 'dot accent' }), 'update available');
    else if (newer === false) badge = h('span', { class: 'badge' }, h('span', { class: 'dot good' }), 'up to date');

    let ncbi;
    if (remote.loading) ncbi = h('span', { class: 'muted' }, 'checking…');
    else if (remote.error || (info && info.remote_error)) ncbi = h('span', {}, `not reachable: ${remote.error || info.remote_error}`);
    else if (info && info.remote) {
      ncbi = `${info.remote.updated} · ${fmt.int(info.remote.sequences)} sequences · ${mb(info.remote.download_bytes)} download, ${mb(info.remote.disk_bytes)} on disk`;
    } else ncbi = '–';

    const label = !src.installed ? 'Download' : newer ? 'Update' : 'Download again';
    const primary = !src.installed || newer;
    return h('div', { class: 'stack' },
      h('div', { class: 'row' }, h('strong', {}, src.title), h('span', { class: 'mono small muted' }, src.name), badge),
      h('div', { class: 'kv' },
        h('div', {}, 'Installed'),
        h('div', {}, src.installed
          ? `${src.installed.updated || 'unknown version'} · ${fmt.int(src.installed.sequences)} sequences`
            + (src.installed.taxdb ? '' : ' · taxonomy files missing')
          : 'no'),
        h('div', {}, 'NCBI'), h('div', {}, ncbi),
        h('div', {}, 'Jobs use it as'), h('div', { class: 'mono' }, src.db)),
      running ? progress(status) : null,
      !status.running && mine && status.state === 'error' ? callout('error', 'The last download failed', status.error) : null,
      !status.running && mine && status.state === 'cancelled' ? h('p', { class: 'small muted' }, 'The last download was cancelled.') : null,
      running ? null : h('div', { class: 'row' },
        h('button', {
          class: `btn${primary ? ' primary' : ''}`, type: 'button', disabled: status.running,
          onclick: () => start(src, info),
        }, label),
        h('button', { class: 'btn ghost small', type: 'button', onclick: () => checkRemote(true) }, 'Check NCBI again'),
        status.running ? h('span', { class: 'small muted' }, `Another download is running (${status.db}).`) : null));
  }

  const poller = live(box, {
    load: async () => {
      const [sources, status] = await Promise.all([api.dbSources(false), api.downloadStatus()]);
      return { sources, status, remote };
    },
    render: ({ sources, status }) => {
      if (wasRunning && !status.running) onFinished();
      wasRunning = status.running;
      box.replaceChildren(...sources.map((src) => sourceBlock(src, status)));
    },
    interval: (d) => (d && d.status.running ? 1000 : 10000),
  });
  checkRemote(false);

  const card = h('section', { class: 'card' },
    h('h2', {}, 'Download from NCBI'),
    h('p', { class: 'secondary small' },
      'Prebuilt BLAST databases from NCBI, with the taxonomy files for the species names. ',
      'The download runs in the background; you can leave this page.'),
    box);
  return { card, dispose: () => poller.dispose() };
}

export function settingsView(main) {
  const root = h('div', {}, h('div', { class: 'empty' }, 'Loading…'));
  main.append(root);
  let download = null;

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
          : callout('warn', 'No BLAST database found.',
            `Download one below, or put nucleotide databases into the folder “${values.blast_db_dir}”.`));
      } catch (err) {
        box.replaceChildren(callout('error', 'Could not list the databases', err.message));
      }
    };
    fill(false);
    const card = h('section', { class: 'card' },
      h('div', { class: 'card-head' }, h('h2', {}, 'BLAST databases'),
        h('button', { class: 'btn small', type: 'button', onclick: () => fill(true) }, 'Refresh')),
      h('p', { class: 'secondary small' }, 'Folder: ', h('span', { class: 'mono' }, values.blast_db_dir)),
      box);
    return { card, refresh: () => fill(true) };
  }

  function render(values, schema, info, stages) {
    if (download) download.dispose();
    const dbs = dbCard(values);
    download = downloadCard(dbs.refresh);
    root.replaceChildren(
      h('div', { class: 'page-head' }, h('h1', {}, 'Settings')),
      h('div', { class: 'stack' },
        globalCard(values, schema),
        defaultsCard(values, stages),
        dbs.card,
        download.card,
        h('section', { class: 'card' }, h('h2', {}, 'About'),
          h('div', { class: 'kv' },
            h('div', {}, 'Version'), h('div', {}, info.version),
            h('div', {}, 'Data folder'), h('div', { class: 'mono break' }, info.data_dir)))));
  }

  load().catch((err) => root.replaceChildren(callout('error', 'Could not load the settings', err.message)));
  return { dispose: () => { if (download) download.dispose(); } };
}
