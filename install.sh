#!/usr/bin/env bash
# ==============================================================================
# Installer for Raspberry Pi BLE Wi-Fi Controller
# ==============================================================================
set -e

if [ "$EUID" -ne 0 ]; then
    echo "[!] Error: Please run this installer as root (e.g. sudo bash install.sh)"
    exit 1
fi

echo "========================================================"
echo "    Raspberry Pi BLE Wi-Fi Controller Installer         "
echo "========================================================"

# 1. Unblock wireless hardware
echo "[+] Unblocking RF devices (Bluetooth / Wi-Fi)..."
rfkill unblock bluetooth 2>/dev/null || true
rfkill unblock wifi 2>/dev/null || true

# 2. Update and install packages
echo "[+] Updating apt repositories..."
apt-get update -y

echo "[+] Installing system dependencies (BlueZ, Python, D-Bus)..."
apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    python3-dbus \
    python3-gi \
    bluetooth \
    bluez \
    libglib2.0-dev \
    rfkill \
    wireless-tools

# 3. Configure BlueZ experimental mode and disable battery plugin (fixes iOS pairing loop)
BTD_PATH=""
for p in /usr/libexec/bluetooth/bluetoothd /usr/lib/bluetooth/bluetoothd /usr/sbin/bluetoothd $(which bluetoothd 2>/dev/null); do
    if [ -x "$p" ]; then
        BTD_PATH="$p"
        break
    fi
done

if [ -n "$BTD_PATH" ]; then
    echo "[+] Configuring BlueZ: experimental mode enabled, battery plugin disabled ($BTD_PATH)..."
    mkdir -p /etc/systemd/system/bluetooth.service.d
    cat << EOF > /etc/systemd/system/bluetooth.service.d/override.conf
[Service]
ExecStart=
ExecStart=$BTD_PATH --experimental -P battery
EOF
fi

# Configure /etc/bluetooth/main.conf for iOS compatibility and auto-enable on boot
if [ -f /etc/bluetooth/main.conf ]; then
    echo "[+] Configuring /etc/bluetooth/main.conf (ReverseServiceDiscovery = false, AutoEnable = true)..."
    grep -q "^ReverseServiceDiscovery" /etc/bluetooth/main.conf && \
        sed -i 's/^ReverseServiceDiscovery.*/ReverseServiceDiscovery = false/' /etc/bluetooth/main.conf || \
        sed -i '/^\[General\]/a ReverseServiceDiscovery = false' /etc/bluetooth/main.conf

    # Ensure [Policy] section exists and has AutoEnable=true
    if ! grep -q "^\[Policy\]" /etc/bluetooth/main.conf; then
        echo -e "\n[Policy]\nAutoEnable=true" >> /etc/bluetooth/main.conf
    else
        grep -q "^AutoEnable" /etc/bluetooth/main.conf && \
            sed -i 's/^AutoEnable.*/AutoEnable=true/' /etc/bluetooth/main.conf || \
            sed -i '/^\[Policy\]/a AutoEnable=true' /etc/bluetooth/main.conf
    fi
fi

systemctl daemon-reload
systemctl restart bluetooth

# Ensure bluetooth and UART hardware services are enabled on boot
systemctl enable bluetooth 2>/dev/null || true
systemctl enable hciuart 2>/dev/null || true
systemctl start bluetooth 2>/dev/null || true
bluetoothctl power on 2>/dev/null || true

# 4. Set up installation directory and virtual environment
INSTALL_DIR="/opt/pizero-wifi-ble"
echo "[+] Setting up installation directory at $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"

echo "[+] Creating Python virtual environment with system site packages..."
python3 -m venv --system-site-packages "$INSTALL_DIR/venv"

echo "[+] Installing Python Bluezero library..."
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install bluezero

# 5. Copy agent script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "[+] Installing wifi_ble_agent.py..."
cp "$SCRIPT_DIR/wifi_ble_agent.py" "$INSTALL_DIR/wifi_ble_agent.py"
chmod +x "$INSTALL_DIR/wifi_ble_agent.py"

# 6. Configure D-Bus permissions for bluezero
echo "[+] Configuring D-Bus policy for bluezero..."
cat << 'EOF' > /etc/dbus-1/system.d/bluezero.conf
<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <policy user="root">
    <allow own_prefix="org.bluezero"/>
    <allow send_destination="org.bluez"/>
  </policy>
  <policy at_console="true">
    <allow own_prefix="org.bluezero"/>
    <allow send_destination="org.bluez"/>
  </policy>
  <policy context="default">
    <allow own_prefix="org.bluezero"/>
    <allow send_destination="org.bluez"/>
  </policy>
</busconfig>
EOF

# 7. Install and enable systemd service (configured to survive reboots)
echo "[+] Installing systemd background service..."
cat << 'EOF' > /etc/systemd/system/pizero-wifi-ble.service
[Unit]
Description=Raspberry Pi BLE Wi-Fi Setup Service
After=dbus.service bluetooth.service hciuart.service network.target
Wants=bluetooth.service hciuart.service
StartLimitIntervalSec=0

[Service]
Type=simple
User=root
WorkingDirectory=/opt/pizero-wifi-ble
ExecStartPre=-/bin/sh -c 'rfkill unblock bluetooth 2>/dev/null || true; hciconfig hci0 up 2>/dev/null || true; bluetoothctl power on 2>/dev/null || true'
ExecStart=/opt/pizero-wifi-ble/venv/bin/python3 /opt/pizero-wifi-ble/wifi_ble_agent.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable pizero-wifi-ble.service
systemctl restart pizero-wifi-ble.service

# 7. Verification
sleep 2
echo "--------------------------------------------------------"
if systemctl is-active --quiet pizero-wifi-ble.service; then
    echo "[✓] SUCCESS: BLE Wi-Fi Controller is RUNNING!"
    echo "[✓] Broadcasting BLE device name: 'Pi-WiFi'"
    echo ""
    echo "Next steps on your iPhone:"
    echo "1. Download 'Adafruit Bluefruit LE Connect' or 'BLE Terminal HM-10' from the App Store."
    echo "2. Open the app, scan for 'Pi-WiFi', and tap Connect."
    echo "3. Tap 'UART' (Terminal mode). The Wi-Fi menu will appear instantly!"
else
    echo "[!] Service started but seems inactive. Checking logs:"
    journalctl -u pizero-wifi-ble.service -n 20 --no-pager
fi
echo "========================================================"
