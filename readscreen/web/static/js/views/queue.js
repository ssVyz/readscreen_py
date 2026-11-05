// Jobs page: worker controls and the job queue.

import { api } from '../api.js';
import { workerPanel } from '../components/worker.js';
import { fmt, h, live, pauseText, statusBadge } from '../ui.js';
import { cancelJob, continueJob, deleteJob, queueJob } from './actions.js';

export function queueView(main) {
  let stageNames = [];
  const worker = workerPanel();
  const listCard = h('section', { class: 'card', 'aria-label': 'Jobs' });
  main.append(
    h('div', { class: 'page-head' }, h('h1', {}, 'Jobs'), h('a', { class: 'btn primary', href: '#/new' }, '+ New job')),
    h('div', { class: 'stack' }, worker.el, listCard));

  const refreshSoon = () => setTimeout(() => list.refresh(), 150);

  function actionsFor(job) {
    const out = [];
    const btn = (label, fn, cls = '') => h('button', {
      class: `btn small ${cls}`, type: 'button',
      onclick: async (e) => {
        e.stopPropagation();
        if (await fn(job)) refreshSoon();
      },
    }, label);
    if (job.status === 'paused' && job.pause_reason === 'select') {
      out.push(h('a', { class: 'btn small primary', href: `#/job/${job.id}/hits`, onclick: (e) => e.stopPropagation() }, 'Choose hits'));
    } else if (job.status === 'paused') out.push(btn('Continue', continueJob, 'primary'));
    if (job.status === 'stopped' || job.status === 'error') out.push(btn('Queue', queueJob));
    if (['queued', 'running', 'paused'].includes(job.status)) out.push(btn('Cancel', cancelJob));
    out.push(btn('Delete', deleteJob, 'ghost'));
    return h('div', { class: 'row', style: { justifyContent: 'flex-end' } }, out);
  }

  function render(jobs) {
    if (!jobs.length) {
      listCard.replaceChildren(h('div', { class: 'empty' }, 'No jobs yet. ', h('a', { href: '#/new' }, 'Create the first one.')));
      return;
    }
    const rows = [...jobs].reverse().map((j) => {
      const idx = j.current_stage ? stageNames.indexOf(j.current_stage) + 1 : stageNames.length;
      const detail = j.status === 'error' ? (j.error || '').split('\n')[0] : pauseText(j);
      return h('tr', { class: 'clickable', onclick: () => { location.hash = `#/job/${j.id}`; } },
        h('td', { class: 'num secondary' }, `#${j.id}`),
        h('td', {}, h('a', { href: `#/job/${j.id}`, onclick: (e) => e.stopPropagation() }, j.name),
          j.notes ? h('div', { class: 'help' }, j.notes.split('\n')[0].slice(0, 90)) : null),
        h('td', {}, statusBadge(j.status), detail ? h('div', { class: 'help', title: j.error || '' }, detail.slice(0, 110)) : null),
        h('td', { class: 'nowrap' }, j.current_stage ? `${j.current_stage}` : 'complete',
          h('span', { class: 'muted small' }, ` ${Math.min(idx, stageNames.length)}/${stageNames.length}`)),
        h('td', {}, j.platform, h('span', { class: 'muted small' }, j.paired ? ' · paired' : ' · single')),
        h('td', { class: 'nowrap secondary small' }, fmt.ago(j.updated)),
        h('td', {}, actionsFor(j)));
    });
    listCard.replaceChildren(h('div', { class: 'table-wrap' }, h('table', { class: 'data' },
      h('thead', {}, h('tr', {},
        h('th', { class: 'num' }, 'ID'), h('th', {}, 'Name'), h('th', {}, 'Status'), h('th', {}, 'Stage'),
        h('th', {}, 'Platform'), h('th', {}, 'Updated'), h('th', {}))),
      h('tbody', {}, rows))));
  }

  const list = live(listCard, {
    load: async () => {
      if (!stageNames.length) stageNames = (await api.info()).stages;
      return api.jobs();
    },
    render,
    interval: 3000,
  });
  return {
    dispose() {
      list.dispose();
      worker.dispose();
    },
  };
}
