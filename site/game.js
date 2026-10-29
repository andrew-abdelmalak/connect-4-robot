// Page side of the demo. Every legality decision, robot move, and board
// reading comes from the repository's Python (brain.js runs it in Pyodide),
// and every motor command comes from the real Arduino sketch (arduino.js runs
// it in avr8js). This file draws the board and the camera picture, moves the
// tokens the motors would move, and follows the robot loop's timing.

const ROWS = 6;
const COLS = 7;
const RED = 1;
const NAMES = { 1: "red", 2: "yellow" };
// dispenser.ino (supplement, Table II): carriage drive time from home to each column (ms).
const COLUMN_MS = [0, 330, 600, 900, 1200, 1500, 1800];
// hardware.py: the robot loop's timing (s) and its timeout message.
const ANALYSIS_INTERVAL = 1.0;
const ROBOT_SETTLE_SEC = 2.5;
const VERIFY_POLL_SEC = 0.4;
const VERIFY_TIMEOUT = 10.0;
const TIMEOUT_MSG = "Timed out waiting for verified robot move.";
const WIN_SCORE = 1_000_000; // minimax.py
const TARGET_PULSES = 430; // dispenser.ino: pulses for one token release
const ROLL_COLS_PER_S = 8; // measured from our demo video: the token rolls along the rail
const COUNTS_PER_TURN = 400; // supplement: encoder counts per turn of the motor disk
// The dispenser drawing is on the board's grid: 100 units a hole, 16 a gap.
const MECH_X0 = 16;
const MECH_REST_X = 820; // the rod's tip at rest: the rail's right end, past column 1
const MECH_PITCH = 116;

const $ = (sel) => document.querySelector(sel);
const rigPlay = $(".rig-play");
const boardEl = $("#board");
const colsEl = $("#cols");
const scoresEl = $("#scores");
const statusEl = $("#status");
const mech = $("#mech");
const rod = $("#mx-rod");
const rollLine = $("#mx-roll"); // the rod's top, where the token rolls
const dispenser = $(".dispenser");
const nextBtn = $("#next");
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
let lastCol = 3;
let keyboardPlay = false; // focus follows the board only for keyboard players
let visionOn = false;
let stepMode = false;
let tilt = 0;

class Stale extends Error {}
const guard = (e) => { if (e !== epoch) throw new Stale(); };

