/* ════════════════════════════════════════════════════════════
   SUPERMAKS · mission control

   The brain is your own `hermes` CLI/profile, so configured Hermes tools,
   browser automation, skills, memory, MCP servers and third-party connectors
   are live. The Mac panel talks to a second machine over SSH via the server.

   No build step, no dependencies. Load order matters only in that everything
   below boots from the IIFE at the bottom.
   ════════════════════════════════════════════════════════════ */

const $  = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];

const TOKEN = document.querySelector('meta[name="supermaks-token"]')?.content || '';
/* The server rewrites the placeholder when it serves index.html. If it's still
   here we were opened straight off disk — run the whole HUD against a fake
   backend so the interface can be reviewed without Hermes, a Mac, or a key. */
const DEMO = !TOKEN || TOKEN === '__SUPERMAKS_' + 'TOKEN__' || location.protocol === 'file:';
const headers = extra => ({'x-supermaks-token': TOKEN, ...(extra || {})});

const RT = {tts:false, stt:false, browserStt:false, browserTts:false, mac:false};
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
const speechSynth = window.speechSynthesis;

const now  = () => new Date().toLocaleTimeString('en-GB', {hour12:false});
const esc  = s => String(s ?? '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const clamp = (v,a,b) => Math.max(a, Math.min(b, v));
const rid  = () => 'run_' + (crypto.randomUUID ? crypto.randomUUID().replace(/-/g,'').slice(0,10)
                                               : Math.random().toString(16).slice(2,12));

const api = (url, opts) => (DEMO ? mockFetch(url, opts) : fetch(url, opts));

/* ══════════════ 1. state ══════════════ */

const STATES = {
  standby:      ['STANDBY',      ''],
  listening:    ['LISTENING',    'listening'],
  transcribing: ['TRANSCRIBING', 'thinking'],
  thinking:     ['THINKING',     'thinking'],
  speaking:     ['SPEAKING',     'speaking'],
  done:         ['COMPLETE',     'done'],
  fault:        ['FAULT',        'fault'],
};
let stateName = 'standby';

function setState(name, sub){
  stateName = name;
  const [word, cls] = STATES[name] || STATES.standby;
  document.body.className = cls ? 'state-' + cls : '';
  $('#stateWord').textContent = word;
  if (sub != null) $('#stateSub').textContent = sub;
}
const setSub = t => { $('#stateSub').textContent = t; };

function setTag(cls, text){
  const t = $('#rtag');
  t.className = 'rtag ' + (cls || '');
  t.textContent = text;
}

/* ══════════════ 2. event log ══════════════ */

const LOG_GROUP = {
  run:'agent', status:'agent', complete:'agent', latency:'agent', tool:'agent',
  note:'agent', send:'agent', command:'agent',
  voice:'voice', mac:'mac', error:'error',
};
let logFilter = 'all';

function log(kind, label, msg){
  const el = document.createElement('div');
  el.className = 'entry k-' + kind;
  el.dataset.group = LOG_GROUP[kind] || 'agent';
  el.innerHTML = `<div class="top"><span class="ts">${now()}</span>`
               + `<span class="kind">${esc(label)}</span></div>`
               + (msg ? `<div class="msg">${esc(msg)}</div>` : '');
  if (logFilter !== 'all' && el.dataset.group !== logFilter) el.hidden = true;
  const box = $('#log');
  box.insertBefore(el, box.firstChild);
  while (box.children.length > 120) box.removeChild(box.lastChild);
}

$('#logFilters').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  logFilter = b.dataset.f;
  $$('#logFilters button').forEach(x => x.classList.toggle('on', x === b));
  $$('#log .entry').forEach(x => { x.hidden = logFilter !== 'all' && x.dataset.group !== logFilter; });
});

/* ══════════════ 3. audio bus ══════════════
   One AudioContext for everything. The reactor reads whichever analyser is
   currently live — the microphone while listening, the speech playback while
   talking — so the visualiser is always showing real audio rather than a
   decorative loop. */

const bus = {
  ctx:null, micNode:null, micAnalyser:null, outAnalyser:null, out:null, buf:null,
};

function audioCtx(){
  if (!bus.ctx) bus.ctx = new (window.AudioContext || window.webkitAudioContext)();
  if (bus.ctx.state === 'suspended') bus.ctx.resume().catch(()=>{});
  return bus.ctx;
}

/* One persistent <audio>. A MediaElementSource can only ever be created once
   per element, so we reuse the element and swap its src per chunk. */
function outputNode(){
  if (bus.out) return bus.out;
  const el = new Audio();
  el.crossOrigin = 'anonymous';
  const ctx = audioCtx();
  const src = ctx.createMediaElementSource(el);
  bus.outAnalyser = ctx.createAnalyser();
  bus.outAnalyser.fftSize = 1024;
  src.connect(bus.outAnalyser);
  bus.outAnalyser.connect(ctx.destination);
  bus.out = el;
  return el;
}

function attachMic(stream){
  const ctx = audioCtx();
  bus.micNode = ctx.createMediaStreamSource(stream);
  bus.micAnalyser = ctx.createAnalyser();
  bus.micAnalyser.fftSize = 1024;
  bus.micNode.connect(bus.micAnalyser);
  bus.buf = new Uint8Array(bus.micAnalyser.fftSize);
}

function detachMic(){
  try { bus.micNode?.disconnect(); } catch(_){}
  bus.micNode = bus.micAnalyser = null;
}

function liveAnalyser(){
  if (stateName === 'speaking' && bus.outAnalyser) return bus.outAnalyser;
  if (bus.micAnalyser && (convo || pttArmed)) return bus.micAnalyser;
  return null;
}

/* Buffers are cached on the analyser itself. These run at 60fps from two
   places; allocating a typed array per frame is pure garbage. */
function timeBuf(a){ return a._tbuf || (a._tbuf = new Uint8Array(a.fftSize)); }
function freqBuf(a){ return a._fbuf || (a._fbuf = new Uint8Array(a.frequencyBinCount)); }

function rmsOf(analyser){
  if (!analyser) return 0;
  const buf = timeBuf(analyser);
  analyser.getByteTimeDomainData(buf);
  let s = 0;
  for (let i = 0; i < buf.length; i++){ const v = (buf[i] - 128) / 128; s += v * v; }
  return Math.sqrt(s / buf.length);
}

/* ══════════════ 4. the reactor ══════════════ */

