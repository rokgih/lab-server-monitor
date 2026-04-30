const REFRESH_MS = 30_000;
const STALE_MIN = 15;          // mark a master "stale" if its snapshot is older
const TZ = "Asia/Seoul";

function fmtKst(iso) {
  // sv-SE locale gives "YYYY-MM-DD HH:MM:SS" — same shape as ISO without the T.
  return new Date(iso).toLocaleString("sv-SE", { timeZone: TZ });
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const btn = document.getElementById("theme-toggle");
  if (btn) btn.textContent = theme === "dark" ? "☀" : "🌙";
  localStorage.setItem("theme", theme);
}

async function loadIndex() {
  const r = await fetch("manifest.json?_=" + Date.now());
  if (!r.ok) throw new Error("manifest.json missing — has the deploy workflow run?");
  const m = await r.json();
  // Tolerate two manifest formats: legacy ["a.json", ...] or
  // new [{name:"a.json", committed_at:"..."}].
  m.files = (m.files || []).map(f =>
    typeof f === "string" ? { name: f, committed_at: null } : f
  );
  return m;
}

async function loadMaster(entry) {
  const r = await fetch(`data/${entry.name}?_=` + Date.now());
  if (!r.ok) throw new Error(`failed to load ${entry.name}: ${r.status}`);
  const m = await r.json();
  m._committed_at = entry.committed_at || null;
  return m;
}

function fmt(n) { return n == null ? "–" : n.toLocaleString("en-US"); }
function fmtBytes(b) {
  if (b == null) return "–";
  const gb = b / 1024 / 1024 / 1024;
  return gb >= 1 ? gb.toFixed(1) + " G" : (b / 1024 / 1024).toFixed(0) + " M";
}
function ageMin(iso) {
  return (Date.now() - new Date(iso).getTime()) / 60_000;
}

function barClass(pct) {
  if (pct == null) return "";
  if (pct >= 80) return "hot";
  if (pct >= 40) return "mid";
  return "";
}

function bar(pct) {
  const cls = barClass(pct);
  const w = Math.max(0, Math.min(100, pct ?? 0));
  return `<div class="bar"><span class="${cls}" style="width:${w}%"></span></div>`;
}

function renderGpu(g) {
  const utilPct = g["utilization.gpu"] ?? g.utilization ?? 0;
  const memUsed = g["memory.used"] ?? 0;
  const memTotal = g["memory.total"] ?? 0;
  const memPct = memTotal > 0 ? (memUsed / memTotal * 100) : 0;
  const procs = (g.processes || []).map(p => {
    const u = p.username || "?";
    const m = p.gpu_memory_usage ?? 0;
    return `<span title="${p.command || ""}">${u}: ${m}M</span>`;
  }).join("");
  return `
    <div class="gpu">
      <div class="gpu-head">
        <span class="name">[${g.index}] ${g.name || "GPU"}</span>
        <span class="temp">${g["temperature.gpu"] ?? "–"}°C</span>
      </div>
      <div class="metric">
        <span class="lbl">util</span>${bar(utilPct)}<span class="val">${utilPct}%</span>
      </div>
      <div class="metric">
        <span class="lbl">mem</span>${bar(memPct)}<span class="val">${memUsed}/${memTotal}M</span>
      </div>
      ${procs ? `<div class="gpu-procs">${procs}</div>` : ""}
    </div>`;
}

function renderNode(n) {
  if (n.error) {
    return `
      <div class="node unreachable">
        <div class="node-head">
          <span class="host${n.is_master ? " master" : ""}">${n.host}</span>
          <span class="desc">${n.description || ""}</span>
        </div>
        <div class="warn">unreachable: ${n.error}</div>
      </div>`;
  }
  const cpu = n.cpu_percent ?? 0;
  const mem = n.mem_percent ?? 0;
  const gpus = n.gpu?.gpus || [];
  const gpuBlock = gpus.length
    ? `<div class="gpus">${gpus.map(renderGpu).join("")}</div>`
    : (n.gpu_error
        ? `<div class="warn">${n.gpu_error}</div>`
        : "");
  return `
    <div class="node">
      <div class="node-head">
        <span class="host${n.is_master ? " master" : ""}">${n.host}</span>
        <span class="desc">${n.description || ""}</span>
      </div>
      <div class="metric">
        <span class="lbl">CPU</span>${bar(cpu)}<span class="val">${cpu.toFixed(0)}% · ${n.cpu_count || "?"}c</span>
      </div>
      <div class="metric">
        <span class="lbl">MEM</span>${bar(mem)}<span class="val">${fmtBytes(n.mem_used)}/${fmtBytes(n.mem_total)}</span>
      </div>
      ${gpuBlock}
    </div>`;
}

function renderMaster(m) {
  // Trust the git commit time (set by GitHub) for staleness; fall back to the
  // master's recorded time when running without git (local dev).
  const truthIso = m._committed_at || m.generated_at;
  const age = ageMin(truthIso);
  const stale = age > STALE_MIN;
  const stamp = `pushed ${age.toFixed(1)} min ago`;

  // Detect clock skew between the master's clock and GitHub's commit time.
  let skewBadge = "";
  if (m._committed_at && m.generated_at) {
    const skewSec = (new Date(m._committed_at).getTime()
                   - new Date(m.generated_at).getTime()) / 1000;
    if (Math.abs(skewSec) > 120) {
      const dir = skewSec > 0 ? "behind" : "ahead";
      const min = Math.abs(skewSec / 60).toFixed(0);
      skewBadge = ` <span class="skew" title="Master's clock is off vs git commit time">⏰ clock ${dir} ${min}m</span>`;
    }
  }

  return `
    <section class="master">
      <h2>${m.master_name}
        <span class="stamp${stale ? " stale" : ""}">${stamp}${stale ? " — stale!" : ""}</span>
        ${skewBadge}
      </h2>
      <div class="nodes">${m.nodes.map(renderNode).join("")}</div>
    </section>`;
}

async function refresh() {
  const root = document.getElementById("masters");
  try {
    const manifest = await loadIndex();
    const masters = await Promise.all(manifest.files.map(entry =>
      loadMaster(entry).catch(err => ({
        master_name: entry.name, generated_at: 0, nodes: [], _err: err.message,
      }))
    ));
    masters.sort((a, b) => (a.master_name || "").localeCompare(b.master_name || ""));
    root.innerHTML = masters.map(m => m._err
      ? `<section class="master"><h2>${m.master_name} <span class="stamp stale">load error: ${m._err}</span></h2></section>`
      : renderMaster(m)
    ).join("");
    const newest = masters.reduce((acc, m) => {
      const t = new Date(m._committed_at || m.generated_at || 0).getTime();
      return Math.max(acc, t);
    }, 0);
    document.getElementById("generated").textContent =
      "Most recent push: " + (newest ? fmtKst(newest) + " KST" : "n/a");
  } catch (e) {
    root.innerHTML = `<div class="warn">Error: ${e.message}</div>`;
  }
}

// Theme toggle: re-applies stored theme (also set inline in <head>) and wires
// the click handler.
applyTheme(localStorage.getItem("theme") || "dark");
document.getElementById("theme-toggle").addEventListener("click", () => {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
});

refresh();
setInterval(refresh, REFRESH_MS);
