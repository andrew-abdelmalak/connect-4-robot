// Page side of the demo. Every legality decision, robot move, and board
// reading comes from the repository's Python (brain.js runs it in Pyodide),
// and every motor command comes from the real Arduino sketch (arduino.js runs
// it in avr8js). This file draws the board and the camera picture, moves the
// tokens the motors would move, and follows the robot loop's timing.

const ROWS = 6;
const COLS = 7;
const RED = 1;
const NAMES = { 1: "red", 2: "yellow" };
// Measured on the robot: drops out of 20 per column that landed where aimed.
const DROP_HITS = [20, 20, 20, 19, 19, 18, 17];
// dispenser.ino: carriage drive time from home to each column (ms).
const COLUMN_MS = [0, 330, 600, 900, 1000, 3000, 4000];
// hardware.py: the robot loop's timing (s) and its timeout message.
const ANALYSIS_INTERVAL = 1.0;
const ROBOT_SETTLE_SEC = 2.5;
const VERIFY_POLL_SEC = 0.4;
const VERIFY_TIMEOUT = 10.0;
const TIMEOUT_MSG = "Timed out waiting for verified robot move.";
const WIN_SCORE = 1_000_000; // minimax.py
const TARGET_PULSES = 400; // dispenser.ino: pulses for one token release

const $ = (sel) => document.querySelector(sel);
const rigPlay = $(".rig-play");
const boardEl = $("#board");
const colsEl = $("#cols");
const scoresEl = $("#scores");
const statusEl = $("#status");
const carriage = $("#carriage");
const dispenser = $(".dispenser");
const motorMag = $("#motor-mag");
const motorCar = $("#motor-car");
const fixBtn = $("#fix");
const nextBtn = $("#next");
const cheat = $("#cheat");
const misdrops = $("#misdrops");
const resultEl = $("#result");
const serialEl = $("#serial");
const frame = $("#frame");
const frameCtx = frame.getContext("2d");
const traces = $("#traces");
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

const empty = () => Array.from({ length: ROWS }, () => Array(COLS).fill(0));
const copy = (b) => b.map((row) => row.slice());
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const lowest = (b, c) => {
  for (let r = ROWS - 1; r >= 0; r--) if (!b[r][c]) return r;
  return -1;
};

let state = null; // GameState.to_dict() from Python
let physical = empty(); // what is really on the board
let you = RED;
let busy = true;
let epoch = 0; // bumped by New game; a flow that sees a newer epoch stops
let pendingFix = null;
let lastCol = 3;
let visionOn = false;
let stepMode = false;
let ghost = null;
let tilt = 0;

class Stale extends Error {}
const guard = (e) => { if (e !== epoch) throw new Stale(); };

// ── brain worker RPC ──────────────────────────────────────────────────────
const brain = new Worker("brain.js", { type: "module" });
let nextId = 1;
const waiting = new Map();
const ready = new Promise((resolve, reject) => waiting.set(0, { resolve, reject }));
brain.onmessage = ({ data }) => {
  const w = waiting.get(data.id);
  if (!w) return;
  waiting.delete(data.id);
  data.ok ? w.resolve(data) : w.reject(new Error(data.error));
};
brain.onerror = (e) => {
  for (const w of waiting.values()) w.reject(new Error(e.message || "the worker stopped"));
  waiting.clear();
};
function call(op, ...args) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    waiting.set(id, { resolve, reject });
    brain.postMessage({ id, op, args });
  });
}

