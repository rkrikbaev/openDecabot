/*
 * openDecabot Server Client
 * https://github.com/triwaca/openDecabot
 *
 * The robot joins a Wi-Fi network and talks to an external server
 * (see /server folder). Every SYNC_INTERVAL_MS it sends its telemetry
 * (battery, motors, Wi-Fi signal, sensors...) as JSON and the server
 * answers with the commands the robot must execute, one per line, using
 * the same one-letter protocol accepted on the Serial port:
 *
 *   f100      forward at 100%            p         stop
 *   t50       backwards at 50%           x30 / y80 joystick axis
 *   d100      turn right                 i500      sync every 500 ms
 *   e100      turn left                  n494,200  buzzer: 494 Hz, 200 ms
 *   f100,800  forward 100% for 800 ms    mHELLO    scroll text on matrix
 *   l1 / l0   blue LED on / off          o         show eyes again
 *   g2000     accelerate 0->100 % of the commanded power in 2000 ms (g0 = off)
 *   w880,80,.--. .-.   Morse on the speaker: tone Hz, dot ms, pattern
 *             (. dot, - dash, space between letters, / between words)
 *   a..c q..s servo calibration (see decabot_webControler)
 *
 * Full protocol description: /server/README.md
 *
 * To use:
 * - Copy config.example.h to config.h and fill in your Wi-Fi network and
 *   the server address (config.h is ignored by git)
 * - Start the server:  python3 server/decabot_server.py
 * - Upload this sketch. The matrix alternates the Wi-Fi logo and the robot
 *   name until the server is reached, then the eyes appear.
 * - Open http://<server ip>:8000 to see the robot and drive it.
 *
 * Libraries (install with Arduino Library Manager):
 * - Adafruit GFX Library        tested on 1.11.10
 * - WEMOS Matrix GFX            tested on 1.4.0  https://github.com/thomasfredericks/wemos_matrix_gfx
 * - ArduinoUniqueID             tested on 1.3.0  https://github.com/ricaun/ArduinoUniqueID
 * ESP8266WiFi, ESP8266HTTPClient and Servo come with the ESP8266 core.
 *
 * Hardware:
 * Wemos D1 Mini - https://www.wemos.cc/en/latest/d1/index.html
 * Matrix Led Shield - https://github.com/wemos/WEMOS_Matrix_LED_Shield_Arduino_Library
 * Decabot Mini Shield
 *
 * PORTS                             _____________
 *                                  /             \
 *                             RST |*  ---------  *| TX
 *           battery ---------- A0 |* |         | *| RX
 *         SERVO esq --GPIO16-- D0 |* |  ESP    | *| D1 ---I2C--- SCL
 *    led matrix CLK ---------- D5 |* |  8266   | *| D2 ---I2C--- SDA
 *         SERVO dir --GPIO12 - D6 |* |         | *| D3 --GPIO0-- SERVO
 *    led matrix DIN ---------- D7 |* |         | *| D4 --GPIO2
 *            buzzer --GPIO15-- D8 |*  ---------  *| GND
 *                             3V3 |*             *| 5V
 *                                  \              |
 *                            reset  |      D1mini |
 *                                    \___________/
 */

#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>
#include <Servo.h>
#include <Adafruit_GFX.h>       // tested on 1.11.10 by Adafruit
#include <WEMOS_Matrix_GFX.h>   // tested on 1.4.0 from Thomas O Fredericks
#include <ArduinoUniqueID.h>    // tested on 1.3.0 from Cassettari
#include "config.h"

#ifndef WIFI_SSID
#error "Copy config.example.h to config.h (same folder) and fill in your Wi-Fi and server settings"
#endif
// defaults for settings missing from an older config.h
#ifndef RAMP_MS
#define RAMP_MS 2000
#endif
#ifndef BUZZER_PIN
#define BUZZER_PIN D8
#endif
#ifndef MORSE_HZ
#define MORSE_HZ 880
#endif
#ifndef MORSE_UNIT_MS
#define MORSE_UNIT_MS 80
#endif
#ifndef HAS_MATRIX
#define HAS_MATRIX true
#endif
#include "ramp.h"

#define buzzer BUZZER_PIN
#define battery A0
const String version = "1.1.20261024";
const char compile_date[] = __DATE__ " " __TIME__;

String robotId = "";
String robotName = "";

