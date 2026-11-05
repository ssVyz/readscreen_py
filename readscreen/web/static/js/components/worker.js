// Worker status card with start/stop controls.

import { api } from '../api.js';
import { act, confirmDialog, fmt, h, live } from '../ui.js';

export function workerPanel(onChange) {
  const el = h('section', { class: 'card', 'aria-label': 'Worker' });

  function render(st) {
    const actions = h('div', { class: 'row' });
    let status;
    if (!st.running) {
      status = h('div', {},
        h('div', { class: 'row' }, h('span', { class: 'dot hollow' }), h('strong', {}, 'Worker not running')),
        h('div', { class: 'help' }, 'Queued jobs wait until the worker runs. It keeps running when you close this page.'));
      actions.append(h('button', {
        class: 'btn primary', type: 'button',
        onclick: async () => { await act(() => api.startWorker(), 'Worker started'); refresh(); },
      }, 'Start worker'));
    } else {
      const job = st.job_id
        ? h('span', {}, ' · job ', h('a', { href: `#/job/${st.job_id}` }, `#${st.job_id}`), st.stage ? ` · stage ${st.stage}` : '')
        : ' · idle';
      const stopping = st.stop_request
        ? h('div', { class: 'help' }, st.stop_request === 'now' ? 'Stopping now…' : 'Stopping after the current stage…')
        : null;
      status = h('div', {},
        h('div', { class: 'row' }, h('span', { class: 'dot accent pulse' }), h('strong', {}, 'Worker running'),
          h('span', { class: 'secondary small' }, `pid ${st.pid} · since ${fmt.time(st.started)}`, job)),
        stopping);
      actions.append(
        h('button', {
          class: 'btn', type: 'button', disabled: !!st.stop_request,
          title: 'Finish the current stage, then stop',
          onclick: async () => { await act(() => api.stopWorker(false), 'Worker will stop after the current stage'); refresh(); },
        }, 'Stop'),
        h('button', {
          class: 'btn danger', type: 'button', disabled: st.stop_request === 'now',
          title: 'Kill the running tool now; the stage restarts later',
          onclick: async () => {
            const ok = await confirmDialog({
              title: 'Stop the worker now?',
              message: 'The running tool is killed and the partial output of the current stage is discarded. The job goes back to the queue at the start of that stage.',
              confirmLabel: 'Stop now', danger: true,
            });
            if (ok) {
              await act(() => api.stopWorker(true), 'Worker stopping');
              refresh();
            }
          },
        }, 'Stop now'));
    }
    el.replaceChildren(h('div', { class: 'row spread' }, status, actions));
    if (onChange) onChange(st);
  }

  const handle = live(el, { load: api.worker, render, interval: 3000 });
  const refresh = () => handle.refresh();
  return { el, dispose: handle.dispose, refresh };
}