// ── Arduino worker: the compiled sketch on an emulated ATmega328P ─────────
const avr = new Worker("arduino.js", { type: "module" });
const ard = { t: 0, waiters: [], samples: [], marks: [], cycleStart: null, move: null, lines: [], dir: 0, failed: null };
function untilSim(t, e) {
  return new Promise((resolve, reject) => {
    if (e !== epoch) return reject(new Stale());
    if (ard.failed) return reject(ard.failed);
    if (ard.t >= t) return resolve();
    ard.waiters.push({ t, resolve, reject, e });
  });
}
function untilLine(prefix, e) {
  return new Promise((resolve, reject) => {
    if (e !== epoch) return reject(new Stale());
    if (ard.failed) return reject(ard.failed);
    const hit = ard.lines.find((l) => l.startsWith(prefix));
    if (hit) return resolve(hit);
    ard.waiters.push({ prefix, resolve, reject, e });
  });
}
function wake(text) {
  ard.waiters = ard.waiters.filter((w) => {
    if (w.e !== epoch) {
      w.reject(new Stale());
      return false;
    }
    if (w.t !== undefined ? ard.t >= w.t : text?.startsWith(w.prefix)) {
      w.resolve(text);
      return false;
    }
    return true;
  });
}
function serialPrint(text, cls) {
  const line = document.createElement("span");
  if (cls) line.className = cls;
  line.textContent = `${(ard.t / 1000).toFixed(2).padStart(6)} s  ${text}\n`;
  serialEl.append(line);
  while (serialEl.childElementCount > 40) serialEl.firstChild.remove();
  serialEl.scrollTop = serialEl.scrollHeight;
}
// Carriage drive time (ms from home) as a position in columns, through the sketch's own table.
function columnAt(pos) {
  for (let c = 1; c < COLS; c++) {
    if (pos <= COLUMN_MS[c]) return c - 1 + (pos - COLUMN_MS[c - 1]) / (COLUMN_MS[c] - COLUMN_MS[c - 1]);
  }
  return COLS - 1;
}
avr.onmessage = ({ data: m }) => {
  if (m.type === "tick") {
    ard.t = m.t;
    carriage.style.setProperty("--x", columnAt(m.pos).toFixed(3));
    $("#enc").textContent = `${m.pulses} pulses`;
    $("#gear").style.setProperty("--turn", `${(m.total * 4.5) % 360}deg`);
    $("#ard-speed").textContent = `Uno, running the compiled sketch at ${m.realtime.toFixed(1)}x real time`;
    if (ard.cycleStart !== null) ard.samples.push(...m.samples);
    const last = m.samples.at(-1); // [t, IN1, IN2, ENA, IN3, IN4, ENB, pulses]
    if (last) {
      motorMag.querySelector("b").textContent = motorMag.dataset.on === "true" ? `PWM ${Math.round(last[6] * 100)}%, ${last[7]} pulses` : "off";
      motorCar.querySelector("b").textContent = motorCar.dataset.on === "true" ? `PWM ${Math.round(last[3] * 100)}%, ${motorCar.dataset.dir}` : "off";
    }
    drawTraces();
    wake();
  } else if (m.type === "line") {
    ard.t = m.t;
    ard.lines.push(m.text);
    if (ard.cycleStart !== null) ard.marks.push([m.t, m.text]);
    serialPrint(m.text);
    onLine(m.text);
    // Between moves the sketch only waits for a byte: pause it to save the visitor's CPU.
    if (m.text === "READY" && !ard.move) avr.postMessage({ op: "idle" });
    wake(m.text);
  } else if (m.type === "sent") {
    serialPrint(`Pi sends "${String.fromCharCode(m.byte)}"`, "tx");
  } else if (m.type === "motor") {
    onMotor(m);
  } else if (m.type === "paused") {
    if (stepMode && m.reason === "line" && ard.move) {
      // After DONE the camera check may still be waiting on robot time: keep the clock running.
      if (ard.lines.at(-1)?.startsWith("DONE")) avr.postMessage({ op: "resume" });
      else offerNext("let the Arduino continue", () => avr.postMessage({ op: "resume" }));
    }
  } else if (m.type === "reset") {
    ard.failed = null;
    ard.t = 0;
    ard.lines = [];
    ard.dir = 0;
    serialEl.replaceChildren();
    serialPrint("Arduino powered on", "tx");
  } else if (m.type === "error") {
    arduinoFailed(m.error);
  }
};
avr.onerror = (e) => arduinoFailed(e.message || "the worker stopped");
// Without the emulator no robot move can finish: fail every waiter so the board unlocks.
function arduinoFailed(why) {
  ard.failed = new Error(`the Arduino emulator stopped: ${why}`);
  for (const w of ard.waiters) w.reject(ard.failed);
  ard.waiters = [];
  setStage("arduino", "fail", ard.failed.message);
}
function onLine(text) {
  const mv = ard.move;
  if (!mv) return;
  const [word, arg] = text.split(" ");
  const msg = {
    BUSY: `BUSY ${arg}: column ${arg} received, releasing one token`,
    MAG: Number(arg) < TARGET_PULSES
      ? `MAG ${arg}: the sketch stopped the magazine early, at ${arg} of ${TARGET_PULSES} pulses. It reads its pulse count while the encoder interrupt can change it, a known issue in the sketch (see the README). The page still drops the token.`
      : `MAG ${arg}: the encoder counted ${arg} pulses, one token released`,
    MOVE: `MOVE ${arg}: carriage driving for ${COLUMN_MS[arg - 1]} ms`,
    DONE: `DONE ${arg}: carriage back home`,
  }[word];
  if (msg) setStage("arduino", word === "DONE" ? "pass" : "busy", msg);
  // Column 1 is home: the carriage does not move, so the token drops at once.
  if (word === "MOVE" && COLUMN_MS[mv.col] === 0) dropFromCarriage(mv);
}
// The magazine's bottom token drops into the carriage waiting under it at home.
function feedCarriage() {
  carriage.dataset.loaded = "true"; // at once: at full speed the carriage can leave before an animation ends
  if (reduceMotion) return;
  $("#magazine span:last-child").animate([{ transform: "translateY(0)" }, { transform: "translateY(180%)", opacity: 0 }], { duration: 220, easing: "cubic-bezier(0.5, 0, 1, 1)" });
}
function onMotor(m) {
  if (m.motor === "magazine") {
    $("#mag").textContent = m.on ? "turning" : "off";
    motorMag.dataset.on = m.on;
    dispenser.dataset.mag = m.on;
    if (!m.on) motorMag.querySelector("b").textContent = "off";
    if (!m.on && ard.move && m.pulses > 0) feedCarriage();
    return;
  }
  motorCar.dataset.on = m.dir !== 0;
  motorCar.dataset.dir = m.dir > 0 ? "out" : "home";
  if (m.dir === 0) motorCar.querySelector("b").textContent = "off";
  $("#car").textContent = m.dir > 0 ? "driving out" : m.dir < 0 ? "driving home" : m.pos > 1 ? `stopped over column ${Math.round(columnAt(m.pos)) + 1}` : "stopped at home";
  // The carriage stops over the column: the token drops through the funnel.
  if (ard.dir > 0 && m.dir === 0 && ard.move) dropFromCarriage(ard.move);
  ard.dir = m.dir;
}
function dropFromCarriage(mv) {
  if (mv.dropped || carriage.dataset.loaded !== "true") return;
  mv.dropped = true;
  carriage.dataset.loaded = "false";
  const r = lowest(physical, mv.landed);
  if (mv.e !== epoch || r < 0) return;
  fall(r, mv.landed, mv.robot, mv.col).then(() => {
    if (mv.e !== epoch) return;
    physical[r][mv.landed] = mv.robot;
    draw();
  });
}

