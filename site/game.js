// Page side of the demo. Every legality decision and every robot move comes
// from the repository's Python (brain.js runs it in Pyodide); this file only
// draws the board, animates the dispenser, and simulates where a token lands.

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
function call(op, ...args) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    waiting.set(id, { resolve, reject });
    worker.postMessage({ id, op, args });
  });
}

// ── drawing ───────────────────────────────────────────────────────────────
const cells = [];
for (let r = 0; r < ROWS; r++) {
  for (let c = 0; c < COLS; c++) {
    const cell = document.createElement("div");
    cell.className = "cell";
    boardEl.append(cell);
    cells.push(cell);
  }
}
const colButtons = [];
for (let c = 0; c < COLS; c++) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "col-btn";
  b.dataset.col = c;
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
    colButtons[(i + (e.key === "ArrowRight" ? 1 : COLS - 1)) % COLS].focus();
  }
});
document.addEventListener("keydown", (e) => {
  if (e.target.closest("input, textarea")) return;
  const n = Number(e.key);
  if (n >= 1 && n <= COLS && document.activeElement?.closest("#play")) userDrop(n - 1);
});

function draw(board, { dropAt = null, ghost = null } = {}) {
  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      const cell = cells[r * COLS + c];
      const v = board[r][c];
      cell.replaceChildren();
      cell.classList.remove("win");
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

function setGate(gate, s, msg) {
  gate.dataset.state = s;
  gate.querySelector(".gate-msg").innerHTML = msg;
}
const quote = (reason) => `<code>${reason.replace(/[<>&]/g, "")}</code>`;
function say(text) { statusEl.textContent = text; }
const wait = (ms) => new Promise((r) => setTimeout(r, reduceMotion ? 0 : ms));

function newChip(before, after) {
  for (let r = 0; r < ROWS; r++)
    for (let c = 0; c < COLS; c++) if (!before[r][c] && after[r][c]) return [r, c];
  return null;
}
const drop = (col, player, board) => call("drop", col, player, board ? JSON.stringify(board) : null).then((r) => r.value);
function lock(on) {
  busy = on;
  const over = state && (state.winner || state.is_draw);
  const legal = state ? state.legal_cols : [];
  colButtons.forEach((b, c) => { b.disabled = on || over || !legal.includes(c); });
  cheat.querySelectorAll("button").forEach((b) => { b.disabled = on || over; });
}

// ── game flow ─────────────────────────────────────────────────────────────
async function accept(board) {
  const { value } = await call("accept", JSON.stringify(board));
  state = value.state;
  return value;
}

async function userDrop(col) {
  if (busy || !state || state.whose_turn !== "user") return;
  lock(true);
  cheat.open = false;
  const candidate = await drop(col, USER);
  if (!candidate) { say("That column is full."); lock(false); return; }
  draw(candidate, { dropAt: newChip(state.trusted, candidate) });
  await wait(450);
  setGate(gate1, "busy", "Checking the new board");
  const res = await accept(candidate);
  setGate(gate1, res.ok ? "pass" : "fail", quote(res.reason));
  setGate(gate2, "idle", "Only checks the robot's own moves");
  if (!res.ok) { draw(state.trusted); say("Rejected. The robot keeps the last board it trusted."); lock(false); return; }
  if (finished()) return;
  await robotTurn();
}

async function robotTurn() {
  const col = state.mm_col;
  say(`The robot looked ahead 5 moves and chose column ${col + 1}. ${state.mm_reason}.`);
  await moveCarriage(col);
  const expected = await drop(col, ROBOT);
  let landed = col;
  if (misdrops.checked && Math.random() > DROP_SUCCESS[col]) landed = col === COLS - 1 ? col - 1 : col + (Math.random() < 0.5 ? -1 : 1);
  if (!state.legal_cols.includes(landed)) landed = col; // neighbour full: the token falls in the aimed column
  const actual = landed === col ? expected : await drop(landed, ROBOT);
  carriage.dataset.loaded = "false";
  draw(actual, { dropAt: newChip(state.trusted, actual) });
  await wait(550);
  setGate(gate2, "busy", "Camera compares the board with the expected one");
  await wait(500);
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    pendingFix = expected;
    setGate(gate2, "fail", `Token seen in column ${landed + 1}, expected column ${col + 1}. Robot stopped.`);
    say("Gate 2 caught a missed drop, as on the real robot (3 of 20 column-7 drops slid into column 6). Fix it by hand to continue.");
    fixBtn.hidden = false;
    lock(true);
    moveCarriage(0);
    return;
  }
  const res = await accept(expected);
  setGate(gate2, res.ok ? "pass" : "fail", res.ok ? "Board matches. Move accepted." : quote(res.reason));
  moveCarriage(0);
  if (finished()) return;
  say("Your turn. Drop a red token, or try to cheat.");
  lock(false);
}

async function moveCarriage(col) {
  const ms = Math.max(250, Math.abs(CARRIAGE_MS[col] - CARRIAGE_MS[Number(carriage.style.getPropertyValue("--x")) || 0]) * TRAVEL_SCALE + 300);
  carriage.style.setProperty("--travel", `${ms}ms`);
  carriage.style.setProperty("--x", col);
  if (col !== 0 || carriage.dataset.loaded !== "false") carriage.dataset.loaded = "true";
  await wait(ms + 150);
}