MLED matrix(7); // intensity 7 (maximum)

// motors
Servo motorDir;
Servo motorEsq;
int velocidade = 0;   // joystick Y
int direcao = 0;      // joystick X
Ramp rampaEsq;        // smooth acceleration, see ramp.h
Ramp rampaDir;
unsigned long rampMs = RAMP_MS;   // 0 -> 100 % of the commanded power takes this long; 0 = off
unsigned long rampTimer = 0;

// Morse playback on the speaker (non-blocking)
String morsePattern = "";
unsigned int morseIndex = 0;
unsigned long morseNext = 0;
bool morseToneOn = false;
int morseHz = MORSE_HZ;
unsigned int morseUnit = MORSE_UNIT_MS;
unsigned long motionEnd = 0; // when > 0, stop motors at this time (timed commands)
// calibration (same letters as decabot_webControler)
int motorEsqMin = 33;   // 'a'
int motorEsqZero = 93;  // 'b'
int motorEsqMax = 153;  // 'c'
int motorDirMin = 153;  // 'q'
int motorDirZero = 93;  // 'r'
int motorDirMax = 33;   // 's'

// server link
WiFiClient wifiClient;
HTTPClient http;
String syncUrl = "";
unsigned long syncInterval = SYNC_INTERVAL_MS;
unsigned long lastSync = 0;
unsigned long lastServerContact = 0;
bool wifiOk = false;
bool serverOk = false;
bool failsafeTriggered = false;
unsigned long syncCount = 0;
unsigned long syncErrors = 0;
unsigned long commandCount = 0;
String lastCommand = "";

// serial input
String txtSerial = "";

// display
const int LED_PIN = LED_BUILTIN;
bool ledState = false;
int lpos = 1;         // left eye column
int rpos = 6;         // right eye column
int blinkStep = 2;
int blinkDir = 1;
int blinksLeft = 0;
unsigned long eyeTimer = 0;
String scrollText = "";
int scrollPos = 0;
unsigned long scrollTimer = 0;
uint8_t contDisplayWifi = 0;
unsigned long nameTimer = 0;
unsigned long batTimer = 0;
bool showingBat = false;
bool batteryAlarm = false;
bool charging = false;
int chargingImageStep = 0;

static const uint8_t PROGMEM wifiLogo[] = {B01111110,B10000001,B00111100,B01000010,B00011000,B00100100,B00011000,B00011000};
const int decabotMusic[4][3]{ { 494, 2, 3 }, { 554, 2, 4 }, { 440, 2, 5 }, { 880, 1, 6 } };
static const uint8_t PROGMEM decabotLogo[4][5] = {{0b11001110,0b10101000,0b10101100,0b10101000,0b11001110},{0b00110010,0b01000101,0b01000111,0b01000101,0b00110101},{0b11000100,0b10101010,0b11001010,0b10101010,0b11000100},{0b01110100,0b00100100,0b00100100,0b00100000,0b00100100}};

void setup() {
  Serial.begin(115200);
  Serial.println();
  Serial.println();
  Serial.println("Copyright (C) Decano Robotics - www.decabot.com");
  Serial.print("Decabot Version: ");
  Serial.print(version);
  Serial.print(" - ");
  Serial.println(compile_date);
  Serial.println("MAC: " + WiFi.macAddress());
  robotId = decabotUniqueID();
  robotName = decabotName(robotId);
  Serial.println("Robot ID " + robotId);
  Serial.println("I am an openDecabot and my name is " + robotName);

  pinMode(buzzer, OUTPUT);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, HIGH); // LED off (active LOW)
  matrix.setRotation(2);
  matrix.setTextSize(1);
  matrix.setTextWrap(false);
  matrix.setTextColor(LED_ON);
  motorDir.attach(D0, 500, 2400); // correct PWM range on ESP8266
  motorEsq.attach(D6, 500, 2400);
  pare();

  // startup animation
  for (int i = 0; i < 4; i++) {
    if (HAS_MATRIX) {
      matrix.clear();
      matrix.drawBitmap(0, 2, decabotLogo[i], 8, 5, LED_ON);
      matrix.writeDisplay();
    }
    tone(buzzer, decabotMusic[i][0], decabotMusic[i][1] * 200);
    delay(decabotMusic[i][1] * 200);
    noTone(buzzer);
  }
  if (HAS_MATRIX) {
    matrix.clear();
    matrix.writeDisplay();
  }

  // Wi-Fi as a station: connection happens in background, loop() watches it
  WiFi.mode(WIFI_STA);
  WiFi.hostname("decabot-" + robotName);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting to Wi-Fi ");
  Serial.println(WIFI_SSID);

  syncUrl = String("http://") + SERVER_HOST + ":" + String(SERVER_PORT) + SERVER_BASE_PATH + robotName + "/sync";
  http.setReuse(true);     // keep the TCP connection open between syncs
  http.setTimeout(1500);
  Serial.println("Server: " + syncUrl);
}