// ── signal traces ─────────────────────────────────────────────────────────
const TRACE_ROWS = [
  ["IN1", 1, "#b9f3c8"],
  ["IN2", 2, "#b9f3c8"],
  ["ENA", 3, "#ffc61a"],
  ["IN3", 4, "#9fc3ff"],
  ["IN4", 5, "#9fc3ff"],
  ["ENB", 6, "#ffc61a"],
  ["ENC", 7, "#ff7483"],
];
let traceFrame = 0;
function drawTraces() {
  if (traceFrame) return;
  traceFrame = requestAnimationFrame(() => {
    traceFrame = 0;
    // Draw at the canvas's on-screen size, so the labels stay readable on a phone.
    const dpr = devicePixelRatio || 1;
    const W = traces.clientWidth || 720;
    const H = 210;
    if (traces.width !== Math.round(W * dpr)) {
      traces.width = Math.round(W * dpr);
      traces.height = Math.round(H * dpr);
    }
    const g = traces.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    const left = 46;
    const top = 34;
    const rowH = (H - top - 6) / TRACE_ROWS.length;
    g.clearRect(0, 0, W, H);
    g.font = "11px ui-monospace, Menlo, monospace";
    g.textBaseline = "middle";
    const t0 = ard.cycleStart ?? ard.t;
    const span = Math.max(6000, ard.t - t0);
    const x = (t) => left + ((t - t0) / span) * (W - left - 8);
    TRACE_ROWS.forEach(([name], i) => {
      g.fillStyle = "#8ea4dc";
      g.fillText(name, 6, top + rowH * (i + 0.5));
    });
    const laneEnd = [-Infinity, -Infinity, -Infinity]; // labels close together take one of three heights
    for (const [t, text] of ard.marks) {
      const lx = Math.min(x(t) + 3, W - g.measureText(text).width - 2);
      let lane = laneEnd.findIndex((end) => lx > end);
      if (lane < 0) lane = 0;
      laneEnd[lane] = lx + g.measureText(text).width + 6;
      g.strokeStyle = "rgba(214, 224, 255, 0.35)";
      g.setLineDash([3, 3]);
      g.beginPath();
      g.moveTo(x(t), top - 4);
      g.lineTo(x(t), H);
      g.stroke();
      g.setLineDash([]);
      g.fillStyle = "#d6e0ff";
      g.fillText(text, lx, 6 + lane * 10);
    }
    const s = ard.samples;
    if (!s.length) return;
    TRACE_ROWS.forEach(([name, k, colour], i) => {
      const y0 = top + rowH * (i + 0.85);
      const hgt = rowH * 0.7;
      g.strokeStyle = colour;
      g.lineWidth = 1.5;
      g.beginPath();
      for (let j = 0; j < s.length; j++) {
        const v = name === "ENC" ? s[j][k] / 400 : s[j][k];
        const px = x(s[j][0]);
        const py = y0 - Math.min(1, v) * hgt;
        j ? g.lineTo(px, py) : g.moveTo(px, py);
      }
      g.stroke();
    });
  });
}

// ── drawing the board ─────────────────────────────────────────────────────
const cells = [];
for (let i = 0; i < ROWS * COLS; i++) {
  const cell = document.createElement("div");
  cell.className = "cell";
  boardEl.append(cell);
  cells.push(cell);
}
const scoreSlots = [];
for (let c = 0; c < COLS; c++) scoreSlots.push(scoresEl.appendChild(document.createElement("span")));
const clearScores = () => {
  scoreSlots.forEach((s) => { s.textContent = ""; s.className = ""; });
  $("#scores-cap").hidden = true;
};
const colButtons = [];
for (let c = 0; c < COLS; c++) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "col-btn";
  b.addEventListener("click", () => userDrop(c));
  colsEl.append(b);
  colButtons.push(b);
}
colsEl.addEventListener("keydown", (e) => {
  const i = colButtons.indexOf(document.activeElement);
  if (i < 0) return;
  if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
    e.preventDefault();
    const step = e.key === "ArrowRight" ? 1 : COLS - 1;
    for (let k = 1, j = (i + step) % COLS; k < COLS; k++, j = (j + step) % COLS) {
      if (!colButtons[j].disabled) {
        colButtons[j].focus();
        break;
      }
    }
  } else if (!e.ctrlKey && !e.metaKey && !e.altKey && !e.repeat) {
    const n = Number(e.key);
    if (n >= 1 && n <= COLS) userDrop(n - 1);
  }
});

function draw() {
  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      const cell = cells[r * COLS + c];
      const v = physical[r][c];
      cell.classList.remove("win");
      cell.replaceChildren();
      if (!v) continue;
      const t = document.createElement("span");
      t.className = "token " + NAMES[v];
      if (ghost && ghost[0] === r && ghost[1] === c) t.classList.add("ghost");
      cell.append(t);
    }
  }
}

