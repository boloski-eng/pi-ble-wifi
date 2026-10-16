#!/usr/bin/env python3
"""
Raspberry Pi BLE Wi-Fi Controller
Allows an iPhone (or Android) to scan and connect the Raspberry Pi to Wi-Fi
over Bluetooth Low Energy (BLE) using Nordic UART Service (NUS).
"""

import os
import sys
import time
import shutil
import socket
import logging
import threading
import subprocess
from gi.repository import GLib

# Bluezero BLE modules
try:
    from bluezero import adapter
    from bluezero import peripheral
    from bluezero import device
except ImportError:
    print("Error: 'bluezero' is not installed.")
    print("Install it with: pip install bluezero")
    sys.exit(1)

# Nordic UART Service (NUS) standard UUIDs (Supported natively by iOS BLE Terminal apps)
UART_SERVICE = '6E400001-B5A3-F393-E0A9-E50E24DCCA9E'
RX_CHARACTERISTIC = '6E400002-B5A3-F393-E0A9-E50E24DCCA9E'  # Central writes to Pi
TX_CHARACTERISTIC = '6E400003-B5A3-F393-E0A9-E50E24DCCA9E'  # Pi notifies Central

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("BLE_WiFi")

# State Machine definitions
STATE_MENU = "MENU"
STATE_SELECT_NETWORK = "SELECT_NETWORK"
STATE_ENTER_PASSWORD = "ENTER_PASSWORD"
STATE_ENTER_MANUAL_SSID = "ENTER_MANUAL_SSID"

current_state = STATE_MENU
scanned_networks = []
selected_ssid = None
selected_security = None
input_buffer = ""
state_lock = threading.Lock()


# ---------------------------------------------------------
# Wi-Fi Helper Functions (NetworkManager & wpa_supplicant)
# ---------------------------------------------------------

def has_nmcli():
    """Check if NetworkManager (nmcli) is available and functional."""
    return shutil.which("nmcli") is not None


def get_ip_address():
    """Retrieve the primary IPv4 address of the Pi."""
    try:
        res = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=3)
        ips = res.stdout.strip().split()
        if ips:
            return ips[0]
    except Exception:
        pass
    return "Not connected (No IP)"


def get_current_wifi_info():
    """Get currently connected SSID and interface status."""
    ssid = None
    if has_nmcli():
        try:
            res = subprocess.run(
                ["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"],
                capture_output=True, text=True, timeout=5
            )
            for line in res.stdout.strip().splitlines():
                if line.startswith("yes:"):
                    ssid = line.split(":", 1)[1]
                    break
        except Exception:
            pass
    
    if not ssid and shutil.which("iwgetid"):
        try:
            res = subprocess.run(["iwgetid", "-r"], capture_output=True, text=True, timeout=3)
            val = res.stdout.strip()
            if val:
                ssid = val
        except Exception:
            pass

    ip = get_ip_address()
    return ssid, ip


def scan_wifi_networks():
    """Scan available Wi-Fi networks and return a deduplicated list."""
    networks = []
    seen = set()

    if has_nmcli():
        logger.info("Triggering nmcli Wi-Fi scan...")
        try:
            subprocess.run(["nmcli", "dev", "wifi", "rescan"], capture_output=True, timeout=10)
        except Exception:
            pass  # Rescan may fail if recently scanned, ignore

        try:
            res = subprocess.run(
                ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list"],
                capture_output=True, text=True, timeout=10
            )
            if res.returncode == 0:
                for line in res.stdout.strip().splitlines():
                    parts = line.split(":")
                    if len(parts) >= 2:
                        ssid = parts[0].strip()
                        signal = parts[1].strip()
                        sec = parts[2].strip() if len(parts) >= 3 else ""
                        if not ssid or ssid == "--" or ssid in seen:
                            continue
                        seen.add(ssid)
                        networks.append({
                            "ssid": ssid,
                            "signal": signal,
                            "security": sec if sec else "Open"
                        })
        except Exception as e:
            logger.error(f"nmcli scan error: {e}")

    # Fallback to wpa_cli if nmcli not available or found nothing
    if not networks and shutil.which("wpa_cli"):
        logger.info("Scanning via wpa_cli fallback...")
        try:
            subprocess.run(["wpa_cli", "-i", "wlan0", "scan"], capture_output=True, timeout=5)
            time.sleep(2)
            res = subprocess.run(
                ["wpa_cli", "-i", "wlan0", "scan_results"],
                capture_output=True, text=True, timeout=5
            )
            if res.returncode == 0:
                for line in res.stdout.strip().splitlines()[1:]:
                    parts = line.split("\t")
                    if len(parts) >= 5:
                        ssid = parts[4].strip()
                        signal = parts[2].strip()
                        flags = parts[3].strip()
                        if not ssid or ssid in seen:
                            continue
                        seen.add(ssid)
                        sec = "WPA2" if "WPA" in flags else ("Open" if "ESS" in flags else flags)
                        networks.append({
                            "ssid": ssid,
                            "signal": signal,
                            "security": sec
                        })
        except Exception as e:
            logger.error(f"wpa_cli scan error: {e}")

    return networks


