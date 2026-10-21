// Final integrated Connect-4 actuator sketch.
// Serial input from Pi: ASCII '1'..'7'
// Sequence:
//   1. Release exactly one token from the magazine motor
//   2. Move the carriage/shuttle to the requested column and back home
//   3. Report progress on serial

// ── Carriage / positioning motor (TT motor on L298N OUT1/OUT2) ─────────────
const int ENA = 9;
const int IN1 = 7;
const int IN2 = 8;
const int carriageSpeed = 100;
const long columnDurations[7] = {
  0,    // column 1
  330,  // column 2
  600,  // column 3
  900,  // column 4
  1000, // column 5
  3000, // column 6
  4000  // column 7
};

// ── Magazine / token release motor (encoder motor on L298N OUT3/OUT4) ──────
const int ENB = 10;
const int IN3 = 11;
const int IN4 = 12;
const int encoderPin = 2;
volatile long pulses = 0;
const long targetPulses = 400;  // Lower stop target to reduce overshoot
const int magFastSpeed = 50;
const int magSlowSpeed = 30;
const long slowDownWindow = 40;

// ── Timing ────────────────────────────────────────────────────────────────────
const unsigned long carriagePauseMs = 2000;
const unsigned long magazineSettleMs = 300;
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

void executeColumnCycle(int column) {
  busy = true;
  Serial.print("BUSY ");
  Serial.println(column);

  releaseOneToken();
  delay(magazineSettleMs);
  moveToColumnAndReturn(column);

  busy = false;
  Serial.print("DONE ");
  Serial.println(column);
}

void releaseOneToken() {
  pulses = 0;
  bool slowedDown = false;

  // Counter-clockwise release. Swap IN3/IN4 if physical motion is reversed.
  digitalWrite(IN3, LOW);
  digitalWrite(IN4, HIGH);
  analogWrite(ENB, magFastSpeed);

  while (pulses < targetPulses) {
    long currentPulses = pulses;
    if (!slowedDown && currentPulses >= (targetPulses - slowDownWindow)) {
      analogWrite(ENB, magSlowSpeed);
      slowedDown = true;
    }
  }

  stopMagazine();
  Serial.print("MAG ");
  Serial.println(pulses);
}

void moveToColumnAndReturn(int column) {
  long ms = columnDurations[column - 1];

  Serial.print("MOVE ");
  Serial.println(column);

  digitalWrite(IN1, HIGH);
  digitalWrite(IN2, LOW);
  analogWrite(ENA, carriageSpeed);
  delay(ms);

  stopCarriage();
  delay(carriagePauseMs);

  digitalWrite(IN1, LOW);
  digitalWrite(IN2, HIGH);
  analogWrite(ENA, carriageSpeed);
  delay(ms);

  stopCarriage();
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