// ── brain worker RPC ──────────────────────────────────────────────────────
const brain = new Worker("brain.js", { type: "module" });
let nextId = 1;
const waiting = new Map();
const ready = new Promise((resolve, reject) => waiting.set(0, { resolve, reject }));
brain.onmessage = ({ data }) => {
  if (data.progress) return bootProgress(data);
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
    // At rest the rod waits out of the board, past the storage end at the right,
    // and every hole is open. Driving it slides its tip over columns 1, 2, ... in turn.
    rod.setAttribute("x", (MECH_REST_X - columnAt(m.pos) * MECH_PITCH).toFixed(1));
    $("#mx-disk").setAttribute("transform", `translate(152 112) rotate(${((m.total / COUNTS_PER_TURN) * 360).toFixed(1)})`);
    $("#enc").textContent = `${m.pulses} pulses`;
    $("#gear").style.setProperty("--turn", `${(m.total * 4.5) % 360}deg`);
    $("#ard-speed").textContent = `Uno, sketch at ${m.realtime.toFixed(1)}x real time`;
    if (ard.cycleStart !== null) ard.samples.push(...m.samples);
    const last = m.samples.at(-1); // [t, IN1, IN2, ENA, IN3, IN4, ENB, pulses]
    if (last) {
      if (ard.magOn) $("#mag").textContent = `turning, PWM ${Math.round(last[6] * 100)}%`;
      if (ard.dir) $("#car").textContent = `driving ${ard.dir > 0 ? "out" : "home"}, PWM ${Math.round(last[3] * 100)}%`;
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
    if (m.text === "READY" && !ard.booted) {
      ard.booted = true;
      bootLog("Arduino Uno: the sketch printed READY", "ok");
    }
    wake(m.text);
  } else if (m.type === "sent") {
    serialPrint(`Pi sends "${String.fromCharCode(m.byte)}"`, "tx");
  } else if (m.type === "motor") {
    onMotor(m);
  } else if (m.type === "paused") {
    if (stepMode && m.reason === "line" && ard.move) {
      // After DONE the camera check may still be waiting on robot time: keep the clock running.
      if (ard.lines.at(-1)?.startsWith("DONE")) {
        if (!holds) avr.postMessage({ op: "resume" });
      } else {
        ard.stepWait = true;
        offerNext("let the Arduino continue", () => {
          ard.stepWait = false;
          if (!holds) avr.postMessage({ op: "resume" });
        });
      }
    }
  } else if (m.type === "reset") {
    ard.failed = null;
    ard.t = 0;
    ard.lines = [];
    ard.dir = 0;
    mech.dataset.phase = "";
    serialEl.replaceChildren();
    serialPrint("Arduino powered on", "tx");
    if (!ard.booted) bootLog("Arduino Uno: dispenser.hex on an emulated ATmega328P, 16 MHz");
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
    BUSY: arg === "1"
      ? "BUSY 1: column 1 received. Column 1 sits under the chute, so the rod stays out; it holds 2 s"
      : `BUSY ${arg}: column ${arg} received. The rod slides out for ${COLUMN_MS[arg - 1]} ms to cover column${arg === "2" ? " 1" : `s 1 to ${arg - 1}`}, then holds 2 s`,
    MAG_PULSES: `MAG_PULSES ${arg}: the encoder counted ${arg} pulses, one token released. It rolls over the rod to the first open hole`,
    DONE: `DONE ${arg}: the rod is back out of the board, every hole open`,
  }[word];
  if (msg) setStage("arduino", word === "DONE" ? "pass" : "busy", msg);
  if (word === "DONE") mech.dataset.phase = "";
}
// The magazine: the robot's 21 tokens (one Connect-4 set), up to 5 in view,
// lying flat in the storage tube. Each release takes the bottom one out.
const magazineEl = $("#mx-stack");
const MAGAZINE_SHOWN = 5;
let magazineLeft = 21;
const SVG_NS = "http://www.w3.org/2000/svg";
for (let c = 0; c < COLS; c++) {
  const x = 828 - (MECH_X0 + c * MECH_PITCH + 7) - 86; // column 1 at the right
  const slot = document.createElementNS(SVG_NS, "rect");
  for (const [k, v] of Object.entries({ x, y: 230, width: 86, height: 16, fill: "#9db2ec" })) slot.setAttribute(k, v);
  $("#mx-slots").append(slot);
  const num = document.createElementNS(SVG_NS, "text");
  for (const [k, v] of Object.entries({ x: x + 43, y: 224 })) num.setAttribute(k, v);
  num.textContent = c + 1;
  $("#mx-nums").append(num);
}
function drawMagazine() {
  magazineEl.replaceChildren(...Array.from({ length: Math.min(MAGAZINE_SHOWN, magazineLeft) }, (_, i) => {
    const t = document.createElementNS(SVG_NS, "rect");
    for (const [k, v] of Object.entries({ class: "flat", x: 23, y: 98 - i * 20, width: 86, height: 18, rx: 9 })) t.setAttribute(k, v);
    return t;
  }));
}
const settle = (anim, ms) => Promise.race([anim.finished, new Promise((r) => setTimeout(r, ms))]).catch(() => {});
// The rod already covers the columns before the target: the released token
// rolls along it from the storage end and drops through the first open hole.
async function releaseToken(mv) {
  dropToken(mv);
  magazineLeft = Math.max(0, magazineLeft - 1);
  const bottom = magazineEl.firstElementChild;
  if (!bottom || reduceMotion) return drawMagazine();
  await settle(bottom.animate([{ transform: "none" }, { transform: "translateY(60px)", opacity: 0 }], { duration: 220, easing: "cubic-bezier(0.5, 0, 1, 1)" }), 300);
  drawMagazine();
  for (const token of magazineEl.children) {
    token.animate([{ transform: "translateY(-20px)" }, { transform: "none" }], { duration: 260, easing: "cubic-bezier(0.3, 0, 0.6, 1)" });
  }
}
function onMotor(m) {
  if (m.motor === "magazine") {
    $("#mag").textContent = m.on ? "turning" : "off";
    ard.magOn = m.on;
    if (ard.move) mech.dataset.phase = m.on ? "release" : "roll";
    if (!m.on && ard.move && m.pulses > 0) releaseToken(ard.move);
    return;
  }
  if (ard.move) mech.dataset.phase = m.dir > 0 ? "cover" : m.dir < 0 ? "back" : ard.dir > 0 ? "hold" : "";
  const covered = Math.round(columnAt(m.pos)); // columns 1 to this one are under the rod
  $("#car").textContent = m.dir > 0 ? "driving out" : m.dir < 0 ? "driving home" : covered > 0 ? `stopped over column${covered > 1 ? `s 1 to ${covered}` : " 1"}` : "stopped at home";
  ard.dir = m.dir;
}
function dropToken(mv) {
  if (mv.dropped) return;
  mv.dropped = true;
  const r = lowest(physical, mv.col);
  if (mv.e !== epoch || r < 0) return;
  // The chip runs ahead of the drawing at full speed: hold it while the token
  // rolls and drops, so the rod never closes over a token still on the rail.
  // Its clock pauses too, so the timings it prints stay the sketch's own.
  holdAvr();
  fall(r, mv.col, mv.robot, { roll: true }).then(() => {
    if (mv.e === epoch) {
      physical[r][mv.col] = mv.robot;
      draw();
    }
    releaseAvr();
  });
}
// While the page draws a falling token or reads a camera frame, the emulated
// Uno waits, so its clock keeps the robot's timing on a slow device. A step-mode
// pause holds it too, until the visitor presses Next.
let holds = 0;
function holdAvr() {
  holds++;
  avr.postMessage({ op: "idle" });
}
function releaseAvr() {
  holds = Math.max(0, holds - 1);
  if (!holds && ard.move && !ard.stepWait) avr.postMessage({ op: "resume" });
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
    const H = traces.clientHeight || 210;
    if (traces.width !== Math.round(W * dpr) || traces.height !== Math.round(H * dpr)) {
      traces.width = Math.round(W * dpr);
      traces.height = Math.round(H * dpr);
    }
    const g = traces.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    const pad = 12; // margin on every side, so no label touches the edge
    const left = pad + 36;
    const top = pad + 36; // three lanes of event labels above the plot
    const rowH = (H - top - pad) / TRACE_ROWS.length;
    g.clearRect(0, 0, W, H);
    g.font = `${Math.min(11, Math.floor(rowH) - 1)}px ui-monospace, Menlo, monospace`; // the lane names never touch
    g.textBaseline = "middle";
    const t0 = ard.cycleStart ?? ard.t;
    const span = Math.max(6000, ard.t - t0);
    const x = (t) => left + ((t - t0) / span) * (W - left - pad);
    TRACE_ROWS.forEach(([name], i) => {
      g.fillStyle = "#8ea4dc";
      g.fillText(name, pad, top + rowH * (i + 0.5));
    });
    const laneEnd = [-Infinity, -Infinity, -Infinity]; // labels close together take one of three heights
    for (const [t, text] of ard.marks) {
      const lx = Math.min(x(t) + 4, W - g.measureText(text).width - pad);
      let lane = laneEnd.findIndex((end) => lx > end);
      if (lane < 0) lane = 0;
      laneEnd[lane] = lx + g.measureText(text).width + 6;
      g.strokeStyle = "rgba(214, 224, 255, 0.35)";
      g.setLineDash([3, 3]);
      g.beginPath();
      g.moveTo(x(t), top - 4);
      g.lineTo(x(t), H - pad / 2);
      g.stroke();
      g.setLineDash([]);
      g.fillStyle = "#d6e0ff";
      g.fillText(text, lx, pad + 2 + lane * 12);
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
colsEl.addEventListener("pointerdown", () => { keyboardPlay = false; });
colsEl.addEventListener("keydown", (e) => {
  keyboardPlay = true;
  const i = colButtons.indexOf(document.activeElement);
  if (i < 0) return;
  if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
    e.preventDefault();
    const step = e.key === "ArrowLeft" ? 1 : COLS - 1; // column 1 is at the right
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
      cell.append(t);
    }
  }
  labelColumns();
}
// The board is hidden from screen readers, so each column's button says what it holds.
function labelColumns() {
  colButtons.forEach((b, c) => {
    const stack = [];
    for (let r = ROWS - 1; r >= 0 && physical[r][c]; r--) stack.push(NAMES[physical[r][c]]);
    const held = stack.length === ROWS ? "full" : stack.length ? `${stack.length} of ${ROWS} filled, from the bottom: ${stack.join(", ")}` : "empty";
    b.setAttribute("aria-label", `Column ${c + 1}, ${held}. Drop your ${NAMES[you]} token`);
  });
}

// A token falls into its cell through an open hole in the rail. The robot's
// first rolls along the rod from under the chute, over column 1 at the right.
async function fall(row, col, colour, { roll = false } = {}) {
  if (reduceMotion) return;
  const host = rigPlay.getBoundingClientRect();
  const cr = cells[row * COLS + col].getBoundingClientRect();
  const size = cr.width * 0.86;
  const endX = cr.left - host.left + cr.width * 0.07;
  const endY = cr.top - host.top + cr.width * 0.07;
  const startY = rollLine.getBoundingClientRect().top - host.top - size;
  const el = document.createElement("span");
  el.className = "falling " + NAMES[colour];
  Object.assign(el.style, { left: `${endX}px`, top: "0px", width: `${size}px`, height: `${size}px` });
  rigPlay.append(el);
  const fallMs = 280 + Math.sqrt(Math.max(0, endY - startY)) * 18;
  const rollCols = col; // from the storage end, over column 1, to the target
  const rollMs = roll ? (rollCols / ROLL_COLS_PER_S) * 1000 : 0;
  const pitch = cells[1].getBoundingClientRect().left - cells[0].getBoundingClientRect().left;
  const at = (ms) => (rollMs + ms) / (rollMs + fallMs);
  const down = [
    { transform: `translate(0, ${startY}px)`, offset: at(0) },
    { transform: `translate(0, ${endY}px)`, offset: at(fallMs * 0.86), easing: "ease-out" },
    { transform: `translate(0, ${endY - size * 0.06}px)`, offset: at(fallMs * 0.93) },
    { transform: `translate(0, ${endY}px)`, offset: 1 },
  ];
  down[0].easing = "cubic-bezier(0.55, 0, 1, 0.45)";
  // from under the chute, over column 1, one turn per 3.14 token widths (the pitch
  // is negative on the board drawn right to left, so the roll goes leftward)
  const turn = Math.round(((rollCols * pitch) / (Math.PI * size)) * 360);
  const frames = rollMs ? [{ transform: `translate(${-rollCols * pitch}px, ${startY}px) rotate(${-turn}deg)`, offset: 0, easing: "cubic-bezier(0.2, 0.6, 0.6, 1)" }, ...down] : down;
  const anim = el.animate(frames, { duration: rollMs + fallMs });
  // A hidden tab freezes animations; the game goes on without waiting for this one.
  await Promise.race([anim.finished, new Promise((r) => setTimeout(r, rollMs + fallMs + 300))]);
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
// A fresh board in every camera view, so nothing from the last game stays on screen.
function clearCamera() {
  drawFrame(physical);
  $("#cam-grid").replaceChildren();
  const blank = physical.flat();
  miniBoard($("#cam-board"), blank, blank, "Board as read: no frame read yet");
  $("#cam-read").textContent = "Board as read";
  $("#frame-cap").textContent = "Camera picture, drawn by the page";
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
    if (lowData && !visionFailed) loadVision();
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
  follow();
}
function resetStages() {
  setStage("camera", "idle", visionOn ? "Waiting for a move" : visionLoading || !lowData ? "Loading the vision code (OpenCV, about 14 MB)" : "The vision code (about 14 MB) loads after your first move");
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
    b.disabled = !booting && (on || over || !legal.includes(c));
  });
  labelColumns();
  $("#depth").disabled = on; // the scores must come from the depth the robot searched
  if (!on && keyboardPlay && document.activeElement === document.body) {
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
  if (e === epoch && !finished()) lock(false);
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
  if (booting) return queueMove(col);
  if (!state || state.whose_turn !== "user") return;
  lastCol = col;
  flow(async (e) => {
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
  const cap = $("#scores-cap");
  cap.hidden = false;
  if (t.scores) {
    cap.textContent = "The robot's score for each column. The highlighted one is its pick.";
    setStage("think", "pass", `Looked ${t.depth} moves ahead: ${nodes.toLocaleString("en")} positions in ${t.time.toFixed(2)} s. Column ${col + 1} scored highest (${scoreText(t.scores[col])}).`);
  } else {
    cap.textContent = `${human(t.reason)}: no search needed.`; // a win or a forced block, checked before minimax
    setStage("think", "pass", cap.textContent);
  }
  say(`The robot chose column ${col + 1}. Watch the Arduino drop the token.`);
  await stepGate(`send "${col + 1}" to the Arduino`);
  guard(e);

  const expected = copy(state.trusted);
  expected[lowest(expected, col)][col] = robot;
  ard.move = { col, robot, e, dropped: false };
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
    if (!state.winner && !state.is_draw) say(`Your turn. Drop a ${NAMES[you]} token.`);
    return;
  }
  // As on the robot: no matching board in time puts the controller in its error state until Reset.
  setStage("gate2", "fail", TIMEOUT_MSG);
  await untilLine("DONE", e);
  ard.move = null;
  avr.postMessage({ op: "idle" });
  say("Gate 2 saw no matching board in time and stopped the robot. Press New game to start again.");
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
      // On the robot a frame takes about 50 ms; a slow browser takes longer, so the Uno waits for it.
      holdAvr();
      inflight = readBoard(e).finally(releaseAvr);
      latest = (await inflight).board;
      inflight = null;
      if (!done) setStage("camera", "pass", `Frame read ${((ard.t - sentAt) / 1000).toFixed(1)} s after the command`);
    }
  })().catch((err) => {
    if (!(err instanceof Stale)) setStage("camera", "fail", `The vision code failed (${err.message})`);
  });
  // The check is over: a frame still being read must not leave step 1 looking busy.
  const finish = (verdict) => {
    done = true;
    if (stages.camera.dataset.state === "busy") setStage("camera", "pass", `Frames read every ${ANALYSIS_INTERVAL} s until the check ended`);
    return verdict;
  };
  for (let t = ROBOT_SETTLE_SEC; t <= VERIFY_TIMEOUT + 1e-9; t += VERIFY_POLL_SEC) {
    await untilSim(sentAt + t * 1000, e);
    // The emulator can outrun the browser's vision code; wait for the frame it is reading.
    if (inflight) await inflight.catch(() => {});
    guard(e);
    setStage("gate2", "busy", `Waiting for the expected board: ${t.toFixed(1)} of ${VERIFY_TIMEOUT} s`);
    if (latest && same(latest, expected)) return finish({ ok: true, board: latest, at: sentAt + t * 1000 }); // the poll that matched
  }
  return finish({ ok: false });
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
// Where the four in a row is, in words, for the status line: columns 1 to 7, rows from the bottom.
function describeLine(line) {
  const [[r0, c0], [r1, c1]] = [line[0], line.at(-1)];
  const row = (r) => ROWS - r;
  if (r0 === r1) return `across row ${row(r0)}, columns ${Math.min(c0, c1) + 1} to ${Math.max(c0, c1) + 1}`;
  if (c0 === c1) return `up column ${c0 + 1}, rows ${row(r1)} to ${row(r0)}`;
  return `diagonally from column ${c0 + 1}, row ${row(r0)} to column ${c1 + 1}, row ${row(r1)}`;
}
function celebrate(colour, text) {
  draw();
  const line = winLine(physical, colour);
  for (const [r, c] of line) cells[r * COLS + c].classList.add("win");
  $("#result-text").textContent = text;
  resultEl.toggleAttribute("data-after-win", !reduceMotion);
  resultEl.hidden = false;
  if (keyboardPlay) $("#again").focus({ preventScroll: true });
  if (reduceMotion) return line;
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
  return line;
}
function finished() {
  if (!state) return false;
  if (state.winner) {
    const depth = $("#depth").value;
    if (state.winner === "Robot") {
      const line = celebrate(3 - you, "Four in a row for the robot.");
      say(`The robot wins this one, ${describeLine(line)}. Press Play again for a rematch.`);
    } else {
      const line = celebrate(you, depth === "5" ? "You beat the robot at full strength!" : "You win!");
      say(`You win, ${describeLine(line)}! You beat minimax looking ${depth} move${depth === "1" ? "" : "s"} ahead.`);
    }
    lock(true);
    return true;
  }
  if (state.is_draw) {
    say("Draw: the board is full.");
    $("#result-text").textContent = "A draw.";
    resultEl.removeAttribute("data-after-win");
    resultEl.hidden = false;
    lock(true);
    return true;
  }
  return false;
}