// A token falls from the funnel's height into its cell; fromCol is where it starts.
async function fall(row, col, colour, fromCol = col) {
  if (reduceMotion) return;
  const host = rigPlay.getBoundingClientRect();
  const cr = cells[row * COLS + col].getBoundingClientRect();
  const from = cells[row * COLS + fromCol].getBoundingClientRect();
  const size = cr.width * 0.86;
  const endX = cr.left - host.left + cr.width * 0.07;
  const endY = cr.top - host.top + cr.width * 0.07;
  const startY = carriage.getBoundingClientRect().bottom - host.top - size * 0.9;
  const dx = from.left - cr.left;
  const el = document.createElement("span");
  el.className = "falling " + NAMES[colour];
  Object.assign(el.style, { left: `${endX}px`, top: "0px", width: `${size}px`, height: `${size}px` });
  rigPlay.append(el);
  const ms = 280 + Math.sqrt(Math.max(0, endY - startY)) * 18;
  const anim = el.animate(
    [
      { transform: `translate(${dx}px, ${startY}px)` },
      { transform: `translate(${dx * 0.3}px, ${startY + (endY - startY) * 0.55}px)`, offset: 0.45 },
      { transform: `translate(0, ${endY}px)`, offset: 0.86 },
      { transform: `translate(0, ${endY - size * 0.06}px)`, offset: 0.93 },
      { transform: `translate(0, ${endY}px)` },
    ],
    { duration: ms, easing: "cubic-bezier(0.45, 0, 0.9, 0.6)" },
  );
  // A hidden tab freezes animations; the game goes on without waiting for this one.
  await Promise.race([anim.finished, new Promise((r) => setTimeout(r, ms + 300))]);
  el.remove();
}

// ── the camera: a drawn picture of the board, read by the real vision code ─
function drawFrame(board) {
  const g = frameCtx;
  const W = frame.width;
  const H = frame.height;
  g.setTransform(1, 0, 0, 1, 0, 0);
  g.fillStyle = "#cfc6b4";
  g.fillRect(0, 0, W, H);
  const light = g.createLinearGradient(0, 0, W, H);
  light.addColorStop(0, "rgba(255,255,255,0.22)");
  light.addColorStop(1, "rgba(0,0,0,0.14)");
  g.fillStyle = light;
  g.fillRect(0, 0, W, H);
  g.translate(W / 2, H / 2 + 6);
  g.rotate(tilt);
  const bw = 470;
  const bh = 400;
  const cw = bw / COLS;
  const ch = bh / ROWS;
  g.fillStyle = "#1f4fc9";
  g.beginPath();
  g.roundRect(-bw / 2, -bh / 2, bw, bh, 14);
  g.fill();
  const fill = { 0: "#ebe8e1", 1: "#d61f2e", 2: "#f4c21a" };
  const rad = Math.min(cw, ch) * 0.36;
  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      const cx = -bw / 2 + cw * (c + 0.5);
      const cy = -bh / 2 + ch * (r + 0.5);
      g.fillStyle = fill[board[r][c]];
      g.beginPath();
      g.arc(cx, cy, rad, 0, Math.PI * 2);
      g.fill();
      g.strokeStyle = "rgba(0, 0, 30, 0.35)";
      g.lineWidth = 3;
      g.beginPath();
      g.arc(cx, cy, rad - 1.5, Math.PI * 1.05, Math.PI * 1.95);
      g.stroke();
    }
  }
  g.setTransform(1, 0, 0, 1, 0, 0);
  const img = g.getImageData(0, 0, W, H);
  const d = img.data;
  for (let i = 0; i < d.length; i += 4) {
    const n = (Math.random() - 0.5) * 18; // sensor noise
    d[i] += n;
    d[i + 1] += n;
    d[i + 2] += n;
  }
  g.putImageData(img, 0, 0);
}
function miniBoard(el, board, truth, label) {
  el.replaceChildren(...board.map((v, i) => {
    const s = document.createElement("span");
    s.className = "m" + v + (v === truth[i] ? "" : " wrong");
    return s;
  }));
  el.setAttribute("aria-label", label);
}
function picture(b64, alt = "") {
  const img = new Image();
  img.src = "data:image/jpeg;base64," + b64;
  img.alt = alt;
  return img;
}
async function showOverlay(b64) {
  const img = picture(b64);
  await img.decode();
  frameCtx.drawImage(img, 0, 0, frame.width, frame.height);
}
// One camera analysis of the physical board, through VisionPipeline.
async function readBoard(e) {
  const seen = copy(physical);
  drawFrame(seen);
  if (!visionOn) {
    $("#frame-cap").textContent = "Camera picture, drawn by the page. The vision code is still loading.";
    miniBoard($("#cam-board"), seen.flat(), seen.flat(), "Board as drawn");
    return { board: seen, wrong: 0, ms: 0, skipped: true };
  }
  const blob = await new Promise((r) => frame.toBlob(r, "image/jpeg", 0.85));
  const { value, ms } = await call("frame", new Uint8Array(await blob.arrayBuffer()));
  guard(e);
  await showOverlay(value.overlay);
  $("#cam-grid").replaceChildren(picture(value.grid, "The warped board with a dot on each hole showing how it was read"));
  const truth = seen.flat();
  const wrong = value.board.flat().filter((v, i) => v !== truth[i]).length;
  miniBoard($("#cam-board"), value.board.flat(), truth, `Board as read: ${42 - wrong} of 42 holes match`);
  $("#cam-read").textContent = wrong ? `Read ${42 - wrong} of 42 holes right` : "All 42 holes read right";
  $("#frame-cap").textContent = value.detected ? `Camera picture drawn by the page; board found in ${Math.round(ms)} ms` : "Board not found: using the last corners";
  return { board: value.board, wrong, ms, skipped: false };
}

