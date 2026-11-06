// Job page: status, actions, stages (log, parameters, reset, pause), inputs, analysis.

import { api } from '../api.js';
import { pathField } from '../components/filepicker.js';
import { paramEditor } from '../components/params.js';
import { qualityPlot, qualityTable } from '../components/plot.js';
import {
  act, callout, fmt, h, live, modal, pauseText, promptDialog, stateBadge, statusBadge, toast,
} from '../ui.js';
import { cancelJob, continueJob, deleteJob, queueJob, resetJob, saveParams } from './actions.js';

const SKIPPABLE = new Set(['analyze', 'trim', 'assemble']);
const LONG_PLATFORMS = new Set(['ont', 'hifi', 'clr']);

function summaryText(stage, s) {
  if (!s) return '';
  switch (stage) {
    case 'input': return (s.files || []).map((f) => `${f.role}: ${fmt.bytes(f.size)}`).join(', ');
    case 'analyze': return `${(s.platform || {}).name || '?'}; ${(s.suggestions || []).length} suggestion(s), ${(s.warnings || []).length} warning(s)`;
    case 'trim': return `${s.mode ? `${s.mode} mode: ` : ''}${fmt.int(s.reads_in)} → ${fmt.int(s.reads_out)} reads`;
    case 'assemble': return `${fmt.int(s.sequences)} contigs, ${fmt.bp(s.bases)}, N50 ${fmt.int(s.n50)}`;
    case 'blast': return `${s.mode}, ${s.task}: ${fmt.int(s.queries)} queries, ${fmt.int(s.hsps)} hits`
      + (s.database && s.database.updated ? ` (database of ${s.database.updated})` : '');
    case 'rank': return `${fmt.int(s.references_supported)} references in ${fmt.int(s.groups)} groups, ${s.auto_selected} auto-selected`;
    case 'select': return `${s.selected} selected (${s.mode})`;
    case 'extract': return `${s.references} references, ${fmt.bp(s.bases)}`
      + (s.database_changed ? ` (database updated since blast: ${s.database_changed.blast} → ${s.database_changed.extract})` : '');
    case 'map': return s.skipped ? s.skipped : `${fmt.int(s.alignments)} alignments (${s.preset}${s.sensitivity ? `, ${s.sensitivity}` : ''})`;
    case 'report': return `${s.with_reads} of ${s.references} references with reads`;
    default: return '';
  }
}