def connect_to_wifi(ssid, password=None):
    """Connect to a Wi-Fi network using nmcli or wpa_cli."""
    logger.info(f"Connecting to SSID '{ssid}'...")
    if has_nmcli():
        cmd = ["nmcli", "dev", "wifi", "connect", ssid]
        if password:
            cmd.extend(["password", password])
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
            if res.returncode == 0:
                return True, "Successfully connected!"
            else:
                err = res.stderr.strip() or res.stdout.strip()
                return False, err
        except subprocess.TimeoutExpired:
            return False, "Connection timed out."
        except Exception as e:
            return False, str(e)
    
    # Fallback: wpa_cli
    if shutil.which("wpa_cli"):
        try:
            add_res = subprocess.run(["wpa_cli", "-i", "wlan0", "add_network"], capture_output=True, text=True, timeout=5)
            net_id = add_res.stdout.strip().splitlines()[-1]
            subprocess.run(["wpa_cli", "-i", "wlan0", "set_network", net_id, "ssid", f'"{ssid}"'], check=True, timeout=5)
            if password:
                subprocess.run(["wpa_cli", "-i", "wlan0", "set_network", net_id, "psk", f'"{password}"'], check=True, timeout=5)
            else:
                subprocess.run(["wpa_cli", "-i", "wlan0", "set_network", net_id, "key_mgmt", "NONE"], check=True, timeout=5)
            subprocess.run(["wpa_cli", "-i", "wlan0", "enable_network", net_id], check=True, timeout=5)
            subprocess.run(["wpa_cli", "-i", "wlan0", "save_config"], check=True, timeout=5)

            # Wait up to 15 seconds for valid IP
            for _ in range(15):
                time.sleep(1)
                ip = get_ip_address()
                if ip and "Not connected" not in ip:
                    return True, f"Connected with IP {ip}"
            return False, "Connected to network, but failed to obtain IP via DHCP."
        except Exception as e:
            return False, str(e)

    return False, "Neither nmcli nor wpa_cli found."


def disconnect_wifi():
    """Disconnect current Wi-Fi connection."""
    if has_nmcli():
        try:
            subprocess.run(["nmcli", "dev", "disconnect", "wlan0"], capture_output=True, timeout=10)
            return True, "Wi-Fi disconnected."
        except Exception as e:
            return False, str(e)
    if shutil.which("wpa_cli"):
        try:
            subprocess.run(["wpa_cli", "-i", "wlan0", "disconnect"], capture_output=True, timeout=5)
            return True, "Wi-Fi disconnected."
        except Exception as e:
            return False, str(e)
    return False, "Network tool not found."


# ---------------------------------------------------------
# BLE UART Communication Layer
# ---------------------------------------------------------

class UARTDevice:
    tx_obj = None

    @classmethod
    def on_connect(cls, ble_device: device.Device):
        logger.info(f"Connected to BLE central device: {ble_device.address}")

    @classmethod
    def on_disconnect(cls, adapter_address, device_address):
        logger.info(f"Disconnected from BLE device: {device_address}")
        cls.tx_obj = None

    @classmethod
    def uart_notify(cls, notifying, characteristic):
        """Called when Central (iPhone) enables/disables notifications."""
        if notifying:
            cls.tx_obj = characteristic
            logger.info("iPhone connected & subscribed to UART notifications.")
            # Send welcome menu with a small delay to let BLE handshake settle
            threading.Thread(target=cls._on_client_connected).start()
        else:
            cls.tx_obj = None
            logger.info("iPhone unsubscribed from UART notifications.")

    @classmethod
    def _on_client_connected(cls):
        global current_state, input_buffer
        time.sleep(1.0)
        with state_lock:
            current_state = STATE_MENU
            input_buffer = ""
        send_menu()

    @classmethod
    def uart_write(cls, value, options):
        """Called when Central sends data to the RX characteristic."""
        global input_buffer
        try:
            text = bytes(value).decode('utf-8', errors='ignore')
        except Exception:
            return

        with state_lock:
            input_buffer += text
            clean = input_buffer.strip()

            # Check for newline/carriage return
            if '\n' in input_buffer or '\r' in input_buffer:
                line = input_buffer.replace('\r', '\n').split('\n')[0].strip()
                input_buffer = ""
                if line:
                    threading.Thread(target=process_user_input, args=(line,)).start()
                else:
                    # Empty enter / Send refreshes menu
                    threading.Thread(target=send_menu).start()
            # Fast reaction for single-character navigation without waiting for newline
            elif current_state == STATE_MENU and len(clean) == 1 and clean.lower() in "12345mh?rc":
                line = clean
                input_buffer = ""
                threading.Thread(target=process_user_input, args=(line,)).start()


