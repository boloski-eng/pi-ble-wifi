#!/usr/bin/env bash
# ==============================================================================
# Uninstaller for Raspberry Pi BLE Wi-Fi Controller
# ==============================================================================
set -e

if [ "$EUID" -ne 0 ]; then
    echo "[!] Error: Please run as root: sudo bash uninstall.sh"
    exit 1
fi

echo "[+] Stopping and disabling pizero-wifi-ble service..."
systemctl stop pizero-wifi-ble.service 2>/dev/null || true
systemctl disable pizero-wifi-ble.service 2>/dev/null || true

echo "[+] Removing service file..."
rm -f /etc/systemd/system/pizero-wifi-ble.service

echo "[+] Removing /opt/pizero-wifi-ble..."
rm -rf /opt/pizero-wifi-ble

systemctl daemon-reload
echo "[✓] pizero-wifi-ble service completely uninstalled."
