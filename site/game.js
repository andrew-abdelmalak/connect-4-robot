// Page side of the demo. Every legality decision and every robot move comes
// from the repository's Python (brain.js runs it in Pyodide); this file only
// draws the board, animates the dispenser, simulates where a token lands, and
// builds the deliberately impossible boards of the cheat menu.

const ROWS = 6;
const COLS = 7;
const USER = 1; // red
const ROBOT = 2; // yellow
// Measured on the robot: share of 20 drops per column that landed where aimed.
const DROP_SUCCESS = [1, 1, 1, 0.95, 0.95, 0.9, 0.85];
const DROP_COUNTS = [20, 20, 20, 19, 19, 18, 17];
// Carriage drive time per column in the Arduino sketch (ms), shown faster here.
const CARRIAGE_MS = [0, 330, 600, 900, 1000, 3000, 4000];
const TRAVEL_SCALE = 0.3;

const $ = (sel) => document.querySelector(sel);
const boardEl = $("#board");
const colsEl = $("#cols");
const statusEl = $("#status");
const carriage = $("#carriage");
const gate1 = $("#gate1");
const gate2 = $("#gate2");
const fixBtn = $("#fix");
const cheat = $("#cheat");
const misdrops = $("#misdrops");
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

let state = null; // GameState.to_dict() from Python
let busy = true;
let pendingFix = null; // expected board after a missed drop
let epoch = 0; // bumped by New game; a flow that sees a newer epoch stops
let lastCol = 3;

// ── worker RPC ────────────────────────────────────────────────────────────
const worker = new Worker("brain.js", { type: "module" });
let nextId = 1;
const waiting = new Map();
const ready = new Promise((resolve, reject) => waiting.set(0, { resolve, reject }));
worker.onmessage = ({ data }) => {
  const w = waiting.get(data.id);
  if (!w) return;
  waiting.delete(data.id);
  data.ok ? w.resolve(data) : w.reject(new Error(data.error));
};
worker.onerror = (e) => {
  for (const w of waiting.values()) w.reject(new Error(e.message || "the worker stopped"));
  waiting.clear();
};
function call(op, ...args) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    waiting.set(id, { resolve, reject });
    worker.postMessage({ id, op, args });
  });
}
const drop = (col, player, board) => call("drop", col, player, board ? JSON.stringify(board) : null).then((r) => r.value);

class Stale extends Error {}
const guard = (e) => { if (e !== epoch) throw new Stale(); };

// ── drawing ───────────────────────────────────────────────────────────────
const cells = [];
for (let i = 0; i < ROWS * COLS; i++) {
  const cell = document.createElement("div");
  cell.className = "cell";
  boardEl.append(cell);
  cells.push(cell);
}
const colButtons = [];
for (let c = 0; c < COLS; c++) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "col-btn";
  b.setAttribute("aria-label", `Drop your red token in column ${c + 1}`);
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
      if (!colButtons[j].disabled) { colButtons[j].focus(); break; }
    }
  } else if (!e.ctrlKey && !e.metaKey && !e.altKey && !e.repeat) {
    const n = Number(e.key);
    if (n >= 1 && n <= COLS) userDrop(n - 1);
  }
});

function draw(board, { dropAt = null, ghost = null } = {}) {
  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      const cell = cells[r * COLS + c];
      const v = board[r][c];
      cell.replaceChildren();
      if (!v) continue;
      const t = document.createElement("span");
      t.className = "token " + (v === USER ? "red" : "yellow");
      if (ghost && ghost[0] === r && ghost[1] === c) t.classList.add("ghost");
      if (dropAt && dropAt[0] === r && dropAt[1] === c && !reduceMotion) {
        t.style.setProperty("--from", `${-(r + 1) * 120}%`);
        t.style.setProperty("--dur", `${260 + r * 50}ms`);
        t.classList.add("falling");
      }
      cell.append(t);
    }
  }
}

