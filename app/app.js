'use strict';
/* Types text back in the user's own handwriting, using glyph copies cut from their sample sheets.
 *
 * Layout happens once, in PDF points, from a seed (so preview and export match exactly);
 * painting happens at any dpi. Pencil look = real graphite-textured glyphs + a grain layer. */
(() => {
  const $ = (id) => document.getElementById(id);
  const PAPER = { letter: [612, 792], a4: [595, 842] };
  const PEN = {
    pencil: { color: null, dark: 1.0 },
    dark: { color: null, dark: 1.9 },
    blue: { color: '#1d3b9c', dark: 1.0 },
    black: { color: '#121214', dark: 1.0 },
  };
  const NORMALIZE = {
    '’': "'", '‘': "'", '´': "'", '`': "'", '“': '"', '”': '"',
    '«': '"', '»': '"', '–': '-', '—': '-', '−': '-', '…': '...',
    '•': '-', '[': '(', ']': ')', '{': '(', '}': ')', '_': '-', ' ': ' ', '\t': '  ',
  };

  let pack = null;
  let doc = null;

  // ---------------------------------------------------------------- random
  function mulberry32(a) {
    return () => {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function gauss(rng) {
    let u = 0, v = 0;
    while (!u) u = rng();
    while (!v) v = rng();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  }
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const median = (a) => { const s = [...a].sort((x, y) => x - y); return s[Math.floor(s.length / 2)]; };

  // ----------------------------------------------------------------- pack
  function setStatus(msg) { $('status').textContent = msg; }

  async function loadPack() {
    const inline = window.__PACK_INLINE__;
    pack = inline || await (await fetch('pack/pack.json')).json();
    const all = [];
    for (const list of Object.values(pack.glyphs)) for (const g of list) all.push(g);
    let done = 0;
    await Promise.all(all.map((g) => new Promise((resolve, reject) => {
      g.img = new Image();
      g.img.onload = () => { done++; if (done % 40 === 0) setStatus(`Loading your letters… ${done}/${all.length}`); resolve(); };
      g.img.onerror = () => reject(new Error('a glyph image failed to load'));
      g.img.src = inline ? g.data : 'pack/' + g.f;
    })));
  }

  // ---------------------------------------------------------------- layout
  function readOptions() {
    return {
      text: $('text').value,
      paper: $('paper').value,
      size: $('size').value,
      tint: $('tint').value,
      pen: $('pen').value,
      xh: +$('xh').value,
      pitch: +$('pitch').value,
      mess: +$('mess').value,
      dark: +$('dark').value,
      spacing: +$('spacing').value,
      slant: +$('slant').value,
      transparent: $('transparent').checked,
      seed: seed,
    };
  }

  function normalizeText(text) {
    let out = '';
    for (const ch of text) out += NORMALIZE[ch] !== undefined ? NORMALIZE[ch] : ch;
    return out;
  }

  function layoutDoc(o) {
    const rng = mulberry32(o.seed);
    const [PW, PH] = PAPER[o.size];
    const k = o.xh / pack.xh;                       // points per native glyph pixel
    const jscale = o.xh / 8;                        // jitter magnitudes scale with writing size
    const marginL = o.paper === 'lined' ? 82 : 64;
    const marginR = 46;
    const firstY = 64 + o.pitch;
    const perPage = Math.max(1, Math.floor((PH - 48 - firstY) / o.pitch) + 1);
    const maxW = PW - marginL - marginR;
    const letterMed = median(pack.spacing.letter);
    const wordMed = median(pack.spacing.word);
    const lastPick = {};
    const missing = new Set();

    // gaps are sampled from the user's own measurements, but kept apart so a wide letter gap
    // never looks like a word break (and vice versa)
    const LETTER_MAX = 0.42, WORD_MIN = 0.66;
    const sampleGap = (list, med, isWord) => {
      const s = list[Math.floor(rng() * list.length)];
      const g = med + (s - med) * Math.min(o.mess, 1.5);
      return (isWord ? Math.max(g, WORD_MIN) : Math.min(g, LETTER_MAX)) * o.xh * o.spacing;
    };
    const pick = (ch, pool) => {
      if (pool.length === 1) return pool[0];
      const [a, b] = lastPick[ch] || [-1, -1];
      let idx, tries = 0;
      do { idx = Math.floor(rng() * pool.length); tries++; }
      while ((idx === a || (pool.length >= 5 && idx === b)) && tries < 25);
      lastPick[ch] = [idx, a];
      return pool[idx];
    };
    const buildWord = (str) => {
      const glyphs = [];
      let x = 0;
      for (const ch of str) {
        const pool = pack.glyphs[ch];
        if (!pool) { missing.add(ch); continue; }
        const g = pick(ch, pool);
        const wInk = (g.w - 2 * g.pad) * k;
        glyphs.push({ g, x, wInk });
        x += wInk + Math.max(-0.08 * o.xh, sampleGap(pack.spacing.letter, letterMed, false));
      }
      const last = glyphs[glyphs.length - 1];
      return { glyphs, width: last ? last.x + last.wInk : 0 };
    };

    // pass 1: words + wrapping (consumes the rng in text order)
    const lines = [];
    for (const para of normalizeText(o.text).split('\n')) {
      const words = para.split(/ +/).filter(Boolean);
      if (!words.length) { lines.push({ words: [] }); continue; }
      let cur = { words: [], x: 0 };
      for (const w of words) {
        const word = buildWord(w);
        if (!word.glyphs.length) continue;
        const gap = sampleGap(pack.spacing.word, wordMed, true);
        if (cur.words.length && cur.x + gap + word.width > maxW) {
          lines.push(cur);
          cur = { words: [], x: 0 };
        }
        const x = cur.words.length ? cur.x + gap : 0;
        cur.words.push({ word, x });
        cur.x = x + word.width;
      }
      lines.push(cur);
    }

    // pass 2: place glyphs, with per-line wobble (own rng so edits don't reshuffle earlier lines)
    const pages = [];
    lines.forEach((line, li) => {
      const pi = Math.floor(li / perPage), row = li % perPage;
      if (!pages[pi]) pages[pi] = { items: [] };
      const lr = mulberry32((o.seed * 977 + li * 131) >>> 0);
      const lineY = firstY + row * o.pitch;
      const baseY = lineY - (o.paper === 'blank' ? 0 : 0.9 * jscale);
      const slope = gauss(lr) * 0.004 * o.mess;
      const indent = gauss(lr) * 1.5 * o.mess;
      let wordOff = 0, press = 1;
      for (const { word, x } of line.words) {
        wordOff = 0.75 * wordOff + gauss(lr) * 0.35 * jscale * o.mess;
        press = clamp(0.8 * press + 0.2 + gauss(lr) * 0.1 * o.mess, 0.7, 1.15);
        for (const gl of word.glyphs) {
          const cx = marginL + indent + x + gl.x + gl.wInk / 2;
          const sj = 1 + gauss(lr) * 0.025 * o.mess;
          pages[pi].items.push({
            img: gl.g.img,
            w: gl.g.w * k * sj, h: gl.g.h * k * sj, b: gl.g.b * k * sj,
            cx,
            by: baseY + slope * (cx - marginL) + wordOff + gauss(lr) * 0.22 * jscale * o.mess,
            rot: gauss(lr) * 1.2 * o.mess * Math.PI / 180,
            press: clamp(press + gauss(lr) * 0.05 * o.mess, 0.6, 1.2),
          });
        }
      }
    });
    if (!pages.length) pages.push({ items: [] });
    return { PW, PH, marginL, firstY, perPage, pages, missing: [...missing] };
  }

  // ---------------------------------------------------------------- paint
  let noiseTile = null;
  function grainTile() {
    if (noiseTile) return noiseTile;
    const n = 256, c = document.createElement('canvas');
    c.width = c.height = n;
    const x = c.getContext('2d'), d = x.createImageData(n, n);
    const r = mulberry32(12345);
    for (let i = 0; i < n * n; i++) {
      const a = 0.5 + r() * 0.5 - (r() < 0.04 ? 0.35 : 0);   // graphite grain with tiny gaps
      d.data[i * 4] = d.data[i * 4 + 1] = d.data[i * 4 + 2] = 0;
      d.data[i * 4 + 3] = clamp(a, 0, 1) * 255;
    }
    x.putImageData(d, 0, 0);
    noiseTile = c;
    return c;
  }
  let paperTile = null;
  function paperGrain() {
    if (paperTile) return paperTile;
    const n = 256, c = document.createElement('canvas');
    c.width = c.height = n;
    const x = c.getContext('2d'), d = x.createImageData(n, n);
    const r = mulberry32(777);
    for (let i = 0; i < n * n; i++) {
      const v = 246 + r() * 9 - (r() < 0.015 ? 14 : 0);
      d.data[i * 4] = d.data[i * 4 + 1] = d.data[i * 4 + 2] = v;
      d.data[i * 4 + 3] = 255;
    }
    x.putImageData(d, 0, 0);
    paperTile = c;
    return c;
  }

  function drawPaper(ctx, W, H, px, o) {
    ctx.fillStyle = o.tint;
    ctx.fillRect(0, 0, W, H);
    ctx.globalCompositeOperation = 'multiply';
    ctx.fillStyle = ctx.createPattern(paperGrain(), 'repeat');
    ctx.fillRect(0, 0, W, H);
    // soft edge shading
    const g = ctx.createRadialGradient(W / 2, H / 2, Math.min(W, H) * 0.35, W / 2, H / 2, Math.max(W, H) * 0.78);
    g.addColorStop(0, 'rgba(255,255,255,1)');
    g.addColorStop(1, 'rgba(226,220,206,1)');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, H);
    ctx.globalCompositeOperation = 'source-over';

    ctx.lineWidth = Math.max(0.7, 0.55 * px);
    if (o.paper === 'lined') {
      ctx.strokeStyle = 'rgba(105,155,214,0.55)';
      const y0 = doc.firstY - Math.floor((doc.firstY - 30) / o.pitch) * o.pitch;
      for (let y = y0; y < doc.PH - 24; y += o.pitch) {
        ctx.beginPath(); ctx.moveTo(0, y * px); ctx.lineTo(W, y * px); ctx.stroke();
      }
      ctx.strokeStyle = 'rgba(214,106,106,0.6)';
      ctx.beginPath(); ctx.moveTo((doc.marginL - 14) * px, 0); ctx.lineTo((doc.marginL - 14) * px, H); ctx.stroke();
    } else if (o.paper === 'grid') {
      ctx.strokeStyle = 'rgba(105,155,214,0.35)';
      const s = o.pitch / 2;
      for (let x = 0; x < doc.PW; x += s) { ctx.beginPath(); ctx.moveTo(x * px, 0); ctx.lineTo(x * px, H); ctx.stroke(); }
      for (let y = 0; y < doc.PH; y += s) { ctx.beginPath(); ctx.moveTo(0, y * px); ctx.lineTo(W, y * px); ctx.stroke(); }
    }
  }

  function paintPage(pageIndex, dpi, canvas, o) {
    const px = dpi / 72;
    const W = Math.round(doc.PW * px), H = Math.round(doc.PH * px);
    canvas.width = W; canvas.height = H;
    const ctx = canvas.getContext('2d');
    if (!o.transparent) drawPaper(ctx, W, H, px, o);

    // ink layer
    const ink = document.createElement('canvas');
    ink.width = W; ink.height = H;
    const ictx = ink.getContext('2d');
    ictx.imageSmoothingEnabled = true;
    ictx.imageSmoothingQuality = 'high';
    const pen = PEN[o.pen];
    const shear = Math.tan(o.slant * Math.PI / 180);
    const k = o.xh / pack.xh;
    const boost = clamp(Math.pow(1 / (k * px), 0.55), 1, 3.2);   // downscaling thins pencil; compensate
    for (const it of doc.pages[pageIndex].items) {
      const gain = Math.min(5, o.dark * pen.dark * boost * it.press);
      ictx.save();
      ictx.setTransform(px, 0, 0, px, 0, 0);
      ictx.translate(it.cx, it.by);
      ictx.rotate(it.rot);
      ictx.transform(1, 0, -shear, 1, 0, 0);
      for (let g = gain; g > 0.02; g -= 1) {
        ictx.globalAlpha = Math.min(1, g);
        ictx.drawImage(it.img, -it.w / 2, -it.b, it.w, it.h);
      }
      ictx.restore();
    }
    if (pen.color) {
      ictx.globalCompositeOperation = 'source-in';
      ictx.fillStyle = pen.color;
      ictx.fillRect(0, 0, W, H);
    }
    ictx.globalCompositeOperation = 'destination-in';       // graphite grain
    ictx.fillStyle = ictx.createPattern(grainTile(), 'repeat');
    ictx.fillRect(0, 0, W, H);

    ctx.globalCompositeOperation = o.transparent ? 'source-over' : 'multiply';
    ctx.drawImage(ink, 0, 0);
    ctx.globalCompositeOperation = 'source-over';
  }

  // ---------------------------------------------------------------- export
  function download(blob, name) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }
  const toBlob = (canvas, type, q) => new Promise((res) => canvas.toBlob(res, type, q));

  async function exportPng(pageIndex) {
    const c = document.createElement('canvas');
    paintPage(pageIndex, 300, c, readOptions());
    download(await toBlob(c, 'image/png'), `handwriting-page-${pageIndex + 1}.png`);
  }

  async function exportPdf() {
    const o = readOptions();
    const jpegs = [];
    for (let i = 0; i < doc.pages.length; i++) {
      const c = document.createElement('canvas');
      paintPage(i, 200, c, { ...o, transparent: false });
      jpegs.push({ bytes: new Uint8Array(await (await toBlob(c, 'image/jpeg', 0.92)).arrayBuffer()), w: c.width, h: c.height });
    }
    download(new Blob([makePdf(jpegs, doc.PW, doc.PH)], { type: 'application/pdf' }), 'handwriting.pdf');
  }

  function makePdf(jpegs, PW, PH) {
    const enc = new TextEncoder();
    const parts = [], offsets = {};
    let pos = 0;
    const push = (x) => { const b = typeof x === 'string' ? enc.encode(x) : x; parts.push(b); pos += b.length; };
    const obj = (id, ...chunks) => { offsets[id] = pos; push(`${id} 0 obj\n`); chunks.forEach(push); push('\nendobj\n'); };
    push('%PDF-1.4\n');
    const n = jpegs.length;
    obj(1, '<< /Type /Catalog /Pages 2 0 R >>');
    obj(2, `<< /Type /Pages /Kids [${jpegs.map((_, i) => `${5 + 3 * i} 0 R`).join(' ')}] /Count ${n} >>`);
    jpegs.forEach((j, i) => {
      const imgId = 3 + 3 * i, cId = 4 + 3 * i, pId = 5 + 3 * i;
      obj(imgId, `<< /Type /XObject /Subtype /Image /Width ${j.w} /Height ${j.h} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${j.bytes.length} >>\nstream\n`, j.bytes, '\nendstream');
      const content = `q ${PW} 0 0 ${PH} 0 0 cm /Im0 Do Q`;
      obj(cId, `<< /Length ${content.length} >>\nstream\n${content}\nendstream`);
      obj(pId, `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${PW} ${PH}] /Resources << /XObject << /Im0 ${imgId} 0 R >> >> /Contents ${cId} 0 R >>`);
    });
    const total = 3 + 3 * n;
    const xref = pos;
    push(`xref\n0 ${total}\n0000000000 65535 f \n`);
    for (let id = 1; id < total; id++) push(`${String(offsets[id]).padStart(10, '0')} 00000 n \n`);
    push(`trailer\n<< /Size ${total} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`);
    const out = new Uint8Array(pos);
    let p = 0;
    for (const b of parts) { out.set(b, p); p += b.length; }
    return out;
  }

  // -------------------------------------------------------------------- UI
  let seed = (Math.random() * 1e9) | 0;
  let timer = null;

  function render() {
    if (!pack) return;
    const o = readOptions();
    doc = layoutDoc(o);
    const host = $('pages');
    while (host.children.length > doc.pages.length) host.lastChild.remove();
    doc.pages.forEach((_, i) => {
      let fig = host.children[i];
      if (!fig) {
        fig = document.createElement('figure');
        fig.className = 'page';
        fig.style.margin = '0 0 18px';
        fig.innerHTML = '<canvas></canvas><footer><span></span><button type="button">Download PNG</button></footer>';
        fig.querySelector('button').addEventListener('click', () => exportPng([...host.children].indexOf(fig)));
        host.appendChild(fig);
      }
      paintPage(i, 110, fig.querySelector('canvas'), o);
      fig.querySelector('span').textContent = `Page ${i + 1} of ${doc.pages.length}`;
    });
    const note = $('unsupported');
    note.hidden = !doc.missing.length;
    if (doc.missing.length) {
      note.textContent = `I don't have these characters in your handwriting yet, so I skipped them: ${doc.missing.map((c) => `“${c}”`).join(' ')}`;
    }
    $('seed').textContent = `variation #${seed % 100000}`;
    for (const id of ['xh', 'pitch', 'mess', 'dark', 'spacing', 'slant']) {
      $('o-' + id).textContent = $(id).value + (id === 'xh' || id === 'pitch' ? ' pt' : id === 'slant' ? '°' : '');
    }
  }
  const schedule = () => { clearTimeout(timer); timer = setTimeout(() => { try { render(); } catch (e) { fail(e); } }, 90); };

  function fail(e) {
    setStatus('Something broke: ' + (e && e.message ? e.message : e));
    console.error(e);
  }
  window.addEventListener('error', (ev) => fail(ev.error || ev.message));
  window.addEventListener('unhandledrejection', (ev) => fail(ev.reason));

  function guarded(fn) {
    return (...a) => {
      if (!pack || !doc) { setStatus('Still loading your letters — give it a sec, then try again.'); return; }
      try { return fn(...a); } catch (e) { fail(e); }
    };
  }

  async function init() {
    setStatus('Loading your letters… 0');
    if (window.matchMedia('(max-width: 800px)').matches) $('settings').open = false;
    for (const id of ['text', 'paper', 'size', 'tint', 'pen', 'xh', 'pitch', 'mess', 'dark', 'spacing', 'slant', 'transparent']) {
      $(id).addEventListener('input', schedule);
    }
    $('reroll').addEventListener('click', () => { seed = (Math.random() * 1e9) | 0; render(); });
    $('pdf').addEventListener('click', guarded(() => exportPdf().catch(fail)));
    try {
      await loadPack();
      const n = Object.values(pack.glyphs).reduce((s, l) => s + l.length, 0);
      render();
      setStatus(`${Object.keys(pack.glyphs).length} characters · ${n} handwriting samples loaded`);
    } catch (e) {
      fail(e);
    }
  }
  init();
})();