const reactor = (() => {
  const cv = $('#core');
  const ctx = cv.getContext('2d');
  const BARS = 108;
  const spectrum = new Float32Array(BARS);
  let W = 0, H = 0, dpr = 1, t = 0, level = 0, raf = 0;
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

  function resize(){
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    const r = cv.getBoundingClientRect();
    W = r.width; H = r.height;
    cv.width = W * dpr; cv.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  new ResizeObserver(resize).observe(cv);

  const css = n => getComputedStyle(document.body).getPropertyValue(n).trim();

  function frame(){
    raf = requestAnimationFrame(frame);
    t += reduce ? 0.002 : 0.016;

    const analyser = liveAnalyser();
    /* Idle breathing when there's no real audio, so a quiet core still looks
       alive without pretending to visualise something. */
    const target = analyser ? clamp(rmsOf(analyser) * 4.2, 0, 1)
                            : 0.10 + Math.sin(t * 1.15) * 0.045;
    level += (target - level) * 0.18;

    const acc = css('--acc') || '#37e6d0';
    const acc2 = css('--acc-2') || '#7ef4ff';
    const cx = W / 2, cy = H / 2;
    const R = Math.min(W, H) / 2 - 8;

    ctx.clearRect(0, 0, W, H);
    if (R <= 0) return;

    // spectrum, mirrored so the ring reads symmetrically
    if (analyser){
      const freq = freqBuf(analyser);
      analyser.getByteFrequencyData(freq);
      const half = BARS / 2;
      for (let i = 0; i < half; i++){
        const v = (freq[Math.floor(i / half * (freq.length * 0.62))] || 0) / 255;
        spectrum[half + i] += (v - spectrum[half + i]) * 0.32;
        spectrum[half - 1 - i] = spectrum[half + i];
      }
    } else {
      for (let i = 0; i < BARS; i++){
        const v = (Math.sin(t * 1.6 + i * 0.42) * 0.5 + 0.5) * (0.10 + level * 0.5);
        spectrum[i] += (v - spectrum[i]) * 0.1;
      }
    }

    // ── outer tick ring
    ctx.save(); ctx.translate(cx, cy); ctx.rotate(t * 0.055);
    for (let i = 0; i < 120; i++){
      const a = i / 120 * Math.PI * 2, major = i % 10 === 0;
      const r0 = R * (major ? 0.955 : 0.975);
      ctx.globalAlpha = major ? 0.5 : 0.2;
      ctx.strokeStyle = acc; ctx.lineWidth = major ? 1.4 : 1;
      ctx.beginPath();
      ctx.moveTo(Math.cos(a) * r0, Math.sin(a) * r0);
      ctx.lineTo(Math.cos(a) * R, Math.sin(a) * R);
      ctx.stroke();
    }
    ctx.restore();

    // ── rotating arc segments
    const arcs = [
      {r:0.90, from:0.00, len:1.15, sp: 0.30, w:2.2, a:0.85},
      {r:0.80, from:2.20, len:0.85, sp:-0.22, w:1.6, a:0.55},
      {r:0.71, from:4.10, len:1.70, sp: 0.16, w:1.1, a:0.35},
    ];
    for (const arc of arcs){
      ctx.save(); ctx.translate(cx, cy);
      ctx.globalAlpha = arc.a;
      ctx.strokeStyle = acc2; ctx.lineWidth = arc.w; ctx.lineCap = 'round';
      ctx.shadowBlur = 16; ctx.shadowColor = acc;
      ctx.beginPath();
      ctx.arc(0, 0, R * arc.r, arc.from + t * arc.sp, arc.from + arc.len + t * arc.sp);
      ctx.stroke();
      ctx.restore();
    }

    // ── radial spectrum
    const inner = R * 0.44, span = R * 0.22;
    ctx.save(); ctx.translate(cx, cy); ctx.rotate(-Math.PI / 2);
    for (let i = 0; i < BARS; i++){
      const a = i / BARS * Math.PI * 2;
      const h = span * (0.10 + spectrum[i] * 0.95);
      ctx.globalAlpha = 0.25 + spectrum[i] * 0.75;
      ctx.strokeStyle = i % 9 === 0 ? acc2 : acc;
      ctx.lineWidth = 2; ctx.lineCap = 'round';
      ctx.beginPath();
      ctx.moveTo(Math.cos(a) * inner, Math.sin(a) * inner);
      ctx.lineTo(Math.cos(a) * (inner + h), Math.sin(a) * (inner + h));
      ctx.stroke();
    }
    ctx.restore();

    // ── core
    const cr = R * (0.27 + level * 0.06);
    const g = ctx.createRadialGradient(cx, cy - cr * 0.2, 0, cx, cy, cr);
    g.addColorStop(0, '#ffffff');
    g.addColorStop(0.22, acc2);
    g.addColorStop(0.62, acc);
    g.addColorStop(1, 'rgba(2,10,16,0)');
    ctx.globalAlpha = 0.34 + level * 0.5;
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.arc(cx, cy, cr, 0, Math.PI * 2); ctx.fill();

    ctx.globalAlpha = 0.65;
    ctx.strokeStyle = acc; ctx.lineWidth = 1;
    ctx.shadowBlur = 20; ctx.shadowColor = acc;
    ctx.beginPath(); ctx.arc(cx, cy, cr, 0, Math.PI * 2); ctx.stroke();
    ctx.shadowBlur = 0; ctx.globalAlpha = 1;
  }

  return {
    start(){ resize(); if (!raf) frame(); },
  };
})();

/* the small live meter inside the Voice button */
const micMeter = (() => {
  const cv = $('#micMeter');
  const ctx = cv.getContext('2d');
  const N = 30, vals = new Float32Array(N);
  let i = 0;
  function frame(){
    requestAnimationFrame(frame);
    const w = cv.clientWidth, h = cv.clientHeight;
    if (!w) return;
    if (cv.width !== w) { cv.width = w; cv.height = h; }
    vals[i = (i + 1) % N] = (convo || pttArmed) ? clamp(rmsOf(bus.micAnalyser) * 5, 0, 1) : 0;
    ctx.clearRect(0, 0, w, h);
    const bw = w / N;
    ctx.fillStyle = getComputedStyle(document.body).getPropertyValue('--acc').trim() || '#37e6d0';
    for (let k = 0; k < N; k++){
      const v = vals[(i + 1 + k) % N];
      const bh = Math.max(1, v * h);
      ctx.globalAlpha = 0.25 + v * 0.75;
      ctx.fillRect(k * bw + 1, h - bh, bw - 2, bh);
    }
    ctx.globalAlpha = 1;
  }
  return { start(){ frame(); } };
})();

/* ══════════════ 5. the run ══════════════ */

let running = false, answer = '', gotFirstDelta = false, speakThisRun = true;
let speakDone = Promise.resolve(), activeController = null;

async function transmit(message, options = {}){
  if (!message.trim()) return;
  if (running){
    log('note','BUSY',`still working — "${message.slice(0,40)}" not sent. Wait, or press Esc.`);
    return;
  }
  lastActivity = Date.now();       // restarts the idle clock that re-arms dormant mode
  running = true; answer = ''; gotFirstDelta = false;
  speakThisRun = options.speak !== false;

  const fresh = /^\/new\b/.test(message.trim());
  const id = rid();
  setState('thinking', 'dispatching…');
  setTag('live', 'running');
  $('#latency').textContent = '';
  $('#response').innerHTML = '<span class="cur"></span>';
  $('#stop').hidden = false;
  log('run','RUN',`${id} · ${message.slice(0,70)}`);

  const t0 = performance.now();
  const tick = setInterval(() => {
    if (!running || answer) return;
    setSub(`thinking · ${((performance.now() - t0) / 1000).toFixed(1)}s`);
  }, 100);

  try {
    activeController = new AbortController();
    const res = await api('/api/run', {
      method:'POST', headers:headers({'content-type':'application/json'}),
      body: JSON.stringify({message, fresh}), signal:activeController.signal,
    });
    if (!res.ok) throw new Error(`backend returned HTTP ${res.status}`);
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    for (;;){
      const {value, done} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream:true});
      let nl;
      while ((nl = buf.indexOf('\n')) >= 0){
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (line) handleEvent(JSON.parse(line));
      }
    }
  } catch (e){
    if (e?.name === 'AbortError'){
      log('note','CANCEL','run cancelled');
      setState('standby','cancelled'); setTag('', 'idle');
    } else {
      log('error','ERROR', String(e).slice(0,200));
      setState('fault','stream dropped'); setTag('err','error');
    }
  }

  activeController = null;
  running = false;
  $('#stop').hidden = true;
  clearInterval(tick);
  await speakDone;
}

