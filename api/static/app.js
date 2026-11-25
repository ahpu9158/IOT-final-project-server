// Smart Desk Box web app: live data, history, focus sessions.
const $ = (id) => document.getElementById(id);
const PRESENT_CM = 150;
const css = getComputedStyle(document.documentElement);
const tok = (n) => css.getPropertyValue(n).trim();

async function api(path, opts = {}) {
  let r;
  try {
    r = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
  } catch {
    return { ok: false, status: 0, data: null }; // server unreachable; next refresh retries
  }
  let data = null;
  try { data = await r.json(); } catch { /* empty body */ }
  return { ok: r.ok, status: r.status, data };
}

const fmtTime = (epoch) =>
  new Date(epoch * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
const ago = (epoch) => {
  const s = Math.max(0, Math.round(Date.now() / 1000 - epoch));
  return s < 60 ? `${s} s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : fmtTime(epoch);
};

// ---------------- Live ----------------
const CARDS = [
  { k: "temp", label: "Temperature", unit: "°C", f: (v) => v.toFixed(1) },
  { k: "hum", label: "Humidity", unit: "%", f: (v) => v.toFixed(0) },
  { k: "gas", label: "Air quality", unit: "raw", f: (v) => v.toFixed(0) },
  { k: "light", label: "Light", unit: "raw", f: (v) => v.toFixed(0) },
  { k: "dist", label: "At desk", f: (v) => (v > 0 && v < PRESENT_CM ? "Yes" : "No"), flag: (v) => v > 0 && v < PRESENT_CM, sub: (v) => `${v.toFixed(0)} cm` },
  { k: "lid_closed", label: "Phone lid", f: (v) => (v ? "Closed" : "Open"), flag: (v) => v },
  { k: "locked", label: "Lock", f: (v) => (v ? "Engaged" : "Off"), flag: (v) => v },
];
$("cards").innerHTML = CARDS.map(
  (c) => `<div class="card" id="c_${c.k}"><span class="k">${c.label}</span><span class="v">—</span></div>`
).join("");

function chartOpts(extra = {}) {
  return {
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: { mode: "index", intersect: false },
    scales: {
      x: { type: "linear", ticks: { color: tok("--muted"), maxTicksLimit: 7, callback: (v) => new Date(v).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) }, grid: { color: tok("--line") } },
      y: { ticks: { color: tok("--muted") }, grid: { color: tok("--line") } },
    },
    plugins: { legend: { display: false }, tooltip: { callbacks: { title: (it) => new Date(it[0].parsed.x).toLocaleString() } } },
    elements: { point: { radius: 0, hitRadius: 6 }, line: { tension: 0.25, borderWidth: 2 } },
    ...extra,
  };
}
const live = new Chart($("liveChart"), {
  type: "line",
  data: { datasets: [
    { label: "Temp °C", data: [], borderColor: tok("--accent"), yAxisID: "y" },
    { label: "Humidity %", data: [], borderColor: tok("--warn"), yAxisID: "y1" },
  ] },
  options: chartOpts({
    scales: {
      ...chartOpts().scales,
      y1: { position: "right", ticks: { color: tok("--muted") }, grid: { display: false } },
    },
  }),
});
const WINDOW_MS = 15 * 60 * 1000;
function pushLive(t, temp, hum) {
  const [a, b] = live.data.datasets;
  if (temp != null) a.data.push({ x: t, y: temp });
  if (hum != null) b.data.push({ x: t, y: hum });
  const cut = Date.now() - WINDOW_MS;
  for (const d of [a, b]) while (d.data.length && d.data[0].x < cut) d.data.shift();
  live.options.scales.x.min = cut; live.options.scales.x.max = Date.now();
  live.update("none");
}
async function seedLive() {
  const [t, h] = await Promise.all([api("/api/history?field=temp&range=1h"), api("/api/history?field=hum&range=1h")]);
  const cut = Date.now() - WINDOW_MS;
  const pts = (r) => (r.data || []).map((p) => ({ x: Date.parse(p.t), y: p.v })).filter((p) => p.x >= cut);
  live.data.datasets[0].data = pts(t); live.data.datasets[1].data = pts(h);
  live.update("none");
}

let lastTs = 0;
let latest = {};
async function refreshLatest() {
  const r = await api("/api/latest");
  if (!r.ok) return;
  latest = r.data;
  for (const c of CARDS) {
    const el = $("c_" + c.k), v = latest[c.k];
    if (v == null) continue;
    el.querySelector(".v").innerHTML = c.f(v) + (c.unit ? `<small>${c.unit}</small>` : c.sub ? `<small>${c.sub(v)}</small>` : "");
    el.classList.toggle("flag-on", !!(c.flag && c.flag(v)));
  }
  const gw = $("gw");
  gw.className = "gw " + (latest.pi_online ? "on" : "off");
  $("gwText").textContent = latest.pi_online ? "Box online" : "Box offline";
  const pill = $("statePill");
  const st = (latest.state || "—").toUpperCase();
  pill.textContent = st;
  pill.className = "pill " + ({ OK: "ok", WARNING: "warn", "NOT OK": "bad" }[st] || "");
  if (latest.ts) $("lastSeen").textContent = "updated " + ago(latest.ts);
  if (latest.ts && latest.ts !== lastTs) {
    lastTs = latest.ts;
    pushLive(latest.ts * 1000, latest.temp, latest.hum);
  }
}

// ---------------- History ----------------
const hist = new Chart($("histChart"), {
  type: "line",
  data: { datasets: [{ data: [], borderColor: tok("--accent"), backgroundColor: tok("--accent-soft"), fill: true }] },
  options: chartOpts(),
});
async function loadHistory() {
  const field = $("histField").value;
  const range = document.querySelector('input[name="range"]:checked').value;
  const r = await api(`/api/history?field=${field}&range=${range}`);
  const pts = (r.data || []).map((p) => ({ x: Date.parse(p.t), y: p.v }));
  hist.data.datasets[0].data = pts;
  const long = range === "7d" || range === "30d";
  hist.options.scales.x.ticks.callback = (v) =>
    new Date(v).toLocaleString([], long ? { month: "short", day: "numeric" } : { hour: "2-digit", minute: "2-digit" });
  hist.update();
  $("histEmpty").hidden = pts.length > 0;
  if (pts.length) {
    const ys = pts.map((p) => p.y);
    const avg = ys.reduce((a, b) => a + b, 0) / ys.length;
    const f = (v) => (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1));
    $("histStats").innerHTML = `Min<b>${f(Math.min(...ys))}</b> Avg<b>${f(avg)}</b> Max<b>${f(Math.max(...ys))}</b> Points<b>${pts.length}</b>`;
  } else $("histStats").innerHTML = "";
}
$("histField").addEventListener("change", loadHistory);
document.querySelectorAll('input[name="range"]').forEach((el) => el.addEventListener("change", loadHistory));

// ---------------- Session log ----------------
const EVENT_LABEL = { start: "started", finished: "finished", early: "ended early", wrong_safeword: "wrong safeword", emergency: "emergency", start_failed: "lock failed" };
async function loadLog() {
  const r = await api("/api/session/log");
  if (!r.ok) return;
  $("logBody").innerHTML = r.data.length
    ? r.data.map((e) => `<tr><td>${fmtTime(e.ts)}</td><td>${e.mode === "locked" ? "Phone locked" : "Timer"}</td>
        <td><span class="ev ${e.event}">${EVENT_LABEL[e.event] || e.event}</span></td>
        <td>${e.planned_min ?? "—"} min</td><td>${e.actual_min != null ? e.actual_min + " min" : "—"}</td></tr>`).join("")
    : `<tr><td colspan="5" class="muted">No sessions yet. Start one above.</td></tr>`;
}

// ---------------- Focus session ----------------
const mode = () => document.querySelector('input[name="mode"]:checked').value;
function minutes() {
  const v = document.querySelector('input[name="dur"]:checked').value;
  return v === "custom" ? parseInt($("customMin").value, 10) : parseInt(v, 10);
}
document.querySelectorAll('input[name="dur"]').forEach((el) =>
  el.addEventListener("change", () => ($("customMin").hidden = el.value !== "custom" || !el.checked)));
document.querySelectorAll('input[name="mode"]').forEach((el) =>
  el.addEventListener("change", () => { $("safewordField").hidden = mode() === "timer"; $("lidLine").hidden = mode() === "timer"; $("startErr").textContent = ""; }));

let sess = { phase: "idle" };
let prevPhase = null;
function setLid(el, textEl, s) {
  if (!s.pi_online) { el.className = "lidline offline"; textEl.textContent = "Box offline"; return "offline"; }
  if (s.lid_closed === true) { el.className = "lidline closed"; textEl.textContent = "Lid closed · ready"; return "closed"; }
  if (s.lid_closed === false) { el.className = "lidline open"; textEl.textContent = "Lid open · checking every second"; return "open"; }
  el.className = "lidline"; textEl.textContent = "Lid status unknown"; return "unknown";
}
function renderSession() {
  const s = sess;
  const idle = s.phase === "idle";
  $("startForm").hidden = !idle;
  $("running").hidden = idle;
  setLid($("lidLine"), $("lidText"), s);
  if (!idle) {
    const locked = s.mode === "locked";
    $("lockedControls").hidden = !locked;
    $("timerControls").hidden = locked;
    $("runMode").textContent = locked ? "phone locked" : "timer only";
    const total = (s.planned_min || 1) * 60;
    const rem = s.remaining_s ?? total;
    $("countdown").textContent = s.phase === "locking" ? "--:--" : `${String(Math.floor(rem / 60)).padStart(2, "0")}:${String(rem % 60).padStart(2, "0")}`;
    $("ringFg").style.strokeDashoffset = String(326.7 * (1 - rem / total));
    $("runNote").textContent = s.phase === "locking"
      ? "Locking… waiting for the box to confirm"
      : locked ? `Phone locked until ${new Date(s.until * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : "Stay focused. Your desk is being monitored.";
  }
  if (prevPhase && prevPhase !== "idle" && idle) {
    loadLog();
    $("startErr").textContent = "";
  }
  prevPhase = s.phase;
  if (!$("lidModal").hidden) updateModal();
}
async function refreshSession() {
  const r = await api("/api/session");
  if (r.ok) { sess = r.data; renderSession(); }
}

const REASONS = {
  safeword_too_short: "Safeword needs at least 3 characters.",
  session_running: "A session is already running.",
  bad_minutes: "Duration must be 1–600 minutes.",
};
async function tryStart() {
  const body = { mode: mode(), minutes: minutes() };
  if (body.mode === "locked") body.safeword = $("safeword").value;
  const r = await api("/api/session/start", { method: "POST", body });
  if (r.ok) {
    closeModal($("lidModal"));
    $("safeword").value = "";
    await refreshSession(); loadLog();
    return;
  }
  const reason = r.data?.reason;
  if (reason === "lid_open" || reason === "box_offline") { openLidModal(); return; }
  closeModal($("lidModal"));
  $("startErr").textContent = REASONS[reason] || "Couldn't start the session.";
}
$("startForm").addEventListener("submit", (e) => {
  e.preventDefault();
  if (!Number.isFinite(minutes()) || minutes() < 1) { $("startErr").textContent = REASONS.bad_minutes; return; }
  if (mode() === "locked" && $("safeword").value.length < 3) { $("startErr").textContent = REASONS.safeword_too_short; return; }
  $("startErr").textContent = "";
  tryStart();
});
$("unlockForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const r = await api("/api/session/unlock", { method: "POST", body: { safeword: $("unlockWord").value } });
  $("unlockErr").textContent = r.ok ? "" : r.status === 401 ? "Wrong safeword." : "Couldn't unlock.";
  $("unlockWord").value = "";
  refreshSession(); loadLog();
});
$("stopBtn").addEventListener("click", async () => { await api("/api/session/stop", { method: "POST" }); refreshSession(); loadLog(); });
$("emergencyBtn").addEventListener("click", () => openModal($("emModal")));
$("emCancel").addEventListener("click", () => closeModal($("emModal")));
$("emGo").addEventListener("click", async () => {
  await api("/api/session/emergency", { method: "POST" });
  closeModal($("emModal")); refreshSession(); loadLog();
});

// ---------------- Modals ----------------
let lastFocus = null;
function openModal(m) { lastFocus = document.activeElement; m.hidden = false; m.querySelector("button")?.focus(); }
function closeModal(m) { if (m.hidden) return; m.hidden = true; lastFocus?.focus?.(); }
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { closeModal($("lidModal")); closeModal($("emModal")); }
});
$("mCancel").addEventListener("click", () => closeModal($("lidModal")));
$("mStart").addEventListener("click", tryStart);