// The robot's own messages count columns and rows from 0, top row first.
// Show them with columns 1 to 7 and rows counted from the bottom.
function human(reason) {
  return reason
    .replace(/\bcol (\d)/g, (_, c) => `column ${Number(c) + 1}`)
    .replace(/\b(expected )?row (\d)/g, (_, pre, r) => `${pre || ""}row ${ROWS - Number(r)} from the bottom`)
    .replace(/ Now (user|robot)'s turn\.$/, "");
}
function setGate(gate, s, msg, code = false) {
  gate.dataset.state = s;
  const el = gate.querySelector(".gate-msg");
  if (code) {
    const c = document.createElement("code");
    c.textContent = msg;
    el.replaceChildren(c);
  } else {
    el.textContent = msg;
  }
}
function say(text) { statusEl.textContent = text; }
const wait = (ms) => new Promise((r) => setTimeout(r, reduceMotion ? 0 : ms));

function newChip(before, after) {
  for (let r = 0; r < ROWS; r++)
    for (let c = 0; c < COLS; c++) if (!before[r][c] && after[r][c]) return [r, c];
  return null;
}
function lock(on) {
  busy = on;
  const over = state && (state.winner || state.is_draw);
  const legal = state ? state.legal_cols : [];
  colButtons.forEach((b, c) => { b.disabled = on || over || !legal.includes(c); });
  cheat.querySelectorAll("button").forEach((b) => { b.disabled = on || over; });
  if (!on && colsEl.contains(document.activeElement) === false && document.activeElement === document.body) {
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
    say(`Something went wrong (${err.message}). Press New game to start again.`);
  }
  if (e === epoch && !pendingFix && !finished()) lock(false);
}

// ── game flow ─────────────────────────────────────────────────────────────
async function accept(board, e) {
  const { value } = await call("accept", JSON.stringify(board));
  guard(e);
  state = value.state;
  return value;
}

function userDrop(col) {
  if (!state || state.whose_turn !== "user") return;
  lastCol = col;
  flow(async (e) => {
    cheat.open = false;
    const candidate = await drop(col, USER);
    guard(e);
    if (!candidate) { say("That column is full."); return; }
    draw(candidate, { dropAt: newChip(state.trusted, candidate) });
    await wait(450);
    setGate(gate1, "busy", "Checking the new board");
    const res = await accept(candidate, e);
    setGate(gate1, res.ok ? "pass" : "fail", human(res.reason), true);
    setGate(gate2, "idle", "Only checks the robot's own moves");
    if (!res.ok) { draw(state.trusted); say(`Rejected: ${human(res.reason)}. The robot keeps the last board it trusted.`); return; }
    if (state.winner || state.is_draw) return;
    await robotTurn(e);
  });
}

async function robotTurn(e) {
  const col = state.mm_col;
  say(`The robot looked ahead 5 moves and chose column ${col + 1}.`);
  await moveCarriage(col);
  guard(e);
  const expected = await drop(col, ROBOT);
  guard(e);
  let landed = col;
  if (misdrops.checked && Math.random() > DROP_SUCCESS[col]) landed = col === COLS - 1 ? col - 1 : col + (Math.random() < 0.5 ? -1 : 1);
  if (!state.legal_cols.includes(landed)) landed = col; // neighbour full: the token falls in the aimed column
  const actual = landed === col ? expected : await drop(landed, ROBOT);
  guard(e);
  carriage.dataset.loaded = "false";
  draw(actual, { dropAt: newChip(state.trusted, actual) });
  await wait(550);
  setGate(gate2, "busy", "Camera compares the board with the expected one");
  await wait(500);
  guard(e);
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    pendingFix = expected;
    setGate(gate2, "fail", `Token seen in column ${landed + 1}, expected column ${col + 1}. Robot stopped.`);
    say("Gate 2 caught a missed drop. On the real robot, 3 of 20 column-7 drops in the final stress test slid into column 6. Fix it by hand to continue.");
    fixBtn.hidden = false;
    moveCarriage(0);
    return;
  }
  const res = await accept(expected, e);
  setGate(gate2, res.ok ? "pass" : "fail", res.ok ? "Board matches. Move accepted." : human(res.reason), !res.ok);
  moveCarriage(0);
  if (!state.winner && !state.is_draw) say("Your turn. Drop a red token, or try to cheat.");
}

