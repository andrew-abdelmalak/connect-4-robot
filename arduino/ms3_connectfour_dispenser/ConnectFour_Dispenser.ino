// ============================================================
//  Connect Four Token Dispenser
//  DC Motor + Ruler Mechanism -- 7 Column Positions
//  Motor Driver: L298N (or similar H-Bridge)
// ============================================================
//
//  HOW IT WORKS:
//  The ruler starts at HOME (aligned with column 1 -- no motion needed).
//  Each entry in columnTimeMs[] is the absolute travel time (ms)
//  from HOME to reach that column.
//  Column 1 = 0ms  (already there, no motion needed)
//  Columns 2-7 = measured individually for your physical build.
//
//  WIRING (L298N Motor Driver):
//    ENA  -> Arduino Pin 9  (PWM speed)
//    IN1  -> Arduino Pin 7  (direction)
//    IN2  -> Arduino Pin 8  (direction)
//    OUT1/OUT2 -> DC Motor terminals
//
//  INPUT: Send '1'-'7' via Serial Monitor (9600 baud) to test.
//         Uncomment the BUTTON section for physical buttons.
// ============================================================

// --- Motor Driver Pins ---
#define ENA  9
#define IN1  7
#define IN2  8

// --- Motor Speed ---
#define MOTOR_SPEED 150

// ============================================================
//  COLUMN TIME ARRAY
// ============================================================
int columnTimeMs[7] = {
  0,
  250,
  500,
  780,
  1200,
  1600,
  2000
};

// ============================================================
//  INTERNAL STATE
// ============================================================
int currentMs = 0;

void motorForward(int spd) {
  digitalWrite(IN1, HIGH);
  digitalWrite(IN2, LOW);
  analogWrite(ENA, spd);
}

void motorBackward(int spd) {
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, HIGH);
  analogWrite(ENA, spd);
}

void motorStop() {
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, LOW);
  analogWrite(ENA, 0);
}

void moveToPosition(int targetMs) {
  int delta = targetMs - currentMs;

  if (delta > 0) {
    motorForward(MOTOR_SPEED);
    delay(delta);
    motorStop();
  } else if (delta < 0) {
    motorBackward(MOTOR_SPEED);
    delay(-delta);
    motorStop();
  }

  currentMs = targetMs;
}

void returnHome() {
  if (currentMs != 0) {
    motorBackward(MOTOR_SPEED);
    delay(currentMs);
    motorStop();
    currentMs = 0;
  }
  Serial.println("  >> Ruler back at HOME.\n");
}

void dispenseHole1() {
  Serial.println("Column 1 -- already at HOME, no motion needed.");
  moveToPosition(columnTimeMs[0]);
  Serial.println("  >> Hole 1 open. Dispensing token...");
  delay(2000);
  returnHome();
}

void dispenseHole2() {
  Serial.println("Column 2 selected -- moving ruler...");
  moveToPosition(columnTimeMs[1]);
  Serial.println("  >> Hole 2 open. Dispensing token...");
  delay(2000);
  returnHome();
}

void dispenseHole3() {
  Serial.println("Column 3 selected -- moving ruler...");
  moveToPosition(columnTimeMs[2]);
  Serial.println("  >> Hole 3 open. Dispensing token...");
  delay(2000);
  returnHome();
}

void dispenseHole4() {
  Serial.println("Column 4 selected -- moving ruler...");
  moveToPosition(columnTimeMs[3]);
  Serial.println("  >> Hole 4 open. Dispensing token...");
  delay(2000);
  returnHome();
}

void dispenseHole5() {
  Serial.println("Column 5 selected -- moving ruler...");
  moveToPosition(columnTimeMs[4]);
  Serial.println("  >> Hole 5 open. Dispensing token...");
  delay(2000);
  returnHome();
}

void dispenseHole6() {
  Serial.println("Column 6 selected -- moving ruler...");
  moveToPosition(columnTimeMs[5]);
  Serial.println("  >> Hole 6 open. Dispensing token...");
  delay(2000);
  returnHome();
}

void dispenseHole7() {
  Serial.println("Column 7 selected -- moving ruler...");
  moveToPosition(columnTimeMs[6]);
  Serial.println("  >> Hole 7 open. Dispensing token...");
  delay(2000);
  returnHome();
}

void selectColumn(int col) {
  switch (col) {
    case 1: dispenseHole1(); break;
    case 2: dispenseHole2(); break;
    case 3: dispenseHole3(); break;
    case 4: dispenseHole4(); break;
    case 5: dispenseHole5(); break;
    case 6: dispenseHole6(); break;
    case 7: dispenseHole7(); break;
    default:
      Serial.println("Invalid column! Send a number 1-7.");
  }
}

void setup() {
  pinMode(ENA, OUTPUT);
  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  motorStop();

  Serial.begin(9600);
  Serial.println("=== Connect Four Dispenser Ready ===");
  Serial.println("Send 1-7 in Serial Monitor to test each column.");
  Serial.println();
}

void loop() {
  if (Serial.available() > 0) {
    char input = Serial.read();

    if (input >= '1' && input <= '7') {
      int col = input - '0';
      selectColumn(col);
    }
  }

  // Wire 7 momentary buttons between GND and these pins (INPUT_PULLUP):
  //
  // const int buttonPins[7] = {2, 3, 4, 5, 6, 10, 11};
  // for (int i = 0; i < 7; i++) {
  //   if (digitalRead(buttonPins[i]) == LOW) {
  //     selectColumn(i + 1);
  //     delay(300);
  //   }
  // }
}
