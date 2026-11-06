// New job: one card per pipeline step. Each card has the main choices, its own "More options"
// (all other parameters of its stages) and its pause checkboxes.

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
const MAP_PRESETS = { ont: 'map-ont', hifi: 'map-hifi', clr: 'map-pb' };
// parameters shown directly in the step cards; "More options" lists the rest
const MAIN = {
  analyze: ['skip'],
  trim: ['skip', 'mode', 'apply_suggestions', 'min_mean_quality', 'min_length'],
  assemble: ['mode', 'fragment_length', 'fragment_overlap'],
  blast: ['db'],
  rank: ['top_groups', 'alternatives', 'min_score'],
  select: ['mode'],
  map: ['sensitivity'],
};
const STEPS = [
  { key: 'reads', title: 'Reads', stages: ['input'] },
  { key: 'trim', title: 'Read check and trimming', stages: ['analyze', 'trim'] },
  { key: 'assembly', title: 'Assembly', stages: ['assemble'] },
  { key: 'blast', title: 'BLAST', stages: ['blast'] },
  { key: 'hits', title: 'Hit selection', stages: ['rank', 'select'] },
  { key: 'mapping', title: 'Mapping and coverage', stages: ['extract', 'map', 'report'] },
];
const NO_PAUSE = new Set(['select', 'report']);
const STAGE_LABEL = {
  input: 'input check', analyze: 'analysis', trim: 'trimming', assemble: 'assembly', blast: 'BLAST',
  rank: 'ranking', select: 'selection', extract: 'reference extraction', map: 'mapping', report: 'coverage report',
};

