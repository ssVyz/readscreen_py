// Ranked BLAST hits: groups with alternatives, and the selection of references for mapping.

import { api } from '../api.js';
import { act, callout, flagBadge, fmt, h, meter, toast } from '../ui.js';
import { resetJob } from './actions.js';

export function hitsView(main, idText) {
  const id = Number(idText);
  const root = h('div', {}, h('div', { class: 'empty' }, 'Loading…'));
  main.append(root);
  let job = null;
  let rows = [];
  let stageNames = [];
  let selected = new Set();
  let saved = new Set();
  let expanded = new Set();
  let filter = '';
  let disposed = false;

  const editable = () => {
    const st = Object.fromEntries(job.stages.map((s) => [s.name, s.state]));
    return job.status !== 'running' && st.rank === 'done' && st.select === 'pending';
  };
  const dirty = () => selected.size !== saved.size || [...selected].some((r) => !saved.has(r));

  async function load() {
    const [j, ranking, info] = await Promise.all([api.job(id), api.ranking(id), api.info()]);
    if (disposed) return;
    job = j;
    rows = ranking;
    stageNames = info.stages;
    selected = new Set(rows.filter((r) => r.selected).map((r) => r.ref));
    saved = new Set(selected);
    expanded = new Set(rows.filter((r) => r.selected && !r.leader).map((r) => r.group));
    render();
  }

  async function save(cont) {
    const res = await act(() => api.setSelection(id, [...selected], cont),
      cont ? 'Selection saved; the job continues' : 'Selection saved');
    if (res !== undefined) {
      if (cont) location.hash = `#/job/${id}`;
      else await load();
    }
  }

  function matches(r) {
    if (!filter) return true;
    const text = `${r.ref} ${r.species || ''} ${r.title || ''} ${r.taxid || ''}`.toLowerCase();
    return text.includes(filter);
  }

  function table() {
    const can = editable();
    const groups = new Map();
    for (const r of rows) {
      if (!groups.has(r.group)) groups.set(r.group, []);
      groups.get(r.group).push(r);
    }
    const body = [];
    for (const [gid, members] of groups) {
      const visible = members.filter(matches);
      if (!visible.length) continue;
      const leader = members.find((m) => m.leader) || members[0];
      const others = members.filter((m) => m !== leader);
      const open = expanded.has(gid) || (filter && visible.some((m) => m !== leader));
      const line = (r, isMember) => {
        const box = h('input', {
          type: 'checkbox', checked: selected.has(r.ref), disabled: !can,
          'aria-label': `Select ${r.ref}`,
          onchange: (e) => {
            if (e.target.checked) selected.add(r.ref);
            else selected.delete(r.ref);
            render();
          },
        });
        return h('tr', { class: `${isMember ? 'member' : ''}${selected.has(r.ref) ? ' sel' : ''}` },
          h('td', {}, box),
          h('td', { class: 'num secondary' }, isMember ? '' : gid),
          h('td', { class: isMember ? 'indent' : '' },
            !isMember && others.length
              ? h('span', {
                class: 'caret', role: 'button', tabindex: '0', 'aria-expanded': String(!!open),
                title: open ? 'Hide alternatives' : 'Show alternatives',
                onclick: () => { if (expanded.has(gid)) expanded.delete(gid); else expanded.add(gid); render(); },
                onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.target.click(); } },
              }, open ? '▾' : '▸')
              : h('span', { class: 'caret' }),
            h('span', {}, r.species || r.title),
            !isMember && others.length ? h('span', { class: 'muted small' }, ` +${others.length}`) : null,
            h('div', { class: 'help' }, h('span', { class: 'mono' }, r.ref), r.species && r.title !== r.species ? ` · ${r.title}` : '')),
          h('td', { class: 'num' }, fmt.int(r.score)),
          h('td', { class: 'num' }, fmt.int(r.queries)),
          h('td', { class: 'num' }, fmt.pct(r.ref_cov), meter(r.ref_cov)),
          h('td', { class: 'num' }, fmt.pctValue(r.identity)),
          h('td', { class: 'num' }, r.depth == null ? '–' : fmt.num(r.depth, 1)),
          h('td', { class: 'num secondary' }, fmt.bp(r.length)),
          h('td', { class: 'flags-cell' }, r.weak ? flagBadge('weak') : null,
            r.auto_selected ? h('span', { class: 'flag info', title: 'Part of the automatic selection' }, h('span', { class: 'icon' }, '★'), 'auto') : null));
      };
      body.push(line(leader, false));
      if (open) for (const m of others) if (matches(m) || !filter) body.push(line(m, true));
    }
    if (!body.length) return h('div', { class: 'empty' }, rows.length ? 'No hits match the search.' : 'BLAST found no hits.');
    return h('div', { class: 'table-wrap' }, h('table', { class: 'data' },
      h('thead', {}, h('tr', {},
        h('th', {}, ''), h('th', { class: 'num' }, 'Group'), h('th', {}, 'Reference'),
        h('th', { class: 'num', title: 'Non-redundant bitscore along the reference' }, 'Score'),
        h('th', { class: 'num', title: 'Supporting contigs or reads' }, 'Queries'),
        h('th', { class: 'num', title: 'Part of the reference covered by the supporting alignments' }, 'Covered'),
        h('th', { class: 'num' }, 'Identity'),
        h('th', { class: 'num', title: 'Summed depth of the supporting contigs' }, 'Contig depth'),
        h('th', { class: 'num' }, 'Length'), h('th', {}, ''))),
      h('tbody', {}, body)));
  }

  function render() {
    const can = editable();
    const rank = job.stages.find((s) => s.name === 'rank');
    const select = job.stages.find((s) => s.name === 'select');
    const waiting = job.status === 'paused' && job.pause_reason === 'select';
    const notice = [];
    if (rank.state !== 'done') notice.push(callout('info', 'No ranking yet.', 'The hits appear here when the BLAST and ranking stages are done.'));
    else if (select.state === 'done') {
      notice.push(callout('info', 'The selection is done and the job has moved on.',
        h('div', { class: 'row', style: { marginTop: '6px' } },
          h('span', { class: 'help' }, 'To change it, reset the job to “select”; mapping and the report are redone.'),
          h('button', { class: 'btn small', type: 'button', disabled: job.status === 'running',
            onclick: async () => { if (await resetJob(job, 'select', stageNames)) load(); } }, 'Reset to select'))));
    } else if (job.status === 'running') notice.push(callout('info', 'The job is running.', 'The selection can be changed when it is not running.'));
    else if (waiting) notice.push(callout('info', 'The job waits for your selection.', 'The automatic choice is preselected. Change it if needed, then save and continue.'));

    const search = h('input', { type: 'text', placeholder: 'Search name or accession', value: filter, style: { maxWidth: '280px' } });
    search.addEventListener('input', () => {
      filter = search.value.trim().toLowerCase();
      const pos = search.selectionStart;
      render();
      const again = root.querySelector('input[type="text"]');
      if (again) { again.focus(); again.setSelectionRange(pos, pos); }
    });
    const summary = rank.summary || {};
    root.replaceChildren(
      h('div', { class: 'crumbs' }, h('a', { href: '#/' }, 'Jobs'), ' / ', h('a', { href: `#/job/${id}` }, `#${id} ${job.name}`), ' / hits'),
      h('div', { class: 'page-head' }, h('h1', {}, 'Hits'),
        h('span', { class: 'secondary small' }, rank.state === 'done'
          ? `${fmt.int(summary.references_hit)} references hit · ${fmt.int(summary.references_supported)} supported · ${fmt.int(summary.groups)} groups`
          : '')),
      h('div', { class: 'stack' }, ...notice,
        h('section', { class: 'card' },
          h('div', { class: 'card-head' },
            h('strong', {}, `${selected.size} selected`),
            dirty() ? h('span', { class: 'badge' }, h('span', { class: 'dot warning' }), 'unsaved') : null,
            h('span', { class: 'grow' }),
            search,
            h('button', { class: 'btn small', type: 'button', onclick: () => { expanded = new Set(rows.map((r) => r.group)); render(); } }, 'Expand all'),
            h('button', { class: 'btn small', type: 'button', onclick: () => { expanded = new Set(); render(); } }, 'Collapse all'),
            can ? h('button', { class: 'btn small', type: 'button', onclick: () => { selected = new Set(rows.filter((r) => r.auto_selected).map((r) => r.ref)); render(); } }, 'Automatic selection') : null,
            can ? h('button', { class: 'btn small', type: 'button', onclick: () => { selected = new Set(); render(); } }, 'Clear') : null),
          table(),
          can ? h('div', { class: 'row', style: { marginTop: '14px' } },
            waiting ? h('button', { class: 'btn primary', type: 'button', onclick: () => save(true) }, 'Save and continue') : null,
            h('button', { class: `btn${waiting ? '' : ' primary'}`, type: 'button', disabled: !dirty(), onclick: () => save(false) }, 'Save selection'),
            selected.size === 0 ? h('span', { class: 'help' }, 'Nothing selected: mapping is skipped and the report is empty.') : null) : null)));
  }

  load().catch((err) => root.replaceChildren(callout('error', 'Could not load the hits', err.message)));
  const beforeUnload = (e) => { if (job && dirty()) { e.preventDefault(); e.returnValue = ''; } };
  window.addEventListener('beforeunload', beforeUnload);
  return {
    dispose() {
      disposed = true;
      window.removeEventListener('beforeunload', beforeUnload);
      if (job && dirty()) toast('Unsaved hit selection was discarded', 'warn');
    },
  };
}