// ── stages, status, step mode ─────────────────────────────────────────────
const stages = Object.fromEntries([...document.querySelectorAll("[data-stage]")].map((el) => [el.dataset.stage, el]));
function setStage(name, s, msg, code = false) {
  const el = stages[name];
  el.dataset.state = s;
  const m = el.querySelector(".stage-msg");
  if (code) {
    const c = document.createElement("code");
    c.textContent = msg;
    m.replaceChildren(c);
  } else {
    m.textContent = msg;
  }
}
function resetStages() {
  setStage("camera", "idle", visionOn ? "Waiting for a move" : "Loading the vision code (OpenCV, about 14 MB)");
  setStage("gate1", "idle", "Waiting for a move");
  setStage("think", "idle", "Scores appear above each column");
  setStage("serial", "idle", "One byte at 9600 baud");
  setStage("arduino", "idle", "Waiting for a column");
  setStage("gate2", "idle", "Checks only the robot's own moves");
}
// The robot's messages count columns and rows from 0, top row first.
// Show them with columns 1 to 7 and rows counted from the bottom.
function human(reason) {
  return reason
    .replace(/\b([Cc])ol(?:umn)? (\d)/g, (_, c, n) => `${c}olumn ${Number(n) + 1}`)
    .replace(/\b(expected )?row (\d)/g, (_, pre, r) => `${pre || ""}row ${ROWS - Number(r)} from the bottom`)
    .replace(/ — /g, ": ")
    .replace(/ Now (user|robot)'s turn\.$/, "");
}
function say(text) {
  statusEl.textContent = text;
}
const wait = (ms) => new Promise((r) => setTimeout(r, reduceMotion ? 0 : ms));

let nextAction = null;
function offerNext(label, action) {
  nextAction = action;
  nextBtn.textContent = `Next: ${label}`;
  nextBtn.hidden = false;
}
nextBtn.addEventListener("click", () => {
  nextBtn.hidden = true;
  const a = nextAction;
  nextAction = null;
  a?.();
});
const stepGate = (label) => (stepMode ? new Promise((resolve) => offerNext(label, resolve)) : null);

function lock(on) {
  busy = on;
  const over = state && (state.winner || state.is_draw);
  const legal = state ? state.legal_cols : [];
  colButtons.forEach((b, c) => {
    b.disabled = on || over || !legal.includes(c);
    b.setAttribute("aria-label", `Drop your ${NAMES[you]} token in column ${c + 1}`);
  });
  cheat.querySelectorAll("button").forEach((b) => { b.disabled = on || over; });
  $("#depth").disabled = on; // the scores must come from the depth the robot searched
  if (!on && document.activeElement === document.body) {
    const target = colButtons[lastCol]?.disabled ? colButtons.find((b) => !b.disabled) : colButtons[lastCol];
    target?.focus({ preventScroll: true });
  }
}

// Run one user-triggered flow: stale flows stop quietly, errors unlock the board.
async function flow(fn) {
  if (busy || !state) return;
  const e = epoch;
  lock(true);
  try {
    await fn(e);
  } catch (err) {
    if (err instanceof Stale) return;
    if (ard.move) {
      ard.move = null;
      avr.postMessage({ op: "idle" });
    }
    say(`Something went wrong (${err.message}). Press New game to start again.`);
  }
  if (e === epoch && !pendingFix && !finished()) lock(false);
}

// ── the loop ──────────────────────────────────────────────────────────────
async function accept(board, e) {
  const { value } = await call("accept", JSON.stringify(board));
  guard(e);
  state = value.state;
  return value;
}

// Steps 1 and 2: the camera reads the board and gate 1 judges what it read.
async function cameraThenGate1(e) {
  setStage("camera", "busy", "Reading the board");
  const seen = await readBoard(e);
  setStage("camera", seen.wrong ? "fail" : "pass", seen.skipped ? "Passed on as drawn (vision code still loading)" : `Read in ${Math.round(seen.ms)} ms`);
  await stepGate("gate 1 checks the board");
  guard(e);
  setStage("gate1", "busy", "Checking the new board");
  const res = await accept(seen.board, e);
  setStage("gate1", res.ok ? "pass" : "fail", human(res.reason), true);
  return res;
}

function userDrop(col) {
  if (!state || state.whose_turn !== "user") return;
  lastCol = col;
  flow(async (e) => {
    cheat.open = false;
    const r = lowest(physical, col);
    if (r < 0) {
      say("That column is full.");
      return;
    }
    clearScores();
    resetStages();
    await fall(r, col, you);
    guard(e);
    physical[r][col] = you;
    draw();
    const res = await cameraThenGate1(e);
    if (!res.ok) {
      say(`Rejected: ${human(res.reason)}. The robot keeps the last board it trusted.`);
      await wait(1100);
      guard(e);
      physical = copy(state.trusted);
      draw();
      return;
    }
    if (state.winner || state.is_draw) return;
    await robotTurn(e, res.nodes);
  });
}

function scoreText(s) {
  if (s >= WIN_SCORE - 100) return "win";
  if (s <= -WIN_SCORE + 100) return "lose";
  return String(s);
}

// Steps 3 to 6: minimax, the serial byte, the Arduino, and the camera check.
async function robotTurn(e, nodes) {
  const robot = 3 - you;
  await stepGate("minimax picks a column");
  guard(e);
  setStage("think", "busy", "Scoring every column");
  const { value: t } = await call("think");
  guard(e);
  const col = t.col;
  scoreSlots.forEach((s, c) => {
    s.textContent = t.scores ? (c in t.scores ? scoreText(t.scores[c]) : "") : c === col ? "here" : "";
    s.className = c === col ? "best" : "";
  });
  $("#scores-cap").hidden = !t.scores;
  if (t.scores) {
    setStage("think", "pass", `Looked ${t.depth} moves ahead: ${nodes.toLocaleString("en")} positions in ${t.time.toFixed(2)} s. Column ${col + 1} scored highest (${scoreText(t.scores[col])}).`);
  } else {
    setStage("think", "pass", `${human(t.reason)}: no search needed.`);
  }
  say(`The robot chose column ${col + 1}. Watch the Arduino drop the token.`);
  await stepGate(`send "${col + 1}" to the Arduino`);
  guard(e);

  const expected = copy(state.trusted);
  expected[lowest(expected, col)][col] = robot;
  let landed = col;
  if (misdrops.checked && Math.random() * 20 >= DROP_HITS[col]) landed = col === COLS - 1 ? col - 1 : col + (Math.random() < 0.5 ? -1 : 1);
  if (lowest(physical, landed) < 0) landed = col; // neighbour full: the token falls in the aimed column
  ard.move = { col, landed, robot, e, dropped: false };
  ard.samples = [];
  ard.marks = [];
  ard.lines = [];
  ard.cycleStart = ard.t;
  const sentAt = ard.t;
  setStage("serial", "pass", `Sent "${col + 1}": one byte, 0x${(48 + col + 1).toString(16)}, at 9600 baud`);
  setStage("arduino", "busy", "Byte received");
  avr.postMessage({ op: "send", byte: 48 + col + 1 });

  const verdict = await verify(e, sentAt, expected);
  if (verdict.ok) {
    setStage("gate2", "pass", `Board matches the expected one, ${((verdict.at - sentAt) / 1000).toFixed(1)} s after the command`);
    setStage("gate1", "busy", "Checking the robot's board");
    const res = await accept(verdict.board, e);
    setStage("gate1", res.ok ? "pass" : "fail", human(res.reason), true);
    await untilLine("DONE", e);
    ard.move = null;
    avr.postMessage({ op: "idle" });
    if (!state.winner && !state.is_draw) say(`Your turn. Drop a ${NAMES[you]} token, or try to cheat.`);
    return;
  }
  setStage("gate2", "fail", landed === col ? TIMEOUT_MSG : `${TIMEOUT_MSG} The camera saw the token in column ${landed + 1}, not column ${col + 1}.`);
  await untilLine("DONE", e);
  ard.move = null;
  avr.postMessage({ op: "idle" });
  pendingFix = { col, landed, robot };
  fixBtn.hidden = false;
  say("Gate 2 caught a missed drop and stopped the robot. On the real robot, 3 of 20 column-7 drops slid into column 6. Fix it by hand to continue.");
}

// Gate 2, as RobotController runs it: the camera thread analyses a frame every
// ANALYSIS_INTERVAL; from ROBOT_SETTLE_SEC after the command, the latest reading
// is compared with the expected board every VERIFY_POLL_SEC until VERIFY_TIMEOUT.
async function verify(e, sentAt, expected) {
  let latest = null;
  let inflight = null;
  let done = false;
  (async () => {
    for (let k = 1; !done; k++) {
      await untilSim(sentAt + k * ANALYSIS_INTERVAL * 1000, e);
      if (done) return;
      setStage("camera", "busy", `Analysing one frame every ${ANALYSIS_INTERVAL} s`);
      inflight = readBoard(e);
      latest = (await inflight).board;
      inflight = null;
      if (!done) setStage("camera", "pass", `Frame read ${((ard.t - sentAt) / 1000).toFixed(1)} s after the command`);
    }
  })().catch((err) => {
    if (!(err instanceof Stale)) setStage("camera", "fail", `The vision code failed (${err.message})`);
  });
  for (let t = ROBOT_SETTLE_SEC; t <= VERIFY_TIMEOUT + 1e-9; t += VERIFY_POLL_SEC) {
    await untilSim(sentAt + t * 1000, e);
    // The emulator can outrun the browser's vision code; wait for the frame it is reading.
    if (inflight) await inflight.catch(() => {});
    guard(e);
    setStage("gate2", "busy", `Waiting for the expected board: ${t.toFixed(1)} of ${VERIFY_TIMEOUT} s`);
    if (latest && same(latest, expected)) {
      done = true;
      return { ok: true, board: latest, at: ard.t };
    }
  }
  done = true;
  return { ok: false };
}

const DIRS = [[0, 1], [1, 0], [1, 1], [1, -1]];
function winLine(b, p) {
  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      for (const [dr, dc] of DIRS) {
        const line = [0, 1, 2, 3].map((i) => [r + dr * i, c + dc * i]);
        if (line.every(([y, x]) => y >= 0 && y < ROWS && x >= 0 && x < COLS && b[y][x] === p)) return line;
      }
    }
  }
  return [];
}
function celebrate(colour, text) {
  draw();
  for (const [r, c] of winLine(physical, colour)) cells[r * COLS + c].classList.add("win");
  $("#result-text").textContent = text;
  resultEl.hidden = false;
  if (reduceMotion) return;
  const host = rigPlay.getBoundingClientRect();
  for (let i = 0; i < 40; i++) {
    const s = document.createElement("span");
    s.className = "confetti";
    s.style.background = `var(--${i % 3 ? NAMES[colour] : "on-blue"})`;
    s.style.left = `${Math.random() * host.width}px`;
    s.style.top = "0px";
    rigPlay.append(s);
    s.animate(
      [
        { transform: "translateY(-20px) rotate(0)" },
        { transform: `translate(${(Math.random() - 0.5) * 120}px, ${host.height * (0.6 + Math.random() * 0.4)}px) rotate(${Math.random() * 720}deg)`, opacity: 0 },
      ],
      { duration: 1400 + Math.random() * 1400, delay: Math.random() * 400, easing: "cubic-bezier(0.2, 0.6, 0.4, 1)", fill: "backwards" },
    ).finished.then(() => s.remove());
  }
}
function finished() {
  if (!state) return false;
  if (state.winner) {
    const depth = $("#depth").value;
    if (state.winner === "Robot") {
      say("The robot wins this one. Press Play again for a rematch.");
      celebrate(3 - you, "Four in a row for the robot.");
    } else {
      say(`You win! You beat minimax looking ${depth} moves ahead.`);
      celebrate(you, depth === "5" ? "You beat the robot at full strength!" : "You win!");
    }
    lock(true);
    return true;
  }
  if (state.is_draw) {
    say("Draw: the board is full.");
    $("#result-text").textContent = "A draw.";
    resultEl.hidden = false;
    lock(true);
    return true;
  }
  return false;
}