let box3d = null;
function openLidModal() {
  openModal($("lidModal"));
  updateModal();
  if (!box3d) box3d = initBox3D($("box3d")).catch((e) => {
    console.error(e);
    $("box3d").querySelector(".fallback").textContent = "3D model could not load";
  });
}
function updateModal() {
  const st = setLid($("mStatus"), $("mStatusText"), sess);
  const offline = st === "offline";
  $("mTitle").textContent = offline ? "The box is offline" : "Close the phone lid first";
  $("mText").textContent = offline
    ? "The server hasn't heard from the box in the last few seconds. Check that it's powered and on Wi-Fi."
    : "Slide the phone lid all the way to the left until it stops. The session starts only when the box sees the lid fully closed.";
  $("mStart").disabled = st !== "closed";
  $("mStart").hidden = offline;
  $("box3d").classList.toggle("closed", st === "closed");
  lidClosed3d = st === "closed";
}

// ---------------- 3D model (team's own STL meshes) ----------------
let lidClosed3d = false;
async function initBox3D(host) {
  const THREE = await import("three");
  const { STLLoader } = await import("three/addons/loaders/STLLoader.js");
  const { OrbitControls } = await import("three/addons/controls/OrbitControls.js");
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  const scene = new THREE.Scene();
  const cam = new THREE.PerspectiveCamera(30, 1, 1, 4000);
  cam.up.set(0, 0, 1); cam.position.set(330, -420, 380);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x667788, 1.2));
  const dl = new THREE.DirectionalLight(0xffffff, 1.6); dl.position.set(150, -250, 400); scene.add(dl);
  const ctl = new OrbitControls(cam, renderer.domElement);
  ctl.target.set(0, 0, 40); ctl.enablePan = false; ctl.enableZoom = false;
  ctl.autoRotate = !reduce; ctl.autoRotateSpeed = 0.9;
  const loader = new STLLoader();
  const load = async (f, color, extra = {}) => {
    const g = await loader.loadAsync("models/" + f);
    g.computeVertexNormals();
    const m = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color, roughness: 0.65, metalness: 0.05, ...extra }));
    scene.add(m); return m;
  };
  const accent = new THREE.Color(tok("--accent"));
  const [, , plid] = await Promise.all([
    load("body.stl", new THREE.Color("#cfd5d2")),
    load("lid.stl", new THREE.Color("#e9ebe8")),
    load("plid.stl", accent, { emissive: accent, emissiveIntensity: 0.15 }),
  ]);
  host.querySelector(".fallback")?.remove();
  host.appendChild(renderer.domElement);
  const size = () => { const w = host.clientWidth, h = host.clientHeight; if (!w || !h) return; renderer.setSize(w, h); cam.aspect = w / h; cam.updateProjectionMatrix(); };
  new ResizeObserver(size).observe(host); size();
  const OPEN = 95; let x = OPEN; const t0 = performance.now();
  renderer.setAnimationLoop((now) => {
    if ($("lidModal").hidden) return;
    const t = (now - t0) / 1000;
    // open: lid nudges toward closed to show the motion; closed: glides shut
    const target = lidClosed3d ? 0 : reduce ? OPEN : OPEN - 55 * (0.5 - 0.5 * Math.cos(t * 2.2));
    x += (target - x) * (lidClosed3d ? 0.12 : 0.25);
    plid.position.x = x;
    plid.material.emissiveIntensity = lidClosed3d ? 0.05 : 0.12 + 0.12 * Math.sin(t * 4);
    ctl.update(); renderer.render(scene, cam);
  });
}

// ---------------- Loops ----------------
await Promise.all([refreshLatest(), refreshSession(), seedLive(), loadHistory(), loadLog()]);
setInterval(refreshLatest, 2000);
setInterval(refreshSession, 1000);
setInterval(loadHistory, 60000);
setInterval(loadLog, 15000);