function handleEvent(ev){
  switch (ev.t){
    case 'status':
      if (ev.session_id) $('#sessionTag').textContent = 'session ' + String(ev.session_id).slice(0,12);
      log('status','STATUS',
          `core online · tools=${ev.tools ?? 0} profile=${ev.profile||'default'} permission=${ev.permission||'normal'}`);
      break;

    case 'latency':
      $('#latency').textContent = `first token ${ev.ms}ms`;
      log('latency','LATENCY',`first token after ${ev.ms}ms`);
      break;

    case 'tool':
      log('tool','TOOL', ev.phase === 'use' ? `→ ${ev.name}(${ev.input || ''})`
                                            : `✓ ${ev.ok === false ? 'error' : 'ok'}`);
      break;

    case 'delta':
      answer += ev.text;
      renderAnswer();
      if (!gotFirstDelta){ gotFirstDelta = true; setSub('receiving'); }
      break;

    case 'complete':
      setTag('done','complete');
      log('complete','COMPLETE', ev.ms != null ? `run completed in ${ev.ms}ms` : 'run completed');
      renderAnswer(true);
      speakDone = speakThisRun ? speak(answer)
                               : (setState('done','ready'), Promise.resolve());
      break;

    case 'error':
      setState('fault', (ev.message || 'error').slice(0,46));
      setTag('err','error');
      log('error','ERROR', ev.message || 'unknown');
      if (!answer) $('#response').innerHTML = `<span class="fault">⚠ ${esc(ev.message || 'Run failed.')}</span>`;
      else renderAnswer(true);
      break;

    case 'note':
      log('note','NOTE', ev.message || '');
      // The server emits a note for /screen; pull the image the moment it lands.
      if (/screenshot ok/i.test(ev.message || '')) grabScreenshot();
      break;
  }
}

function renderAnswer(final){
  const el = $('#response');
  el.innerHTML = esc(answer) + (final ? '' : '<span class="cur"></span>');
  el.scrollTop = el.scrollHeight;
}

/* ══════════════ 6. voice out ══════════════ */

function cleanForSpeech(text){
  return String(text || '')
    .replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '')
    .split(/\r?\n/)
    .map(l => l.trim())
    .filter(l => l && !/^(session_id:|session:|duration:|messages:|query:|initializing agent|resume this session|hermes --resume|[-─=]{3,})/i.test(l))
    .join(' ')
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/[*_#>`]/g, '')
    .replace(/\s{2,}/g, ' ')
    .trim();
}

/* Split into speakable chunks on sentence boundaries. Synthesising the first
   sentence while the rest is still being generated is the difference between
   a reply that starts in half a second and one that starts in four. */
function chunkForSpeech(text, size = 190){
  const out = [];
  let cur = '';
  for (const part of text.split(/(?<=[.!?…])\s+/)){
    if ((cur + ' ' + part).trim().length > size && cur){ out.push(cur.trim()); cur = part; }
    else cur += ' ' + part;
  }
  if (cur.trim()) out.push(cur.trim());
  return out.slice(0, 8);
}

let muted = false;

function speak(text){
  const clean = cleanForSpeech(text);
  if (muted || !clean){ setState('done','ready'); return Promise.resolve(); }
  suppress = true;
  setState('speaking','speaking');

  // Demo mode keeps the fish.audio badge (it reflects the real config) but has
  // no server to synthesise with, so it falls through to browser speech.
  if (RT.tts && !DEMO) return speakViaFish(chunkForSpeech(clean));
  if (RT.browserTts && speechSynth) return speakViaBrowser(clean.slice(0, 700));
  setState('done','ready');
  return Promise.resolve();
}

async function speakViaFish(chunks){
  const el = outputNode();
  const fetchChunk = txt => api('/api/speak', {
      method:'POST', headers:headers({'content-type':'application/json'}),
      body: JSON.stringify({text: txt}),
    }).then(r => r.ok ? r.blob() : Promise.reject(new Error('tts ' + r.status)));

  try {
    let pending = fetchChunk(chunks[0]);
    for (let i = 0; i < chunks.length; i++){
      const blob = await pending;
      pending = i + 1 < chunks.length ? fetchChunk(chunks[i + 1]) : null;   // prefetch
      const url = URL.createObjectURL(blob);
      el.src = url;
      await new Promise(res => {
        el.onended = el.onerror = res;
        el.play().catch(res);
      });
      URL.revokeObjectURL(url);
      if (muted) break;
    }
  } catch (e){
    log('error','VOICE', 'speech synthesis failed — ' + String(e.message || e).slice(0,120));
  }
  setState('done','ready');
}

function speakViaBrowser(text){
  return new Promise(res => {
    try {
      speechSynth.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.rate = 0.98; u.pitch = 0.85;
      u.onend = u.onerror = () => { setState('done','ready'); res(); };
      speechSynth.speak(u);
    } catch(_){ setState('done','ready'); res(); }
  });
}

/* ══════════════ 7. voice in ══════════════
   Click Voice once. From then on: listen → you stop → transcribe → answer →
   speak → listen again. No re-clicking between turns.

   The mic is deliberately DEAF while SuperMaks is thinking or speaking
   (`suppress`) — otherwise it transcribes its own voice through the speakers
   and talks to itself forever. Re-arming happens only after it finishes. */

let convo = false, suppress = false, recognition = null;
let micStream = null, recorder = null, chunks = [];
let vad = null, spoke = false, loudAt = 0, turnStart = 0;
let floorSum = 0, floorN = 0, threshold = 0.02, peak = 0, calibrating = true;
const SILENCE = 900, MIN_TURN_MS = 350, MAX_TURN_MS = 18000, NO_SPEECH_MS = 10000;

async function micToggle(){
  if (convo) return stopConvo();

  if (RT.stt){
    try {
      micStream = await navigator.mediaDevices.getUserMedia(
        {audio:{echoCancellation:true, noiseSuppression:true, autoGainControl:true}});
    } catch(e){
      log('error','VOICE','microphone blocked. Allow it for this page — and note that a plain-http LAN address can never get the mic, only localhost can.');
      return;
    }
    attachMic(micStream);
    convo = true; suppress = false;
    micButton(true, 'Listening');
    log('voice','VOICE','fish.audio conversation open · listening');
    beginTurn();
    return;
  }

  if (SpeechRecognition){
    convo = true; suppress = false;
    micButton(true, 'Listening');
    log('voice','VOICE','browser speech recognition open · listening');
    setState('listening','browser speech online');
    startBrowserRecognition();
    return;
  }
  log('error','VOICE','no speech recognition available — add FISH_AUDIO_API_KEY, or use Chrome');
}

function micButton(on, label){
  const b = $('#mic');
  b.classList.toggle('on', on);
  b.querySelector('.tlabel').textContent = label;
}

function stopConvo(){
  convo = false; suppress = false;
  if (recognition){ try{ recognition.onend = null; recognition.stop(); }catch(_){} recognition = null; }
  if (vad){ clearInterval(vad); vad = null; }
  if (recorder && recorder.state === 'recording'){ recorder._cancel = true; try{ recorder.stop(); }catch(_){} }
  recorder = null;
  detachMic();
  if (micStream){ micStream.getTracks().forEach(t => t.stop()); micStream = null; }
  micButton(false, 'Voice');
  log('voice','VOICE','conversation closed');
  setState('standby','awaiting uplink');
}

/* ── browser speech turn ── */
function startBrowserRecognition(){
  if (!convo || suppress || !SpeechRecognition) return;
  recognition = new SpeechRecognition();
  recognition.lang = 'en-US';
  recognition.interimResults = true;
  recognition.continuous = false;
  let finalText = '';

  recognition.onresult = e => {
    let interim = '';
    for (let i = e.resultIndex; i < e.results.length; i++){
      const txt = e.results[i][0].transcript;
      if (e.results[i].isFinal) finalText += txt; else interim += txt;
    }
    const shown = (finalText || interim || '').trim();
    if (shown) setSub('heard: ' + shown.slice(0, 44));
  };
  recognition.onerror = e => log('error','VOICE', `browser speech: ${e.error || 'error'}`);
  recognition.onend = async () => {
    const text = finalText.trim();
    if (!convo) return;
    if (!text){ if (!suppress) setTimeout(startBrowserRecognition, 250); return; }
    await sendVoiceTurn(text);
    if (convo) setTimeout(startBrowserRecognition, 350);
  };

  setState('listening','browser speech online');
  try { recognition.start(); } catch(e){ log('error','VOICE','speech recognition failed to start'); }
}

