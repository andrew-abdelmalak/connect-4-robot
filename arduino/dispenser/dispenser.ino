// Connect-4 dispenser sketch, rebuilt from our final course report.
//
// The sketch that ran in our demo was not kept. This file follows the
// report's actuation sequence and carriage drive time per column, both
// reproduced in paper/supplement.tex (Fig. 2 and Table II). What the listing does not give (the pins, the motor
// speeds, READY and ERR on serial) comes from the team's earlier sketch, which
// released the token first; it is in this file's git history.
//
// Serial input from the Pi: one ASCII digit '1'..'7' per move. Per move:
//   1. Drive the carriage (the moving rod) out for the column's time, so it
//      covers the holes before the target. Hard-brake, then hold 2 s.
//   2. Turn the magazine motor 430 encoder pulses, fast and then slow for the
//      last 40, to push out one token. Nothing holds the token: it rolls along
//      the rail by gravity and drops through the first open hole.
//   3. Wait 0.5 s, then drive the carriage back home.

// ── Carriage / moving rod (TT motor on L298N OUT1/OUT2) ──────────────────────
const int ENA = 9;
const int IN1 = 7;
const int IN2 = 8;
const int carriageSpeed = 100;
const long columnDurations[7] = {
  0,    // column 1
  330,  // column 2
  600,  // column 3
  900,  // column 4
  1200, // column 5
  1500, // column 6
  1800  // column 7
};

// ── Magazine / token release motor (encoder motor on L298N OUT3/OUT4) ──────
const int ENB = 10;
const int IN3 = 11;
const int IN4 = 12;
const int encoderPin = 2;
volatile long pulses = 0;
const long targetPulses = 430;  // about 1.075 turns at 400 counts per turn
const int magFastSpeed = 50;
const int magSlowSpeed = 30;
const long slowDownWindow = 40;

// ── Timing ────────────────────────────────────────────────────────────────────
const unsigned long carriageHoldMs = 2000;
const unsigned long releaseWaitMs = 500;
const unsigned long hardBrakeMs = 100;

bool busy = false;

void setup() {
  Serial.begin(9600);

  pinMode(ENA, OUTPUT);
  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);

  pinMode(ENB, OUTPUT);
  pinMode(IN3, OUTPUT);
  pinMode(IN4, OUTPUT);

  pinMode(encoderPin, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(encoderPin), countPulse, RISING);

  stopCarriage();
  stopMagazine();
  Serial.println("READY");
}

void loop() {
  if (Serial.available() <= 0 || busy) {
    return;
  }

  char input = Serial.read();
  if (input < '1' || input > '7') {
    Serial.print("ERR invalid command: ");
    Serial.println(input);
    flushSerialLine();
    return;
  }

  flushSerialLine();
  int column = input - '0';
  executeColumnCycle(column);
}

void countPulse() {
  pulses++;
}

// The count is 4 bytes and the interrupt can change it mid-read.
long readPulses() {
  noInterrupts();
  long count = pulses;
  interrupts();
  return count;
}

void executeColumnCycle(int column) {
  busy = true;
  Serial.print("BUSY ");
  Serial.println(column);

  long ms = columnDurations[column - 1];

  // 1. Move the carriage to the selected column, then hold.
  driveCarriage(HIGH, LOW, ms);
  delay(carriageHoldMs);

  // 2. Release exactly one token.
  releaseOneToken();
  delay(releaseWaitMs);

  // 3. Retract the carriage to home.
  driveCarriage(LOW, HIGH, ms);

  busy = false;
  Serial.print("DONE ");
  Serial.println(column);
}

void driveCarriage(int in1, int in2, long ms) {
  digitalWrite(IN1, in1);
  digitalWrite(IN2, in2);
  analogWrite(ENA, carriageSpeed);
  delay(ms);
  stopCarriage();
}

void releaseOneToken() {
  noInterrupts();
  pulses = 0;
  interrupts();
  bool slowedDown = false;

  // Counter-clockwise release. Swap IN3/IN4 if physical motion is reversed.
  digitalWrite(IN3, LOW);
  digitalWrite(IN4, HIGH);
  analogWrite(ENB, magFastSpeed);

  long count;
  while ((count = readPulses()) < targetPulses) {
    if (!slowedDown && count >= (targetPulses - slowDownWindow)) {
      analogWrite(ENB, magSlowSpeed);
      slowedDown = true;
    }
  }

  stopMagazine();
  Serial.print("MAG_PULSES ");
  Serial.println(readPulses());
}

void stopCarriage() {
  digitalWrite(IN1, HIGH);
  digitalWrite(IN2, HIGH);
  delay(hardBrakeMs);
  analogWrite(ENA, 0);
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, LOW);
}

void stopMagazine() {
  digitalWrite(IN3, HIGH);
  digitalWrite(IN4, HIGH);
  delay(hardBrakeMs);
  analogWrite(ENB, 0);
  digitalWrite(IN3, LOW);
  digitalWrite(IN4, LOW);
}

void flushSerialLine() {
  while (Serial.available() > 0) {
    Serial.read();
  }
}
