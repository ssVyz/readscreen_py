// New job: inputs, platform, workflow choices and (advanced) all stage parameters.

import { api } from '../api.js';
import { paramEditor } from '../components/params.js';
import { pathField } from '../components/filepicker.js';
import { act, callout, h, toast } from '../ui.js';

const PLATFORMS = [
  ['illumina', 'Illumina / short reads (also AVITI, MGI)'],
  ['ont', 'Oxford Nanopore'],
  ['hifi', 'PacBio HiFi'],
  ['clr', 'PacBio CLR'],
];
const LONG = new Set(['ont', 'hifi', 'clr']);
// parameters controlled by the simple form; the advanced section shows everything else
const SIMPLE = {
  analyze: ['skip'],
  trim: ['skip', 'apply_suggestions'],
  assemble: ['mode'],
  blast: ['db'],
  rank: ['top_groups', 'alternatives', 'min_score'],
  select: ['mode'],
  map: ['sensitivity'],
};

export function createView(main) {
  const state = {
    platform: 'illumina',
    stages: [],
    settings: {},
    dbs: [],
    assemblyTouched: false,
  };
  const editors = {};

  const r1 = pathField({ kind: 'fastq', title: 'Choose the reads (R1)', placeholder: 'Path to the reads (R1 or single file)', onChange: (p) => detect(p, 'r1') });
  const r2 = pathField({ kind: 'fastq', title: 'Choose the second reads (R2)', placeholder: 'Path to R2 (paired reads only)', onChange: (p) => detect(p, 'r2') });
  const detectNote = h('div', { class: 'note' });
  const nameInput = h('input', { type: 'text', placeholder: 'Taken from the file name' });
  const notesInput = h('textarea', { rows: 2, placeholder: 'Optional' });
  const platformSel = h('select', {}, PLATFORMS.map(([v, l]) => h('option', { value: v }, l)));
  const r2Row = h('div', { class: 'stack' }, r2.el);

  const analyzeChk = h('input', { type: 'checkbox', checked: true });
  const trimChk = h('input', { type: 'checkbox', checked: true });
  const suggestChk = h('input', { type: 'checkbox', checked: true });
  const asmRadios = {};
  const asmField = pathField({ kind: 'fasta', title: 'Choose an assembly (FASTA)', placeholder: 'Path to contigs (FASTA)' });
  const dbSel = h('select', {});
  const selectRadios = {};
  const topGroups = h('input', { type: 'number', min: 0, step: 1 });
  const alternatives = h('input', { type: 'number', min: 0, step: 1 });
  const minScore = h('input', { type: 'number', min: 0, step: 'any' });
  const sensSel = h('select', {},
    h('option', { value: 'normal' }, 'normal (-x sr)'),
    h('option', { value: 'sensitive' }, 'sensitive (-k15 -w5), recommended'),
    h('option', { value: 'very_sensitive' }, 'very sensitive (-k11 -w3 -m15 -s25)'));
  const sensRow = h('div', {}, sensSel, h('div', { class: 'help' }, 'Higher sensitivity finds more reads of strains that are distant from the reference.'));
  const pauseBoxes = {};
  const pauseRow = h('div', { class: 'row' });
  const advanced = h('div', {});
  const errorBox = h('div', {});

  function radio(group, store, value, label, help, onchange) {
    const input = h('input', { type: 'radio', name: group, value, onchange });
    store[value] = input;
    return h('label', { class: 'check', style: { alignItems: 'flex-start' } }, input,
      h('span', {}, label, help ? h('div', { class: 'help' }, help) : null));
  }

  const asmList = h('div', { class: 'radio-list' },
    radio('asm', asmRadios, 'assemble', 'Assemble the reads', 'shortread_assembler; long reads are cut into fragments first', () => { state.assemblyTouched = true; syncAssembly(); }),
    radio('asm', asmRadios, 'external', 'Use an existing assembly', null, () => { state.assemblyTouched = true; syncAssembly(); }),
    asmField.el,
    radio('asm', asmRadios, 'skip', 'No assembly: BLAST the reads', 'Large read sets are subsampled for BLAST', () => { state.assemblyTouched = true; syncAssembly(); }));

  const selectList = h('div', { class: 'radio-list' },
    radio('sel', selectRadios, 'auto', 'Automatic', 'Best groups by score; the job runs through', syncSelect),
    h('div', { class: 'row', style: { marginLeft: '24px' } },
      h('label', { class: 'small secondary' }, 'groups'), topGroups,
      h('label', { class: 'small secondary' }, 'alternatives per group'), alternatives,
      h('label', { class: 'small secondary' }, 'min. score'), minScore),
    radio('sel', selectRadios, 'manual', 'Pause and choose the hits on this website', 'The automatic choice is preselected', syncSelect));

  function effective(stage) {
    const st = state.stages.find((x) => x.name === stage);
    const values = {};
    for (const p of st ? st.params : []) {
      const key = `default.${stage}.${p.name}`;
      values[p.name] = key in state.settings ? state.settings[key] : p.default;
    }
    return values;
  }

  function syncAssembly() {
    const mode = Object.values(asmRadios).find((r) => r.checked)?.value;
    asmField.el.style.display = mode === 'external' ? '' : 'none';
  }

  function syncSelect() {
    const auto = selectRadios.auto.checked;
    for (const el of [topGroups, alternatives, minScore]) el.disabled = !auto;
  }

  function syncPlatform() {
    const long = LONG.has(state.platform);
    r2Row.style.display = long ? 'none' : '';
    sensRow.style.display = long ? 'none' : '';
    if (!state.assemblyTouched) {
      asmRadios[effective('assemble').mode || (long ? 'skip' : 'assemble')].checked = true;
      syncAssembly();
    }
  }

  async function detect(path, role) {
    detectNote.textContent = '';
    detectNote.className = 'note';
    if (!path) return;
    try {
      const sug = await api.fsSuggest(path);
      if (!nameInput.value) nameInput.placeholder = sug.name || 'Taken from the file name';
      if (role === 'r1' && sug.role === 'r2' && sug.mate) {
        r2.value = path;
        r1.value = sug.mate;
        detectNote.textContent = 'This was the R2 file; R1 was found next to it and filled in.';
        detectNote.className = 'note ok';
      } else if (role === 'r1' && sug.mate && !r2.value && !LONG.has(state.platform)) {
        r2.value = sug.mate;
        detectNote.textContent = 'R2 found automatically.';
        detectNote.className = 'note ok';
      } else if (role === 'r2' && sug.mate && !r1.value) {
        r1.value = sug.mate;
        detectNote.textContent = 'R1 found automatically.';
        detectNote.className = 'note ok';
      }
    } catch {
      /* suggestion is optional */
    }
  }

  function renderAdvanced() {
    advanced.replaceChildren();
    for (const st of state.stages) {
      const ed = paramEditor(st.params, effective(st.name), { exclude: SIMPLE[st.name] || [], title: st.name });
      editors[st.name] = ed;
      if (!ed.empty) advanced.append(ed.el);
    }
    const pd = effective('rank');
    topGroups.value = pd.top_groups;
    alternatives.value = pd.alternatives;
    minScore.value = pd.min_score;
    const sel = effective('select').mode || 'auto';
    selectRadios[sel].checked = true;
    syncSelect();
    sensSel.value = effective('map').sensitivity || 'sensitive';
    analyzeChk.checked = !effective('analyze').skip;
    trimChk.checked = !effective('trim').skip;
    suggestChk.checked = effective('trim').apply_suggestions !== false;
  }

  async function loadPlatform() {
    state.stages = await api.stages(state.platform);
    renderAdvanced();
    syncPlatform();
  }

  function collect() {
    const params = {};
    for (const [stage, ed] of Object.entries(editors)) {
      const ch = ed.changes();
      if (Object.keys(ch).length) params[stage] = ch;
    }
    const set = (stage, key, value) => { params[stage] = { ...(params[stage] || {}), [key]: value }; };
    set('analyze', 'skip', !analyzeChk.checked);
    set('trim', 'skip', !trimChk.checked);
    set('trim', 'apply_suggestions', suggestChk.checked);
    const mode = Object.values(asmRadios).find((r) => r.checked)?.value || 'assemble';
    if (mode !== 'external') set('assemble', 'mode', mode);
    set('blast', 'db', dbSel.value || null);
    const selMode = selectRadios.manual.checked ? 'manual' : 'auto';
    set('select', 'mode', selMode);
    if (selMode === 'auto') {
      set('rank', 'top_groups', topGroups.value === '' ? null : Number(topGroups.value));
      set('rank', 'alternatives', alternatives.value === '' ? null : Number(alternatives.value));
      set('rank', 'min_score', minScore.value === '' ? null : Number(minScore.value));
    }
    if (!LONG.has(state.platform)) set('map', 'sensitivity', sensSel.value);
    const body = {
      platform: state.platform,
      r1: r1.value,
      r2: LONG.has(state.platform) ? null : r2.value || null,
      assembly: mode === 'external' ? asmField.value : null,
      name: nameInput.value.trim() || null,
      notes: notesInput.value,
      params,
      pauses: Object.entries(pauseBoxes).filter(([, c]) => c.checked).map(([k]) => k),
    };
    if (!body.r1) return [null, 'Choose the read file (R1).'];
    if (mode === 'external' && !body.assembly) return [null, 'Choose the assembly file, or pick another assembly option.'];
    if (!body.params.blast.db) return [null, 'Choose a BLAST database.'];
    return [body, null];
  }

  async function submit(queue) {
    const [body, problem] = collect();
    errorBox.replaceChildren();
    if (problem) {
      errorBox.append(callout('error', problem));
      return;
    }
    body.queue = queue;
    const job = await act(() => api.createJob(body));
    if (job) {
      toast(`Job #${job.id} created${queue ? ' and queued' : ''}`, 'good');
      location.hash = `#/job/${job.id}`;
    }
  }

  platformSel.addEventListener('change', async () => {
    state.platform = platformSel.value;
    await act(loadPlatform);
  });

  const row = (label, control, help) => [h('label', {}, label), control, help ? h('div', { class: 'help' }, help) : null];

  main.append(
    h('div', { class: 'page-head' }, h('h1', {}, 'New job')),
    h('div', { class: 'stack' },
      h('section', { class: 'card' }, h('h2', {}, 'Reads'),
        h('div', { class: 'form-grid' },
          ...row('Platform', platformSel),
          ...row('Reads (R1)', h('div', {}, r1.el, detectNote)),
          h('label', {}, 'Reads (R2)'), r2Row,
          ...row('Name', nameInput),
          ...row('Notes', notesInput))),
      h('section', { class: 'card' }, h('h2', {}, 'Workflow'),
        h('div', { class: 'form-grid' },
          ...row('Analyze', h('label', { class: 'check' }, analyzeChk, 'Inspect the reads (simpletrim analyze)')),
          ...row('Trim', h('div', { class: 'radio-list' },
            h('label', { class: 'check' }, trimChk, 'Trim the reads (simpletrim)'),
            h('label', { class: 'check' }, suggestChk, 'Apply the settings suggested by the analysis'))),
          ...row('Assembly', asmList),
          ...row('BLAST database', dbSel),
          ...row('Hit selection', selectList),
          h('label', {}, 'Mapping sensitivity'), sensRow,
          ...row('Pause after', pauseRow, 'The job stops after these stages until you continue it.'))),
      h('section', { class: 'card' },
        h('details', { class: 'advanced' }, h('summary', {}, 'All other parameters'), advanced)),
      errorBox,
      h('div', { class: 'row' },
        h('button', { class: 'btn primary', type: 'button', onclick: () => submit(true) }, 'Create and queue'),
        h('button', { class: 'btn', type: 'button', onclick: () => submit(false) }, 'Create without queueing'),
        h('a', { class: 'btn ghost', href: '#/' }, 'Cancel'))));

  (async () => {
    try {
      const [info, settings, dbs] = await Promise.all([api.info(), api.settings(), api.dbs().catch(() => [])]);
      state.settings = settings;
      state.dbs = dbs;
      for (const name of info.stages) {
        if (name === 'select' || name === 'report') continue;
        const c = h('input', { type: 'checkbox' });
        pauseBoxes[name] = c;
        pauseRow.append(h('label', { class: 'check small' }, c, name));
      }
      dbSel.replaceChildren(
        dbs.length ? null : h('option', { value: '' }, 'No database found'),
        ...dbs.map((d) => h('option', { value: d.name }, `${d.title} (${d.name}, ${d.sequences.toLocaleString('en-US')} sequences)`)));
      const preferred = settings['default.blast.db'];
      if (preferred) dbSel.value = preferred;
      await loadPlatform();
      syncAssembly();
    } catch (err) {
      errorBox.replaceChildren(callout('error', 'Could not load the form data', err.message));
    }
  })();
  return null;
}
