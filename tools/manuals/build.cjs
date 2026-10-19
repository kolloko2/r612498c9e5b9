// Собирает руководства из docs/manuals/*.md в HTML и PDF (output/manuals).
// Разметка — упрощённый Markdown и несколько своих команд:
//   !fig имя | Подпись | 1: что это; 2: ...     — скриншот с указателями
//   !note Заголовок | текст                       — выделенный абзац
//   :значок:                                       — значок в строке (телефон, бригада...)
//   ## Раздел {#якорь}                             — раздел с якорем для ссылок
// Запуск: node tools/manuals/build.cjs
const fs = require('fs');
const path = require('path');
const {chromium} = require('playwright');

const ROOT = path.join(__dirname, '..', '..');
const SRC = path.join(ROOT, 'docs', 'manuals');
const SCREENS = path.join(SRC, 'screens');
const OUT = path.join(ROOT, 'output', 'manuals');
const marks = JSON.parse(fs.readFileSync(path.join(SCREENS, 'marks.json'), 'utf8'));

// Простые линейные значки 24×24, один стиль во всех документах.
const ICON_PATHS = {
  phone: '<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/>',
  crew: '<path d="M3 17h2m14 0h2M3 17V8h11v9M14 11h4l3 3v3"/><circle cx="7.5" cy="17.5" r="1.8"/><circle cx="17.5" cy="17.5" r="1.8"/>',
  timer: '<circle cx="12" cy="13" r="8"/><path d="M12 9v4l3 2M9 2h6"/>',
  ai: '<path d="M12 3l1.8 4.6L18 9.4l-4.2 1.8L12 16l-1.8-4.8L6 9.4l4.2-1.8z"/><path d="M19 15l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z"/>',
  card: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9h10M7 13h10M7 17h6"/>',
  headset: '<path d="M4 15v-3a8 8 0 0 1 16 0v3"/><rect x="3" y="14" width="4" height="6" rx="1.5"/><rect x="17" y="14" width="4" height="6" rx="1.5"/><path d="M20 20c0 1-1.5 2-4 2h-2"/>',
  check: '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.7 2.7L16 10"/>',
  alert: '<path d="M12 3l9.5 17h-19z"/><path d="M12 10v4M12 17.2v.3"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 13.5a6.5 6.5 0 0 1 4 6.5"/>',
  shield: '<path d="M12 3l8 3v6c0 4.5-3.4 8.2-8 9-4.6-.8-8-4.5-8-9V6z"/><path d="M8.5 12l2.5 2.5 4.5-5"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1L7 17M17 7l2.1-2.1"/>',
  disk: '<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
  book: '<path d="M4 4h6a3 3 0 0 1 3 3v13a2.5 2.5 0 0 0-2.5-2.5H4zM20 4h-6a3 3 0 0 0-3 3"/><path d="M20 4v13.5h-6.5A2.5 2.5 0 0 0 11 20"/>',
  login: '<path d="M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4M10 16l4-4-4-4M14 12H3"/>',
  chart: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  report: '<path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 13h7M9 17h5"/>',
  hint: '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2V16h5v-.1c0-.8.4-1.5 1-2A6 6 0 0 0 12 3z"/>',
  server: '<rect x="3" y="4" width="18" height="7" rx="1.5"/><rect x="3" y="13" width="18" height="7" rx="1.5"/><path d="M7 7.5h.01M7 16.5h.01"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  wrench: '<path d="M14.5 6.5a4 4 0 0 0 5 5L21 13l-8 8-3.5-3.5 8-8zM3 21l6-6"/><path d="M14.5 6.5L17 4l3 3-2.5 2.5"/>',
};
const icon = (name, cls = 'i') => ICON_PATHS[name]
  ? `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON_PATHS[name]}</svg>`
  : `:${name}:`;

