// Focus sounds: generated in the browser with Web Audio (no files, no copyright),
// plus the user's own audio files.
let ctx = null, master = null, current = null;

function audio() {
  if (!ctx) {
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    master = ctx.createGain();
    master.gain.value = 0.5;
    master.connect(ctx.destination);
  }
  if (ctx.state === "suspended") ctx.resume();
  return ctx;
}

function noiseBuffer(kind, seconds = 4) {
  const n = ctx.sampleRate * seconds, buf = ctx.createBuffer(2, n, ctx.sampleRate);
  for (let ch = 0; ch < 2; ch++) {
    const d = buf.getChannelData(ch);
    let last = 0;
    for (let i = 0; i < n; i++) {
      const w = Math.random() * 2 - 1;
      if (kind === "brown") { last = (last + 0.02 * w) / 1.02; d[i] = last * 3.5; } else d[i] = w;
    }
  }
  return buf;
}
function loopNoise(kind, out) {
  const src = ctx.createBufferSource();
  src.buffer = noiseBuffer(kind); src.loop = true; src.connect(out); src.start();
  return src;
}

const SOUNDS = {
  brown: {
    name: "Brown noise", desc: "deep, steady rumble",
    start(out) { return [loopNoise("brown", out)]; },
  },
  rain: {
    name: "Rain", desc: "soft rain on a window",
    start(out) {
      const hp = ctx.createBiquadFilter(); hp.type = "highpass"; hp.frequency.value = 900;
      const lp = ctx.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = 7000;
      const g = ctx.createGain(); g.gain.value = 0.35;
      hp.connect(lp).connect(g).connect(out);
      const body = ctx.createGain(); body.gain.value = 0.5; body.connect(out);
      const nodes = [loopNoise("white", hp), loopNoise("brown", body)];
      // slow swell so it doesn't sound static
      const lfo = ctx.createOscillator(), lg = ctx.createGain();
      lfo.frequency.value = 0.07; lg.gain.value = 0.12; lfo.connect(lg).connect(g.gain); lfo.start();
      nodes.push(lfo);
      return nodes;
    },
  },
  fire: {
    name: "Fireplace", desc: "warm crackle",
    start(out) {
      const lp = ctx.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = 500;
      lp.connect(out);
      const nodes = [loopNoise("brown", lp)];
      let alive = true;
      const pop = () => {
        if (!alive) return;
        const t = ctx.currentTime, s = ctx.createBufferSource(), g = ctx.createGain(), bp = ctx.createBiquadFilter();
        s.buffer = noiseBuffer("white", 0.05); bp.type = "bandpass"; bp.frequency.value = 1500 + Math.random() * 3000;
        g.gain.setValueAtTime(0.25 + Math.random() * 0.5, t); g.gain.exponentialRampToValueAtTime(0.001, t + 0.04);
        s.connect(bp).connect(g).connect(out); s.start(t);
        setTimeout(pop, 60 + Math.random() * (Math.random() < 0.2 ? 900 : 250));
      };
      pop();
      nodes.push({ stop() { alive = false; } });
      return nodes;
    },
  },
  lofi: {
    name: "Lo-fi keys", desc: "slow chords with vinyl hiss",
    start(out) {
      const lp = ctx.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = 1400; lp.connect(out);
      const hiss = ctx.createGain(); hiss.gain.value = 0.03; hiss.connect(out);
      const nodes = [loopNoise("white", hiss)];
      // Fmaj7 - Em7 - Dm7 - Cmaj7, one chord every 4 s
      const chords = [[53, 57, 60, 64], [52, 55, 59, 62], [50, 53, 57, 60], [48, 52, 55, 59]];
      let alive = true, i = 0;
      const hz = (m) => 440 * 2 ** ((m - 69) / 12);
      const play = () => {
        if (!alive) return;
        const t = ctx.currentTime;
        chords[i++ % chords.length].forEach((m, k) => {
          const o = ctx.createOscillator(), g = ctx.createGain();
          o.type = "triangle"; o.frequency.value = hz(m); o.detune.value = (Math.random() - 0.5) * 12;
          g.gain.setValueAtTime(0, t + k * 0.06);
          g.gain.linearRampToValueAtTime(0.09, t + k * 0.06 + 0.05);
          g.gain.exponentialRampToValueAtTime(0.001, t + 3.9);
          o.connect(g).connect(lp); o.start(t + k * 0.06); o.stop(t + 4);
        });
        setTimeout(play, 4000);
      };
      play();
      nodes.push({ stop() { alive = false; } });
      return nodes;
    },
  },
};