fixBtn.addEventListener("click", () => {
  if (!pendingFix) return;
  const { col, landed, robot } = pendingFix;
  pendingFix = null;
  fixBtn.hidden = true;
  busy = false;
  flow(async (e) => {
    // The operator lifts the token out of the wrong column and drops it in the right one.
    physical[lowest(physical, landed) + 1][landed] = 0;
    physical[lowest(physical, col)][col] = robot;
    draw();
    say("The operator moved the token. The camera reads the board and gate 1 checks it, as the dashboard's Accept button did.");
    const res = await cameraThenGate1(e);
    if (!res.ok) {
      physical[lowest(physical, col) + 1][col] = 0;
      physical[lowest(physical, landed)][landed] = robot;
      draw();
      pendingFix = { col, landed, robot };
      fixBtn.hidden = false;
      say(`Gate 1 rejected the fixed board: ${human(res.reason)}. Try the fix again.`);
      return;
    }
    setStage("gate2", "pass", "The operator fixed the missed drop by hand; gate 1 accepted the board");
    if (!state.winner && !state.is_draw) say(`Your turn. Drop a ${NAMES[you]} token.`);
  });
});

// ── cheats: impossible boards, read by the camera and judged by the real validator
cheat.addEventListener("click", (ev) => {
  const kind = ev.target.dataset?.cheat;
  if (!kind || !state || state.whose_turn !== "user") return;
  flow(async (e) => {
    cheat.open = false;
    resetStages();
    clearScores();
    const legal = state.legal_cols;
    const board = copy(state.trusted);
    const put = (c, p) => {
      const r = lowest(board, c);
      if (r >= 0) board[r][c] = p;
    };
    if (kind === "two") {
      const [a, b] = legal.slice().sort(() => Math.random() - 0.5);
      put(a, you);
      put(b ?? a, you);
    } else if (kind === "vanish") {
      const chips = [];
      board.forEach((row, r) => row.forEach((v, c) => v && chips.push([r, c])));
      if (!chips.length) {
        say("No chips to make vanish yet. Play a move first.");
        return;
      }
      const [r, c] = chips[Math.floor(Math.random() * chips.length)];
      board[r][c] = 0;
    } else if (kind === "float") {
      // Deliberately impossible: a chip one cell above the lowest empty cell.
      const options = legal.filter((c) => lowest(board, c) >= 1);
      if (!options.length) {
        say("No column has room to float a chip.");
        return;
      }
      const c = options[Math.floor(Math.random() * options.length)];
      const r = lowest(board, c) - 1;
      board[r][c] = you;
      ghost = [r, c];
    } else if (kind === "robot") {
      if (state.robot_color === "None") {
        say("Play one move first, so the robot knows its colour.");
        return;
      }
      put(legal[Math.floor(Math.random() * legal.length)], 3 - you);
    }
    physical = board;
    draw();
    const res = await cameraThenGate1(e);
    say(res.ok ? "That one got through." : `Rejected by the robot's validator: ${human(res.reason)}.`);
    await wait(1400);
    guard(e);
    ghost = null;
    physical = copy(state.trusted);
    draw();
  });
});

