# Raspberry Pi Zero Bluetooth Wi-Fi Controller (iOS & Android)

Control and configure your Raspberry Pi Zero's Wi-Fi network directly from your **iPhone** over **Bluetooth Low Energy (BLE)** using a terminal app—no active network, monitor, or keyboard required.

---

## Why BLE for iPhone?

Apple strictly restricts standard Bluetooth Classic Serial Port Profile (SPP/RFCOMM) on iOS unless hardware is Apple MFi certified. 

This project uses the industry-standard **Nordic UART Service (NUS)** over **Bluetooth Low Energy (BLE)** (`6E400001-B5A3-F393-E0A9-E50E24DCCA9E`), which is natively supported by iOS and works with multiple free terminal apps from the App Store.

---

## Requirements

1. **Raspberry Pi**: Pi Zero W, Pi Zero 2 W (or any Pi with built-in Bluetooth and Wi-Fi) running Raspberry Pi OS (Bookworm or Bullseye).
2. **Current Access**: SSH access (via USB gadget mode, Ethernet hat, or existing Wi-Fi) to run the initial setup.
3. **iPhone**: Any iPhone running iOS 12+ with Bluetooth turned on.
4. **Free iOS App** (Install one from the App Store):
   - **Recommended:** [Adafruit Bluefruit LE Connect](https://apps.apple.com/app/adafruit-bluefruit-le-connect/id830125974)
   - *Alternative 1:* **BLE Terminal HM-10**
   - *Alternative 2:* **nRF Toolbox** (by Nordic Semiconductor -> UART)

---

## Quick Setup (via SSH)

### Step 1: Copy Files to Raspberry Pi

From your computer terminal, transfer this folder to your Pi using `scp`:

```bash
scp -r c:\Users\dhanu\Downloads\antigravity\pi pi@<PI_IP_OR_HOSTNAME>:~/pi-ble-wifi
```

*Or, if you are already SSH'd into your Pi, clone or create the files in a folder `~/pi-ble-wifi`.*

### Step 2: Run the Automated Installer

SSH into your Pi and execute:

```bash
cd ~/pi-ble-wifi
sudo bash install.sh
```

The installer will automatically:
1. Unblock Bluetooth and Wi-Fi radios (`rfkill`).
2. Install necessary system packages (`bluez`, `python3-dbus`, `python3-gi`, etc.).
3. Configure BlueZ experimental mode for BLE GATT advertising.
4. Set up an isolated Python virtual environment at `/opt/pizero-wifi-ble`.
5. Install and enable the `pizero-wifi-ble.service` systemd background service to start automatically on every boot.

---

## How to Connect and Control from iPhone

### 1. Open the App
1. Open **Adafruit Bluefruit LE Connect** on your iPhone.
2. Grant Bluetooth permission if prompted.

### 2. Connect to the Pi
1. In the device scanner list, find **`Pi-WiFi`**.
2. Tap **Connect**.

### 3. Open UART Terminal
1. Under the module options, tap **UART**.
2. The Pi Zero will immediately send the interactive menu:

```text
================================
   Pi Zero Wi-Fi Controller     
================================
Status: Disconnected
IP    : Not connected (No IP)
--------------------------------
[1] Scan Wi-Fi Networks
[2] Connection Status & IP
[3] Connect Manually (Enter SSID)
[4] Disconnect Current Wi-Fi
[5] Reboot Raspberry Pi
--------------------------------
Enter choice (1-5): 
```

---

## Interactive Walkthrough

### 1. Scan and Connect to Wi-Fi
1. Type `1` in the input field and tap **Send**.
2. The Pi scans nearby 2.4 GHz (or 5 GHz on Pi Zero 2 W) access points and displays:

```text
Available Wi-Fi Networks:
  [1] Home-WiFi (Signal: 90%, WPA2)
  [2] CoffeeShop-Guest (Signal: 65%, Open)
  [3] Office-Net (Signal: 40%, WPA2)
--------------------------------
Enter network number (or 'r' to rescan, 'm' for menu): 
```

3. Type `1` and press **Send**.
4. The Pi prompts:
   ```text
   Selected: 'Home-WiFi' (WPA2)
   Enter Wi-Fi password (or 'c' to cancel): 
   ```
5. Enter your Wi-Fi password and press **Send**.
6. Within 10–20 seconds, the Pi connects and sends back your new IP address:

```text
========================================
  >>> SUCCESS: CONNECTED TO WI-FI! <<<  
========================================
Network Name : Home-WiFi
IP Address   : 192.168.1.145
SSH Login    : ssh pi@192.168.1.145
========================================
```

You can now immediately SSH into `ssh pi@192.168.1.145` from your computer or phone!

---

## Managing the Service

| Action | Command |
| :--- | :--- |
| **Check service status** | `sudo systemctl status pizero-wifi-ble` |
| **View live logs** | `sudo journalctl -u pizero-wifi-ble -f` |
| **Restart service** | `sudo systemctl restart pizero-wifi-ble` |
| **Stop service** | `sudo systemctl stop pizero-wifi-ble` |
| **Uninstall completely** | `sudo bash uninstall.sh` |

---

## Troubleshooting

- **Can't find `Pi-WiFi` in the Bluetooth scanner?**
  - Ensure Bluetooth is powered on: `sudo bluetoothctl power on`
  - Check if adapter is blocked: `sudo rfkill unblock bluetooth`
  - Restart the service: `sudo systemctl restart pizero-wifi-ble`
- **Wi-Fi scan returns no networks:**
  - Raspberry Pi Zero W only supports **2.4 GHz Wi-Fi** (Pi Zero 2 W supports both 2.4 GHz and 5 GHz). Ensure your router's 2.4 GHz band is broadcasted.
  - Set your Wi-Fi country code in `sudo raspi-config` under **Localisation Options -> WLAN Country**.
