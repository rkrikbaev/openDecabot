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

/* Set true if the robot has the battery measurement circuit on A0 */
#define SHOW_BATTERY     false