const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
function inline(text) {
  return esc(text)
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/«([^»]+)»/g, '«$1»')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, (m, t, href) => `<a href="${href}">${t}</a>`)
    .replace(/:([a-z]+):/g, (m, n) => ICON_PATHS[n] ? icon(n) : m);
}

function figure(spec) {
  const [name, caption = '', legend = ''] = spec.split('|').map(s => s.trim());
  const file = path.join(SCREENS, name + '.png');
  if (!fs.existsSync(file)) throw Error('нет скриншота ' + name);
  const data = fs.readFileSync(file).toString('base64');
  const m = marks[name] || {w: 1440, h: 900, marks: []};
  const items = legend ? legend.split(';').map(s => s.trim()).filter(Boolean).map(s => s.split(/:(.*)/s)) : [];
  const shown = new Set(items.map(([n]) => n.trim()));
  const pins = m.marks.filter(p => shown.has(p.n)).map(p => {
    // Метка у левого верхнего угла элемента, рамка вокруг него.
    const l = (p.x / m.w * 100).toFixed(2), t = (p.y / m.h * 100).toFixed(2);
    const w = (p.w / m.w * 100).toFixed(2), h = (p.h / m.h * 100).toFixed(2);
    return `<span class="frame" style="left:${l}%;top:${t}%;width:${w}%;height:${h}%"></span>` +
           `<span class="pin" style="left:${l}%;top:${t}%">${p.n}</span>`;
  }).join('');
  const list = items.length ? `<ol class="legend">${items.map(([n, t]) => `<li><span class="num">${esc(n.trim())}</span>${inline((t || '').trim())}</li>`).join('')}</ol>` : '';
  return `<figure><div class="shot"><img src="data:image/png;base64,${data}" alt="">${pins}</div>` +
         (caption ? `<figcaption>${inline(caption)}</figcaption>` : '') + list + '</figure>';
}