/* ── one server-side Fish Audio listening turn ── */
function beginTurn(){
  if (!convo || suppress || !micStream) return;
  chunks = []; spoke = false; peak = 0;
  floorSum = 0; floorN = 0; calibrating = true; threshold = 0.02;
  recorder = new MediaRecorder(micStream, pickMime());
  recorder.ondataavailable = e => { if (e.data?.size) chunks.push(e.data); };
  recorder.onstop = ship;
  recorder.start(250);                        // timeslice → data flows reliably
  turnStart = loudAt = performance.now();
  setState('listening','listening…');
  if (!vad) vad = setInterval(vtick, 40);
}

function pickMime(){
  for (const m of ['audio/webm;codecs=opus','audio/webm','audio/mp4'])
    if (window.MediaRecorder?.isTypeSupported?.(m)) return {mimeType:m};
  return undefined;
}

function endTurn(reason){
  log('voice','VOICE',`${reason} · peak ${peak.toFixed(3)} · threshold ${threshold.toFixed(3)}`);
  try { recorder.stop(); } catch(_){}          // → ship()
}

function vtick(){
  if (!bus.micAnalyser || suppress || !recorder || recorder.state !== 'recording') return;
  const rms = rmsOf(bus.micAnalyser);
  const t = performance.now();
  peak = Math.max(peak, rms);

  // first 300ms: measure the room's noise floor, set a threshold just above it
  if (calibrating){
    floorSum += rms; floorN++;
    if (t - turnStart > 300){
      threshold = Math.max(0.006, (floorSum / Math.max(1, floorN)) * 1.6 + 0.003);
      calibrating = false;
    }
    return;
  }

  if (rms > threshold){
    loudAt = t;
    if (!spoke){ spoke = true; setSub('speech detected'); }
  } else if (spoke && t - loudAt > SILENCE){
    return endTurn('silence');
  }

  // failsafes so a turn can never hang
  if (t - turnStart > MAX_TURN_MS) return endTurn('max-length');
  if (!spoke && t - turnStart > NO_SPEECH_MS) return endTurn('no-speech');
}

async function ship(){
  const cancelled = recorder?._cancel;
  const blob = new Blob(chunks, {type: recorder?.mimeType || 'audio/webm'});
  chunks = [];
  if (cancelled) return;

  // no speech, too short, or too small — listen again rather than ship ambient noise
  if (!spoke || blob.size < 1400 || performance.now() - turnStart < MIN_TURN_MS){
    if (convo && !suppress) beginTurn();
    return;
  }

  setState('transcribing','transcribing…');
  const t0 = performance.now();
  try {
    const r = await api('/api/listen', {
      method:'POST', headers:headers({'content-type': blob.type || 'audio/webm'}), body:blob,
    }).then(r => r.json());
    const text = (r.text || '').trim();
    if (!text){
      log('voice','VOICE','nothing transcribed');
      if (convo) beginTurn(); else setState('standby','nothing heard');
      return;
    }
    log('voice','VOICE',`transcribed in ${Math.round(performance.now() - t0)}ms: "${text}"`);
    await sendVoiceTurn(text);
    suppress = false;
    if (convo) beginTurn();
  } catch(e){
    log('error','VOICE','transcription failed');
    suppress = false;
    if (convo) beginTurn(); else setState('fault','transcription failed');
  }
}

async function sendVoiceTurn(text){
  const armed = $('#input').dataset.cmd || '';
  const full = (armed ? armed + ' ' : '') + text;
  $('#input').value = ''; disarm();
  log('send','SEND', full);
  await transmit(full);
  suppress = false;
}

/* ══════════════ 8. commands ══════════════ */

const COMMANDS = [
  ['/new',         'Fresh Hermes thread'],
  ['/briefing',    'The once-a-day wake report'],
  ['/mac',         'Do something on the Mac'],
  ['/screen',      'Capture the Mac screen'],
  ['/browser',     'Chrome / browser operation'],
  ['/goal',        'Set or read the standing objective'],
  ['/background',  'Run an async mission'],
  ['/mission',     'Read or add to the mission queue'],
  ['/personality', 'Change the tone overlay'],
  ['/profile',     'Add a fact about you'],
  ['/tools',       'Hermes tool status'],
  ['/toolsets',    'Enabled toolsets'],
  ['/connectors',  'How integrations are inherited'],
  ['/voice',       'Test the Fish Audio voice path'],
  ['/status',      'Runtime, profile, Mac, voice'],
  ['/commands',    'Show every command'],
];
const PAYLOAD = new Set(['/goal','/browser','/background','/mission','/personality','/profile','/mac','/connect']);
const QUIET   = new Set(['/tools','/commands','/toolsets','/status','/connectors']);

function disarm(){
  $$('.cmd').forEach(c => c.classList.remove('armed'));
  $('#input').dataset.cmd = '';
  $('#armedTag').textContent = '';
}

function runCommand(cmd){
  if (running){ log('note','BUSY',`${cmd} not sent — still working.`); return; }
  disarm();
  if (PAYLOAD.has(cmd)){
    const btn = $(`.cmd[data-cmd="${cmd}"]`);
    btn?.classList.add('armed');
    $('#input').dataset.cmd = cmd;
    $('#armedTag').textContent = cmd + ' armed';
    $('#input').focus();
  }
  log('command','COMMAND', cmd);
  transmit(cmd, {speak: !QUIET.has(cmd)});
}

$('#cmds').addEventListener('click', e => {
  const b = e.target.closest('.cmd');
  if (b) runCommand(b.dataset.cmd);
});

function sendFromInput(){
  const armed = $('#input').dataset.cmd || '';
  const raw = $('#input').value.trim();
  if (!raw && !armed) return;
  const full = (armed && !raw.startsWith('/')) ? `${armed} ${raw}` : (raw || armed);
  $('#input').value = ''; disarm();
  log('send','SEND', full);
  transmit(full);
}

$('#run').onclick = sendFromInput;
$('#mic').onclick = micToggle;
$('#clearBtn').onclick = () => {
  answer = '';
  $('#response').innerHTML = '<span class="placeholder">Display cleared. Standing by.</span>';
  setTag('','idle'); $('#latency').textContent = '';
};
$('#stop').onclick = cancelRun;
$('#mute').onclick = () => {
  muted = !muted;
  const b = $('#mute');
  b.textContent = muted ? '🔇' : '🔊';
  b.classList.toggle('off', muted);
  if (muted && bus.out) bus.out.pause();
  if (muted) speechSynth?.cancel();
};
$('#input').addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)){ e.preventDefault(); sendFromInput(); }
});

function cancelRun(){
  if (!running || !activeController) return;
  api('/api/cancel', {method:'POST', headers:headers({'content-type':'application/json'}), body:'{}'}).catch(()=>{});
  activeController.abort();
}

/* ── command palette ── */
const pal = {open:false, sel:0, items:COMMANDS};

function openPalette(){
  pal.open = true; pal.sel = 0; pal.items = COMMANDS;
  $('#palette').hidden = false;
  $('#palInput').value = '';
  renderPalette();
  $('#palInput').focus();
}
function closePalette(){ pal.open = false; $('#palette').hidden = true; }

function renderPalette(){
  $('#palList').innerHTML = pal.items.map(([c, d], i) =>
    `<div class="pal-item ${i === pal.sel ? 'sel' : ''}" data-cmd="${c}"><b>${c}</b><span>${esc(d)}</span></div>`
  ).join('') || '<div class="pal-item"><span>nothing matches</span></div>';
}