// ── new game and settings ─────────────────────────────────────────────────
async function newGame() {
  epoch++;
  closeConfirm();
  lock(true);
  wake();
  nextAction = null;
  Object.assign(ard, { move: null, cycleStart: null, samples: [], marks: [], stepWait: false });
  holds = 0;
  nextBtn.hidden = true;
  resultEl.hidden = true;
  you = Number(document.querySelector('[name="colour"]:checked').value);
  tilt = (Math.random() - 0.5) * 0.05;
  physical = empty();
  draw();
  clearScores();
  dispenser.style.setProperty("--token", `var(--${NAMES[3 - you]})`);
  magazineLeft = 21;
  drawMagazine();
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
  clearCamera();
  say(`New game. You play ${NAMES[you]} and go first, as on the robot.`);
  lock(false);
  return true;
}
// Starting over in the middle of a game asks first, on the board itself:
// New game, or a new colour, opens the question; Keep playing (or Esc) closes it.
const confirmEl = $("#confirm");
let onConfirm = null;
let confirmBack = null; // where the focus goes back to
const midGame = () => physical.some((row) => row.some(Boolean)) && !(state && (state.winner || state.is_draw));
function askStartOver(then, back = $("#reset")) {
  if (!midGame()) return then();
  onConfirm = then;
  confirmEl.hidden = false;
  confirmBack = back;
  $("#confirm-no").focus(); // the safe answer has the focus
}
function closeConfirm() {
  confirmEl.hidden = true;
  onConfirm = null;
}
$("#confirm-yes").addEventListener("click", () => {
  const then = onConfirm;
  closeConfirm();
  then?.();
});
$("#confirm-no").addEventListener("click", () => {
  closeConfirm();
  confirmBack?.focus();
});
confirmEl.addEventListener("keydown", (e) => e.key === "Escape" && $("#confirm-no").click());
$("#reset").addEventListener("click", () => askStartOver(newGame));

