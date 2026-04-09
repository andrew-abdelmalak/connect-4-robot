#include <Servo.h>

Servo servo1;

void setup() {
  Serial.begin(9600);
  servo1.attach(9);
  servo1.write(90);  // start at center
  delay(500);
}

void loop() {
  if (Serial.available() >= 2) {
    int angle1 = Serial.read();   // 0-180 from brightness
    int angle2 = Serial.read();   // 0-180 from rotation angle

    servo1.write(angle1);
    delay(500);
    servo1.write(angle2);
    delay(500);
  }
}