export function jobView(main, idText) {
  const id = Number(idText);
  let stageInfo = null;
  let stageNames = [];
  let job = null;
  const root = h('div', {});
  main.append(root);

  async function load() {
    if (!stageInfo) {
      const first = await api.job(id);
      stageInfo = await api.stages(first.platform);
      stageNames = stageInfo.map((s) => s.name);
    }
    return api.job(id);
  }

  const refresh = () => view.refresh();
  const after = (ok) => { if (ok) refresh(); };

  async function showLog(stage) {
    const pre = h('pre', { class: 'log' }, 'Loading…');
    let timer = null;
    const loadLog = async () => {
      const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
      try {
        const res = await api.log(id, stage);
        pre.textContent = res.text || '(empty log)';
        if (atBottom) pre.scrollTop = pre.scrollHeight;
      } catch (err) {
        pre.textContent = err.message;
      }
    };
    await loadLog();
    pre.scrollTop = pre.scrollHeight;
    if (job && job.status === 'running') timer = setInterval(loadLog, 2000);
    modal({
      title: `Log: ${stage}`, wide: true, body: pre,
      actions: [{ label: 'Refresh', onClick: async () => { await loadLog(); return false; } }, { label: 'Close', kind: 'primary' }],
      onClose: () => clearInterval(timer),
    });
  }

  function editParams(stage) {
    const st = job.stages.find((s) => s.name === stage);
    const info = stageInfo.find((s) => s.name === stage);
    const readMode = LONG_PLATFORMS.has(job.platform) ? 'long' : 'short';
    const ed = paramEditor(info.params, st.params, { exclude: ['confirmed'], mode: readMode, autoMode: readMode });
    const note = st.state !== 'pending'
      ? callout('warn', 'This stage is done.', 'Saving changed parameters resets the job to this stage.')
      : null;
    modal({
      title: `Parameters: ${stage}`, wide: true,
      body: h('div', { class: 'stack' }, note, ed.el),
      actions: [
        { label: 'Cancel', kind: 'ghost' },
        {
          label: 'Save', kind: 'primary',
          onClick: async () => {
            const changes = ed.changes();
            if (!Object.keys(changes).length) return true;
            const ok = await saveParams(job, stage, changes, stageNames);
            if (ok) {
              toast('Parameters saved', 'good');
              refresh();
            }
            return ok;
          },
        },
      ],
    });
  }

  function reassign() {
    const fields = {
      r1: pathField({ kind: 'fastq', title: 'Choose R1', value: job.inputs.r1?.path }),
      r2: job.paired ? pathField({ kind: 'fastq', title: 'Choose R2', value: job.inputs.r2?.path }) : null,
      assembly: pathField({ kind: 'fasta', title: 'Choose the assembly', value: job.inputs.assembly?.path || '' }),
    };
    const force = h('input', { type: 'checkbox' });
    const msg = h('div', {});
    modal({
      title: 'Reassign input files', wide: true,
      body: h('div', { class: 'stack' },
        h('p', { class: 'secondary' }, 'Point the job to moved files. If a file has the same content (checked by a fingerprint), all results are kept.'),
        h('div', { class: 'form-grid' },
          h('label', {}, 'Reads (R1)'), fields.r1.el,
          fields.r2 ? [h('label', {}, 'Reads (R2)'), fields.r2.el] : null,
          h('label', {}, 'Assembly'), fields.assembly.el),
        h('label', { class: 'check' }, force, 'The files are different: reset the job (reads: to the start, assembly: to “assemble”)'),
        msg),
      actions: [
        { label: 'Cancel', kind: 'ghost' },
        {
          label: 'Reassign', kind: 'primary',
          onClick: async () => {
            const body = { force: force.checked };
            for (const [role, f] of Object.entries(fields)) {
              if (f && f.value && f.value !== job.inputs[role]?.path) body[role] = f.value;
            }
            if (!body.r1 && !body.r2 && !body.assembly) {
              msg.replaceChildren(callout('info', 'Nothing changed.'));
              return false;
            }
            try {
              const res = await api.reassign(id, body);
              toast(res.reset_to ? `Inputs changed; job reset to “${res.reset_to}”` : 'Same files at a new location: results kept', 'good');
              refresh();
              return true;
            } catch (err) {
              msg.replaceChildren(callout(err.code === 'conflict' ? 'warn' : 'error', err.message));
              return false;
            }
          },
        },
      ],
    });
  }

  function header() {
    const actions = h('div', { class: 'row' });
    const add = (label, fn, cls = '') => actions.append(h('button', {
      class: `btn ${cls}`, type: 'button', onclick: async () => after(await fn(job)),
    }, label));
    const stageState = (name) => job.stages.find((s) => s.name === name)?.state;
    if (job.status === 'paused' && job.pause_reason === 'select') {
      actions.append(h('a', { class: 'btn primary', href: `#/job/${id}/hits` }, 'Choose hits'));
    } else if (job.status === 'paused') add('Continue', continueJob, 'primary');
    if (job.status === 'stopped' || job.status === 'error') add('Queue', queueJob, job.current_stage ? 'primary' : '');
    if (['queued', 'running', 'paused'].includes(job.status)) add('Cancel', cancelJob);
    if (stageState('rank') === 'done' && job.pause_reason !== 'select') actions.append(h('a', { class: 'btn', href: `#/job/${id}/hits` }, 'Hits'));
    if (stageState('report') === 'done') actions.append(h('a', { class: 'btn primary', href: `#/job/${id}/results` }, 'Results'));
    actions.append(h('button', {
      class: 'btn ghost', type: 'button',
      onclick: async () => {
        const name = await promptDialog({ title: 'Rename job', value: job.name });
        if (name != null && name.trim()) after(await act(() => api.editJob(id, { name: name.trim() })));
      },
    }, 'Rename'));
    actions.append(h('button', {
      class: 'btn ghost', type: 'button',
      onclick: async () => { if (await deleteJob(job)) location.hash = '#/'; },
    }, 'Delete'));
    return h('div', {},
      h('div', { class: 'crumbs' }, h('a', { href: '#/' }, 'Jobs'), ` / #${id}`),
      h('div', { class: 'page-head' },
        h('h1', {}, job.name), statusBadge(job.status), actions));
  }

  function notices() {
    const out = [];
    if (job.status === 'error') {
      const stage = job.error ? job.error.split(':')[0] : null;
      out.push(callout('error', 'The job stopped with an error.',
        h('pre', {}, job.error || ''),
        h('div', { class: 'row', style: { marginTop: '8px' } },
          stage && stageNames.includes(stage) ? h('button', { class: 'btn small', type: 'button', onclick: () => showLog(stage) }, 'View log') : null,
          h('span', { class: 'help' }, 'Fix the cause (e.g. reassign a missing input or change a parameter), then queue the job again.'))));
    }
    if (job.status === 'paused' && job.pause_reason === 'select') {
      out.push(callout('info', 'Waiting for hit selection.', 'Choose which reference sequences to map, then continue the job.'));
    } else if (job.status === 'paused') {
      out.push(callout('info', `${pauseText(job)}.`, 'Check the results so far and change parameters if needed, then continue.'));
    }
    if (job.cancel_request) out.push(callout('warn', 'Cancelling…', 'The worker is stopping this job.'));
    if (job.status === 'queued') out.push(callout('info', 'Queued.', 'The job runs when the worker reaches it. Make sure the worker is running (see the Jobs page).'));
    return out;
  }

  function stagesCard() {
    const rows = job.stages.map((st) => {
      const running = job.status === 'running' && job.current_stage === st.name;
      const took = st.started && st.finished ? fmt.dur(st.finished - st.started) : running && st.started ? `${fmt.dur(Date.now() / 1000 - st.started)} so far` : '';
      const info = stageInfo.find((s) => s.name === st.name);
      const hasParams = info && info.params.length > 0;
      const canReset = job.status !== 'running' && st.state !== 'pending';
      const pause = h('input', {
        type: 'checkbox', checked: st.pause_after, title: 'Pause after this stage',
        onchange: async (e) => after(await act(() => api.setPause(id, st.name, e.target.checked))),
      });
      let state = stateBadge(st.state, running);
      if (st.state === 'pending' && st.params.skip === true) state = h('span', { class: 'secondary small' }, 'will be skipped');
      if (st.name === 'assemble' && st.state === 'pending' && st.params.mode === 'skip') state = h('span', { class: 'secondary small' }, 'will be skipped');
      return h('tr', { class: running ? 'current' : '' },
        h('td', { class: 'num muted' }, st.idx),
        h('td', { class: 'nowrap' }, h('strong', {}, st.name)),
        h('td', { class: 'state' }, state),
        h('td', { class: 'num secondary small' }, took),
        h('td', { class: 'small' }, st.error ? h('span', { style: { color: 'var(--critical)' } }, st.error.split('\n')[0]) : summaryText(st.name, st.summary)),
        h('td', { class: 'nowrap' }, h('label', { class: 'check small secondary' }, pause, 'pause')),
        h('td', { class: 'actions' },
          h('button', { class: 'btn small ghost', type: 'button', onclick: () => showLog(st.name) }, 'Log'),
          hasParams ? h('button', { class: 'btn small ghost', type: 'button', disabled: job.status === 'running', onclick: () => editParams(st.name) }, 'Parameters') : null,
          canReset ? h('button', { class: 'btn small ghost', type: 'button', onclick: async () => after(await resetJob(job, st.name, stageNames)) }, 'Reset to here') : null));
    });
    return h('section', { class: 'card' },
      h('div', { class: 'card-head' }, h('h2', {}, 'Stages'),
        SKIPPABLE.size ? h('span', { class: 'help' }, 'Reset discards a stage and everything after it.') : null),
      h('div', { class: 'table-wrap' }, h('table', { class: 'data stages' },
        h('thead', {}, h('tr', {}, h('th', { class: 'num' }, '#'), h('th', {}, 'Stage'), h('th', {}, 'State'),
          h('th', { class: 'num' }, 'Time'), h('th', {}, 'Summary'), h('th', {}, ''), h('th', {}, ''))),
        h('tbody', {}, rows))));
  }

  function infoCard() {
    const inputs = Object.entries(job.inputs).map(([role, f]) => [
      h('div', {}, role),
      h('div', {}, h('div', { class: 'mono break' }, f.path), h('div', { class: 'help' }, fmt.bytes(f.size))),
    ]);
    return h('section', { class: 'card' },
      h('div', { class: 'card-head' }, h('h2', {}, 'Job'),
        h('button', { class: 'btn small', type: 'button', disabled: job.status === 'running', onclick: reassign }, 'Reassign files')),
      h('div', { class: 'kv' },
        h('div', {}, 'Platform'), h('div', {}, `${job.platform}, ${job.paired ? 'paired reads' : 'single read file'}`),
        h('div', {}, 'Created'), h('div', {}, fmt.time(job.created)),
        h('div', {}, 'Updated'), h('div', {}, fmt.time(job.updated)),
        ...inputs.flat(),
        h('div', {}, 'Notes'),
        h('div', {}, job.notes ? h('div', { style: { whiteSpace: 'pre-wrap' } }, job.notes) : h('span', { class: 'muted' }, 'none'), ' ',
          h('button', {
            class: 'btn small ghost', type: 'button',
            onclick: async () => {
              const notes = await promptDialog({ title: 'Notes', value: job.notes, multiline: true });
              if (notes != null) after(await act(() => api.editJob(id, { notes })));
            },
          }, 'Edit'))));
  }

  // read statistics: loaded once per analyze run (keyed by its finish time), kept across re-renders
  const analysis = { key: null, data: null, error: null };
  let analysisPlots = [];
  let showQualityTable = false;

  function loadAnalysis(key) {
    analysis.key = key;
    analysis.data = null;
    analysis.error = null;
    api.analysis(id)
      .then((d) => { analysis.data = d; })
      .catch((err) => { analysis.error = err.message; })
      .finally(() => { if (job && analysis.key === key) render(job); });
  }

  const ge = (label) => label.replace(/>=/g, '≥');

  function statsBlock() {
    if (analysis.error) return callout('info', 'No statistics', analysis.error);
    if (!analysis.data) return h('div', { class: 'empty' }, 'Loading the statistics…');
    const a = analysis.data;
    const table = h('div', { class: 'table-wrap analysis-wrap' }, h('table', { class: 'data analysis-table' },
      a.columns.length > 1 ? h('thead', {}, h('tr', {}, h('th', {}, ''), a.columns.map((c) => h('th', { class: 'num' }, c)))) : null,
      h('tbody', {}, a.rows.map((r) => h('tr', {}, h('td', { class: 'secondary' }, ge(r.label)),
        r.values.map((v) => h('td', { class: 'num' }, v)))))));
    const extra = a.extra.length
      ? h('div', { class: 'kv', style: { marginTop: '14px', gridTemplateColumns: '190px 1fr' } },
        a.extra.flatMap((e) => [h('div', {}, e.label), h('div', { class: /^[ACGTN]{6,}/.test(e.value) ? 'mono break' : '' }, e.value)]))
      : null;
    let quality = null;
    if (a.quality) {
      const q = a.quality;
      const opts = { series: q.series, xLabel: q.x_label, xName: q.x_name, xBin: q.x_bin, yLabel: q.y_label };
      const plot = qualityPlot(opts);
      analysisPlots.push(plot);
      const area = h('div', {}, showQualityTable ? qualityTable(opts) : null);
      const toggle = h('button', {
        class: 'btn small ghost', type: 'button',
        onclick: () => {
          showQualityTable = !showQualityTable;
          area.replaceChildren(...(showQualityTable ? [qualityTable(opts)] : []));
          toggle.textContent = showQualityTable ? 'Hide table' : 'Show as table';
        },
      }, showQualityTable ? 'Hide table' : 'Show as table');
      quality = h('div', { style: { marginTop: '18px' } },
        h('div', { class: 'card-head' }, h('h3', {}, 'Quality along the reads'), toggle),
        plot.el, area);
    }
    return h('div', {}, table, extra, quality,
      a.footnote ? h('p', { class: 'help', style: { marginTop: '8px' } }, a.footnote) : null);
  }

  function analysisCard() {
    const st = job.stages.find((s) => s.name === 'analyze');
    if (st.state !== 'done' || !st.summary) return null;
    if (analysis.key !== st.finished) loadAnalysis(st.finished);
    const s = st.summary;
    const trim = job.stages.find((x) => x.name === 'trim');
    const applies = trim.params.apply_suggestions;
    const toggle = h('input', {
      type: 'checkbox', checked: applies,
      onchange: async (e) => after(await saveParams(job, 'trim', { apply_suggestions: e.target.checked }, stageNames)),
      disabled: job.status === 'running',
    });
    const applied = trim.state === 'done' ? (trim.summary?.applied_suggestions || []) : null;
    const a = analysis.data;
    const plat = s.platform || {};
    return h('section', { class: 'card' },
      h('h2', {}, 'Read analysis'),
      h('div', { class: 'kv', style: { marginBottom: '14px' } },
        h('div', {}, 'Detected'), h('div', {}, a ? a.platform : (plat.name || 'unknown')),
        h('div', {}, 'Inspected'), h('div', {}, a ? a.inspected : fmt.int(s.sample?.records))),
      (s.warnings || []).length ? h('div', { style: { marginBottom: '14px' } }, callout('warn', 'Warnings', h('ul', { class: 'plain' }, s.warnings.map((w) => h('li', {}, w))))) : null,
      statsBlock(),
      (s.notes || []).length ? h('div', { style: { marginTop: '16px' } }, h('h3', {}, 'Notes'), h('ul', { class: 'plain small secondary' }, s.notes.map((n) => h('li', {}, n)))) : null,
      h('div', { style: { marginTop: '16px' } },
        h('h3', {}, 'Suggested trim settings'),
        (s.suggestions || []).length
          ? h('ul', { class: 'plain' }, s.suggestions.map((x) => h('li', {}, h('code', {}, x.flags.join(' ')), h('span', { class: 'secondary small' }, ` — ${x.reason}`))))
          : h('p', { class: 'secondary small' }, 'No changes suggested; the defaults fit these reads.'),
        h('label', { class: 'check small', style: { marginTop: '6px' } }, toggle, 'Apply the suggestions when trimming'),
        applied ? h('div', { class: 'help' }, applied.length ? `Applied: ${applied.join(' ')}` : 'Nothing was applied in the last trimming run.') : null));
  }

  function render(data) {
    job = data;
    document.title = `${job.name} · readscreen`;
    analysisPlots.forEach((p) => p.dispose());
    analysisPlots = [];
    root.replaceChildren(header(), h('div', { class: 'stack' }, ...notices(), stagesCard(), analysisCard(), infoCard()));
  }

  const view = live(root, {
    load,
    render,
    interval: (d) => (d && ['running', 'queued'].includes(d.status) ? 2000 : 8000),
  });
  return {
    dispose() {
      view.dispose();
      analysisPlots.forEach((p) => p.dispose());
      document.title = 'readscreen';
    },
  };
}