export function createView(main) {
  const state = {
    platform: 'illumina',
    stages: [],
    settings: {},
    dbs: [],
    assemblyTouched: false,
  };
  const editors = {}; // "More options" editor per stage
  const mainEditors = {}; // parameter rows shown directly in a card
  const moreBoxes = {};
  const pauseBoxes = {};

  // ---------------------------------------------------------------- reads
  const r1 = pathField({ kind: 'fastq', title: 'Choose the reads (R1)', placeholder: 'Path to the reads (R1 or single file)', onChange: (p) => detect(p, 'r1') });
  const r2 = pathField({ kind: 'fastq', title: 'Choose the second reads (R2)', placeholder: 'Path to R2 (paired reads only)', onChange: (p) => detect(p, 'r2') });
  const detectNote = h('div', { class: 'note' });
  const nameInput = h('input', { type: 'text', placeholder: 'Taken from the file name' });
  const notesInput = h('textarea', { rows: 2, placeholder: 'Optional' });
  const platformSel = h('select', {}, PLATFORMS.map(([v, l]) => h('option', { value: v }, l)));
  const r2Row = h('div', { class: 'stack' }, r2.el);

  // ---------------------------------------------------------------- read check and trimming
  const analyzeChk = h('input', { type: 'checkbox', checked: true });
  const trimChk = h('input', { type: 'checkbox', checked: true });
  const suggestChk = h('input', { type: 'checkbox', checked: true });
  const trimModeSel = h('select', {});
  const trimMainBox = h('div', {});
  const trimOptions = h('div', { class: 'stack' });

  // ---------------------------------------------------------------- assembly, BLAST, hits, mapping
  const asmRadios = {};
  const asmField = pathField({ kind: 'fasta', title: 'Choose an assembly (FASTA)', placeholder: 'Path to contigs (FASTA)' });
  const fragmentBox = h('div', {});
  const dbSel = h('select', {});
  const dbHint = h('div', { class: 'help' });
  const selectRadios = {};
  const topGroups = h('input', { type: 'number', min: 0, step: 1 });
  const alternatives = h('input', { type: 'number', min: 0, step: 1 });
  const minScore = h('input', { type: 'number', min: 0, step: 'any' });
  const sensSel = h('select', {},
    h('option', { value: 'normal' }, 'normal (-x sr)'),
    h('option', { value: 'sensitive' }, 'sensitive (-k15 -w5), recommended'),
    h('option', { value: 'very_sensitive' }, 'very sensitive (-k11 -w3 -m15 -s25)'));
  const sensGrid = h('div', { class: 'form-grid' }, h('label', {}, 'Sensitivity'),
    h('div', {}, sensSel, h('div', { class: 'help' }, 'Higher sensitivity finds more reads of strains that are distant from the reference.')));
  const longMapNote = h('p', { class: 'help' });
  const errorBox = h('div', {});

  function radio(group, store, value, label, help, onchange) {
    const input = h('input', { type: 'radio', name: group, value, onchange });
    store[value] = input;
    return h('label', { class: 'check', style: { alignItems: 'flex-start' } }, input,
      h('span', {}, label, help ? h('div', { class: 'help' }, help) : null));
  }

  const asmChanged = () => { state.assemblyTouched = true; syncAssembly(); };
  const asmList = h('div', { class: 'radio-list' },
    radio('asm', asmRadios, 'assemble', 'Assemble the reads', 'shortread_assembler; long reads are cut into overlapping fragments for it', asmChanged),
    radio('asm', asmRadios, 'external', 'Use an existing assembly', null, asmChanged),
    asmField.el,
    radio('asm', asmRadios, 'skip', 'No assembly: BLAST the reads', 'Large read sets are subsampled for BLAST', asmChanged));

  const selectList = h('div', { class: 'radio-list' },
    radio('sel', selectRadios, 'auto', 'Automatic', 'Best groups by score; the job runs through', syncSelect),
    h('div', { class: 'row', style: { marginLeft: '24px' } },
      h('label', { class: 'small secondary' }, 'groups'), topGroups,
      h('label', { class: 'small secondary' }, 'alternatives per group'), alternatives,
      h('label', { class: 'small secondary' }, 'min. score'), minScore),
    radio('sel', selectRadios, 'manual', 'Pause and choose the hits on this website', 'The automatic choice is preselected', syncSelect));

  const row = (label, control, help) => [h('label', {}, label), control, help ? h('div', { class: 'help' }, help) : null];

  function stepCard(step, index, intro, ...body) {
    moreBoxes[step.key] = h('div', {});
    const pausable = step.stages.filter((s) => !NO_PAUSE.has(s));
    const pauses = pausable.map((s) => {
      pauseBoxes[s] = h('input', { type: 'checkbox' });
      return h('label', { class: 'check small' }, pauseBoxes[s], STAGE_LABEL[s]);
    });
    const more = h('details', { class: 'advanced' }, h('summary', {}, 'More options'), moreBoxes[step.key]);
    return h('section', { class: 'card step' },
      h('div', { class: 'step-head' }, h('span', { class: 'step-num', 'aria-hidden': 'true' }, String(index)), h('h2', {}, step.title)),
      intro ? h('p', { class: 'secondary small step-intro' }, intro) : null,
      ...body,
      h('div', { class: 'step-foot' },
        step.key === 'reads' ? null : more,
        pauses.length ? h('div', { class: 'row pause-row' }, h('span', { class: 'small secondary' }, 'Pause after:'), pauses) : null));
  }

  function effective(stage) {
    const st = state.stages.find((x) => x.name === stage);
    const values = {};
    for (const p of st ? st.params : []) {
      const key = `default.${stage}.${p.name}`;
      values[p.name] = key in state.settings ? state.settings[key] : p.default;
    }
    return values;
  }

  const autoTrimMode = () => (LONG.has(state.platform) ? 'long' : 'short');
  const trimMode = () => (trimModeSel.value === 'auto' ? autoTrimMode() : trimModeSel.value);

  function syncTrim() {
    const m = trimMode();
    for (const ed of [mainEditors.trim, editors.trim]) if (ed) ed.setMode(m);
    trimOptions.classList.toggle('inactive', !trimChk.checked);
  }

  function syncAssembly() {
    const mode = Object.values(asmRadios).find((r) => r.checked)?.value;
    asmField.el.style.display = mode === 'external' ? '' : 'none';
    fragmentBox.style.display = mode === 'assemble' && LONG.has(state.platform) ? '' : 'none';
  }

  function syncSelect() {
    const auto = selectRadios.auto.checked;
    for (const el of [topGroups, alternatives, minScore]) el.disabled = !auto;
  }

  function syncPlatform() {
    const long = LONG.has(state.platform);
    r2Row.style.display = long ? 'none' : '';
    sensGrid.style.display = long ? 'none' : '';
    longMapNote.style.display = long ? '' : 'none';
    longMapNote.textContent = `Long reads are mapped with the minimap2 preset ${MAP_PRESETS[state.platform] || ''} (see "More options").`;
    trimModeSel.options[0].textContent = `auto: ${autoTrimMode()} reads for this platform`;
    if (!state.assemblyTouched) {
      asmRadios[effective('assemble').mode || (long ? 'skip' : 'assemble')].checked = true;
    }
    syncAssembly();
    syncTrim();
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

  function renderParams() {
    const byName = Object.fromEntries(state.stages.map((s) => [s.name, s]));
    for (const step of STEPS) {
      const box = moreBoxes[step.key];
      box.replaceChildren();
      const eds = step.stages.filter((s) => byName[s]).map((s) => {
        const ed = paramEditor(byName[s].params, effective(s), { exclude: MAIN[s] || [] });
        editors[s] = ed;
        return [s, ed];
      }).filter(([, ed]) => !ed.empty);
      for (const [s, ed] of eds) {
        if (eds.length > 1) ed.el.prepend(h('h3', {}, STAGE_LABEL[s]));
        box.append(ed.el);
      }
      if (!eds.length) box.append(h('p', { class: 'help' }, 'No further options.'));
    }
    mainEditors.trim = paramEditor(byName.trim.params, effective('trim'), {
      include: ['min_mean_quality', 'min_length'], headings: false,
      labels: { min_mean_quality: 'Min. mean read quality', min_length: 'Min. read length' },
    });
    trimMainBox.replaceChildren(mainEditors.trim.el);
    mainEditors.assemble = paramEditor(byName.assemble.params, effective('assemble'), {
      include: ['fragment_length', 'fragment_overlap'], mode: 'long', headings: false,
      labels: { fragment_length: 'Fragment length', fragment_overlap: 'Fragment overlap' },
    });
    fragmentBox.replaceChildren(mainEditors.assemble.el);

    const trim = effective('trim');
    trimModeSel.value = trim.mode || 'auto';
    analyzeChk.checked = !effective('analyze').skip;
    trimChk.checked = !trim.skip;
    suggestChk.checked = trim.apply_suggestions !== false;
    const pd = effective('rank');
    topGroups.value = pd.top_groups;
    alternatives.value = pd.alternatives;
    minScore.value = pd.min_score;
    selectRadios[effective('select').mode || 'auto'].checked = true;
    syncSelect();
    sensSel.value = effective('map').sensitivity || 'sensitive';
  }

  async function loadPlatform() {
    state.stages = await api.stages(state.platform);
    renderParams();
    syncPlatform();
  }

  function collect() {
    const params = {};
    const add = (stage, changes) => {
      if (Object.keys(changes).length) params[stage] = { ...(params[stage] || {}), ...changes };
    };
    for (const [stage, ed] of Object.entries(editors)) add(stage, ed.changes());
    for (const [stage, ed] of Object.entries(mainEditors)) add(stage, ed.changes());
    const set = (stage, key, value) => add(stage, { [key]: value });
    set('analyze', 'skip', !analyzeChk.checked);
    set('trim', 'skip', !trimChk.checked);
    set('trim', 'mode', trimModeSel.value);
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
    if (trimChk.checked && trimMode() === 'long' && body.r2) return [null, 'Trimming mode "long" takes one read file; choose "short" or "auto" for paired reads.'];
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
  trimModeSel.append(
    h('option', { value: 'auto' }, 'auto'),
    h('option', { value: 'short' }, 'short: Illumina-style reads, single or paired'),
    h('option', { value: 'long' }, 'long: nanopore and PacBio reads'));
  trimModeSel.addEventListener('change', syncTrim);
  trimChk.addEventListener('change', syncTrim);

  trimOptions.append(h('div', { class: 'form-grid' },
    ...row('Mode', trimModeSel, 'Which simpletrim mode runs. The options below and under "More options" follow it.'),
    ...row('Suggestions', h('label', { class: 'check' }, suggestChk, 'Apply the settings suggested by the analysis'))),
  trimMainBox);

  const cards = [
    stepCard(STEPS[0], 1, null,
      h('div', { class: 'form-grid' },
        ...row('Platform', platformSel),
        ...row('Reads (R1)', h('div', {}, r1.el, detectNote)),
        h('label', {}, 'Reads (R2)'), r2Row,
        ...row('Name', nameInput),
        ...row('Notes', notesInput))),
    stepCard(STEPS[1], 2, 'simpletrim inspects the reads first (quality, lengths, adapters) and suggests trim settings.',
      h('div', { class: 'radio-list' },
        h('label', { class: 'check' }, analyzeChk, 'Inspect the reads (simpletrim analyze)'),
        h('label', { class: 'check' }, trimChk, 'Trim and filter the reads (simpletrim)')),
      trimOptions),
    stepCard(STEPS[2], 3, null, asmList, fragmentBox),
    stepCard(STEPS[3], 4, 'Contigs (or reads, without assembly) are compared with the reference database.',
      h('div', { class: 'form-grid' }, ...row('Database', h('div', {}, dbSel, dbHint)))),
    stepCard(STEPS[4], 5, 'BLAST hits are ranked and grouped by reference; the selected references are mapped.', selectList),
    stepCard(STEPS[5], 6, 'The reads are mapped to the selected references (minimap2) and coverage is reported.',
      sensGrid, longMapNote),
  ];

  main.append(
    h('div', { class: 'page-head' }, h('h1', {}, 'New job')),
    h('div', { class: 'stack' },
      ...cards,
      errorBox,
      h('div', { class: 'row' },
        h('button', { class: 'btn primary', type: 'button', onclick: () => submit(true) }, 'Create and queue'),
        h('button', { class: 'btn', type: 'button', onclick: () => submit(false) }, 'Create without queueing'),
        h('a', { class: 'btn ghost', href: '#/' }, 'Cancel'))));

  (async () => {
    try {
      const [settings, dbs] = await Promise.all([api.settings(), api.dbs().catch(() => [])]);
      state.settings = settings;
      state.dbs = dbs;
      dbSel.replaceChildren(
        dbs.length ? null : h('option', { value: '' }, 'No database found'),
        ...dbs.map((d) => h('option', { value: d.name }, `${d.title} (${d.name}, ${d.sequences.toLocaleString('en-US')} sequences)`)));
      if (!dbs.length) {
        dbHint.replaceChildren('No database installed yet. Download the RefSeq viral reference genomes on the ',
          h('a', { href: '#/settings' }, 'Settings page'), '.');
      }
      const preferred = settings['default.blast.db'];
      if (preferred) dbSel.value = preferred;
      await loadPlatform();
    } catch (err) {
      errorBox.replaceChildren(callout('error', 'Could not load the form data', err.message));
    }
  })();
  return null;
}
