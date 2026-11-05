// readscreen single-page app: hash router and the header worker indicator.

import { api } from './api.js';
import { h } from './ui.js';
import { createView } from './views/create.js';
import { hitsView } from './views/hits.js';
import { jobView } from './views/job.js';
import { queueView } from './views/queue.js';
import { resultDetailView, resultsView } from './views/results.js';
import { settingsView } from './views/settings.js';

const ROUTES = [
  [/^#?\/?$/, queueView, 'jobs'],
  [/^#\/new$/, createView, 'new'],
  [/^#\/job\/(\d+)$/, jobView, 'jobs'],
  [/^#\/job\/(\d+)\/hits$/, hitsView, 'jobs'],
  [/^#\/job\/(\d+)\/results$/, resultsView, 'jobs'],
  [/^#\/job\/(\d+)\/results\/(.+)$/, resultDetailView, 'jobs'],
  [/^#\/settings$/, settingsView, 'settings'],
];

const main = document.getElementById('view');
let current = null;

function navigate() {
  if (current && current.dispose) current.dispose();
  current = null;
  const hash = location.hash || '#/';
  for (const [re, view, nav] of ROUTES) {
    const m = hash.match(re);
    if (!m) continue;
    document.querySelectorAll('[data-nav]').forEach((a) => a.classList.toggle('active', a.dataset.nav === nav));
    main.replaceChildren();
    current = view(main, ...m.slice(1).map(decodeURIComponent)) || null;
    window.scrollTo(0, 0);
    return;
  }
  main.replaceChildren(h('div', { class: 'empty' }, 'Page not found. ', h('a', { href: '#/' }, 'Back to the jobs')));
}

async function updatePill() {
  const pill = document.getElementById('worker-pill');
  const dot = pill.querySelector('.dot');
  const label = pill.querySelector('.label');
  try {
    const st = await api.worker();
    dot.className = st.running ? 'dot accent pulse' : 'dot hollow';
    label.textContent = st.running ? (st.job_id ? `Worker: job #${st.job_id}` : 'Worker idle') : 'Worker stopped';
    pill.title = st.running ? `Worker running (pid ${st.pid})` : 'Worker not running';
  } catch {
    dot.className = 'dot critical';
    label.textContent = 'Server unreachable';
    pill.title = 'The readscreen server does not answer';
  }
}

window.addEventListener('hashchange', navigate);
navigate();
updatePill();
setInterval(() => {
  if (!document.hidden) updatePill();
}, 4000);