$('#palInput').addEventListener('input', e => {
  const q = e.target.value.toLowerCase().replace(/^\//, '');
  pal.items = COMMANDS.filter(([c, d]) => (c + ' ' + d).toLowerCase().includes(q));
  pal.sel = 0; renderPalette();
});
$('#palList').addEventListener('click', e => {
  const it = e.target.closest('.pal-item');
  if (it?.dataset.cmd){ closePalette(); runCommand(it.dataset.cmd); }
});
$('#paletteBtn').onclick = openPalette;
$('#palette').addEventListener('click', e => { if (e.target.id === 'palette') closePalette(); });

document.addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k'){
    e.preventDefault(); pal.open ? closePalette() : openPalette(); return;
  }
  if (pal.open){
    if (e.key === 'Escape'){ closePalette(); return; }
    if (e.key === 'ArrowDown'){ e.preventDefault(); pal.sel = Math.min(pal.sel + 1, pal.items.length - 1); renderPalette(); }
    if (e.key === 'ArrowUp'){   e.preventDefault(); pal.sel = Math.max(pal.sel - 1, 0); renderPalette(); }
    if (e.key === 'Enter' && pal.items[pal.sel]){ const c = pal.items[pal.sel][0]; closePalette(); runCommand(c); }
    return;
  }
  if (e.key === 'Escape'){
    if (!$('#lightbox').hidden){ $('#lightbox').hidden = true; return; }
    if (running) return cancelRun();
    if (convo) stopConvo();
  }
});

/* ══════════════ 9. the Mac panel ══════════════ */

async function pollMac(){
  if (!RT.mac) return;
  try {
    const s = await api('/api/mac', {headers:headers()}).then(r => r.json());
    const up = !!s.reachable;
    $('#macDot').className = 'dot ' + (up ? 'ok' : 'bad');
    $('#macName').textContent = s.name || s.host || '—';
    $('#macDetail').textContent = up ? (s.gui_blocked ? 'GUI permissions needed' : (s.macos ? 'macOS ' + s.macos : 'online'))
                                     : (s.detail || 'unreachable');
    $('#macFront').textContent = s.front || (s.gui_blocked ? 'blocked' : '—');
    $('#macBatt').textContent  = [s.battery, s.power].filter(Boolean).join(' ') || '—';
    $('#macVol').textContent   = s.volume != null ? s.volume : '—';
    $('#macUp').textContent    = s.uptime || '—';
    $$('#macActs button').forEach(b => { b.disabled = !up; });
    $('#pillMac').className = 'pill ' + (up ? 'ok' : 'bad');
    $('#pillMac').querySelector('b').textContent = up ? (s.name || 'online') : 'offline';
  } catch(_){ /* server restarting; the next poll will tell us */ }
}

async function grabScreenshot(){
  if (!RT.mac) return;
  const wrap = $('#shotWrap');
  wrap.classList.add('loading');
  try {
    const r = await api('/api/mac/screenshot', {
      method:'POST', headers:headers({'content-type':'application/json'}), body:'{}',
    });
    const j = await r.json();
    if (j.image){
      $('#shot').src = 'data:image/jpeg;base64,' + j.image;
      $('#shot').classList.add('on');
      $('#shotEmpty').hidden = true;
      log('mac','SCREEN','captured');
    } else {
      $('#shotEmpty').textContent = 'capture blocked';
      log('error','ERROR', j.error || 'screenshot failed');
    }
  } catch(e){
    log('error','ERROR','screenshot request failed');
  }
  wrap.classList.remove('loading');
}

$('#macRefresh').onclick = grabScreenshot;
$('#shotWrap').onclick = e => {
  if (e.target === $('#macRefresh')) return;
  if (!$('#shot').classList.contains('on')) return grabScreenshot();
  $('#lightboxImg').src = $('#shot').src;
  $('#lightbox').hidden = false;
};
$('#lightbox').onclick = () => { $('#lightbox').hidden = true; };

$('#macActs').addEventListener('click', async e => {
  const b = e.target.closest('button[data-mac]');
  if (!b) return;
  b.disabled = true;
  try {
    const r = await api('/api/mac/action', {
      method:'POST', headers:headers({'content-type':'application/json'}),
      body: JSON.stringify({name: b.dataset.mac}),
    }).then(r => r.json());
    log(r.ok ? 'mac' : 'error', r.ok ? 'MAC' : 'ERROR',
        r.ok ? `${r.label}${r.output ? ' → ' + r.output.slice(0,80) : ''}` : (r.error || 'action failed'));
  } catch(_){ log('error','ERROR','Mac action failed'); }
  b.disabled = false;
  pollMac();
});

/* ══════════════ 9b. Mac action approvals ══════════════
   tools/mac-guard.sh (used by Hermes' mac-sh / mac-osa) blocks a running Mac
   tool call the moment it classifies something as risky, and files a request
   here. This polls for those requests and lets a human resolve them one at a
   time — the shell script on the other end is genuinely waiting on this. */

const knownApprovals = new Set();

function renderApprovals(list){
  const box = $('#approvals');
  const ids = new Set(list.map(a => a.id));
  // drop cards for anything no longer pending (approved/denied/timed out elsewhere)
  $$('#approvals .approval').forEach(el => { if (!ids.has(el.dataset.id)) el.remove(); });

  for (const a of list){
    if (knownApprovals.has(a.id) && $(`.approval[data-id="${a.id}"]`)) continue;
    knownApprovals.add(a.id);
    log('mac','APPROVAL NEEDED', `${a.tool}: ${a.desc}`);
    const el = document.createElement('div');
    el.className = 'approval';
    el.dataset.id = a.id;
    el.innerHTML = `
      <div class="approval-head"><b>⚠ confirm required</b><span class="tool">${esc(a.tool)}</span></div>
      <div class="approval-desc">${esc(a.desc)}</div>
      <div class="approval-cmd">${esc(a.cmd)}</div>
      <div class="approval-row">
        <button class="deny">Deny</button>
        <button class="allow">Approve</button>
      </div>`;
    box.appendChild(el);
  }
}

async function decideApproval(id, approve){
  const el = $(`.approval[data-id="${id}"]`);
  el?.remove();
  knownApprovals.delete(id);
  try {
    await api('/api/mac/approvals/decide', {
      method:'POST', headers:headers({'content-type':'application/json'}),
      body: JSON.stringify({id, approve}),
    });
    log('mac', approve ? 'APPROVED' : 'DENIED', id);
  } catch(_){ log('error','ERROR','could not send the decision'); }
}

$('#approvals').addEventListener('click', e => {
  const card = e.target.closest('.approval'); if (!card) return;
  if (e.target.classList.contains('allow')) decideApproval(card.dataset.id, true);
  if (e.target.classList.contains('deny'))  decideApproval(card.dataset.id, false);
});

async function pollApprovals(){
  if (!RT.mac) return;
  try {
    const j = await api('/api/mac/approvals', {headers:headers()}).then(r => r.json());
    renderApprovals(j.pending || []);
  } catch(_){}
}

/* ══════════════ 10. telemetry ══════════════ */

function pill(id, cls, text){
  const el = $(id);
  el.className = 'pill ' + cls;
  el.querySelector('b').textContent = text;
}