void loop() {
  readSerial();
  watchWifi();
  if (wifiOk && millis() - lastSync >= syncInterval) {
    lastSync = millis();
    syncWithServer();
  }
  checkFailsafe();
  checkMotionTimer();
  updateRamp();
  updateMorse();
  updateDisplay();
}

/* ---------------------------------------------------------------- network */

void watchWifi() {
  bool connected = WiFi.status() == WL_CONNECTED;
  if (connected && !wifiOk) {
    Serial.println("Wi-Fi connected, IP " + WiFi.localIP().toString());
    tone(buzzer, 880, 100);
  } else if (!connected && wifiOk) {
    Serial.println("Wi-Fi lost");
    serverOk = false;
  }
  wifiOk = connected;
}

void syncWithServer() {
  http.begin(wifiClient, syncUrl);
  http.addHeader("Content-Type", "application/json");
  if (strlen(SERVER_TOKEN) > 0) {
    http.addHeader("Authorization", String("Bearer ") + SERVER_TOKEN);
  }
  int code = http.POST(buildTelemetry());
  syncCount++;
  if (code == HTTP_CODE_OK) {
    if (!serverOk) {
      Serial.println("Server online");
      tone(buzzer, 1760, 80);
    }
    serverOk = true;
    failsafeTriggered = false;
    lastServerContact = millis();
    String body = http.getString();
    executeCommandLines(body);
  } else {
    syncErrors++;
    if (serverOk || syncErrors % 25 == 1) {
      Serial.print("Server error: ");
      Serial.println(code < 0 ? HTTPClient::errorToString(code) : String(code));
    }
    serverOk = false;
  }
  http.end();
}

String buildTelemetry() {
  // Keep the JSON flat and small: the ESP8266 has little RAM.
  // Add your own sensors inside "sensors".
  String json = "{";
  json += "\"id\":\"" + robotId + "\",";
  json += "\"name\":\"" + robotName + "\",";
  json += "\"fw\":\"" + version + "\",";
  json += "\"uptime\":" + String(millis()) + ",";
  json += "\"ip\":\"" + WiFi.localIP().toString() + "\",";
  json += "\"rssi\":" + String(WiFi.RSSI()) + ",";
  json += "\"heap\":" + String(ESP.getFreeHeap()) + ",";
  json += "\"battery\":" + (SHOW_BATTERY ? String(readBatteryPercent()) : String("null")) + ",";
  json += "\"motors\":{\"esq\":" + String(rampaEsq.applied()) + ",\"dir\":" + String(rampaDir.applied()) + "},";
  json += "\"targets\":{\"esq\":" + String(rampaEsq.target) + ",\"dir\":" + String(rampaDir.target) + "},";
  json += "\"moving\":" + String(isMoving() ? "true" : "false") + ",";
  json += "\"interval\":" + String(syncInterval) + ",";
  json += "\"ramp\":" + String(rampMs) + ",";
  json += "\"speaking\":" + String(morsePattern.length() > 0 ? "true" : "false") + ",";
  json += "\"matrix\":" + String(HAS_MATRIX ? "true" : "false") + ",";
  json += "\"syncs\":" + String(syncCount) + ",";
  json += "\"errors\":" + String(syncErrors) + ",";
  json += "\"commands\":" + String(commandCount) + ",";
  json += "\"lastCommand\":\"" + jsonEscape(lastCommand) + "\",";
  json += "\"sensors\":{\"a0\":" + String(analogRead(battery)) + "}";
  json += "}";
  return json;
}

String jsonEscape(const String &text) {
  String out = "";
  for (unsigned int i = 0; i < text.length(); i++) {
    char c = text.charAt(i);
    if (c == '"' || c == '\\') out += '\\';
    if (c < 0x20) continue;
    out += c;
  }
  return out;
}

