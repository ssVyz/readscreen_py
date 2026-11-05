// Job actions shared by the queue and the job page (with the confirmations they need).

import { api } from '../api.js';
import { act, choose, confirmDialog, h } from '../ui.js';

export async function cancelJob(job) {
  if (job.status === 'running') {
    const ok = await confirmDialog({
      title: `Cancel job #${job.id}?`,
      message: 'The running tool is stopped and the partial output of the current stage is discarded. Earlier stages are kept; you can queue the job again later.',
      confirmLabel: 'Cancel job', danger: true,
    });
    if (!ok) return false;
  }
  const res = await act(() => api.cancel(job.id));
  return res !== undefined;
}

export async function deleteJob(job) {
  const ok = await confirmDialog({
    title: `Delete job #${job.id}?`,
    message: h('div', {},
      h('p', {}, `“${job.name}” and its folder with all intermediate files are deleted. This cannot be undone.`),
      h('p', { class: 'secondary' }, 'Your input files are not touched.'),
      job.status === 'running' ? h('p', { class: 'secondary' }, 'The job is running: it is stopped first.') : null),
    confirmLabel: 'Delete', danger: true,
  });
  if (!ok) return false;
  const res = await act(() => api.deleteJob(job.id), 'Job deleted');
  return res !== undefined;
}

export const queueJob = (job) => act(() => api.queue(job.id), 'Job queued').then((r) => r !== undefined);
export const continueJob = (job) => act(() => api.cont(job.id), 'Job continues').then((r) => r !== undefined);

// Reset to a stage: asks, then resets (and optionally queues). Returns true when done.
export async function resetJob(job, stage, stageNames) {
  const later = stageNames.slice(stageNames.indexOf(stage));
  const choice = await choose({
    title: `Reset job #${job.id} to “${stage}”?`,
    message: h('div', {},
      h('p', {}, 'The results of these stages are discarded:'),
      h('p', { class: 'mono' }, later.join(' → ')),
      h('p', { class: 'secondary' }, 'Earlier stages are kept.')),
    choices: [
      { label: 'Reset', kind: '', value: 'reset' },
      { label: 'Reset and queue', kind: 'primary', value: 'queue' },
    ],
  });
  if (!choice) return false;
  const res = await act(() => api.reset(job.id, stage, choice === 'queue'), choice === 'queue' ? 'Job reset and queued' : 'Job reset');
  return res !== undefined;
}

// Save stage parameters; a finished stage needs a reset, which is asked for.
export async function saveParams(job, stage, updates, stageNames) {
  if (!Object.keys(updates).length) return true;
  try {
    await api.setParams(job.id, stage, updates, false);
    return true;
  } catch (err) {
    if (err.code !== 'conflict') {
      act(() => Promise.reject(err));
      return false;
    }
  }
  const later = stageNames.slice(stageNames.indexOf(stage));
  const choice = await choose({
    title: `Stage “${stage}” is already done`,
    message: h('div', {},
      h('p', {}, 'Changing its parameters discards the results of these stages:'),
      h('p', { class: 'mono' }, later.join(' → '))),
    choices: [
      { label: 'Reset', value: 'reset' },
      { label: 'Reset and queue', kind: 'primary', value: 'queue' },
    ],
  });
  if (!choice) return false;
  const ok = await act(() => api.setParams(job.id, stage, updates, true));
  if (ok === undefined) return false;
  if (choice === 'queue') await act(() => api.queue(job.id), 'Parameters saved, job queued');
  return true;
}
