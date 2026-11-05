// Results: overview per taxon or per reference, and the detail page of one reference.

import { api } from '../api.js';
import { binsTable, coveragePlot } from '../components/plot.js';
import { FLAG_HELP, callout, flagBadge, fmt, h, meter } from '../ui.js';

function crumbs(id, job, tail) {
  return h('div', { class: 'crumbs' }, h('a', { href: '#/' }, 'Jobs'), ' / ',
    h('a', { href: `#/job/${id}` }, `#${id} ${job.name}`), ' / ', tail);
}

const refLink = (id, ref) => `#/job/${id}/results/${encodeURIComponent(ref)}`;

export function resultsView(main, idText) {
  const id = Number(idText);
  const root = h('div', {}, h('div', { class: 'empty' }, 'Loading…'));
  main.append(root);
  let mode = 'taxa';
  let showEmpty = false;
  const open = new Set();
  let data = null;

  function flagsCell(flags) {
    return h('td', { class: 'flags-cell' }, (flags || []).map(flagBadge));
  }

  function refRow(r, cls = '') {
    return h('tr', { class: `clickable ${cls}`, onclick: () => { location.hash = refLink(id, r.ref); } },
      h('td', { class: cls === 'member' ? 'indent' : '' },
        h('a', { href: refLink(id, r.ref), onclick: (e) => e.stopPropagation() }, r.species || r.title || r.ref),
        h('div', { class: 'help mono' }, r.ref)),
      h('td', { class: 'num' }, fmt.int(r.reads)),
      h('td', { class: 'num secondary' }, fmt.int(r.ambiguous)),
      h('td', { class: 'num' }, fmt.pct(r.breadth), meter(r.breadth)),
      h('td', { class: 'num' }, fmt.num(r.mean_depth, 2)),
      h('td', { class: 'num' }, fmt.int(r.max_depth)),
      h('td', { class: 'num' }, fmt.pct(r.identity)),
      flagsCell(r.flags));
  }

  const head = (first) => h('thead', {}, h('tr', {},
    h('th', {}, first), h('th', { class: 'num' }, 'Reads'),
    h('th', { class: 'num', title: 'Reads with MAPQ 0 (fit another reference equally well)' }, 'Ambiguous'),
    h('th', { class: 'num', title: 'Part of the reference covered at the minimum depth' }, 'Breadth'),
    h('th', { class: 'num' }, 'Mean depth'), h('th', { class: 'num' }, 'Max depth'),
    h('th', { class: 'num', title: 'Mean identity of the mapped reads' }, 'Identity'), h('th', {}, 'Flags')));

  function taxaTable() {
    const byRef = Object.fromEntries(data.references.map((r) => [r.ref, r]));
    const body = [];
    for (const t of data.taxa) {
      if (!showEmpty && t.reads === 0) continue;
      const best = byRef[t.best.ref] || t.best;
      const multi = t.references.length > 1;
      const isOpen = open.has(t.key);
      body.push(h('tr', { class: 'clickable', onclick: () => { location.hash = refLink(id, best.ref); } },
        h('td', {},
          multi ? h('span', {
            class: 'caret', role: 'button', tabindex: '0', 'aria-expanded': String(isOpen),
            onclick: (e) => { e.stopPropagation(); if (isOpen) open.delete(t.key); else open.add(t.key); render(); },
          }, isOpen ? '▾' : '▸') : h('span', { class: 'caret' }),
          h('strong', {}, t.name),
          h('div', { class: 'help' }, multi ? `${t.references.length} references · best ` : 'reference ', h('span', { class: 'mono' }, best.ref))),
        h('td', { class: 'num' }, fmt.int(t.reads)),
        h('td', { class: 'num secondary' }, fmt.int(t.ambiguous)),
        h('td', { class: 'num' }, fmt.pct(best.breadth), meter(best.breadth)),
        h('td', { class: 'num' }, fmt.num(best.mean_depth, 2)),
        h('td', { class: 'num' }, fmt.int(best.max_depth)),
        h('td', { class: 'num' }, fmt.pct(best.identity)),
        flagsCell(best.flags)));
      if (multi && isOpen) {
        for (const ref of t.references) if (byRef[ref]) body.push(refRow(byRef[ref], 'member'));
      }
    }
    return { body, header: head('Taxon (best reference)') };
  }

  function refsTable() {
    const refs = data.references.filter((r) => showEmpty || r.reads > 0);
    return { body: refs.map((r) => refRow(r)), header: head('Reference') };
  }

  function render() {
    if (!data.available) {
      root.replaceChildren(crumbs(id, data.job, 'results'),
        h('div', { class: 'page-head' }, h('h1', {}, 'Results')),
        callout('info', 'No results yet.', 'Results appear when the report stage of the job is done.'));
      return;
    }
    const withReads = data.references.filter((r) => r.reads > 0);
    const total = withReads.reduce((a, r) => a + r.reads, 0);
    const empty = data.references.length - withReads.length;
    const { body, header } = mode === 'taxa' ? taxaTable() : refsTable();
    const seg = h('div', { class: 'seg', role: 'group', 'aria-label': 'View' },
      h('button', { type: 'button', class: mode === 'taxa' ? 'on' : '', onclick: () => { mode = 'taxa'; render(); } }, 'Per taxon'),
      h('button', { type: 'button', class: mode === 'refs' ? 'on' : '', onclick: () => { mode = 'refs'; render(); } }, 'Per reference'));
    root.replaceChildren(
      crumbs(id, data.job, 'results'),
      h('div', { class: 'page-head' }, h('h1', {}, 'Results'),
        h('a', { class: 'btn', href: api.exportUrl(id, 'tsv'), download: '' }, 'Download TSV'),
        h('a', { class: 'btn', href: api.exportUrl(id, 'json'), download: '' }, 'Download JSON')),
      h('div', { class: 'stack' },
        h('div', { class: 'tiles' },
          h('div', { class: 'tile' }, h('div', { class: 'label' }, 'Taxa with reads'), h('div', { class: 'value' }, fmt.int(data.taxa.filter((t) => t.reads > 0).length))),
          h('div', { class: 'tile' }, h('div', { class: 'label' }, 'References with reads'),
            h('div', { class: 'value' }, fmt.int(withReads.length)), h('div', { class: 'sub' }, `of ${fmt.int(data.references.length)} selected`)),
          h('div', { class: 'tile' }, h('div', { class: 'label' }, 'Mapped reads'), h('div', { class: 'value' }, fmt.int(total)))),
        h('section', { class: 'card' },
          h('div', { class: 'card-head' }, seg, h('span', { class: 'grow' }),
            empty ? h('label', { class: 'check small' },
              h('input', { type: 'checkbox', checked: showEmpty, onchange: (e) => { showEmpty = e.target.checked; render(); } }),
              `Show ${empty} reference(s) without reads`) : null),
          body.length
            ? h('div', { class: 'table-wrap' }, h('table', { class: 'data' }, header, h('tbody', {}, body)))
            : h('div', { class: 'empty' }, 'No reads mapped to the selected references.'),
          h('div', { class: 'help', style: { marginTop: '10px' } },
            'Flags: ', Object.keys(FLAG_HELP).filter((f) => f !== 'weak').map((f) => h('span', {}, flagBadge(f), ' ')),
            '— hover a flag for its meaning.'))));
  }

  Promise.all([api.results(id)])
    .then(([res]) => { data = res; render(); })
    .catch((err) => root.replaceChildren(callout('error', 'Could not load the results', err.message)));
  return null;
}