function convert(md) {
  const lines = md.replace(/\r/g, '').split('\n');
  const meta = {};
  while (lines.length && /^[a-z]+:/.test(lines[0])) {
    const [k, ...v] = lines.shift().split(':');
    meta[k] = v.join(':').trim();
  }
  const html = [], toc = [];
  let list = null, para = [], table = null;
  const flush = () => {
    if (para.length) { html.push(`<p>${inline(para.join(' '))}</p>`); para = []; }
    if (list) { html.push(`<${list.tag}>${list.items.map(i => `<li>${inline(i)}</li>`).join('')}</${list.tag}>`); list = null; }
    if (table) {
      const [head, ...rows] = table.filter(r => !/^\s*\|?\s*-{3}/.test(r)).map(r => r.replace(/^\||\|$/g, '').split('|').map(c => c.trim()));
      html.push(`<table><thead><tr>${head.map(c => `<th>${inline(c)}</th>`).join('')}</tr></thead><tbody>${rows.map(r => `<tr>${r.map(c => `<td>${inline(c)}</td>`).join('')}</tr>`).join('')}</tbody></table>`);
      table = null;
    }
  };
  let section = 0;
  for (const raw of lines) {
    const line = raw.trimEnd();
    let m;
    if (!line.trim()) { flush(); continue; }
    if ((m = line.match(/^(#{2,3}) (.+?)(?: \{#([\w-]+)\})?$/))) {
      flush();
      const level = m[1].length, id = m[3] || 's' + (++section);
      if (level === 2) toc.push({id, title: m[2]});
      html.push(`<h${level} id="${id}">${inline(m[2])}</h${level}>`);
    } else if ((m = line.match(/^!fig (.+)$/))) { flush(); html.push(figure(m[1])); }
    else if ((m = line.match(/^!note (.+?)\s*\|\s*(.+)$/))) {
      flush();
      const kind = /важно|внимание/i.test(m[1]) ? 'warn' : /ии/i.test(m[1]) ? 'ai' : 'info';
      const ic = kind === 'warn' ? 'alert' : kind === 'ai' ? 'ai' : 'hint';
      html.push(`<aside class="note ${kind}">${icon(ic, 'ni')}<div><b>${inline(m[1])}</b><p>${inline(m[2])}</p></div></aside>`);
    } else if ((m = line.match(/^!tiles (.+)$/))) {
      flush();
      html.push('<div class="tiles">' + m[1].split(';;').map(t => {
        const [ic, title, text] = t.split('|').map(s => s.trim());
        return `<div class="tile">${icon(ic, 'ti')}<b>${inline(title)}</b><span>${inline(text || '')}</span></div>`;
      }).join('') + '</div>');
    } else if (line.startsWith('|')) { if (!table) { flush(); table = []; } table.push(line); }
    else if ((m = line.match(/^(\d+)\. (.+)$/)) || (m = line.match(/^- (.+)$/))) {
      const tag = /^\d/.test(line) ? 'ol' : 'ul', text = m[2] || m[1];
      if (para.length) flush();
      if (!list || list.tag !== tag) { if (list) flush(); list = {tag, items: []}; }
      list.items.push(text);
    } else if (list && /^\s{2,}/.test(raw)) { list.items[list.items.length - 1] += ' ' + line.trim(); }
    else { if (list) flush(); para.push(line.trim()); }
  }
  flush();
  return {meta, html: html.join('\n'), toc};
}

const CSS = fs.readFileSync(path.join(__dirname, 'manual.css'), 'utf8');
function page({meta, html, toc}) {
  const tocHtml = toc.length > 2 ? `<nav class="toc"><h2>Содержание</h2><ol>${toc.map(t => `<li><a href="#${t.id}">${inline(t.title)}</a></li>`).join('')}</ol></nav>` : '';
  return `<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>${esc(meta.title || '')}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=Golos+Text:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>${CSS}</style></head><body class="${esc(meta.flow || '')}">
<section class="cover"><div class="brand">NKDL</div><div class="cover-body">${icon(meta.icon || 'book', 'cover-icon')}
<p class="kicker">${esc(meta.kicker || 'Тренажёр диспетчера ДДС')}</p><h1>${esc(meta.title || '')}</h1><p class="lead">${esc(meta.lead || '')}</p></div>
<div class="cover-foot"><span>${esc(meta.audience || '')}</span><span>${esc(meta.date || 'Октябрь 2026')}</span></div></section>
${tocHtml}<main>${html}</main></body></html>`;
}

(async () => {
  fs.mkdirSync(OUT, {recursive: true});
  const files = fs.readdirSync(SRC).filter(f => /^\d\d_.+\.md$/.test(f) && (!process.argv[2] || f.includes(process.argv[2])));
  const browser = await chromium.launch();
  for (const file of files) {
    const doc = convert(fs.readFileSync(path.join(SRC, file), 'utf8'));
    const base = file.replace(/\.md$/, '');
    const htmlPath = path.join(OUT, base + '.html');
    fs.writeFileSync(htmlPath, page(doc));
    const pg = await browser.newPage();
    await pg.goto('file:///' + htmlPath.replace(/\\/g, '/'), {waitUntil: 'networkidle'});
    await pg.evaluate(() => document.fonts.ready);
    const pdfName = (doc.meta.file || base) + '.pdf';
    await pg.pdf({path: path.join(OUT, pdfName), format: 'A4', printBackground: true,
      margin: {top: '16mm', bottom: '16mm', left: '0', right: '0'}, displayHeaderFooter: true,
      headerTemplate: `<div style="width:100%;font:8px Arial;color:#8a96a0;padding:0 18mm;display:flex;justify-content:space-between"><span>NKDL · Тренажёр диспетчера ДДС</span><span>${esc(doc.meta.title || '')}</span></div>`,
      footerTemplate: '<div style="width:100%;font:8px Arial;color:#8a96a0;text-align:center"><span class="pageNumber"></span></div>'});
    await pg.close();
    console.log('собран', pdfName);
  }
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
