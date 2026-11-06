// Coverage plot: mean depth per bin along a reference (one series), with crosshair tooltip.

import { fmt, h, s } from '../ui.js';

const HEIGHT = 250;
const M = { l: 54, r: 18, t: 14, b: 30 };

function niceStep(range, count) {
  const raw = range / Math.max(1, count);
  const mag = 10 ** Math.floor(Math.log10(raw || 1));
  const norm = raw / mag;
  return (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
}

function linearTicks(max, count) {
  const step = niceStep(max, count);
  const top = Math.max(step, Math.ceil(max / step) * step);
  const ticks = [];
  for (let v = 0; v <= top + step / 2; v += step) ticks.push(Math.round(v * 1e6) / 1e6);
  return ticks;
}

function depthLabel(v) {
  if (v >= 10000) return `${(v / 1000).toFixed(0)}K`;
  if (v >= 1000) return v.toLocaleString('en-US');
  return String(v);
}

function posLabel(bp, step) {
  if (step >= 1e6) return `${(bp / 1e6).toFixed(0)} Mb`;
  if (step >= 1e5 && bp % 1e6 !== 0) return `${(bp / 1e6).toFixed(1)} Mb`;
  if (step >= 1e3) return `${(bp / 1e3).toFixed(0)} kb`;
  return `${bp.toLocaleString('en-US')}`;
}

// data: {length, bins: {count, mean[], max[]}, minDepth}
export function coveragePlot({ length, bins, minDepth = 1, title }) {
  const el = h('div', { class: 'plot' });
  const tip = h('div', { class: 'tooltip', hidden: true });
  let scale = 'linear';
  let width = 0;
  let svg = null;
  let active = -1;
  const n = bins.count;
  const start = (i) => Math.floor((length * i) / n);
  const end = (i) => Math.floor((length * (i + 1)) / n);
  let peak = 0;
  for (const v of bins.mean) if (v > peak) peak = v;

  function render() {
    width = Math.max(320, el.clientWidth || 800);
    const pw = width - M.l - M.r;
    const ph = HEIGHT - M.t - M.b;
    const top = Math.max(peak, minDepth, 1);
    let yTicks;
    let yMap;
    if (scale === 'log') {
      const decades = Math.max(1, Math.ceil(Math.log10(top)));
      yTicks = [0];
      for (let k = 0; k <= decades; k++) yTicks.push(10 ** k);
      const f = (v) => Math.log10(1 + v);
      const fmax = f(yTicks[yTicks.length - 1]);
      yMap = (v) => M.t + ph - (f(v) / fmax) * ph;
    } else {
      yTicks = linearTicks(top, 4);
      const ymax = yTicks[yTicks.length - 1];
      yMap = (v) => M.t + ph - (v / ymax) * ph;
    }
    const x = (bp) => M.l + (bp / length) * pw;

    // stepped area and line through the bin means
    let line = '';
    for (let i = 0; i < n; i++) {
      const y = yMap(bins.mean[i]).toFixed(1);
      line += `${i ? 'L' : 'M'}${x(start(i)).toFixed(1)},${y}L${x(end(i)).toFixed(1)},${y}`;
    }
    const base = yMap(0).toFixed(1);
    const area = `${line}L${x(length).toFixed(1)},${base}L${x(0).toFixed(1)},${base}Z`;

    const xStep = niceStep(length, Math.max(2, Math.floor(pw / 110)));
    const xTicks = [];
    for (let v = 0; v <= length; v += xStep) xTicks.push(v);

    svg = s('svg', {
      viewBox: `0 0 ${width} ${HEIGHT}`, height: HEIGHT, role: 'img', tabindex: '0',
      'aria-label': `${title || 'Coverage'}: mean depth per bin along ${fmt.bp(length)}; maximum bin mean ${fmt.num(peak, 1)}. Use the arrow keys to read values.`,
    },
    yTicks.map((t) => s('line', { class: t === 0 ? 'axis' : 'grid', x1: M.l, x2: width - M.r, y1: yMap(t), y2: yMap(t) })),
    yTicks.map((t) => s('text', { class: 'tick', x: M.l - 8, y: yMap(t) + 4, 'text-anchor': 'end' }, depthLabel(t))),
    xTicks.map((t) => s('text', { class: 'tick', x: x(t), y: HEIGHT - 10, 'text-anchor': t === 0 ? 'start' : 'middle' }, posLabel(t, xStep))),
    s('path', { class: 'area', d: area }),
    s('path', { class: 'line', d: line }),
    minDepth > 1
      ? [s('line', { class: 'ref', x1: M.l, x2: width - M.r, y1: yMap(minDepth), y2: yMap(minDepth) }),
        s('text', { class: 'ref-label', x: width - M.r, y: yMap(minDepth) - 4, 'text-anchor': 'end' }, `min depth ${minDepth}`)]
      : null,
    s('line', { class: 'cross', y1: M.t, y2: M.t + ph, x1: 0, x2: 0, visibility: 'hidden' }),
    s('circle', { class: 'marker', r: 4, cx: 0, cy: 0, visibility: 'hidden' }),
    s('rect', { class: 'hit', x: M.l, y: M.t, width: pw, height: ph }));

    const hit = svg.querySelector('.hit');
    hit.addEventListener('pointermove', (e) => {
      const r = svg.getBoundingClientRect();
      const px = ((e.clientX - r.left) / r.width) * width;
      show(Math.floor(((px - M.l) / pw) * n));
    });
    hit.addEventListener('pointerleave', hide);
    svg.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        e.preventDefault();
        const stepSize = e.shiftKey ? Math.max(1, Math.round(n / 20)) : 1;
        show(active < 0 ? 0 : active + (e.key === 'ArrowRight' ? stepSize : -stepSize));
      } else if (e.key === 'Escape') hide();
    });
    svg.addEventListener('blur', hide);
    svg._map = { x, yMap, pw };
    el.replaceChildren(svg, tip);
  }

  function show(i) {
    if (!svg) return;
    active = Math.max(0, Math.min(n - 1, i));
    const { x, yMap } = svg._map;
    const cx = (x(start(active)) + x(end(active))) / 2;
    const cy = yMap(bins.mean[active]);
    const cross = svg.querySelector('.cross');
    cross.setAttribute('x1', cx);
    cross.setAttribute('x2', cx);
    cross.setAttribute('visibility', 'visible');
    const marker = svg.querySelector('.marker');
    marker.setAttribute('cx', cx);
    marker.setAttribute('cy', cy);
    marker.setAttribute('visibility', 'visible');
    const span = end(active) - start(active);
    tip.replaceChildren(
      h('div', {}, h('span', { class: 'key' }), h('span', { class: 'v' }, `${fmt.num(bins.mean[active], 1)}×`), ' ',
        h('span', { class: 'k' }, 'mean depth')),
      h('div', { class: 'k' }, `max ${fmt.int(bins.max[active])}× in this bin`),
      h('div', { class: 'k' }, span > 1 ? `${fmt.int(start(active) + 1)}–${fmt.int(end(active))} bp` : `${fmt.int(start(active) + 1)} bp`));
    tip.hidden = false;
    const scaleX = el.clientWidth / width;
    const left = cx * scaleX;
    const tipW = tip.offsetWidth || 160;
    tip.style.left = `${left + 12 + tipW > el.clientWidth ? left - tipW - 12 : left + 12}px`;
    tip.style.top = `${Math.max(0, cy * (svg.getBoundingClientRect().height / HEIGHT) - 30)}px`;
  }

  function hide() {
    if (!svg) return;
    active = -1;
    tip.hidden = true;
    svg.querySelector('.cross').setAttribute('visibility', 'hidden');
    svg.querySelector('.marker').setAttribute('visibility', 'hidden');
  }

  const ro = new ResizeObserver(() => {
    if (Math.abs((el.clientWidth || 0) - width) > 4) render();
  });
  ro.observe(el);
  requestAnimationFrame(render);

  return {
    el,
    setScale(v) {
      scale = v;
      render();
    },
    dispose: () => ro.disconnect(),
  };
}