// ── setting notes: open on hover or focus, stay open on click or tap ──────
// Esc or a click outside closes a note (the browser's own popover behaviour).
for (const btn of document.querySelectorAll(".info")) {
  const note = document.getElementById(btn.getAttribute("aria-controls"));
  let pinned = false;
  let timer = 0;
  const show = () => {
    clearTimeout(timer);
    if (!note.matches(":popover-open")) note.showPopover();
  };
  const hideSoon = () => {
    clearTimeout(timer);
    timer = setTimeout(() => !pinned && note.matches(":popover-open") && note.hidePopover(), 160);
  };
  btn.addEventListener("click", () => {
    pinned = !(pinned && note.matches(":popover-open"));
    if (pinned) show();
    else note.hidePopover();
  });
  btn.addEventListener("pointerenter", (e) => e.pointerType === "mouse" && show());
  btn.addEventListener("pointerleave", (e) => e.pointerType === "mouse" && hideSoon());
  note.addEventListener("pointerenter", () => clearTimeout(timer));
  note.addEventListener("pointerleave", hideSoon);
  btn.addEventListener("focus", show);
  btn.addEventListener("blur", hideSoon);
  note.addEventListener("toggle", (e) => {
    btn.setAttribute("aria-expanded", String(e.newState === "open"));
    if (e.newState === "closed") pinned = false;
  });
}
$("#again").addEventListener("click", newGame);
// A new colour starts a new game: mid-game the radio waits for the answer.
let colourShown = $('[name="colour"]:checked').value;
document.querySelectorAll('[name="colour"]').forEach((r) =>
  r.addEventListener("change", () => {
    if (!state) return (colourShown = r.value);
    const picked = r;
    document.querySelector(`[name="colour"][value="${colourShown}"]`).checked = true;
    askStartOver(() => {
      picked.checked = true;
      colourShown = picked.value;
      newGame();
    }, picked);
  }),
);
$("#depth").addEventListener("change", (ev) => call("depth", Number(ev.target.value)).catch(() => {}));
$("#speed").addEventListener("change", (ev) => avr.postMessage({ op: "speed", value: Number(ev.target.value) }));
$("#step").addEventListener("change", (ev) => {
  stepMode = ev.target.checked;
  avr.postMessage({ op: "step", value: stepMode });
  if (!stepMode && nextAction) nextBtn.click();
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
  new IntersectionObserver((entries) => {
    const entry = entries.at(-1); // the newest, if several arrive together
    if (!entry.isIntersecting) film.pause();
    else if (!filmPausedByVisitor && !lowData) film.play().catch(() => {}); // data saver: only on Play
  }, { threshold: 0.4 }).observe(film);
}

// ── loading: the board first, then the rest in reading order ─────────────
// On data saver or 2G/3G the 14 MB vision code waits for the first move and
// nothing is fetched ahead.
const net = navigator.connection;
const lowData = Boolean(net && (net.saveData || /(^|-)[23]g$/.test(net.effectiveType || "")));
let visionLoading = null;
let visionFailed = false; // a failed load is not retried on every camera frame
function loadVision() {
  if (!visionLoading) bootLog("Camera: load NumPy and OpenCV, about 14 MB");
  visionLoading ??= call("vision_load")
    .then(() => {
      visionOn = true;
      bootLog("Camera: OpenCV ready, every frame is read with the robot's vision code", "ok");
      if (stages.camera.dataset.state === "idle") setStage("camera", "idle", "Waiting for a move");
    })
    .catch((err) => {
      visionLoading = null;
      visionFailed = true;
      bootLog(`Camera: the vision code could not load (${err.message})`);
      setStage("camera", "fail", `The vision code could not load (${err.message}). The page passes boards on as drawn.`);
    });
  return visionLoading;
}
// Warm the browser cache for what a reader opens next: the PDF viewer, the
// paper, and the search index. One file at a time, only while the tab is visible.
const READING_ORDER = [
  "pdfjs/web/viewer.html", "pdfjs/web/viewer.css", "pdfjs/web/viewer.mjs", "pdfjs/build/pdf.mjs", "pdfjs/build/pdf.worker.mjs",
  "docs/Vision-Based-Connect-4-Robot.pdf", "search-index.json",
];
async function prefetchReading() {
  for (const url of READING_ORDER) {
    while (document.hidden) await new Promise((r) => document.addEventListener("visibilitychange", r, { once: true }));
    await fetch(url, { priority: "low" }).then((r) => r.blob()).catch(() => {});
  }
}

// ── the step in focus and the instrument it needs ─────────────────────────
// While steps overlap (the camera keeps reading frames as the carriage moves),
// the most telling busy step keeps the focus. Once nothing is busy, a failed
// step takes it, so a rejected board stays on screen, else the furthest one done.
// Only a busy or failed step moves the window: the camera check often passes
// while the carriage is still driving home, and the window stays on the Arduino.
const PRIORITY = ["arduino", "gate2", "serial", "think", "gate1", "camera"];
const TAB_FOR = { camera: "camera", gate2: "camera", serial: "arduino", arduino: "arduino" };
const tabs = [...document.querySelectorAll("[data-tab]")];
const focusCap = $("#focus-cap");
let focused = null;
let tabPicked = false; // a tab the visitor picked holds until their next move
function showTab(name, { focus = false } = {}) {
  for (const t of tabs) {
    const on = t.dataset.tab === name;
    t.setAttribute("aria-selected", String(on));
    t.tabIndex = on ? 0 : -1;
    if (on) {
      delete t.dataset.live;
      if (focus) t.focus();
    }
  }
  document.querySelectorAll("[data-panel]").forEach((p) => p.toggleAttribute("data-off", p.dataset.panel !== name));
  if (name === "arduino") drawTraces(); // the canvas may have changed size while hidden
}
tabs.forEach((t, i) => {
  t.addEventListener("click", () => {
    tabPicked = true;
    showTab(t.dataset.tab);
  });
  t.addEventListener("keydown", (e) => {
    const step = { ArrowRight: 1, ArrowLeft: tabs.length - 1 }[e.key];
    if (!step) return;
    e.preventDefault();
    tabPicked = true;
    showTab(tabs[(i + step) % tabs.length].dataset.tab, { focus: true });
  });
});
function follow() {
  const all = Object.values(stages);
  const busyNow = all.filter((s) => s.dataset.state === "busy");
  if (stages.camera.dataset.state === "busy" && busyNow.length === 1) tabPicked = false; // a new move
  const done = all.filter((s) => s.dataset.state !== "idle");
  const next = PRIORITY.map((k) => busyNow.find((s) => s.dataset.stage === k)).find(Boolean) ?? done.find((s) => s.dataset.state === "fail") ?? done.at(-1) ?? stages.camera; // a fresh board starts at step 1
  focused?.removeAttribute("data-focus");
  focused = next;
  next.setAttribute("data-focus", "");
  focusCap.querySelector("b").textContent = next.dataset.long;
  focusCap.querySelector("span").replaceChildren(...next.querySelector(".stage-msg").cloneNode(true).childNodes);
  focusCap.dataset.state = next.dataset.state;
  const tab = TAB_FOR[next.dataset.stage];
  if (!tab || booting || !["busy", "fail"].includes(next.dataset.state)) return;
  if (!tabPicked) showTab(tab);
  else if (next.dataset.state === "busy") tabs.find((t) => t.dataset.tab === tab && t.getAttribute("aria-selected") !== "true")?.setAttribute("data-live", "");
}

// ── powering on: Python downloads, the holes light, a move can wait ───────
// The holes light from the bottom row up as the runtime's bytes arrive; the
// last few wait for Python to start and the robot's code to load.
let booting = true;
let queued = null;
const bootStart = performance.now();
const bootLogEl = $("#boot-log");
const power = $("#power");
const LIT_ORDER = Array.from({ length: ROWS * COLS }, (_, k) => (ROWS - 1 - Math.floor(k / COLS)) * COLS + (k % COLS));
const MB = (b) => (b / 1e6).toFixed(1);
function bootLog(text, cls) {
  const line = document.createElement("span");
  if (cls) line.className = cls;
  line.textContent = `${((performance.now() - bootStart) / 1000).toFixed(2).padStart(6)} s  ${text}\n`;
  bootLogEl.append(line);
  bootLogEl.scrollTop = bootLogEl.scrollHeight;
}
function lightHoles(n) {
  LIT_ORDER.forEach((i, k) => cells[i].classList.toggle("lit", k < n));
}
let logged = 0;
function bootProgress(d) {
  if (d.progress === "download") {
    const pct = Math.floor((d.done / d.total) * 100);
    lightHoles(Math.floor((d.done / d.total) * 36));
    $("#power-pct").textContent = `Powering on: ${pct}%`;
    $("#power-step").textContent = `Downloading Python for the browser: ${MB(d.done)} of ${MB(d.total)} MB`;
    if (pct >= logged + 25) {
      logged = pct - (pct % 25);
      bootLog(`Brain: downloaded ${MB(d.done)} of ${MB(d.total)} MB`);
    }
  } else if (d.progress === "start") {
    lightHoles(39);
    $("#power-step").textContent = "Starting Python";
    bootLog("Brain: start Python (Pyodide, WebAssembly) in a background worker");
  } else if (d.progress === "code") {
    lightHoles(41);
    $("#power-step").textContent = "Loading the robot's code";
    bootLog("Brain: import connect4_robot (board, minimax, game_state), the files the robot ran");
  }
}
function queueMove(col) {
  queued = col;
  draw(); // clears an earlier pick
  const t = document.createElement("span");
  t.className = `token queued ${NAMES[Number($('[name="colour"]:checked').value)]}`; // the colour picked so far
  cells[col].append(t); // over the column, in its top hole
  say(`Column ${col + 1} picked. Your token drops as soon as the robot is ready.`);
}

// ── boot ──────────────────────────────────────────────────────────────────
draw();
drawMagazine(); // the robot's tokens wait in the storage from the start
clearCamera();
lock(true);
resetStages();
bootLog("Brain: fetch pyodide.asm.wasm and python_stdlib.zip, 12.1 MB");
avr.postMessage({ op: "boot" }); // the Arduino powers on while Python downloads
ready
  .then(() => {
    booting = false;
    bootLog("Brain: GameState() ready", "ok");
    return newGame(); // also picks up settings the browser restored or the visitor changed while loading
  })
  .then((ok) => {
    if (!ok) return;
    lightHoles(ROWS * COLS);
    delete boardEl.dataset.boot;
    power.hidden = true;
    showTab("camera");
    if (queued !== null) userDrop(queued);
    else say("Ready. You go first: tap a column.");
    if (lowData) setStage("camera", "idle", "On a slow or metered connection, the vision code (about 14 MB) loads after your first move");
    else loadVision().then(prefetchReading);
  })
  .catch((err) => {
    $("#power-step").textContent = "The robot's brain could not load";
    bootLog(`Brain: could not load (${err.message})`);
    say(`The robot's brain could not load (${err.message}). Reload to try again.`);
  });
