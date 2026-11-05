// Thin client for the readscreen JSON API.

export class ApiError extends Error {
  constructor(message, code, status) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

async function request(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let resp;
  try {
    resp = await fetch(path, opts);
  } catch {
    throw new ApiError('The readscreen server is not reachable.', 'network', 0);
  }
  const text = await resp.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!resp.ok) {
    const message = (data && data.error) || `${resp.status} ${resp.statusText}`;
    throw new ApiError(message, data && data.code, resp.status);
  }
  return data;
}

const q = encodeURIComponent;

export const api = {
  info: () => request('GET', '/api/info'),
  stages: (platform) => request('GET', `/api/stages?platform=${q(platform || '')}`),
  dbs: (refresh = false) => request('GET', '/api/dbs' + (refresh ? '?refresh=1' : '')),
  settings: () => request('GET', '/api/settings'),
  settingsSchema: () => request('GET', '/api/settings/schema'),
  saveSettings: (values) => request('PUT', '/api/settings', values),

  jobs: () => request('GET', '/api/jobs'),
  job: (id) => request('GET', `/api/jobs/${id}`),
  createJob: (body) => request('POST', '/api/jobs', body),
  editJob: (id, body) => request('PATCH', `/api/jobs/${id}`, body),
  deleteJob: (id) => request('DELETE', `/api/jobs/${id}`),
  queue: (id) => request('POST', `/api/jobs/${id}/queue`),
  cont: (id) => request('POST', `/api/jobs/${id}/continue`),
  cancel: (id) => request('POST', `/api/jobs/${id}/cancel`),
  reset: (id, stage, queue = false) => request('POST', `/api/jobs/${id}/reset`, { stage, queue }),
  setParams: (id, stage, updates, reset = false) => request('PUT', `/api/jobs/${id}/params/${stage}`, { updates, reset }),
  setPause: (id, stage, on) => request('PUT', `/api/jobs/${id}/pause/${stage}`, { on }),
  reassign: (id, body) => request('POST', `/api/jobs/${id}/reassign`, body),
  log: (id, stage, lines = 2000) => request('GET', `/api/jobs/${id}/log/${stage}?lines=${lines}`),

  ranking: (id) => request('GET', `/api/jobs/${id}/ranking`),
  setSelection: (id, refs, cont = false) => request('PUT', `/api/jobs/${id}/selection`, { refs, continue: cont }),
  results: (id) => request('GET', `/api/jobs/${id}/results`),
  result: (id, ref) => request('GET', `/api/jobs/${id}/results/${q(ref)}`),
  exportUrl: (id, format) => `/api/jobs/${id}/export?format=${q(format)}`,

  worker: () => request('GET', '/api/worker'),
  startWorker: () => request('POST', '/api/worker/start'),
  stopWorker: (now = false) => request('POST', '/api/worker/stop', { now }),

  fsList: (path, hidden = false) => request('GET', `/api/fs/list?path=${q(path || '')}${hidden ? '&hidden=1' : ''}`),
  fsSuggest: (path) => request('GET', `/api/fs/suggest?path=${q(path)}`),
};