void executeCommandLines(const String &body) {
  int start = 0;
  while (start < (int)body.length()) {
    int end = body.indexOf('\n', start);
    if (end < 0) end = body.length();
    String line = body.substring(start, end);
    line.trim();
    if (line.length() > 0 && line.charAt(0) != '#') {
      maquinaDeEstados(line);
    }
    start = end + 1;
  }
}

void checkFailsafe() {
  if (FAILSAFE_MS == 0 || failsafeTriggered) return;
  if (!isMoving() || lastServerContact == 0) return;
  if (millis() - lastServerContact > FAILSAFE_MS) {
    failsafeTriggered = true;
    pare();
    Serial.println("Failsafe: server silent, motors stopped");
  }
}

void checkMotionTimer() {
  if (motionEnd != 0 && (long)(millis() - motionEnd) >= 0) {
    motionEnd = 0;
    pare();
  }
}

/* --------------------------------------------------------------- commands */

void readSerial() {
  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == '\n') {
      txtSerial.trim();
      if (txtSerial.length() > 0) maquinaDeEstados(txtSerial);
      txtSerial = "";
    } else {
      txtSerial += c;
    }
  }
}

// Receives "f100,800": command letter, parameter and optional duration in ms
void maquinaDeEstados(String txtDigitado) {
  char comando = txtDigitado.charAt(0);
  String parametro = txtDigitado.substring(1);
  String extra = "";
  int virgula = parametro.indexOf(',');
  if (virgula >= 0) {
    extra = parametro.substring(virgula + 1);
    parametro = parametro.substring(0, virgula);
  }
  int valor = parametro.toInt();
  unsigned long duracao = extra.toInt();
  commandCount++;
  lastCommand = txtDigitado;
  Serial.println("Comando " + String(comando) + " e Parametro " + parametro + (extra.length() ? " / " + extra : ""));

  switch (comando) {
    case 'a': motorEsqMin = valor; break;
    case 'b': motorEsqZero = valor; break;
    case 'c': motorEsqMax = valor; break;
    case 'q': motorDirMin = valor; break;
    case 'r': motorDirZero = valor; break;
    case 's': motorDirMax = valor; break;
    case 'y':
      velocidade = constrain(valor, -100, 100);
      break;
    case 'x':
      direcao = constrain(valor, -100, 100);
      moveDir(velocidade - (direcao / 2));
      moveEsq(velocidade + (direcao / 2));
      motionEnd = 0;
      break;
    case 'f': frente(valor);   startMotionTimer(duracao); break;
    case 't': tras(valor);     startMotionTimer(duracao); break;
    case 'd': direita(valor);  startMotionTimer(duracao); break;
    case 'e': esquerda(valor); startMotionTimer(duracao); break;
    case 'p': pare(); break;
    case 'i':
      syncInterval = max(100, valor);
      Serial.println("Sync interval " + String(syncInterval) + " ms");
      break;
    case 'g':
      rampMs = constrain(valor, 0, 10000);
      Serial.println("Ramp " + String(rampMs) + " ms");
      break;
    case 'w': {
      // w<hz>,<dot ms>,<pattern>
      int sep = extra.indexOf(',');
      if (sep < 0) { Serial.println("Morse: use w<hz>,<dot ms>,<pattern>"); break; }
      startMorse(valor, extra.substring(0, sep).toInt(), extra.substring(sep + 1));
      break;
    }
    case 'n':
      // n<frequency>,<duration ms>
      tone(buzzer, valor, duracao > 0 ? duracao : 200);
      break;
    case 'm':
      scrollText = txtDigitado.substring(1);
      scrollPos = 0;
      break;
    case 'o':
      scrollText = "";
      break;
    case 'l':
      ledState = valor != 0;
      digitalWrite(LED_PIN, ledState ? LOW : HIGH);
      break;
    case '?':
      Serial.println("Decabot " + robotName + " id " + robotId + " ip " + WiFi.localIP().toString());
      Serial.println("Server " + syncUrl + (serverOk ? " online" : " offline"));
      break;
    default:
      Serial.println("Comando desconhecido!");
      commandCount--;
  }
}

void startMotionTimer(unsigned long duracao) {
  motionEnd = duracao > 0 ? millis() + duracao : 0;
}