// Read quality along the read: one or two line series (R1/R2, or from the read start/end).
// series: [{name, x[], y[] (null = no data)}]; xBin: x values are centres of bins this wide
export function qualityPlot({ series, xLabel, xName = 'position', xBin = null, yLabel = 'mean quality', height = 210 }) {
  const el = h('div', { class: 'plot' });
  const tip = h('div', { class: 'tooltip', hidden: true });
  const legend = series.length > 1
    ? h('div', { class: 'legend' }, series.map((sr, i) => h('span', {},
      h('span', { class: 'swatch', style: { background: `var(--series-${i + 1})` } }), sr.name)))
    : null;
  const wrap = h('div', {}, legend, el);
  const xs = series[0].x;
  const n = xs.length;
  const xmin = Math.min(...series.map((sr) => sr.x[0]));
  const xmax = Math.max(...series.map((sr) => sr.x[sr.x.length - 1]));
  let ymax = 0;
  for (const sr of series) for (const v of sr.y) if (v != null && v > ymax) ymax = v;
  let width = 0;
  let svg = null;
  let active = -1;
  const m = { l: 44, r: 92, t: 10, b: 34 };
  const nearest = (sr, xv) => {
    let best = -1;
    let dist = Infinity;
    sr.x.forEach((v, i) => { if (Math.abs(v - xv) < dist) { dist = Math.abs(v - xv); best = i; } });
    return best;
  };

  function render() {
    width = Math.max(320, el.clientWidth || 700);
    const pw = width - m.l - m.r;
    const ph = height - m.t - m.b;
    // phred scale: ticks every 10 (every 5 for low-quality data), just above the highest value
    const yStep = ymax > 20 ? 10 : 5;
    const ytop = Math.max(yStep, Math.ceil(ymax / yStep) * yStep);
    const yTicks = [];
    for (let v = 0; v <= ytop; v += yStep) yTicks.push(v);
    const x = (v) => m.l + (xmax > xmin ? ((v - xmin) / (xmax - xmin)) * pw : pw / 2);
    const y = (v) => m.t + ph - (v / ytop) * ph;
    const xStep = niceStep(xmax - xmin || 1, Math.max(2, Math.floor(pw / 90)));
    const xTicks = [];
    for (let v = Math.ceil(xmin / xStep) * xStep; v <= xmax; v += xStep) xTicks.push(v);
    const paths = series.map((sr) => {
      let d = '';
      let open = false;
      sr.y.forEach((v, i) => {
        if (v == null) { open = false; return; }
        d += `${open ? 'L' : 'M'}${x(sr.x[i]).toFixed(1)},${y(v).toFixed(1)}`;
        open = true;
      });
      return d;
    });
    // end labels, pushed apart when the lines end close together
    const ends = series.map((sr, i) => {
      let k = sr.y.length - 1;
      while (k > 0 && sr.y[k] == null) k -= 1;
      return { i, ly: y(sr.y[k] ?? 0) };
    }).sort((a, b) => a.ly - b.ly);
    for (let k = 1; k < ends.length; k++) if (ends[k].ly - ends[k - 1].ly < 13) ends[k].ly = ends[k - 1].ly + 13;

    svg = s('svg', {
      viewBox: `0 0 ${width} ${height}`, height, role: 'img', tabindex: '0',
      'aria-label': `${yLabel} by ${xLabel}: ${series.map((sr) => sr.name).join(' and ')}. Use the arrow keys to read values.`,
    },
    yTicks.map((t) => s('line', { class: t === 0 ? 'axis' : 'grid', x1: m.l, x2: m.l + pw, y1: y(t), y2: y(t) })),
    yTicks.map((t) => s('text', { class: 'tick', x: m.l - 8, y: y(t) + 4, 'text-anchor': 'end' }, `Q${t}`)),
    xTicks.map((t) => s('text', { class: 'tick', x: x(t), y: height - 18, 'text-anchor': 'middle' }, t.toLocaleString('en-US'))),
    s('text', { class: 'tick', x: m.l + pw / 2, y: height - 2, 'text-anchor': 'middle' }, xLabel),
    paths.map((d, i) => s('path', { class: `line${i ? ' s2' : ''}`, d })),
    series.length > 1 ? ends.map((e) => s('text', { class: 'series-label', x: m.l + pw + 8, y: e.ly + 4 }, series[e.i].name)) : null,
    s('line', { class: 'cross', y1: m.t, y2: m.t + ph, x1: 0, x2: 0, visibility: 'hidden' }),
    series.map((_, i) => s('circle', { class: `marker${i ? ' s2' : ''}`, r: 4, cx: 0, cy: 0, visibility: 'hidden' })),
    s('rect', { class: 'hit', x: m.l, y: m.t, width: pw, height: ph }));
    svg._map = { x, y, pw };
    const hit = svg.querySelector('.hit');
    hit.addEventListener('pointermove', (e) => {
      const r = svg.getBoundingClientRect();
      const px = ((e.clientX - r.left) / r.width) * width;
      show(nearest(series[0], xmin + ((px - m.l) / pw) * (xmax - xmin)));
    });
    hit.addEventListener('pointerleave', hide);
    svg.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        e.preventDefault();
        show(active < 0 ? 0 : active + (e.key === 'ArrowRight' ? 1 : -1) * (e.shiftKey ? 10 : 1));
      } else if (e.key === 'Escape') hide();
    });
    svg.addEventListener('blur', hide);
    el.replaceChildren(svg, tip);
  }

  function show(i) {
    if (!svg) return;
    active = Math.max(0, Math.min(n - 1, i));
    const { x, y } = svg._map;
    const xv = xs[active];
    const cx = x(xv);
    const cross = svg.querySelector('.cross');
    cross.setAttribute('x1', cx);
    cross.setAttribute('x2', cx);
    cross.setAttribute('visibility', 'visible');
    const markers = svg.querySelectorAll('.marker');
    const lines = [];
    let topY = height;
    series.forEach((sr, k) => {
      const j = nearest(sr, xv);
      const v = sr.y[j];
      if (v == null) {
        markers[k].setAttribute('visibility', 'hidden');
      } else {
        markers[k].setAttribute('cx', x(sr.x[j]));
        markers[k].setAttribute('cy', y(v));
        markers[k].setAttribute('visibility', 'visible');
        topY = Math.min(topY, y(v));
      }
      lines.push(h('div', {}, h('span', { class: `key${k ? ' s2' : ''}` }),
        h('span', { class: 'v' }, v == null ? '–' : `Q${fmt.num(v, 1)}`), ' ', h('span', { class: 'k' }, sr.name)));
    });
    const where = xBin ? `${fmt.int(xv - xBin / 2 + 1)}–${fmt.int(xv + xBin / 2)}` : fmt.int(xv);
    tip.replaceChildren(...lines, h('div', { class: 'k' }, `${xName} ${where}`));
    tip.hidden = false;
    const scaleX = el.clientWidth / width;
    const left = cx * scaleX;
    const tipW = tip.offsetWidth || 150;
    tip.style.left = `${left + 12 + tipW > el.clientWidth ? left - tipW - 12 : left + 12}px`;
    tip.style.top = `${Math.max(0, topY * (svg.getBoundingClientRect().height / height) - 30)}px`;
  }

  function hide() {
    if (!svg) return;
    active = -1;
    tip.hidden = true;
    svg.querySelector('.cross').setAttribute('visibility', 'hidden');
    svg.querySelectorAll('.marker').forEach((mk) => mk.setAttribute('visibility', 'hidden'));
  }

  const ro = new ResizeObserver(() => {
    if (Math.abs((el.clientWidth || 0) - width) > 4) render();
  });
  ro.observe(el);
  requestAnimationFrame(render);
  return { el: wrap, dispose: () => ro.disconnect() };
}

