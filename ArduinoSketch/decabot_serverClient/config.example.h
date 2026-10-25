/*
 * openDecabot Server Client - configuration
 * Copy this file to config.h and edit it before uploading the sketch.
 * config.h is ignored by git so your Wi-Fi password stays on your computer.
 */
#pragma once

/* Wi-Fi network the robot joins (2.4 GHz only, the ESP8266 has no 5 GHz radio) */
#define WIFI_SSID        "YourNetwork"
#define WIFI_PASSWORD    "YourPassword"

/* Server that processes the robot telemetry and answers with commands.
 * Run server/decabot_server.py on a computer in the same network and put
 * that computer's IP here. The final URL becomes:
 *   http://SERVER_HOST:SERVER_PORT/api/robots/<robot name>/sync             */
#define SERVER_HOST      "192.168.1.100"
#define SERVER_PORT      8000
#define SERVER_BASE_PATH "/api/robots/"

/* Optional shared secret. Leave empty to disable. When set, every request
 * carries the header  Authorization: Bearer <SERVER_TOKEN>  and the server
 * must be started with  --token <SERVER_TOKEN>.                             */
#define SERVER_TOKEN     ""

/* How often the robot contacts the server (milliseconds). The server can
 * change it at runtime with the command  i<ms>  (minimum 100).              */
#define SYNC_INTERVAL_MS 200

/* Safety: if the server stops answering for this long while the robot is
 * moving, the motors are stopped. Set to 0 to disable.                     */
#define FAILSAFE_MS      2000

/* Smooth acceleration: the power climbs from 0 to the commanded value in
 * this many milliseconds. 0 disables it. The dashboard slider changes it
 * at runtime with the command  g<ms>.                                      */
#define RAMP_MS          2000

/* Speaker (the buzzer on the Decabot Mini Shield) used for beeps and for
 * Morse code messages sent from the dashboard. Tone and dot length are the
 * defaults; the dashboard sliders override them per message.             */
#define BUZZER_PIN       D8
#define MORSE_HZ         880
#define MORSE_UNIT_MS    80

/* LED matrix shield (WEMOS Matrix LED on D5/D7). Set false when the front
 * of the robot carries something else, such as the ESP32-CAM camera module
 * (see ArduinoSketch/decabot_camera).                                       */
#define HAS_MATRIX       true

/* Turning by angle (dashboard buttons +-15 degrees and the v<deg> command):
 * milliseconds the robot needs for one full turn in place at TURN_POWER.
 * Calibrate with the "360" button on the dashboard, which also changes this
 * at runtime with the command  k<ms>.                                      */
#define TURN_MS_PER_360  1500
#define TURN_POWER       60

/* Servo calibration, adjustable from the dashboard (commands b r h j u).
 * ZERO: servo angle at which the wheel stands still (default 93, tune if a
 *   wheel creeps at power 0). START: power % at which the wheel begins to
 *   turn, measured with the wheel test sliders; both wheels then leave their
 *   dead band together. TRIM: left wheel speed correction in % so the robot
 *   drives straight (pulls left -> increase).                              */
#define MOTOR_ESQ_ZERO   93
#define MOTOR_DIR_ZERO   93
#define MOTOR_ESQ_START  0
#define MOTOR_DIR_START  0
#define MOTOR_TRIM       0

/* Set true if the robot drives backwards on "forward": the body is mounted
 * with the front on the other side of the servos. Turns are unaffected.   */
#define MOTORS_REVERSED  false

/* Set true if the robot has the battery measurement circuit on A0 */
#define SHOW_BATTERY     false