/* ----------------------------------------------------------------- motors */

void frente(int potencia) {
  moveDir(potencia);
  moveEsq(potencia);
  Serial.println("Andando para frente na potencia " + String(potencia));
}

void tras(int potencia) {
  moveDir(-potencia);
  moveEsq(-potencia);
  Serial.println("Andando para tras na potencia " + String(potencia));
}

void direita(int potencia) {
  moveDir(-potencia);
  moveEsq(potencia);
  Serial.println("Virando para direita na potencia " + String(potencia));
}

void esquerda(int potencia) {
  moveDir(potencia);
  moveEsq(-potencia);
  Serial.println("Virando para esquerda na potencia " + String(potencia));
}

void pare() {
  moveDir(0);
  moveEsq(0);
  motionEnd = 0;
  Serial.println("Parada!");
}

// Commands set the target; the ramp brings the servo there gradually.
void moveDir(int potencia) {
  if (rampaDir.set(potencia, rampMs)) writeServoDir(rampaDir.applied());
}

void moveEsq(int potencia) {
  if (rampaEsq.set(potencia, rampMs)) writeServoEsq(rampaEsq.applied());
}

void updateRamp() {
  unsigned long now = millis();
  unsigned long dt = now - rampTimer;
  if (dt < 20) return;
  rampTimer = now;
  if (rampaEsq.step(dt, rampMs)) writeServoEsq(rampaEsq.applied());
  if (rampaDir.step(dt, rampMs)) writeServoDir(rampaDir.applied());
}

bool isMoving() {
  return rampaEsq.moving() || rampaDir.moving();
}

void writeServoDir(int potencia) {
  int saida = potencia >= 0
      ? map(potencia, 0, 100, motorDirZero, motorDirMax)
      : map(potencia, -100, 0, motorDirMin, motorDirZero);
  motorDir.write(saida);
}

void writeServoEsq(int potencia) {
  int saida = potencia >= 0
      ? map(potencia, 0, 100, motorEsqZero, motorEsqMax)
      : map(potencia, -100, 0, motorEsqMin, motorEsqZero);
  motorEsq.write(saida);
}

/* ---------------------------------------------------------------- speaker */

// Timing follows the Morse standard: dot 1 unit, dash 3, gap inside a letter 1,
// between letters 3, between words 7.
void startMorse(int hz, int unit, const String &pattern) {
  morseHz = hz > 0 ? hz : MORSE_HZ;
  morseUnit = unit > 0 ? unit : MORSE_UNIT_MS;
  morsePattern = pattern;
  morseIndex = 0;
  morseToneOn = false;
  morseNext = millis();
  Serial.println("Morse: " + pattern);
}

void updateMorse() {
  if (morsePattern.length() == 0) return;
  if ((long)(millis() - morseNext) < 0) return;
  if (morseToneOn) {                       // symbol finished: 1 unit of silence
    noTone(buzzer);
    morseToneOn = false;
    morseNext = millis() + morseUnit;
    return;
  }
  if (morseIndex >= morsePattern.length()) {
    morsePattern = "";
    return;
  }
  char c = morsePattern.charAt(morseIndex++);
  switch (c) {
    case '.': tone(buzzer, morseHz); morseToneOn = true; morseNext = millis() + morseUnit; break;
    case '-': tone(buzzer, morseHz); morseToneOn = true; morseNext = millis() + 3 * morseUnit; break;
    case ' ': morseNext = millis() + 2 * morseUnit; break;   // 1 + 2 = 3 units between letters
    case '/': morseNext = millis() + 6 * morseUnit; break;   // 1 + 6 = 7 units between words
    default: break;
  }
}

/* ---------------------------------------------------------------- battery */

int readBatteryPercent() {
  int reading = map(analogRead(battery), 704, 955, 0, 100);
  reading = constrain(reading, 0, 100);
  charging = reading == 100;
  return reading;
}

/* ---------------------------------------------------------------- display */

void updateDisplay() {
  if (!HAS_MATRIX) return;   // no LED matrix (e.g. replaced by the ESP32-CAM)
  if (!wifiOk || !serverOk) {
    // waiting for network: Wi-Fi logo alternating with the robot name
    if (millis() - nameTimer >= 100) {
      nameTimer = millis();
      decabotShowName();
    }
    return;
  }
  if (SHOW_BATTERY && millis() - batTimer >= (showingBat ? 1000UL : 10000UL)) {
    batTimer = millis();
    showingBat = !showingBat;
    if (showingBat) decabotShowBatLevel();
  }
  if (showingBat) return;
  if (scrollText.length() > 0) {
    scrollMessage();
  } else {
    animateEyes();
  }
}

