// Web Worker: runs the robot's real Arduino sketch. The Pages build compiles
// arduino/dispenser/dispenser.ino with arduino-cli for the Uno and ships the
// .hex unchanged; avr8js emulates the ATmega328P instruction by instruction.
// Only the world outside the chip is simulated here: the encoder on the
// magazine motor and the carriage position. The encoder speed is an assumption.

import {
  AVRIOPort,
  AVRTimer,
  AVRUSART,
  CPU,
  PinState,
  avrInstruction,
  portBConfig,
  portDConfig,
  timer0Config,
  timer1Config,
  timer2Config,
  usart0Config,
} from "https://cdn.jsdelivr.net/npm/avr8js@0.21.1/+esm";

const HZ = 16e6; // Arduino Uno clock
const SUBSTEP = HZ / 10000; // advance the outside world every 0.1 ms
const SAMPLE_MS = 5; // pin samples for the signal traces
// Assumed: encoder pulses per ms at full PWM duty. Not measured on the robot.
const ENCODER_PULSES_PER_MS = 2;
// Pin map from dispenser.ino, as ATmega328P port bits.
const PIN = { IN1: ["D", 7], IN2: ["B", 0], ENA: ["B", 1], ENB: ["B", 2], IN3: ["B", 3], IN4: ["B", 4], ENCODER: ["D", 2] };
const TCCR1A = 0x80;
const OCR1AL = 0x88;
const OCR1BL = 0x8a;
const COM1A1 = 0x80;
const COM1B1 = 0x20;

let cpu, ports, usart, world;
let line = "";
let speed = 0; // 1 = real time, 0 = as fast as this browser can
let paused = true;
let stepLines = false;
let wallBase = 0;
let simBase = 0;
let timer = null;
let hex = null;

const ms = () => cpu.cycles / (HZ / 1000);
const high = ([port, bit]) => ports[port].pinState(bit) === PinState.High;
// Duty of a Timer1 PWM pin: its compare register while PWM drives it, else the pin level.
const duty = (com, ocr, pin) => (cpu.data[TCCR1A] & com ? cpu.data[ocr] / 255 : high(pin) ? 1 : 0);
const post = (msg) => self.postMessage(msg);

function load() {
  const program = new Uint8Array(0x8000);
  for (const rec of hex.split("\n")) {
    if (rec[0] !== ":" || parseInt(rec.slice(7, 9), 16) !== 0) continue; // data records only
    const n = parseInt(rec.slice(1, 3), 16);
    const addr = parseInt(rec.slice(3, 7), 16);
    for (let i = 0; i < n; i++) program[addr + i] = parseInt(rec.slice(9 + i * 2, 11 + i * 2), 16);
  }
  cpu = new CPU(new Uint16Array(program.buffer));
  for (const cfg of [timer0Config, timer1Config, timer2Config]) new AVRTimer(cpu, cfg);
  usart = new AVRUSART(cpu, usart0Config, HZ);
  ports = { B: new AVRIOPort(cpu, portBConfig), D: new AVRIOPort(cpu, portDConfig) };
  line = "";
  usart.onByteTransmit = (b) => {
    if (b === 10) {
      post({ type: "line", t: ms(), text: line.trim() });
      line = "";
      if (stepLines) pause("line");
    } else if (b !== 13) {
      line += String.fromCharCode(b);
    }
  };
  world = { pulses: 0, total: 0, acc: 0, enc: false, pos: 0, mag: false, car: 0, samples: [], nextSample: 0 };
}

// One 0.1 ms step of the hardware outside the chip.
function physics(dt) {
  const w = world;
  const mag = high(PIN.IN3) !== high(PIN.IN4);
  const enb = duty(COM1B1, OCR1BL, PIN.ENB);
  if (mag !== w.mag) {
    w.mag = mag;
    if (mag) w.pulses = 0; // count this release, as the sketch does
    post({ type: "motor", motor: "magazine", on: mag, t: ms(), pulses: w.pulses });
  }
  if (mag) {
    w.acc += enb * ENCODER_PULSES_PER_MS * dt * 2; // two edges per pulse
    if (w.acc >= 1) {
      w.acc -= 1;
      w.enc = !w.enc;
      ports.D.setPin(PIN.ENCODER[1], w.enc);
      if (w.enc) {
        w.pulses++;
        w.total++;
      }
    }
  }
  const car = high(PIN.IN1) !== high(PIN.IN2) ? (high(PIN.IN1) ? 1 : -1) : 0;
  const ena = duty(COM1A1, OCR1AL, PIN.ENA);
  if (car !== w.car) {
    w.car = car;
    post({ type: "motor", motor: "carriage", dir: car, t: ms(), pos: w.pos });
  }
  if (car && ena > 0) w.pos = Math.max(0, w.pos + car * dt);
  const t = ms();
  if (t >= w.nextSample) {
    w.nextSample = t + SAMPLE_MS;
    w.samples.push([t, +high(PIN.IN1), +high(PIN.IN2), ena, +high(PIN.IN3), +high(PIN.IN4), enb, w.pulses]);
  }
}

function tick() {
  timer = null;
  if (paused) return;
  const start = performance.now();
  const target = speed ? simBase + (start - wallBase) * speed : Infinity;
  while (!paused && ms() < target && performance.now() - start < 14) {
    const end = cpu.cycles + SUBSTEP;
    while (cpu.cycles < end) {
      avrInstruction(cpu);
      cpu.tick();
    }
    physics(0.1);
  }
  const realtime = (ms() - simBase) / Math.max(1, performance.now() - wallBase);
  post({ type: "tick", t: ms(), pos: world.pos, pulses: world.pulses, total: world.total, realtime, samples: world.samples });
  world.samples = [];
  if (!paused) timer = setTimeout(tick, speed && ms() >= target ? 8 : 0);
}

function rebase() {
  wallBase = performance.now();
  simBase = cpu ? ms() : 0;
}
function resume() {
  if (!paused) return;
  paused = false;
  rebase();
  timer ??= setTimeout(tick, 0);
}
function pause(reason) {
  paused = true;
  post({ type: "paused", reason, t: ms() });
}

self.onmessage = async ({ data }) => {
  try {
    if (data.op === "boot") {
      if (!hex) {
        const res = await fetch("firmware/dispenser.hex");
        if (!res.ok) throw new Error(`could not load the firmware (${res.status})`);
        hex = await res.text();
      }
      paused = true;
      clearTimeout(timer);
      timer = null;
      load();
      post({ type: "reset" });
      resume();
    } else if (data.op === "send") {
      usart.writeByte(data.byte);
      post({ type: "sent", byte: data.byte, t: ms() });
      resume();
    } else if (data.op === "idle") {
      paused = true;
    } else if (data.op === "speed") {
      speed = data.value;
      rebase();
    } else if (data.op === "step") {
      stepLines = data.value;
    } else if (data.op === "resume") {
      resume();
    }
  } catch (err) {
    post({ type: "error", error: String(err && err.message ? err.message : err) });
  }
};