def _idle_set_value(chunk):
    """Thread-safe D-Bus characteristic update via GLib idle."""
    if UARTDevice.tx_obj:
        try:
            UARTDevice.tx_obj.set_value(chunk)
        except Exception as e:
            logger.error(f"Error setting BLE value: {e}")
    return False  # Run once


def send_raw_text(text: str):
    """
    Transmit text over BLE TX characteristic.
    Chunks text into 20-byte packets to conform with BLE ATT MTU standards
    and ensure reliability on iOS terminal apps.
    """
    if not UARTDevice.tx_obj:
        return

    # Normalize line endings for iOS terminal display
    text = text.replace('\r\n', '\n').replace('\n', '\r\n')
    data = text.encode('utf-8', errors='ignore')
    chunk_size = 20

    for i in range(0, len(data), chunk_size):
        chunk = list(data[i:i + chunk_size])
        GLib.idle_add(_idle_set_value, chunk)
        time.sleep(0.025)  # 25ms delay between BLE packets


def send_menu():
    """Display the main interactive menu."""
    ssid, ip = get_current_wifi_info()
    status_str = f"Connected to '{ssid}'" if ssid else "Disconnected"

    menu = (
        "\n"
        "================================\n"
        "   Pi Zero Wi-Fi Controller     \n"
        "================================\n"
        f"Status: {status_str}\n"
        f"IP    : {ip}\n"
        "--------------------------------\n"
        "[1] Scan Wi-Fi Networks\n"
        "[2] Connection Status & IP\n"
        "[3] Connect Manually (Enter SSID)\n"
        "[4] Disconnect Current Wi-Fi\n"
        "[5] Reboot Raspberry Pi\n"
        "--------------------------------\n"
        "Enter choice (1-5): "
    )
    send_raw_text(menu)


# ---------------------------------------------------------
# Input Processing & State Machine
# ---------------------------------------------------------