// ---------- your own files ----------
const el = new Audio();
let playlist = [], idx = 0;
el.addEventListener("ended", () => { if (playlist.length) { idx = (idx + 1) % playlist.length; playFile(); } });
function playFile() {
  el.src = playlist[idx].url; el.play();
  update();
}

// ---------- UI ----------
const $ = (id) => document.getElementById(id);
let onChange = () => {};
function stopAll() {
  if (current?.nodes) current.nodes.forEach((n) => { try { n.stop(); } catch { /* already stopped */ } });
  if (current?.gain) current.gain.disconnect();
  el.pause();
  current = null;
}
function startSound(key) {
  stopAll();
  audio();
  const g = ctx.createGain(); g.gain.value = 0; g.connect(master);
  g.gain.linearRampToValueAtTime(1, ctx.currentTime + 1.2); // fade in
  current = { key, gain: g, nodes: SOUNDS[key].start(g) };
  update();
}
function update() {
  const playing = !!current || !el.paused;
  document.querySelectorAll(".snd").forEach((b) => b.classList.toggle("on", current?.key === b.dataset.key));
  $("ownBtn").classList.toggle("on", !el.paused);
  $("plIcon").setAttribute("href", playing ? "#i-pause" : "#i-play");
  $("plToggle").setAttribute("aria-label", playing ? "Pause" : "Play");
  $("nowPlaying").textContent = current ? SOUNDS[current.key].name
    : !el.paused && playlist.length ? playlist[idx].name : last ? "Paused" : "Nothing playing";
  onChange(playing);
}
let last = null;

export function initPlayer({ onPlayingChange } = {}) {
  onChange = onPlayingChange || onChange;
  $("sounds").innerHTML = Object.entries(SOUNDS).map(([k, s]) =>
    `<button type="button" class="snd" data-key="${k}"><svg class="ic"><use href="#i-${k}"/></svg><span><b>${s.name}</b><small>${s.desc}</small></span></button>`).join("");
  $("sounds").addEventListener("click", (e) => {
    const b = e.target.closest(".snd"); if (!b) return;
    if (current?.key === b.dataset.key) { stopAll(); last = b.dataset.key; update(); }
    else { last = b.dataset.key; startSound(b.dataset.key); }
  });
  $("plToggle").addEventListener("click", () => {
    if (current || !el.paused) { stopAll(); update(); }
    else if (last === "__file" && playlist.length) { audio(); el.play(); update(); }
    else startSound(last && SOUNDS[last] ? last : "brown");
  });
  $("plNext").addEventListener("click", () => {
    if (playlist.length && (last === "__file")) { idx = (idx + 1) % playlist.length; playFile(); return; }
    const keys = Object.keys(SOUNDS), k = keys[(keys.indexOf(current?.key || last || "lofi") + 1) % keys.length];
    last = k; startSound(k);
  });
  $("plVol").addEventListener("input", (e) => {
    const v = +e.target.value; el.volume = v; if (master) master.gain.value = v;
  });
  $("ownFiles").addEventListener("change", (e) => {
    const files = [...e.target.files].filter((f) => f.type.startsWith("audio/"));
    if (!files.length) return;
    playlist.forEach((p) => URL.revokeObjectURL(p.url));
    playlist = files.map((f) => ({ name: f.name.replace(/\.[^.]+$/, ""), url: URL.createObjectURL(f) }));
    idx = 0; stopAll(); last = "__file"; audio(); playFile();
  });
  el.addEventListener("play", update); el.addEventListener("pause", update);
  update();
}
export const player = { stop() { stopAll(); update(); }, playing: () => !!current || !el.paused };