async function moveCarriage(col) {
  const from = Number(carriage.style.getPropertyValue("--x")) || 0;
  const ms = Math.max(250, Math.abs(CARRIAGE_MS[col] - CARRIAGE_MS[from]) * TRAVEL_SCALE + 300);
  carriage.style.setProperty("--travel", `${ms}ms`);
  carriage.style.setProperty("--x", col);
  if (col !== 0) carriage.dataset.loaded = "true";
  await wait(ms + 150);
}

function finished() {
  if (!state) return false;
  draw(state.trusted);
  if (state.winner) {
    say(state.winner === "Robot" ? "The robot wins this one. Press New game to play again." : "You win! The robot's minimax could not block every threat.");
    lock(true);
    return true;
  }
  if (state.is_draw) { say("Draw: the board is full."); lock(true); return true; }
  return false;
}

fixBtn.addEventListener("click", () => {
  if (!pendingFix) return;
  const board = pendingFix;
  pendingFix = null;
  fixBtn.hidden = true;
  busy = false;
  flow(async (e) => {
    const res = await accept(board, e);
    setGate(gate2, res.ok ? "pass" : "fail", res.ok ? "Operator moved the token. Board matches." : human(res.reason), !res.ok);
    if (!state.winner && !state.is_draw) say("Your turn.");
  });
});

// ── cheats: impossible boards, judged by the real validator ───────────────
cheat.addEventListener("click", (ev) => {
  const kind = ev.target.dataset?.cheat;
  if (!kind || !state || state.whose_turn !== "user") return;
  flow(async (e) => {
    cheat.open = false;
    const t = state.trusted;
    const legal = state.legal_cols;
    let candidate = null;
    let ghost = null;
    if (kind === "two") {
      const [a, b] = legal.slice().sort(() => Math.random() - 0.5);
      const once = await drop(a, USER);
      candidate = (b === undefined ? null : await drop(b, USER, once)) || (await drop(a, USER, once)) || once;
    } else if (kind === "vanish") {
      const chips = [];
      t.forEach((row, r) => row.forEach((v, c) => v && chips.push([r, c])));
      if (!chips.length) { say("No chips to make vanish yet. Play a move first."); return; }
      const [r, c] = chips[Math.floor(Math.random() * chips.length)];
      candidate = t.map((row) => row.slice());
      candidate[r][c] = 0;
    } else if (kind === "float") {
      // Deliberately impossible: a chip one cell above the lowest empty cell.
      const rows = await Promise.all(legal.map((c) => call("lowest", c).then((r) => r.value)));
      const options = legal.filter((_, i) => rows[i] >= 1);
      if (!options.length) { say("No column has room to float a chip."); return; }
      const c = options[Math.floor(Math.random() * options.length)];
      const r = rows[legal.indexOf(c)] - 1;
      candidate = t.map((row) => row.slice());
      candidate[r][c] = USER;
      ghost = [r, c];
    } else if (kind === "robot") {
      if (!state.robot_color || state.robot_color === "None") { say("Play one move first, so the robot knows its colour."); return; }
      candidate = await drop(legal[Math.floor(Math.random() * legal.length)], ROBOT);
    }
    guard(e);
    draw(candidate, { ghost });
    setGate(gate1, "busy", "Checking the new board");
    await wait(700);
    const res = await accept(candidate, e);
    setGate(gate1, res.ok ? "pass" : "fail", human(res.reason), true);
    say(res.ok ? "That one got through." : `Rejected by the robot's validator: ${human(res.reason)}.`);
    await wait(1100);
    guard(e);
    draw(state.trusted);
  });
});

$("#reset").addEventListener("click", async () => {
  epoch++;
  pendingFix = null;
  fixBtn.hidden = true;
  try {
    const { value } = await call("reset");
    state = value;
  } catch (err) {
    say(`The robot's brain is not ready (${err.message}).`);
    return;
  }
  draw(state.trusted);
  moveCarriage(0);
  carriage.dataset.loaded = "false";
  setGate(gate1, "idle", "Waiting for a move");
  setGate(gate2, "idle", "Waiting for the robot");
  say("New game. You play red and go first.");
  lock(false);
});

$("#play-cta").addEventListener("click", () => setTimeout(() => colButtons[3].focus({ preventScroll: true }), 300));