// ── new game and settings ─────────────────────────────────────────────────
async function newGame() {
  epoch++;
  lock(true);
  wake();
  pendingFix = null;
  nextAction = null;
  ghost = null;
  Object.assign(ard, { move: null, cycleStart: null, samples: [], marks: [] });
  fixBtn.hidden = true;
  nextBtn.hidden = true;
  resultEl.hidden = true;
  you = Number(document.querySelector('[name="colour"]:checked').value);
  tilt = (Math.random() - 0.5) * 0.05;
  physical = empty();
  draw();
  clearScores();
  carriage.dataset.loaded = "false";
  dispenser.style.setProperty("--token", `var(--${NAMES[3 - you]})`);
  avr.postMessage({ op: "boot" });
  try {
    const { value } = await call("reset");
    state = value;
    await call("depth", Number($("#depth").value));
    if (visionOn) await call("camera_reset");
  } catch (err) {
    say(`The robot's brain is not ready (${err.message}).`);
    return false;
  }
  resetStages();
  drawFrame(physical);
  say(`New game. You play ${NAMES[you]} and go first, as on the robot.`);
  lock(false);
  return true;
}
$("#reset").addEventListener("click", newGame);
$("#again").addEventListener("click", newGame);
document.querySelectorAll('[name="colour"]').forEach((r) => r.addEventListener("change", () => { if (state) newGame(); }));
$("#depth").addEventListener("change", (ev) => call("depth", Number(ev.target.value)).catch(() => {}));
$("#speed").addEventListener("change", (ev) => avr.postMessage({ op: "speed", value: Number(ev.target.value) }));
$("#step").addEventListener("change", (ev) => {
  stepMode = ev.target.checked;
  avr.postMessage({ op: "step", value: stepMode });
  if (!stepMode && nextAction) nextBtn.click();
});

