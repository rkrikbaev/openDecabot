/*
 * openDecabot Camera (ESP32-CAM) - configuration
 * Copy this file to config.h and edit it before uploading the sketch.
 * config.h is ignored by git so your Wi-Fi password stays on your computer.
 */
#pragma once

/* Wi-Fi network (2.4 GHz) - the same one the robot uses */
#define WIFI_SSID        "YourNetwork"
#define WIFI_PASSWORD    "YourPassword"

/* Decabot server (server/decabot_server.py) */
#define SERVER_HOST      "192.168.1.100"
#define SERVER_PORT      8000
#define SERVER_TOKEN     ""          /* same as --token on the server, "" = none */

/* Name of the robot this camera is mounted on: the 4 letters shown on the
 * dashboard (and on the matrix / Serial at boot), e.g. "51F2".            */
#define ROBOT_NAME       "XXXX"

/* How often the camera tells the server its address (milliseconds) */
#define CAMERA_ANNOUNCE_MS 10000

/* Picture. FRAMESIZE_QVGA (320x240) is fastest, VGA (640x480) is a good
 * default, SVGA (800x600) and up need PSRAM (the AI-Thinker has it).
 * Quality: 10 (best) .. 63 (smallest files).                               */
#define CAMERA_FRAME_SIZE   FRAMESIZE_VGA
#define CAMERA_JPEG_QUALITY 12
#define CAMERA_VFLIP        0        /* 1 if the module is mounted upside down */
#define CAMERA_HMIRROR      0