// ── results chart from the measured drop counts ───────────────────────────
const stacks = $("#stacks");
DROP_COUNTS.forEach((hit, c) => {
  const s = document.createElement("div");
  s.className = "stack";
  s.setAttribute("role", "img");
  s.setAttribute("aria-label", `Column ${c + 1}: ${hit} of 20 drops landed`);
  for (let i = 0; i < 20; i++) {
    const chip = document.createElement("span");
    chip.className = "chip" + (i < 20 - hit ? " miss" : "");
    s.append(chip);
  }
  const lbl = document.createElement("span");
  lbl.className = "lbl";
  lbl.textContent = `col ${c + 1}\n${Math.round((hit / 20) * 100)}%`;
  s.append(lbl);
  stacks.append(s);
});

// ── vision demo: the real VisionPipeline on a real photo ──────────────────
// What the photo shows, read by eye (top row first): 1 red, 2 yellow.
const PHOTO_TRUTH = [
  [0, 0, 0, 0, 0, 0, 0],
  [0, 0, 0, 0, 0, 0, 0],
  [0, 0, 0, 1, 0, 0, 0],
  [0, 0, 1, 2, 2, 0, 0],
  [0, 0, 2, 1, 2, 0, 0],
  [0, 2, 1, 1, 2, 2, 0],
];
const miniBoard = (el, board, truth, label) => {
  el.replaceChildren(...board.map((v, i) => {
    const d = document.createElement("span");
    d.className = "m" + v + (v === truth[i] ? "" : " wrong");
    return d;
  }));
  el.setAttribute("aria-label", label);
};
const visionStatus = $("#vision-status");
document.querySelectorAll("[data-vision]").forEach((btn) =>
  btn.addEventListener("click", async () => {
    const mode = btn.dataset.vision;
    const buttons = document.querySelectorAll("[data-vision]");
    buttons.forEach((b) => { b.disabled = true; });
    visionStatus.textContent = "Loading OpenCV and running the robot's vision code";
    try {
      const { value, ms } = await call("vision", mode);
      const truth = PHOTO_TRUTH.flat();
      const right = value.board.flat().filter((v, i) => v === truth[i]).length;
      const picture = (slot, b64, alt) => {
        const img = new Image();
        img.src = "data:image/jpeg;base64," + b64;
        img.alt = alt;
        $(slot).replaceChildren(img);
      };
      picture("#vision-overlay", value.overlay, mode === "auto" ? "Board photo with the corners found by automatic detection, including the legs" : "Board photo with the grid's own corners marked");
      picture("#vision-grid", value.grid, "The warped board with a coloured dot on each hole showing how it was read");
      miniBoard($("#vision-board"), value.board.flat(), truth, `Board as read: ${right} of 42 holes match the photo`);
      $("#vision-score").textContent = `${right} of 42 holes read right${right < 42 ? " (wrong ones circled)" : ""}.`;
      $("#vision-out").hidden = false;
      visionStatus.textContent = `${mode === "auto" ? "Automatic detection" : "Grid corners"}: ${right} of 42 right, in ${Math.round(ms)} ms in your browser.`;
    } catch (err) {
      visionStatus.textContent = `The vision code could not run (${err.message}).`;
    }
    buttons.forEach((b) => { b.disabled = false; });
  }),
);

// ── CLIP experiment: results computed by scripts/eval_clip_cells.py ───────
fetch("clip-cells.json")
  .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.status))))
  .then((d) => {
    const boards = $("#clip-boards");
    for (const [key, label, right] of [["hsv", "Colour thresholds", d.hsv_correct], ["clip", "CLIP, no training", d.clip_correct]]) {
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
    $("#clip-score").textContent = `One photo, ${d.cells} holes, CLIP ViT-B/32 computed offline: ${d.clip_correct} of ${d.cells} on the plain photo, ${d.clip_on_hsv_crops_correct} on the brightened crops the thresholds use.`;
  })
  .catch(() => { $("#clip-score").textContent = "The experiment results did not load."; });

// ── boot ──────────────────────────────────────────────────────────────────
draw(Array.from({ length: ROWS }, () => Array(COLS).fill(0)));
lock(true);
ready
  .then(() => call("reset"))
  .then(({ value }) => {
    state = value;
    say("Ready. You play red and go first: tap a column.");
    lock(false);
  })
  .catch((err) => say(`The robot's brain could not load (${err.message}). Reload to try again.`));
