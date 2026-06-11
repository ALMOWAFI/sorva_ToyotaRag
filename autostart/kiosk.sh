#!/bin/bash
# kiosk.sh — Launch Chromium in fullscreen kiosk mode pointing to the assistant
# Add this to Pi's autostart: /etc/xdg/lxsession/LXDE-pi/autostart

# Wait for the server to be ready
sleep 6

# Disable screen blanking
xset s off
xset s noblank
xset -dpms

# Launch Chromium in kiosk mode
chromium-browser \
  --kiosk \
  --noerrdialogs \
  --disable-infobars \
  --no-first-run \
  --disable-session-crashed-bubble \
  --disable-restore-session-state \
  --autoplay-policy=no-user-gesture-required \
  http://localhost:8000