async function loadStatus(){
  const s = await api('/api/status').then(r => r.json());
  RT.tts = s.tts === 'fish';
  RT.stt = s.stt === 'fish';
  RT.mac = !!s.mac_enabled;
  RT.browserStt = !!SpeechRecognition;
  RT.browserTts = !!speechSynth;
  if (s.wake_song) WAKE_SONG = s.wake_song;
  if (s.wake_idle_hours) WAKE_IDLE_MS = s.wake_idle_hours * 60 * 60 * 1000;

  const hermes = s.runtime === 'hermes';
  pill('#pillBrain',  hermes ? 'ok' : 'bad', hermes ? 'Hermes' : 'offline');
  pill('#pillVoice',  RT.tts ? 'ok' : 'warn', RT.tts ? 'fish.audio' : 'browser');
  pill('#pillProfile','', s.profile || 'default');
  pill('#pillMac',    RT.mac ? 'warn' : '', RT.mac ? 'probing' : 'off');

  $('#tGateway').textContent = 'localhost:' + (location.port || '8730');
  $('#tGateway').className = 'ok';
  $('#tRuntime').textContent = s.runtime || '—';
  $('#tRuntime').className = hermes ? 'ok' : 'bad';
  $('#tProfile').textContent = s.profile || 'default';
  $('#tPerm').textContent = s.permission || 'normal';
  $('#tVoice').textContent = RT.tts ? `${s.voice_model} · ${String(s.voice_id).slice(0,8)}…`
                                    : 'browser Web Speech';
  $('#tVoice').className = RT.tts ? 'ok' : 'warn';
  $('#tWork').textContent = s.workdir || '—';

  const tools = s.tools || [];
  $('#toolCount').textContent = tools.length ? `${tools.length} visible` : 'none reported';
  $('#toolsList').innerHTML = tools.length
    ? tools.slice(0, 14).map(t => `<span class="chip">${esc(t)}</span>`).join('')
    : '<span class="chip ghost">no tool list — set HERMES_CMD if needed</span>';

  $('#mic').querySelector('.tlabel').textContent = 'Voice';
  log('status','BOOT', `${s.runtime} core · profile=${s.profile || 'default'} · permission=${s.permission}`);
  log('voice','VOICE', RT.tts ? `fish.audio ${s.voice_model} ready` : 'browser speech fallback');
  return s;
}

setInterval(() => { $('#clock').textContent = now(); }, 1000);

// finished /background missions, reported once each
setInterval(async () => {
  if (DEMO) return;
  try {
    const j = await api('/api/jobs', {headers:headers()}).then(r => r.json());
    for (const d of (j.done || [])){
      log('complete','MISSION', `${d.mission} → ${(d.result || '').slice(0,200)}`);
      if (!running) speak(`Mission complete. ${(d.result || '').slice(0,300)}`);
    }
  } catch(_){}
}, 4000);

/* ══════════════ 10b. the wake phrase ══════════════
   The HUD boots into "dormant" every time it's opened — near-black, a slow
   breathing mark, nothing else — and runs a background speech recognizer
   listening only for the wake phrase. It re-arms itself the same way after
   WAKE_IDLE_MS of no prompt, so a laptop left running all day drops back into
   "asleep, waiting to be woken" on its own. Two ways in if the mic doesn't
   catch it: tap the dormant screen, or long-press Control — the same key that
   drives push-to-talk once you're already awake does double duty as the
   keyboard fallback for waking up.

   This deliberately never touches Fish Audio — streaming the mic to a paid
   STT endpoint continuously just to catch one phrase would be both slow and
   not free. The browser's own recognizer is free and already running
   locally; it only runs while dormant, not all the time in between. */

const WAKE_PHRASE = /\bwake\s*up\b[\s\S]{0,24}\bdad(?:dy)?'?s?\b[\s\S]{0,24}\bhome\b/i;
// How long without a prompt before the HUD drops back into dormant. Overridable
// server-side via WAKE_IDLE_HOURS in .env; this is just the pre-status default.
let WAKE_IDLE_MS = 4.5 * 60 * 60 * 1000;

let dormantOn = false, waking = false, wakeActive = false, wakeRecognition = null;
let lastActivity = Date.now();

function enterDormant(){
  if (!SpeechRecognition){
    log('note','WAKE','no speech recognition in this browser — skipping the wake phrase, use Control or the Voice button');
    setState('standby','awaiting uplink');
    return;
  }
  dormantOn = true;
  $('#dormant').hidden = false;
  $('#dormant').classList.remove('waking');
  log('voice','WAKE','listening for the wake phrase');
  startWakeListening();
}

function exitDormant(){
  dormantOn = false;
  stopWakeListening();
  $('#dormant').classList.add('waking');
  setTimeout(() => { $('#dormant').hidden = true; }, 650);
}

function startWakeListening(){
  if (!SpeechRecognition || wakeActive) return;
  wakeActive = true;
  armWakeRecognizer();
}
function stopWakeListening(){
  wakeActive = false;
  if (wakeRecognition){
    try { wakeRecognition.onend = null; wakeRecognition.stop(); } catch(_){}
    wakeRecognition = null;
  }
}
function armWakeRecognizer(){
  if (!wakeActive) return;
  wakeRecognition = new SpeechRecognition();
  wakeRecognition.lang = 'en-US';
  wakeRecognition.continuous = true;
  wakeRecognition.interimResults = true;
  wakeRecognition.onresult = e => {
    for (let i = e.resultIndex; i < e.results.length; i++){
      if (WAKE_PHRASE.test(e.results[i][0].transcript)) { handleWake(); return; }
    }
  };
  // Chrome silently ends continuous recognition after a stretch of silence.
  // Re-arm it as long as we're still meant to be listening.
  wakeRecognition.onerror = () => {};
  wakeRecognition.onend = () => { if (wakeActive) setTimeout(armWakeRecognizer, 250); };
  try { wakeRecognition.start(); } catch(_){ if (wakeActive) setTimeout(armWakeRecognizer, 400); }
}

function handleWake(){
  if (!dormantOn || waking) return;
  waking = true;                   // holds off the idle re-arm mid-sequence
  exitDormant();
  log('voice','WAKE','wake phrase heard');
  setTimeout(async () => {
    await playWakeSong();          // resolves quickly on its own if blocked, off, or skipped
    waking = false;
    transmit('/briefing');         // this also stamps lastActivity, restarting the idle clock
  }, 650);                         // let the exit animation clear first
}

/* ── the wake jingle ──
   "Should I Stay or Should I Go" by The Clash, from the top, capped at
   WAKE_SONG.seconds (~1:45). Three sources, chosen in .env:
     youtube — the browser embeds the official video client-side; nothing is
               downloaded or stored by this project, ever
     local   — streams a file you already own, from this machine
     off     — skipped entirely
   Autoplay triggered from a speech-recognition callback (not a click) is
   often blocked by the browser, so this never blocks the greeting: it gives
   itself a few seconds to actually start, then moves on regardless. */

let WAKE_SONG = {source:'off', youtube_id:'', local_ready:false, seconds:105};
let wakeSongPlayer = null, wakeSongAudio = null, wakeSongTimer = null;

function showSongBanner(on, label){
  $('#songBanner').hidden = !on;
  if (label) $('#songLabel').textContent = label;
}

function stopWakeSong(){
  if (wakeSongTimer){ clearTimeout(wakeSongTimer); wakeSongTimer = null; }
  if (wakeSongPlayer){ try { wakeSongPlayer.stopVideo(); wakeSongPlayer.destroy(); } catch(_){} wakeSongPlayer = null; }
  if (wakeSongAudio){ try { wakeSongAudio.pause(); } catch(_){} wakeSongAudio = null; }
  showSongBanner(false);
}

function loadYouTubeAPI(){
  return new Promise(resolve => {
    if (window.YT && window.YT.Player) return resolve();
    const prev = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = () => { prev?.(); resolve(); };
    if (!document.getElementById('ytIframeApi')){
      const s = document.createElement('script');
      s.id = 'ytIframeApi'; s.src = 'https://www.youtube.com/iframe_api';
      document.head.appendChild(s);
    }
  });
}