def process_user_input(line: str):
    global current_state, scanned_networks, selected_ssid, selected_security

    logger.info(f"User input received: '{line}' in state {current_state}")

    # Universal escape commands
    if line.lower() in ["m", "menu", "cancel", "c", "exit"]:
        with state_lock:
            current_state = STATE_MENU
            scanned_networks = []
            selected_ssid = None
        send_raw_text("\nReturning to main menu...\n")
        send_menu()
        return

    with state_lock:
        state = current_state

    # 1. MAIN MENU STATE
    if state == STATE_MENU:
        if line == "1":
            send_raw_text("\nScanning for Wi-Fi networks... Please wait...\n")
            networks = scan_wifi_networks()
            if not networks:
                send_raw_text("\nNo Wi-Fi networks found. Try again or check antenna.\n")
                send_menu()
                return

            with state_lock:
                scanned_networks = networks
                current_state = STATE_SELECT_NETWORK

            output = ["\nAvailable Wi-Fi Networks:"]
            for idx, net in enumerate(networks, start=1):
                sec = net['security']
                output.append(f"  [{idx}] {net['ssid']} (Signal: {net['signal']}%, {sec})")
            output.append("--------------------------------")
            output.append("Enter network number (or 'r' to rescan, 'm' for menu): ")
            send_raw_text("\n".join(output))

        elif line == "2":
            ssid, ip = get_current_wifi_info()
            info = (
                "\n--- Current Wi-Fi Status ---\n"
                f"Connected SSID : {ssid or 'None (Offline)'}\n"
                f"IP Address     : {ip}\n"
                f"SSH Access     : ssh pi@{ip}\n"
                "----------------------------\n"
            )
            send_raw_text(info)
            send_menu()

        elif line == "3":
            with state_lock:
                current_state = STATE_ENTER_MANUAL_SSID
            send_raw_text("\nEnter network SSID name (or 'c' to cancel): ")

        elif line == "4":
            send_raw_text("\nDisconnecting Wi-Fi...\n")
            success, msg = disconnect_wifi()
            send_raw_text(f"{msg}\n")
            send_menu()

        elif line == "5":
            send_raw_text("\nRebooting Raspberry Pi now... Connection will close.\n")
            time.sleep(2)
            subprocess.run(["sudo", "reboot"])

        else:
            send_raw_text("\nInvalid option. Enter 1 to 5, or 'm' for menu.\n")

    # 2. SELECT NETWORK STATE
    elif state == STATE_SELECT_NETWORK:
        if line.lower() in ["r", "rescan"]:
            send_raw_text("\nRescanning Wi-Fi networks...\n")
            networks = scan_wifi_networks()
            with state_lock:
                scanned_networks = networks
            output = ["\nAvailable Wi-Fi Networks:"]
            for idx, net in enumerate(networks, start=1):
                output.append(f"  [{idx}] {net['ssid']} (Signal: {net['signal']}%, {net['security']})")
            output.append("--------------------------------")
            output.append("Enter network number (or 'm' for menu): ")
            send_raw_text("\n".join(output))
            return

        try:
            choice = int(line)
            if 1 <= choice <= len(scanned_networks):
                net = scanned_networks[choice - 1]
                with state_lock:
                    selected_ssid = net['ssid']
                    selected_security = net['security']

                if "open" in selected_security.lower() or selected_security == "":
                    send_raw_text(f"\nConnecting to open network '{selected_ssid}'...\n")
                    success, msg = connect_to_wifi(selected_ssid)
                    _handle_connect_result(success, msg)
                else:
                    with state_lock:
                        current_state = STATE_ENTER_PASSWORD
                    send_raw_text(f"\nSelected: '{selected_ssid}' ({selected_security})\n")
                    send_raw_text("Enter Wi-Fi password (or 'c' to cancel): ")
            else:
                send_raw_text(f"\nPlease enter a number between 1 and {len(scanned_networks)}: ")
        except ValueError:
            send_raw_text("\nInvalid input. Enter the network number, 'r' to rescan, or 'm' for menu: ")

    # 3. ENTER PASSWORD STATE
    elif state == STATE_ENTER_PASSWORD:
        password = line
        with state_lock:
            ssid = selected_ssid
        send_raw_text(f"\nConnecting to '{ssid}'... Please wait (up to 30s)...\n")
        success, msg = connect_to_wifi(ssid, password)
        _handle_connect_result(success, msg)

    # 4. ENTER MANUAL SSID STATE
    elif state == STATE_ENTER_MANUAL_SSID:
        with state_lock:
            selected_ssid = line
            current_state = STATE_ENTER_PASSWORD
        send_raw_text(f"\nSSID set to: '{line}'\n")
        send_raw_text("Enter Wi-Fi password (or send empty if open, 'c' to cancel): ")


def _handle_connect_result(success: bool, msg: str):
    """Handle and format connection results back to the phone."""
    global current_state, scanned_networks, selected_ssid
    with state_lock:
        current_state = STATE_MENU
        ssid = selected_ssid
        selected_ssid = None
        scanned_networks = []

    if success:
        # Give network stack 2 seconds to settle IP assignment
        time.sleep(2)
        ip = get_ip_address()
        res = (
            "\n"
            "========================================\n"
            "  >>> SUCCESS: CONNECTED TO WI-FI! <<<  \n"
            "========================================\n"
            f"Network Name : {ssid}\n"
            f"IP Address   : {ip}\n"
            f"SSH Login    : ssh pi@{ip}\n"
            "========================================\n"
            "\nYou can now disconnect Bluetooth or keep it running.\n"
        )
        send_raw_text(res)
    else:
        res = (
            "\n"
            "----------------------------------------\n"
            "  >>> ERROR: CONNECTION FAILED <<<      \n"
            "----------------------------------------\n"
            f"Reason: {msg}\n"
            "----------------------------------------\n"
        )
        send_raw_text(res)

    send_menu()


# ---------------------------------------------------------
# Main Application Initialization
# ---------------------------------------------------------

