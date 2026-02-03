"""
Script pour collecter des données WiFi de référence
Usage:
- Entraînement interactif : python train_wifi_database.py
- Analyse : python train_wifi_database.py analyze
"""

import serial
import csv
import time
import os

# ─────────────────────────────
# Configuration
# ─────────────────────────────
SERIAL_PORT = "COM3"
SERIAL_BAUD = 115200
OUTPUT_FILE = "wifi_data.csv"


# ─────────────────────────────
# Initialisation CSV
# ─────────────────────────────
def init_csv():
    if not os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["timestamp_ms", "location", "ssid", "bssid", "rssi", "channel"]
            )
        print(f"✓ Fichier {OUTPUT_FILE} créé")
    else:
        print(f"✓ Fichier {OUTPUT_FILE} existant")


# ─────────────────────────────
# Connexion série
# ─────────────────────────────
def connect_serial():
    try:
        ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=2)
        time.sleep(2)
        print(f"✓ Connecté au port {SERIAL_PORT}")
        return ser
    except Exception as e:
        print(f"❌ Erreur connexion série : {e}")
        return None


# ─────────────────────────────
# Localisation
# ─────────────────────────────
def send_location(ser, location):
    try:
        ser.write(f"LOC {location}\n".encode())
        time.sleep(0.5)

        if ser.in_waiting:
            response = ser.readline().decode(errors="ignore").strip()
            if "[OK]" in response:
                print(f"✓ Localisation définie : {location}")
                return True

        print("⚠ Pas de confirmation ESP32")
        return False

    except Exception as e:
        print(f"❌ Erreur localisation : {e}")
        return False


# ─────────────────────────────
# Scan WiFi (UN scan = TOUS les AP)
# ─────────────────────────────
def scan_wifi(ser):
    try:
        print("🔍 Scan WiFi en cours...")
        ser.write(b"SCAN\n")

        rows = []
        recording = False
        start_time = time.time()

        while time.time() - start_time < 15:
            if ser.in_waiting:
                line = ser.readline().decode(errors="ignore").strip()

                if line == "#DATA_START":
                    recording = True
                    continue

                if line == "#DATA_END":
                    break

                if recording and line:
                    parts = line.split(",", 5)
                    if len(parts) == 6 and parts[0].isdigit():
                        rows.append(parts)

        print(f"✓ Scan terminé : {len(rows)} points d’accès détectés")
        return rows

    except Exception as e:
        print(f"❌ Erreur scan : {e}")
        return []


# ─────────────────────────────
# Sauvegarde CSV
# ─────────────────────────────
def save_scan(rows):
    if not rows:
        print("⚠ Aucune donnée à sauvegarder")
        return

    try:
        with open(OUTPUT_FILE, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(rows)
        print(f"✓ {len(rows)} lignes enregistrées")
    except Exception as e:
        print(f"❌ Erreur sauvegarde : {e}")


# ─────────────────────────────
# Résumé scan
# ─────────────────────────────
def display_scan_summary(rows):
    if not rows:
        return

    print("\n" + "=" * 60)
    print("RÉSUMÉ DU SCAN WIFI")
    print("=" * 60)

    rows = sorted(rows, key=lambda x: int(x[4]), reverse=True)

    print(f"{'SSID':<30} {'BSSID':<20} {'RSSI':<6}")
    print("-" * 60)

    for row in rows[:10]:
        ssid = row[2][:28]
        print(f"{ssid:<30} {row[3]:<20} {row[4]:<6}")

    if len(rows) > 10:
        print(f"... + {len(rows) - 10} autres réseaux")

    print("=" * 60 + "\n")


# ─────────────────────────────
# Menu
# ─────────────────────────────
def show_menu():
    print("\n" + "-" * 60)
    print("MENU PRINCIPAL")
    print("-" * 60)
    print("1 - Définir la localisation")
    print("2 - Lancer un scan WiFi")
    print("3 - Quitter")


# ─────────────────────────────
# Mode interactif
# ─────────────────────────────
def interactive_training():
    print("\n" + "=" * 60)
    print("ENTRAÎNEMENT BASE DE DONNÉES WIFI")
    print("=" * 60)

    init_csv()
    ser = connect_serial()
    if not ser:
        return

    current_location = None
    scan_count = 0

    try:
        while True:
            show_menu()
            choice = input("Choix (1-3) : ").strip()

            if choice == "1":
                location = input("📍 Nom de la localisation : ").strip()
                if location and send_location(ser, location):
                    current_location = location

            elif choice == "2":
                if not current_location:
                    print("⚠ Définissez une localisation d'abord")
                    continue

                rows = scan_wifi(ser)
                save_scan(rows)
                display_scan_summary(rows)
                scan_count += 1

                print(f"📊 Scans effectués : {scan_count}")

            elif choice == "3":
                print("👋 Fin de session")
                break

            else:
                print("❌ Choix invalide")

    except KeyboardInterrupt:
        print("\n⚠ Interruption utilisateur")

    finally:
        ser.close()
        print("✓ Port série fermé")


# ─────────────────────────────
# Analyse base de données
# ─────────────────────────────
def analyze_database():
    import pandas as pd

    if not os.path.exists(OUTPUT_FILE):
        print("❌ Base de données introuvable")
        return

    df = pd.read_csv(OUTPUT_FILE)

    print("\n" + "=" * 60)
    print("ANALYSE BASE WIFI")
    print("=" * 60)
    print(f"Lignes totales : {len(df)}")
    print(f"Localisations uniques : {df['location'].nunique()}")
    print(f"BSSID uniques : {df['bssid'].nunique()}")
    print("=" * 60)


# ─────────────────────────────
# Main
# ─────────────────────────────
if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "analyze":
        analyze_database()
    else:
        interactive_training()