function finished() {
  draw(state.trusted);
  if (state.winner) {
    say(state.winner === "Robot" ? "The robot wins this one. New game?" : "You win! The robot's minimax could not block every threat.");
    lock(true);
    return true;
  }
  if (state.is_draw) { say("Draw: the board is full."); lock(true); return true; }
  return false;
}

fixBtn.addEventListener("click", async () => {
  if (!pendingFix) return;
  const res = await accept(pendingFix);
  pendingFix = null;
  fixBtn.hidden = true;
  setGate(gate2, res.ok ? "pass" : "fail", res.ok ? "Operator moved the token. Board matches." : quote(res.reason));
  if (finished()) return;
  say("Your turn.");
  lock(false);
});

// ── cheats: impossible boards, judged by the real validator ───────────────
cheat.addEventListener("click", async (e) => {
  const kind = e.target.dataset?.cheat;
  if (!kind || busy || !state || state.whose_turn !== "user") return;
  cheat.open = false;
  lock(true);
  const t = state.trusted;
  const legal = state.legal_cols;
  let candidate = null;
  let ghost = null;
  if (kind === "two") {
    const [a, b] = legal.slice().sort(() => Math.random() - 0.5);
    candidate = await drop(b ?? a, USER, await drop(a, USER));
  } else if (kind === "vanish") {
    const chips = [];
    t.forEach((row, r) => row.forEach((v, c) => v && chips.push([r, c])));
    if (!chips.length) { say("No chips to make vanish yet. Play a move first."); lock(false); return; }
    const [r, c] = chips[Math.floor(Math.random() * chips.length)];
    candidate = t.map((row) => row.slice());
    candidate[r][c] = 0;
  } else if (kind === "float") {
    // Deliberately impossible: a chip one cell above the lowest empty cell.
    const rows = await Promise.all(legal.map((c) => call("lowest", c).then((r) => r.value)));
    const options = legal.filter((_, i) => rows[i] >= 1);
    const k = Math.floor(Math.random() * options.length);
    const c = options[k];
    const r = rows[legal.indexOf(c)] - 1;
    candidate = t.map((row) => row.slice());
    candidate[r][c] = USER;
    ghost = [r, c];
  } else if (kind === "robot") {
    if (!state.robot_color || state.robot_color === "None") { say("Play one move first, so the robot knows its colour."); lock(false); return; }
    candidate = await drop(legal[Math.floor(Math.random() * legal.length)], ROBOT);
  }
  draw(candidate, { ghost });
  setGate(gate1, "busy", "Checking the new board");
  await wait(700);
  const res = await accept(candidate);
  setGate(gate1, res.ok ? "pass" : "fail", quote(res.reason));
  say(res.ok ? "That one got through." : "Rejected by the robot's validator, in its own words (columns and rows count from 0).");
  await wait(1100);
  draw(state.trusted);
  lock(false);
});

$("#reset").addEventListener("click", async () => {
  const { value } = await call("reset");
  state = value;
  pendingFix = null;
  fixBtn.hidden = true;
  draw(state.trusted);
  moveCarriage(0);
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
  lbl.style.whiteSpace = "pre-line";
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
const visionStatus = $("#vision-status");
document.querySelectorAll("[data-vision]").forEach((btn) =>
  btn.addEventListener("click", async () => {
    const mode = btn.dataset.vision;
    document.querySelectorAll("[data-vision]").forEach((b) => { b.disabled = true; });
    visionStatus.textContent = "Loading OpenCV and running the robot's vision code";
    try {
      const { value, ms } = await call("vision", mode);
      const right = value.board.flat().filter((v, i) => v === PHOTO_TRUTH.flat()[i]).length;
      const picture = (slot, b64, alt) => {
        const img = new Image();
        img.src = "data:image/jpeg;base64," + b64;
        img.alt = alt;
        $(slot).replaceChildren(img);
      };
      picture("#vision-overlay", value.overlay, mode === "auto" ? "Board photo with the corners found by automatic detection, including the legs" : "Board photo with the grid's own corners marked");
      picture("#vision-grid", value.grid, "The warped board with a coloured dot on each hole showing how it was read");
      const mini = $("#vision-board");
      mini.replaceChildren(...value.board.flat().map((v, i) => {
        const d = document.createElement("span");
        d.className = "m" + v + (v === PHOTO_TRUTH.flat()[i] ? "" : " wrong");
        return d;
      }));
      mini.setAttribute("aria-label", `Board as read: ${right} of 42 holes match the photo`);
      $("#vision-score").textContent = `${right} of 42 holes read right${right < 42 ? " (wrong ones circled)" : ""}.`;
      $("#vision-out").hidden = false;
      visionStatus.textContent = `${mode === "auto" ? "Automatic detection" : "Grid corners"}: done in ${Math.round(ms)} ms in your browser.`;
    } catch (err) {
      visionStatus.textContent = `The vision code could not run (${err.message}).`;
    }
    document.querySelectorAll("[data-vision]").forEach((b) => { b.disabled = false; });
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
      mini.setAttribute("aria-label", `${label}: ${right} of ${d.cells} holes read right`);
      mini.append(...d[key].map((v, i) => {
        const s = document.createElement("span");
        s.className = "m" + v + (v === d.truth[i] ? "" : " wrong");
        return s;
      }));
      const cap = document.createElement("figcaption");
      cap.textContent = `${label}: ${right} of ${d.cells} right`;
      fig.append(mini, cap);
      boards.append(fig);
    }
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