export function qualityTable({ series, xName = 'position', xBin = null }) {
  const at = (xv) => (xBin ? `${fmt.int(xv - xBin / 2 + 1)}–${fmt.int(xv + xBin / 2)}` : fmt.int(xv));
  const rows = series[0].x.map((xv) => h('tr', {}, h('td', { class: 'num' }, at(xv)),
    series.map((sr) => {
      const v = sr.y[sr.x.indexOf(xv)];
      return h('td', { class: 'num' }, v == null ? '–' : fmt.num(v, 1));
    })));
  return h('div', { class: 'table-wrap', style: { maxHeight: '320px', overflow: 'auto' } },
    h('table', { class: 'data' },
      h('thead', {}, h('tr', {}, h('th', { class: 'num' }, xName), series.map((sr) => h('th', { class: 'num' }, sr.name)))),
      h('tbody', {}, rows)));
}

// The same numbers as a table (accessibility / exact values).
export function binsTable({ length, bins }) {
  const n = bins.count;
  const rows = [];
  for (let i = 0; i < n; i++) {
    const a = Math.floor((length * i) / n) + 1;
    const b = Math.floor((length * (i + 1)) / n);
    rows.push(h('tr', {}, h('td', { class: 'num' }, `${fmt.int(a)}–${fmt.int(b)}`),
      h('td', { class: 'num' }, fmt.num(bins.mean[i], 2)), h('td', { class: 'num' }, fmt.int(bins.max[i]))));
  }
  return h('div', { class: 'table-wrap', style: { maxHeight: '360px', overflow: 'auto' } },
    h('table', { class: 'data' },
      h('thead', {}, h('tr', {}, h('th', { class: 'num' }, 'Position (bp)'), h('th', { class: 'num' }, 'Mean depth'), h('th', { class: 'num' }, 'Max depth'))),
      h('tbody', {}, rows)));
}