function playWakeSong(){
  return new Promise(resolve => {
    const usable = !DEMO && (
      (WAKE_SONG.source === 'youtube' && WAKE_SONG.youtube_id) ||
      (WAKE_SONG.source === 'local'   && WAKE_SONG.local_ready)
    );
    if (!usable) return resolve();

    let done = false;
    const finish = () => { if (done) return; done = true; stopWakeSong(); resolve(); };
    // Autoplay from a non-click callback may just be refused outright — never
    // let the wake-up greeting hang on it.
    const graceTimer = setTimeout(finish, 4000);
    const cap = Math.max(5, WAKE_SONG.seconds || 105) * 1000;

    showSongBanner(true, WAKE_SONG.source === 'youtube'
      ? 'Should I Stay or Should I Go — The Clash' : 'wake song');
    $('#songSkip').onclick = finish;

    if (WAKE_SONG.source === 'youtube'){
      loadYouTubeAPI().then(() => {
        try {
          wakeSongPlayer = new YT.Player('wakeSongHost', {
            videoId: WAKE_SONG.youtube_id,
            playerVars: {autoplay:1, start:0, controls:0, modestbranding:1, rel:0, playsinline:1},
            events: {
              onReady: e => { clearTimeout(graceTimer); e.target.playVideo(); wakeSongTimer = setTimeout(finish, cap); },
              onStateChange: e => { if (window.YT && e.data === YT.PlayerState.ENDED) finish(); },
              onError: finish,
            },
          });
        } catch(_){ finish(); }
      }).catch(finish);
      return;
    }

    // local
    api('/api/wake-song', {headers: headers()})
      .then(r => r.ok ? r.blob() : Promise.reject(new Error('wake-song ' + r.status)))
      .then(blob => {
        clearTimeout(graceTimer);
        const url = URL.createObjectURL(blob);
        wakeSongAudio = new Audio(url);
        wakeSongAudio.onended = finish;
        wakeSongAudio.onerror = finish;
        wakeSongTimer = setTimeout(finish, cap);
        return wakeSongAudio.play();
      })
      .catch(finish);
  });
}

// A tap on the dormant screen is the failsafe if the mic never catches it.
$('#dormant').addEventListener('click', handleWake);

// Re-arm dormant mode after a long enough silence, without needing a reload.
setInterval(() => {
  if (SpeechRecognition && !dormantOn && !waking && !running && !convo && !pttArmed
      && Date.now() - lastActivity >= WAKE_IDLE_MS) enterDormant();
}, 5 * 60 * 1000);

/* ══════════════ 10c. push-to-talk — long-press Control ══════════════
   Hold Control past a short threshold to arm the mic; it stops the instant
   you release the key OR the instant you stop talking, whichever comes
   first — no 900ms silence tax like the continuous conversation loop uses.
   A tap that doesn't clear the threshold does nothing, so it can't be
   triggered by an accidental brush of the key.

   Same key, second job: if the HUD is dormant when Control is long-pressed,
   it wakes instead of arming the mic — the keyboard fallback for whenever the
   wake phrase doesn't land (background noise, a cold, whatever). */

const PTT_HOLD_MS = 320;      // how long Control must be held to count as a long-press
const PTT_SILENCE_MS = 220;   // near-instant endpointing once armed
const PTT_CALIBRATE_MS = 140;
const PTT_MAX_MS = 15000;

let pttTimer = null, pttArmed = false, pttStream = null, pttRecorder = null, pttChunks = [];
let pttRecognition = null, pttVad = null, pttSpoke = false, pttLoudAt = 0, pttStartedAt = 0;
let pttThreshold = 0.02, pttCalibrating = true, pttFloorSum = 0, pttFloorN = 0;

document.addEventListener('keydown', e => {
  if (e.key !== 'Control' || e.repeat) return;
  if (running || waking || pttArmed || pttTimer) return;
  if (!dormantOn && convo) return;                // already listening continuously
  pttTimer = setTimeout(() => {
    pttTimer = null;
    if (dormantOn) handleWake(); else startPTT();
  }, PTT_HOLD_MS);
});
document.addEventListener('keyup', e => {
  if (e.key !== 'Control') return;
  if (pttTimer){ clearTimeout(pttTimer); pttTimer = null; }
  if (pttArmed) stopPTT();
});
// A held key with no matching keyup (alt-tab, a browser dialog stealing
// focus) must not leave the mic stuck open.
window.addEventListener('blur', () => {
  if (pttTimer){ clearTimeout(pttTimer); pttTimer = null; }
  if (pttArmed) stopPTT();
});

async function startPTT(){
  pttTimer = null;
  if (dormantOn || running || convo || waking) return;
  micButton(true, 'Listening');
  setState('listening','listening…');

  if (RT.stt){
    try {
      pttStream = micStream || await navigator.mediaDevices.getUserMedia(
        {audio:{echoCancellation:true, noiseSuppression:true, autoGainControl:true}});
    } catch(e){
      log('error','VOICE','microphone blocked — allow it for this page');
      micButton(false,'Voice'); setState('standby','awaiting uplink');
      return;
    }
    if (!bus.micAnalyser) attachMic(pttStream);
    pttArmed = true;
    pttChunks = []; pttSpoke = false; pttCalibrating = true;
    pttFloorSum = 0; pttFloorN = 0; pttThreshold = 0.02;
    pttRecorder = new MediaRecorder(pttStream, pickMime());
    pttRecorder.ondataavailable = e => { if (e.data?.size) pttChunks.push(e.data); };
    pttRecorder.onstop = shipPTT;
    pttRecorder.start(100);
    pttStartedAt = performance.now();
    pttVad = setInterval(pttTick, 25);
    return;
  }

  if (SpeechRecognition){
    pttArmed = true;
    pttRecognition = new SpeechRecognition();
    pttRecognition.lang = 'en-US';
    pttRecognition.interimResults = true;
    pttRecognition.continuous = false;
    let finalText = '';
    pttRecognition.onresult = e => {
      for (let i = e.resultIndex; i < e.results.length; i++){
        const t = e.results[i][0].transcript;
        if (e.results[i].isFinal) finalText += t;
        else setSub('heard: ' + t.slice(0, 44));
      }
    };
    pttRecognition.onerror = () => {};
    pttRecognition.onend = async () => {
      pttArmed = false; pttRecognition = null;
      micButton(false,'Voice');
      const text = finalText.trim();
      if (!text){ setState('standby','nothing heard'); return; }
      await sendVoiceTurn(text);
    };
    try { pttRecognition.start(); } catch(_){ pttArmed = false; micButton(false,'Voice'); }
    return;
  }

  log('error','VOICE','no speech recognition available');
  micButton(false,'Voice'); setState('standby','awaiting uplink');
}

function pttTick(){
  if (!pttArmed || !bus.micAnalyser) return;
  const rms = rmsOf(bus.micAnalyser);
  const t = performance.now();

  if (pttCalibrating){
    pttFloorSum += rms; pttFloorN++;
    if (t - pttStartedAt > PTT_CALIBRATE_MS){
      pttThreshold = Math.max(0.006, (pttFloorSum / Math.max(1, pttFloorN)) * 1.6 + 0.003);
      pttCalibrating = false;
    }
    return;
  }

  if (rms > pttThreshold){
    pttLoudAt = t;
    if (!pttSpoke){ pttSpoke = true; setSub('speech detected'); }
  } else if (pttSpoke && t - pttLoudAt > PTT_SILENCE_MS){
    return stopPTT();                              // stops the instant speech ends
  }
  if (t - pttStartedAt > PTT_MAX_MS) stopPTT();
}

// The single stop path for the recorder-based (Fish Audio) branch — called on
// key release, on silence, on blur, whichever happens first.
function stopPTT(){
  if (!pttArmed) return;
  pttArmed = false;
  if (pttVad){ clearInterval(pttVad); pttVad = null; }
  if (pttRecognition){ try { pttRecognition.stop(); } catch(_){} return; }  // → its own onend
  if (pttRecorder && pttRecorder.state === 'recording'){ try { pttRecorder.stop(); } catch(_){} }
}