// The real photo: the same code on a frame from our demo video.
const PHOTO_TRUTH = [
  [0, 0, 0, 0, 0, 0, 0],
  [0, 0, 0, 0, 0, 0, 0],
  [0, 0, 0, 1, 0, 0, 0],
  [0, 0, 1, 2, 2, 0, 0],
  [0, 0, 2, 1, 2, 0, 0],
  [0, 2, 1, 1, 2, 2, 0],
].flat();
$("#photo").addEventListener("click", async (ev) => {
  const btn = ev.target;
  btn.disabled = true;
  const out = $("#photo-out");
  out.textContent = "Running the camera code on the photo";
  try {
    const auto = (await call("vision", "auto")).value;
    const grid = (await call("vision", "grid")).value;
    const right = (b) => b.flat().filter((v, i) => v === PHOTO_TRUTH[i]).length;
    await showOverlay(auto.overlay);
    $("#cam-grid").replaceChildren(picture(auto.grid, "The warped photo with a dot on each hole showing how it was read"));
    miniBoard($("#cam-board"), auto.board.flat(), PHOTO_TRUTH, `Photo as read: ${right(auto.board)} of 42 holes right`);
    $("#frame-cap").textContent = "A frame from our demo video, with the corners the code found";
    $("#cam-read").textContent = `${right(auto.board)} of 42 right (wrong ones ringed)`;
    out.textContent = `Automatic detection reads ${right(auto.board)} of 42: the photo shows the board's blue legs, which join the frame. With the grid's own corners, ${right(grid.board)} of 42.`;
  } catch (err) {
    out.textContent = `The camera code could not run (${err.message}).`;
  }
  btn.disabled = false;
});

// ── Watch it play: muted, it plays while on screen, unless the visitor asked for
// reduced motion or paused it; a visitor's pause sticks.
const film = $("#watch-video");
const filmBtn = $("#watch-toggle");
let filmPausedByVisitor = false;
const showFilm = (playing) => {
  filmBtn.dataset.playing = playing;
  filmBtn.textContent = playing ? "Pause" : "Play";
};
film.addEventListener("play", () => showFilm(true));
film.addEventListener("pause", () => showFilm(false));
filmBtn.addEventListener("click", () => {
  filmPausedByVisitor = !film.paused;
  if (film.paused) film.play().catch(() => {});
  else film.pause();
});
if (!reduceMotion && "IntersectionObserver" in window) {
  new IntersectionObserver(([entry]) => {
    if (!entry.isIntersecting) film.pause();
    else if (!filmPausedByVisitor) film.play().catch(() => {});
  }, { threshold: 0.4 }).observe(film);
}

// ── results chart from the measured drop counts ───────────────────────────
$("#drop-bars").replaceChildren(...DROP_HITS.map((hit, c) => {
  const row = document.createElement("div");
  if (hit < 20) row.className = "short";
  const dt = document.createElement("dt");
  dt.textContent = `Column ${c + 1}`;
  const dd = document.createElement("dd");
  const bar = document.createElement("span");
  bar.style.setProperty("--v", (hit / 20) * 100);
  dd.append(bar, `${hit} of 20 (${(hit / 20) * 100}%)`);
  row.append(dt, dd);
  return row;
}));

// ── CLIP experiment: results computed by scripts/eval_clip_cells.py ───────
fetch("clip-cells.json")
  .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.status))))
  .then((d) => {
    const boards = $("#clip-boards");
    for (const [key, label, right] of [["hsv", "Robot's colour rules", d.hsv_correct], ["clip", "CLIP, never trained on our board", d.clip_correct]]) {
      const fig = document.createElement("figure");
      const mini = document.createElement("div");
      mini.className = "mini";
      mini.setAttribute("role", "img");
      miniBoard(mini, d[key], d.truth, `${label}: ${right} of ${d.cells} holes read right`);
      const cap = document.createElement("figcaption");
      cap.textContent = `${label}: ${right} of ${d.cells} right`;
      fig.append(mini, cap);
      boards.append(fig);
    }
    $("#clip-score").textContent = `One photo, ${d.cells} holes, a small sample computed offline with CLIP ViT-B/32: ${d.clip_correct} of ${d.cells} on the plain photo, ${d.clip_on_hsv_crops_correct} on the brightened crops the colour rules use.`;
  })
  .catch(() => { $("#clip-score").textContent = "The experiment results did not load."; });

// ── boot ──────────────────────────────────────────────────────────────────
draw();
drawFrame(physical);
lock(true);
resetStages();
ready
  .then(newGame) // also picks up settings the browser restored or the visitor changed while loading
  .then((ok) => {
    if (!ok) return;
    say("Ready. You go first: tap a column.");
    call("vision_load")
      .then(() => {
        visionOn = true;
        if (stages.camera.dataset.state === "idle") setStage("camera", "idle", "Waiting for a move");
      })
      .catch((err) => setStage("camera", "fail", `The vision code could not load (${err.message}). The page passes boards on as drawn.`));
  })
  .catch((err) => say(`The robot's brain could not load (${err.message}). Reload to try again.`));