def ensure_bluetooth_ready():
    """Unblock, bring up, and power on the Bluetooth adapter."""
    try:
        subprocess.run(["rfkill", "unblock", "bluetooth"], capture_output=True)
        subprocess.run(["hciconfig", "hci0", "up"], capture_output=True)
        subprocess.run(["bluetoothctl", "power", "on"], capture_output=True)
    except Exception as e:
        logger.warning(f"Could not auto-power Bluetooth: {e}")


def get_adapter_address():
    """Find local Bluetooth adapter address with boot-delay tolerance (up to 60s)."""
    for attempt in range(1, 31):
        # Actively ensure Bluetooth hardware is up on every check
        if attempt % 2 == 1:
            ensure_bluetooth_ready()

        # Method 1: adapter.Adapter.available()
        try:
            adapters = list(adapter.Adapter.available())
            if adapters:
                addr = adapters[0].address
                logger.info(f"Found Bluetooth adapter {addr} via Adapter.available()")
                return addr
        except Exception as e:
            logger.debug(f"Adapter.available() error: {e}")

        # Method 2: adapter.list_adapters()
        try:
            dongles = list(adapter.list_adapters())
            if dongles:
                dongle = dongles[0]
                try:
                    addr = adapter.Adapter(dongle).address
                    logger.info(f"Found Bluetooth adapter {addr} via Adapter({dongle})")
                    return addr
                except Exception:
                    logger.info(f"Using Bluetooth dongle identifier: {dongle}")
                    return dongle
        except Exception as e:
            logger.debug(f"list_adapters() error: {e}")

        # Method 3: Parse from bluetoothctl
        try:
            res = subprocess.run(["bluetoothctl", "list"], capture_output=True, text=True, timeout=2)
            for line in res.stdout.strip().splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[0] == "Controller":
                    addr = parts[1]
                    logger.info(f"Found Bluetooth controller {addr} via bluetoothctl")
                    return addr
        except Exception:
            pass

        logger.info(f"Waiting for Bluetooth hardware to initialize (attempt {attempt}/30)...")
        time.sleep(2)

    return None


def main():
    logger.info("Initializing Raspberry Pi BLE Wi-Fi Controller...")
    ensure_bluetooth_ready()

    # Find local Bluetooth adapter
    adapter_address = get_adapter_address()
    if not adapter_address:
        logger.error("No Bluetooth adapter found! Ensure Bluetooth hardware is available and enabled.")
        sys.exit(1)

    logger.info(f"Using Bluetooth adapter: {adapter_address}")

    # Explicitly ensure adapter power is ON before creating peripheral
    try:
        dongle = adapter.Adapter(adapter_address)
        if not dongle.powered:
            logger.info(f"Adapter {adapter_address} is powered off. Powering on...")
            dongle.powered = True
            time.sleep(1)
    except Exception as e:
        logger.warning(f"Could not verify/set dongle.powered: {e}")

    # BLE Advertising name: Keep under 10 chars to fit standard BLE advertising packet
    device_name = "Pi-WiFi"

    ble_peripheral = peripheral.Peripheral(
        adapter_address=adapter_address,
        local_name=device_name,
        appearance=0
    )

    # 1. Add Nordic UART Service (NUS)
    ble_peripheral.add_service(
        srv_id=1,
        uuid=UART_SERVICE,
        primary=True
    )

    # 2. Add RX Characteristic (Write / Write Without Response)
    ble_peripheral.add_characteristic(
        srv_id=1,
        chr_id=1,
        uuid=RX_CHARACTERISTIC,
        value=[],
        notifying=False,
        flags=['write', 'write-without-response'],
        write_callback=UARTDevice.uart_write,
        read_callback=None,
        notify_callback=None
    )

    # 3. Add TX Characteristic (Notify)
    ble_peripheral.add_characteristic(
        srv_id=1,
        chr_id=2,
        uuid=TX_CHARACTERISTIC,
        value=[],
        notifying=False,
        flags=['notify'],
        notify_callback=UARTDevice.uart_notify,
        read_callback=None,
        write_callback=None
    )

    ble_peripheral.on_connect = UARTDevice.on_connect
    ble_peripheral.on_disconnect = UARTDevice.on_disconnect

    logger.info(f"Broadcasting BLE device as '{device_name}'...")
    logger.info("Ready for iPhone connection using Adafruit Bluefruit LE Connect or nRF Toolbox/BLE Terminal.")

    # Start advertising and GLib event loop
    try:
        ble_peripheral.publish()
    except KeyboardInterrupt:
        logger.info("Shutting down BLE service...")


if __name__ == '__main__':
    main()