export function resultDetailView(main, idText, ref) {
  const id = Number(idText);
  const root = h('div', {}, h('div', { class: 'empty' }, 'Loading…'));
  main.append(root);
  let plot = null;

  function tile(label, value, sub) {
    return h('div', { class: 'tile' }, h('div', { class: 'label' }, label), h('div', { class: 'value' }, value),
      sub ? h('div', { class: 'sub' }, sub) : null);
  }

  Promise.all([api.job(id), api.result(id, ref)]).then(([job, r]) => {
    const report = job.stages.find((s) => s.name === 'report');
    const minDepth = report.params.min_depth || 1;
    const flags = (r.flags || []).map((f) => callout('warn', f, FLAG_HELP[f]));
    let plotArea;
    let tableArea = h('div', {});
    if (r.reads > 0 && r.bins && r.bins.count) {
      plot = coveragePlot({ length: r.length, bins: r.bins, minDepth, title: `Coverage of ${r.ref}` });
      const seg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Depth scale' });
      const setScale = (v) => {
        plot.setScale(v);
        seg.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.v === v));
      };
      seg.append(
        h('button', { type: 'button', class: 'on', dataset: { v: 'linear' }, onclick: () => setScale('linear') }, 'Linear'),
        h('button', { type: 'button', dataset: { v: 'log' }, onclick: () => setScale('log') }, 'Log'));
      const tableToggle = h('button', {
        class: 'btn small ghost', type: 'button',
        onclick: () => {
          if (tableArea.childElementCount) { tableArea.replaceChildren(); tableToggle.textContent = 'Show as table'; }
          else { tableArea.replaceChildren(binsTable({ length: r.length, bins: r.bins })); tableToggle.textContent = 'Hide table'; }
        },
      }, 'Show as table');
      plotArea = h('section', { class: 'card' },
        h('div', { class: 'card-head' }, h('h2', {}, 'Depth along the reference'),
          h('span', { class: 'help' }, `mean depth per bin, ${fmt.int(r.bins.count)} bins of ~${fmt.bp(r.length / r.bins.count)}`),
          h('span', { class: 'grow' }), seg, tableToggle),
        plot.el, tableArea);
    } else {
      plotArea = h('section', { class: 'card' }, h('div', { class: 'empty' }, 'No reads mapped to this reference.'));
    }
    const name = r.species || r.title || r.ref;
    root.replaceChildren(
      crumbs(id, job, h('span', {}, h('a', { href: `#/job/${id}/results` }, 'results'), ` / ${r.ref}`)),
      h('div', { class: 'page-head' }, h('h1', {}, name)),
      h('div', { class: 'secondary small', style: { marginTop: '-10px', marginBottom: '16px' } },
        h('span', { class: 'mono' }, r.ref), ` · ${fmt.bp(r.length)}`, r.taxid ? ` · taxid ${r.taxid}` : '',
        r.title && r.title !== name ? ` · ${r.title}` : ''),
      h('div', { class: 'stack' },
        ...flags,
        h('div', { class: 'tiles' },
          tile('Mapped reads', fmt.int(r.reads), `${fmt.int(r.ambiguous)} ambiguous (MAPQ 0)`),
          tile(`Breadth (≥ ${minDepth}×)`, fmt.pct(r.breadth), `${fmt.pct(r.breadth_1x)} covered at ≥ 1×`),
          tile('Mean depth', `${fmt.num(r.mean_depth, 2)}×`, `${fmt.num(r.mean_depth_covered, 2)}× over covered bases`),
          tile('Max depth', `${fmt.int(r.max_depth)}×`),
          tile('Read identity', fmt.pct(r.identity), 'mean over mapped reads'),
          tile('Expected breadth', fmt.pct(r.expected_breadth),
            r.breadth_ratio == null ? 'if reads were spread evenly' : `observed/expected ${fmt.num(r.breadth_ratio, 2)}`)),
        plotArea));
  }).catch((err) => root.replaceChildren(callout('error', 'Could not load this result', err.message)));

  return { dispose: () => plot && plot.dispose() };
}