async function shipPTT(){
  const blob = new Blob(pttChunks, {type: pttRecorder?.mimeType || 'audio/webm'});
  pttChunks = [];
  micButton(false,'Voice');
  detachMic();
  if (pttStream && pttStream !== micStream){ pttStream.getTracks().forEach(t => t.stop()); }
  pttStream = null;

  if (!pttSpoke || blob.size < 900){ setState('standby','nothing heard'); return; }
  setState('transcribing','transcribing…');
  try {
    const r = await api('/api/listen', {
      method:'POST', headers:headers({'content-type': blob.type || 'audio/webm'}), body:blob,
    }).then(r => r.json());
    const text = (r.text || '').trim();
    if (!text){ log('voice','VOICE','nothing transcribed'); setState('standby','nothing heard'); return; }
    await sendVoiceTurn(text);
  } catch(e){
    log('error','VOICE','transcription failed');
    setState('fault','transcription failed');
  }
}

/* ══════════════ 11. boot ══════════════ */

const BOOT_LINES = [
  'initialising reactor core',
  'attaching Hermes runtime',
  'inheriting profile tools and skills',
  'opening the Mac bridge',
  'arming speech channel',
];

function bootSequence(){
  return new Promise(res => {
    const box = $('#bootLines');
    let i = 0;
    const done = () => { document.removeEventListener('keydown', skip); res(); };
    const skip = () => { clearInterval(iv); $('#bootFill').style.width = '100%'; done(); };
    document.addEventListener('keydown', skip, {once:true});
    $('#boot').addEventListener('click', skip, {once:true});

    const iv = setInterval(() => {
      if (i >= BOOT_LINES.length){ clearInterval(iv); setTimeout(done, 240); return; }
      const el = document.createElement('div');
      el.innerHTML = `<span class="ok">▸</span> ${BOOT_LINES[i]}`;
      box.appendChild(el);
      $('#bootFill').style.width = ((++i) / BOOT_LINES.length * 100) + '%';
    }, 190);
  });
}

(async () => {
  reactor.start();
  micMeter.start();
  $('#clock').textContent = now();

  const boot = bootSequence();
  let status = null;
  try { status = await loadStatus(); }
  catch(e){
    log('error','STATUS','server unreachable');
    pill('#pillBrain','bad','offline');
  }
  await boot;

  document.body.classList.remove('state-boot');

  lastActivity = Date.now();       // boot itself starts the idle clock fresh
  enterDormant();                  // dormant on every launch — see enterDormant() for the fallback

  if (RT.mac){
    pollMac(); grabScreenshot();
    setInterval(pollMac, 6000);
    pollApprovals(); setInterval(pollApprovals, 2500);
  }
  if (DEMO) log('note','DEMO','running against a mock backend — no Hermes, Mac, or key required');
  if (status && status.runtime !== 'hermes' && !DEMO)
    log('error','ERROR','Hermes CLI not reachable — set HERMES_CMD in .env');
})();

/* ══════════════ 12. mock backend ══════════════
   Only ever reached when the page is opened straight off disk. Lets the whole
   interface — states, streaming, voice meters, Mac panel — be reviewed before
   any of the real pieces exist. */

function mockFetch(url, opts = {}){
  const json = body => Promise.resolve({ok:true, status:200, json:() => Promise.resolve(body)});
  const wait = ms => new Promise(r => setTimeout(r, ms));

  if (url === '/api/status') return json({
    runtime:'hermes', permission:'normal', profile:'default', workdir:'/home/maks',
    model:'Hermes default', tools:['terminal','browser','files','google_workspace','memory','mcp:notion','mac-bridge'],
    stt:'fish', tts:'fish', voice_model:'s2.1-pro-free', voice_id:'612b878b113047d9a770c069c8b4fdfe',
    mac_enabled:true, mac_host:'mac', session:null,
    wake_song:{source:'youtube', youtube_id:'xMaE6toi4mk', local_ready:false, seconds:105},
    wake_idle_hours:4.5,
  });

  if (url === '/api/mac') return json({
    reachable:true, host:'mac', name:'Maks-MacBook-Pro', macos:'15.3',
    front:'Safari', battery:'86%', power:'AC Power', volume:42, uptime:'3 days, 4:12',
    load:'1.42', disk:'214Gi',
  });

  if (url === '/api/mac/screenshot'){
    // a small generated placeholder rather than a fake desktop
    const c = document.createElement('canvas');
    c.width = 640; c.height = 400;
    const g = c.getContext('2d');
    const grad = g.createLinearGradient(0,0,640,400);
    grad.addColorStop(0,'#0b1622'); grad.addColorStop(1,'#132b3a');
    g.fillStyle = grad; g.fillRect(0,0,640,400);
    g.fillStyle = 'rgba(55,230,208,.5)';
    g.font = '16px monospace'; g.textAlign = 'center';
    g.fillText('demo mode — no Mac connected', 320, 200);
    return json({image: c.toDataURL('image/jpeg', .8).split(',')[1]});
  }

  if (url === '/api/mac/action') return json({ok:true, label:'Demo action', output:'ok'});

  // A single fake pending approval so the confirmation gate is visible in
  // demo mode too — it disappears for good once you Approve or Deny it.
  if (url === '/api/mac/approvals'){
    if (mockFetch._approvalGone) return json({pending: []});
    return json({pending: [{
      id:'demo1', tool:'mac-sh',
      desc:'run a shell command on the Mac',
      cmd:'rm ~/Desktop/old-build.zip',
      ts: Math.floor(Date.now()/1000),
    }]});
  }
  if (url === '/api/mac/approvals/decide'){
    mockFetch._approvalGone = true;
    return json({ok:true});
  }
  if (url === '/api/jobs')       return json({done:[], running:[]});
  if (url === '/api/cancel' || url === '/api/new') return json({ok:true});
  if (url === '/api/speak')      return Promise.resolve({ok:false, status:503});

  // Demo transcription: real microphone, real silence detection, canned words.
  // The whole listen → answer → speak → listen loop is exercised for real.
  if (url === '/api/listen'){
    const lines = ['What is on my calendar today?',
                   'Open my mail on the Mac.',
                   'How is the build doing?'];
    mockFetch._n = (mockFetch._n || 0) + 1;
    return wait(320).then(() => ({ok:true, status:200,
      json:() => Promise.resolve({text: lines[mockFetch._n % lines.length]})}));
  }

  if (url === '/api/run'){
    const msg = JSON.parse(opts.body || '{}').message || '';
    const reply = /^\/briefing\b/.test(msg)
      ? 'Welcome home, sir. Three new emails, and two things on the calendar today — one of which is a meeting titled simply "sync," which tells me nothing and concerns me slightly. Shall I clear out the newsletters while you decide?'
      : msg.startsWith('/')
      ? `Demo response to ${msg.split(' ')[0]}. The real backend answers this through Hermes.`
      : 'Running in demo mode, so there is no agent behind me yet. Everything you see — the reactor, the streaming, the Mac panel, the wake phrase — is the real interface.';
    const events = [
      {t:'status', tools:7, profile:'default', permission:'normal'},
      {t:'latency', ms:210},
      ...reply.split(' ').map(w => ({t:'delta', text:w + ' '})),
      {t:'complete', ms:1400},
    ];
    const stream = new ReadableStream({
      async start(ctrl){
        const enc = new TextEncoder();
        for (const ev of events){
          await wait(ev.t === 'delta' ? 34 : 240);
          ctrl.enqueue(enc.encode(JSON.stringify(ev) + '\n'));
        }
        ctrl.close();
      },
    });
    return Promise.resolve({ok:true, status:200, body:stream});
  }

  return json({});
}