void drawEyes(int top) {
  matrix.clear();
  matrix.drawLine(lpos, top, lpos, 7, LED_ON);
  matrix.drawLine(rpos, top, rpos, 7, LED_ON);
  matrix.writeDisplay();
}

// Non-blocking version of the classic Decabot eyes: open for a second,
// blink 0-2 times, look to a random side.
void animateEyes() {
  if ((long)(millis() - eyeTimer) < 0) return;
  if (blinksLeft == 0) {
    int aleat = random(10);
    if (aleat < 2) { lpos = 0; rpos = 5; }
    else if (aleat > 7) { lpos = 2; rpos = 7; }
    else { lpos = 1; rpos = 6; }
    blinkStep = 2;
    blinkDir = 1;
    drawEyes(blinkStep);
    blinksLeft = random(3);
    eyeTimer = millis() + 1000;
    return;
  }
  blinkStep += blinkDir;
  drawEyes(blinkStep);
  if (blinkStep >= 7) blinkDir = -1;
  if (blinkStep <= 2 && blinkDir < 0) {
    blinkDir = 1;
    blinksLeft--;
  }
  eyeTimer = millis() + 15;
}

void scrollMessage() {
  if (millis() - scrollTimer < 80) return;
  scrollTimer = millis();
  matrix.clear();
  matrix.setCursor(8 - scrollPos, 1);
  matrix.print(scrollText);
  matrix.writeDisplay();
  scrollPos++;
  if (scrollPos > (int)scrollText.length() * 6 + 8) {
    scrollText = "";
    eyeTimer = 0;
    blinksLeft = 0;
  }
}

void decabotShowName() {
  matrix.clear();
  if (contDisplayWifi < 20) {
    if (contDisplayWifi % 10 < 5 || wifiOk) {
      matrix.drawBitmap(0, 0, wifiLogo, 8, 8, LED_ON);
    }
  } else {
    matrix.setCursor((contDisplayWifi - 20) * -1, 1);
    matrix.print("  " + robotName + " ");
  }
  matrix.writeDisplay();
  contDisplayWifi++;
  if (contDisplayWifi > 56) contDisplayWifi = 0;
}

void decabotShowBatLevel() {
  int level = readBatteryPercent();
  if (level < 10) {
    batteryAlarm = !batteryAlarm;
    if (batteryAlarm) tone(buzzer, 494, 200);
  }
  matrix.clear();
  matrix.drawRect(0, 2, 7, 4, LED_ON);
  matrix.drawLine(7, 3, 7, 4, LED_ON);
  if (charging) {
    matrix.fillRect(1, 3, chargingImageStep, 2, LED_ON);
    chargingImageStep++;
    if (chargingImageStep > 5) chargingImageStep = 0;
  } else {
    matrix.fillRect(1, 3, map(level, 0, 100, 0, 5), 2, LED_ON);
  }
  matrix.writeDisplay();
}

/* --------------------------------------------------------------- identity */

String decabotUniqueID() {
  String id = "";
  for (size_t i = 0; i < UniqueIDsize; i++) {
    if (UniqueID[i] < 0x10) id = id + "0";
    id = id + UniqueID[i];
  }
  return id;
}

// Same hash as decabot_webControler, so the robot keeps its name
String decabotName(String entrada) {
  uint16_t hashCalculado = 0;
  for (unsigned int i = 0; i < entrada.length(); i++) {
    hashCalculado += entrada.charAt(i);
    hashCalculado += (hashCalculado << 10);
    hashCalculado ^= (hashCalculado >> 6);
  }
  hashCalculado += (hashCalculado << 3);
  hashCalculado ^= (hashCalculado >> 11);
  hashCalculado += (hashCalculado << 15);

  const char caracteres[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
  String hashResultado = "";
  for (int i = 0; i < 4; i++) {
    uint16_t indice = (hashCalculado + (i * 17)) % 36;
    hashResultado += caracteres[indice];
    hashCalculado >>= 4;
  }
  return hashResultado;
}
