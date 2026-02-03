import csv
import os
import time
import threading
import serial
import pandas as pd
import numpy as np
import json
from collections import defaultdict, deque
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
import uvicorn
from datetime import datetime
import paho.mqtt.client as mqtt

# ================= CONFIG =================
SERIAL_PORT = "COM3"
SERIAL_BAUD = 115200

WIFI_REF_FILE = "wifi_data.csv"
AP_REALTIME_FILE = "ap_database.csv"
AP_GPS_FILE = "ap_reference.csv"

SCAN_INTERVAL_SEC = 3
MAX_SCAN_HISTORY = 1000

# ================= CONFIG MQTT =================
MQTT_BROKER = "broker.hivemq.com"  # ou "localhost" si tu as Mosquitto local
MQTT_PORT = 1883
MQTT_KEEPALIVE = 60

# Topics MQTT
MQTT_TOPIC_POSITION = "sorbonne/wifi/geolocation/position"
MQTT_TOPIC_NETWORKS = "sorbonne/wifi/geolocation/networks"
MQTT_TOPIC_STATUS = "sorbonne/wifi/geolocation/status"

# ================= PARAMÈTRES ALGORITHME =================
MIN_MATCHED_MACS = 3  # Au moins 3 MACs en commun
RSSI_DIFF_THRESHOLD = 35  # Tolérance différence RSSI

# Stabilisation ULTRA-FORTE
POSITION_HISTORY_SIZE = 12
MIN_CONSISTENT_DETECTIONS = 8  # 8/12 = 67%
CONFIDENCE_CHANGE_THRESHOLD = 25
STRONG_SIGNAL_BOOST = True

# Ambiguïté
SCORE_RATIO_THRESHOLD = 2.0  # Ratio min entre meilleur et 2ème

# CONFIANCE MINIMUM 70%
MIN_CONFIDENCE_TO_DISPLAY = 70

# ================= GPS PAR TRILATÉRATION RSSI =================
# Utiliser trilatération pondérée par RSSI
GPS_USE_TRILATERATION = True
N_BUILDINGS_FOR_TRILATERATION = 3  # Top 3 bâtiments

# Modèle propagation WiFi
RSSI_AT_1M = -30  # RSSI de référence à 1m
PATH_LOSS_EXPONENT = 2.4  # Exposant perte de signal

# Pondération force signal (scoring)
RSSI_WEIGHT_VERY_STRONG = 10.0  # > -50 dBm
RSSI_WEIGHT_STRONG = 6.0        # > -60 dBm  
RSSI_WEIGHT_MEDIUM = 3.0        # > -70 dBm
RSSI_WEIGHT_WEAK = 1.0          # < -70 dBm

# ================= INIT =================
def init_csv(path):
    if not os.path.exists(path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(
                ["timestamp_ms", "location", "ssid", "bssid", "rssi", "channel"]
            )

init_csv(AP_REALTIME_FILE)

# ================= INIT MQTT =================
mqtt_client = None
mqtt_connected = False

def on_mqtt_connect(client, userdata, flags, rc):
    global mqtt_connected
    if rc == 0:
        mqtt_connected = True
        print("✓ MQTT connecté au broker")
    else:
        mqtt_connected = False
        print(f"⚠ MQTT échec connexion : code {rc}")

def on_mqtt_disconnect(client, userdata, rc):
    global mqtt_connected
    mqtt_connected = False
    print("⚠ MQTT déconnecté")

try:
    mqtt_client = mqtt.Client()
    mqtt_client.on_connect = on_mqtt_connect
    mqtt_client.on_disconnect = on_mqtt_disconnect
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, MQTT_KEEPALIVE)
    mqtt_client.loop_start()
    print(f"✓ MQTT initialisé vers {MQTT_BROKER}:{MQTT_PORT}")
except Exception as e:
    mqtt_client = None
    print(f"⚠ MQTT non disponible : {e}")

def publish_to_mqtt(topic, payload):
    """Publie un message sur MQTT"""
    if mqtt_client and mqtt_connected:
        try:
            result = mqtt_client.publish(topic, json.dumps(payload), qos=1)
            if result.rc == 0:
                print(f"📤 MQTT publié sur {topic}")
            else:
                print(f"⚠ MQTT échec publication sur {topic}")
        except Exception as e:
            print(f"⚠ MQTT erreur : {e}")

app = FastAPI(title="WiFi Campus Geolocation")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

try:
    ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=2)
    time.sleep(2)
    print("✓ Serial connecté")
except Exception as e:
    ser = None
    print(f"⚠ Mode démo : {e}")

current_position = {"available": False, "estimated_location": "Inconnu", "latitude": 0.0, "longitude": 0.0, "confidence": 0.0}
position_history = deque(maxlen=POSITION_HISTORY_SIZE)
current_stable_location = None
connected_websockets = []
last_scan_networks = []

# ================= TRILATÉRATION PRÉCISE =================

def rssi_to_distance(rssi, rssi_ref=RSSI_AT_1M, n=PATH_LOSS_EXPONENT):
    """Convertit RSSI en distance estimée (mètres)"""
    if rssi >= rssi_ref:
        return 1.0
    distance = 10 ** ((rssi_ref - rssi) / (10 * n))
    return max(1.0, min(distance, 150))


def calculate_gps_trilateration(location_scores, gps_df):
    """
    Calcule GPS par trilatération pondérée RSSI
    
    Principe:
    - Pour chaque bâtiment: calcule distance estimée à partir RSSI moyen
    - Poids = 1 / (distance²) → plus proche = poids plus fort
    - GPS final = barycentre pondéré des positions
    """
    
    # Top N bâtiments par score
    top_buildings = sorted(
        location_scores.items(),
        key=lambda x: x[1]['score'],
        reverse=True
    )[:N_BUILDINGS_FOR_TRILATERATION]
    
    if len(top_buildings) == 0:
        return None, None, "no_data", []
    
    weighted_lat = 0.0
    weighted_lon = 0.0
    total_weight = 0.0
    distances_info = []
    
    for building, data in top_buildings:
        # Coordonnées GPS du bâtiment
        row = gps_df[gps_df["salle"] == building]
        if row.empty:
            continue
        
        building_lat = float(row.iloc[0]["latitude"])
        building_lon = float(row.iloc[0]["longitude"])
        
        # RSSI moyen pour ce bâtiment
        avg_rssi = data.get('avg_rssi', -999)
        strongest_rssi = data.get('strongest_rssi', -999)
        
        # Utiliser le RSSI le plus fort pour estimer la distance
        # (plus fiable que la moyenne pour la distance)
        if strongest_rssi > -999:
            distance = rssi_to_distance(strongest_rssi)
        elif avg_rssi > -999:
            distance = rssi_to_distance(avg_rssi)
        else:
            continue
        
        # Ajustement distance selon MACs fortes
        if data['primary_macs'] >= 3:
            distance *= 0.5  # Beaucoup de signaux forts = très proche
        elif data['primary_macs'] >= 2:
            distance *= 0.7
        elif data['primary_macs'] >= 1:
            distance *= 0.85
        
        # Poids = inverse du carré de la distance
        # Plus on est proche, plus le poids est élevé
        weight = 1.0 / (distance ** 2.5 + 0.5)
        
        # Bonus ÉNORME pour le meilleur bâtiment
        if building == top_buildings[0][0]:
            weight *= 8.0
        
        # Bonus selon score
        weight *= (data['score'] / 100.0)
        
        weighted_lat += building_lat * weight
        weighted_lon += building_lon * weight
        total_weight += weight
        
        distances_info.append({
            'building': building,
            'distance_m': round(distance, 1),
            'weight': round(weight, 4),
            'rssi_avg': round(avg_rssi, 1),
            'rssi_max': strongest_rssi,
            'primary_macs': data['primary_macs']
        })
    
    if total_weight == 0:
        return None, None, "no_weight", distances_info
    
    # Barycentre pondéré
    final_lat = weighted_lat / total_weight
    final_lon = weighted_lon / total_weight
    
    return final_lat, final_lon, "trilateration", distances_info


# ================= SCAN LOOP =================
def clean_csv_if_needed():
    try:
        df = pd.read_csv(AP_REALTIME_FILE, on_bad_lines="skip")
        if len(df) > MAX_SCAN_HISTORY:
            df.to_csv(AP_REALTIME_FILE, index=False)
    except:
        pass

def scan_loop():
    if not ser:
        return

    global current_position, last_scan_networks

    while True:
        try:
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
                    if not recording or not line:
                        continue

                    parts = line.split(",", 5)
                    if len(parts) != 6 or not parts[0].isdigit():
                        continue

                    try:
                        rows.append([int(parts[0]), "UNKNOWN", parts[2].strip() or "?", parts[3].strip(), int(parts[4]), int(parts[5])])
                    except:
                        continue

            if rows:
                with open(AP_REALTIME_FILE, "a", newline="", encoding="utf-8") as f:
                    csv.writer(f, quoting=csv.QUOTE_MINIMAL).writerows(rows)
                clean_csv_if_needed()

                last_scan_networks = [{"ssid": r[2], "bssid": r[3], "rssi": r[4], "channel": r[5]} for r in rows]
                
                # Publier réseaux détectés sur MQTT
                publish_to_mqtt(MQTT_TOPIC_NETWORKS, {
                    "count": len(last_scan_networks),
                    "networks": last_scan_networks,
                    "timestamp": datetime.now().isoformat()
                })
                
                pos = estimate_with_stability(last_scan_networks)
                if pos:
                    current_position = pos
                    
                    # Publier position sur MQTT
                    publish_to_mqtt(MQTT_TOPIC_POSITION, {
                        "location": pos.get("estimated_location"),
                        "latitude": pos.get("latitude"),
                        "longitude": pos.get("longitude"),
                        "confidence": pos.get("confidence"),
                        "method": pos.get("method"),
                        "timestamp": pos.get("timestamp")
                    })
                    
                    notify_websockets(pos)

        except Exception as e:
            print(f"⚠ Erreur : {e}")

        time.sleep(SCAN_INTERVAL_SEC)

def notify_websockets(position):
    for ws in connected_websockets[:]:
        try:
            import asyncio
            asyncio.create_task(ws.send_json(position))
        except:
            connected_websockets.remove(ws)

# ================= STABILISATION =================
def estimate_with_stability(networks_data):
    global position_history, current_stable_location
    
    raw = estimate_position_final(networks_data)
    
    if not raw or not raw.get("available"):
        return raw
    
    detected = raw["estimated_location"]
    confidence = raw["confidence"]
    reason = "Unknown"  # Initialiser reason
    
    position_history.append({"location": detected, "confidence": confidence, "timestamp": time.time()})
    
    counts = defaultdict(int)
    max_conf = defaultdict(float)
    
    for entry in position_history:
        loc = entry["location"]
        counts[loc] += 1
        max_conf[loc] = max(max_conf[loc], entry["confidence"])
    
    should_change = False
    new_location = detected
    
    if current_stable_location is None:
        should_change = True
        reason = "Init"
    elif detected == current_stable_location:
        should_change = False
        reason = "Stable"
    else:
        # Vérifier ambiguïté
        if raw.get("alternatives") and len(raw["alternatives"]) >= 2:
            ratio = raw["alternatives"][0]["score"] / max(raw["alternatives"][1]["score"], 0.01)
            if ratio < SCORE_RATIO_THRESHOLD:
                should_change = False
                new_location = current_stable_location
                reason = f"Ambiguïté (ratio={ratio:.2f})"
        
        if should_change != False:
            if counts[detected] >= MIN_CONSISTENT_DETECTIONS:
                should_change = True
                reason = f"Majorité ({counts[detected]}/{len(position_history)})"
            elif confidence > max_conf.get(current_stable_location, 0) + CONFIDENCE_CHANGE_THRESHOLD:
                should_change = True
                reason = f"+{confidence - max_conf.get(current_stable_location, 0):.1f}%"
            elif confidence > 90 and raw.get("primary_macs", 0) >= 4:
                should_change = True
                reason = "Confiance exceptionnelle"
            else:
                should_change = False
                new_location = current_stable_location
                reason = f"Maintien ({counts.get(current_stable_location, 0)}/{len(position_history)})"
    
    if should_change:
        current_stable_location = new_location
        print(f"🔄 → {new_location} | {reason}")
    else:
        print(f"📍 {current_stable_location} | {reason} | Détection: {detected} ({confidence:.0f}%)")
    
    # Retourner le bâtiment détecté (pas stabilisé) pour que le texte corresponde au GPS
    return {
        "available": True,
        "estimated_location": detected,  # Bâtiment détecté (correspond au GPS)
        "latitude": raw["latitude"],
        "longitude": raw["longitude"],
        "confidence": raw["confidence"],
        "method": raw.get("method"),
        "gps_method": raw.get("gps_method"),
        "distances": raw.get("distances", []),
        "score": raw.get("score", 0),
        "matched_macs": raw.get("matched_macs", 0),
        "primary_macs": raw.get("primary_macs", 0),
        "alternatives": raw.get("alternatives", []),
        "stability": {"stable": not should_change, "history": dict(counts), "reason": reason},
        "timestamp": datetime.now().isoformat()
    }

# ================= ALGORITHME PRINCIPAL =================
def estimate_position_final(networks_data):
    try:
        ref = pd.read_csv(WIFI_REF_FILE, on_bad_lines="skip")
        gps = pd.read_csv(AP_GPS_FILE, on_bad_lines="skip")

        if ref.empty or gps.empty:
            return {"available": False, "message": "Base vide"}

        ref["location"] = ref["location"].str.upper().str.strip()
        ref["bssid"] = ref["bssid"].str.upper().str.strip()
        ref["rssi"] = pd.to_numeric(ref["rssi"], errors="coerce")
        ref = ref.dropna(subset=["rssi", "bssid", "location"])
        
        gps["salle"] = gps["salle"].str.upper().str.strip()
        valid_buildings = set(gps["salle"])
        ref = ref[ref["location"].isin(valid_buildings)]

        # Base MAC/location
        mac_db = defaultdict(lambda: defaultdict(list))
        for _, row in ref.iterrows():
            mac_db[row["bssid"]][row["location"]].append(row["rssi"])

        for mac in mac_db:
            for loc in mac_db[mac]:
                mac_db[mac][loc] = sum(mac_db[mac][loc]) / len(mac_db[mac][loc])

        # Scoring
        scores = defaultdict(lambda: {'score': 0.0, 'matched_macs': 0, 'primary_macs': 0, 'rssi_diffs': [], 'strongest_rssi': -999, 'rssi_values': []})

        detected_macs = {net["bssid"].upper(): net["rssi"] for net in networks_data}
        matched = len(set(detected_macs.keys()) & set(mac_db.keys()))

        if matched < MIN_MATCHED_MACS:
            return {"available": False, "message": f"Seulement {matched} MAC(s)"}

        # Calcul scores
        for net in networks_data:
            mac = net["bssid"].upper()
            curr_rssi = net["rssi"]
            
            if mac not in mac_db:
                continue

            for loc, ref_rssi in mac_db[mac].items():
                diff = abs(curr_rssi - ref_rssi)
                
                if diff > RSSI_DIFF_THRESHOLD:
                    continue
                
                weight = np.exp(-diff / 10.0)
                
                # Pondération MASSIVE par force signal
                if curr_rssi > -50:
                    weight *= RSSI_WEIGHT_VERY_STRONG
                    scores[loc]['primary_macs'] += 1
                elif curr_rssi > -60:
                    weight *= RSSI_WEIGHT_STRONG
                    scores[loc]['primary_macs'] += 1
                elif curr_rssi > -70:
                    weight *= RSSI_WEIGHT_MEDIUM
                else:
                    weight *= RSSI_WEIGHT_WEAK
                
                scores[loc]['score'] += weight
                scores[loc]['matched_macs'] += 1
                scores[loc]['rssi_diffs'].append(diff)
                scores[loc]['rssi_values'].append(curr_rssi)
                scores[loc]['strongest_rssi'] = max(scores[loc]['strongest_rssi'], curr_rssi)

        if not scores:
            return {"available": False, "message": "Aucune correspondance"}

        # Normalisation
        for loc, data in scores.items():
            data['score'] *= min(2.5, 1 + data['matched_macs'] * 0.15)
            
            if STRONG_SIGNAL_BOOST and data['primary_macs'] > 0:
                data['score'] *= min(3.5, 1 + data['primary_macs'] * 0.5)
            
            data['avg_diff'] = sum(data['rssi_diffs']) / len(data['rssi_diffs']) if data['rssi_diffs'] else 999
            data['avg_rssi'] = sum(data['rssi_values']) / len(data['rssi_values']) if data['rssi_values'] else -999

        best_loc = max(scores.keys(), key=lambda x: scores[x]['score'])
        best = scores[best_loc]

        # Confiance
        all_scores = [d['score'] for d in scores.values()]
        total = sum(all_scores)
        
        if total == 0:
            return {"available": False, "message": "Scores nuls"}
        
        base = (best['score'] / total) * 100
        bonus_match = min(25, best['matched_macs'] * 4)
        bonus_primary = min(35, best['primary_macs'] * 9)
        bonus_consistency = 25 if best['avg_diff'] < 8 else (15 if best['avg_diff'] < 15 else 0)
        
        sorted_scores = sorted(all_scores, reverse=True)
        penalty = 0
        if len(sorted_scores) > 1:
            ratio = sorted_scores[1] / sorted_scores[0]
            penalty = -40 if ratio > 0.9 else (-30 if ratio > 0.75 else (-20 if ratio > 0.6 else 0))
        
        confidence = min(100, max(10, base + bonus_match + bonus_primary + bonus_consistency + penalty))
        
        # Vérif confiance min
        if confidence < MIN_CONFIDENCE_TO_DISPLAY:
            return {"available": False, "message": f"Confiance {confidence:.0f}% < {MIN_CONFIDENCE_TO_DISPLAY}%"}

        # GPS par trilatération
        final_lat, final_lon, gps_method, distances = calculate_gps_trilateration(scores, gps)
        
        if final_lat is None:
            return {"available": False, "message": f"{best_loc} sans GPS"}

        # Alternatives
        alts = []
        for loc, data in sorted(scores.items(), key=lambda x: x[1]['score'], reverse=True)[:5]:
            alts.append({"location": loc, "score": round(data['score'], 1), "matched_macs": data['matched_macs'], "primary_macs": data['primary_macs']})

        print(f"🎯 {best_loc} {confidence:.0f}% | GPS: ({final_lat:.6f}, {final_lon:.6f}) | MACs: {best['matched_macs']} (forts: {best['primary_macs']})")
        if distances:
            for d in distances:
                print(f"   • {d['building']}: {d['distance_m']}m | RSSI: {d['rssi_max']}dBm | Poids: {d['weight']:.4f}")

        return {
            "available": True,
            "estimated_location": best_loc,
            "latitude": final_lat,
            "longitude": final_lon,
            "confidence": round(confidence, 1),
            "method": "trilateration_rssi",
            "gps_method": gps_method,
            "distances": distances,
            "score": round(best['score'], 1),
            "matched_macs": best['matched_macs'],
            "primary_macs": best['primary_macs'],
            "alternatives": alts,
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        print(f"⚠ Erreur : {e}")
        import traceback
        traceback.print_exc()
        return {"available": False, "message": str(e)}


# ================= API =================
@app.get("/position/estimate")
async def api_estimate():
    return JSONResponse(current_position)

@app.get("/networks/current")
async def api_networks():
    try:
        df = pd.read_csv(AP_REALTIME_FILE, on_bad_lines="skip")
        if df.empty:
            return {"networks": []}
        latest = df["timestamp_ms"].max()
        data = df[df["timestamp_ms"] == latest]
        return {"timestamp": int(latest), "count": len(data), "networks": data.to_dict("records")}
    except Exception as e:
        return {"error": str(e)}

@app.get("/locations/all")
async def api_locations():
    try:
        gps = pd.read_csv(AP_GPS_FILE, on_bad_lines="skip")
        return {"locations": gps.to_dict("records")}
    except:
        return {"error": "Erreur"}

@app.websocket("/ws/position")
async def ws_position(ws: WebSocket):
    await ws.accept()
    connected_websockets.append(ws)
    await ws.send_json(current_position)
    try:
        while True:
            await ws.receive_text()
    except:
        connected_websockets.remove(ws)

app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    print("=" * 80)
    print("🚀 WiFi GEOLOCATION - TRILATÉRATION RSSI PRÉCISE + MQTT")
    print("=" * 80)
    print(f"📊 Config:")
    print(f"   • Confiance minimum: {MIN_CONFIDENCE_TO_DISPLAY}%")
    print(f"   • Stabilisation: {MIN_CONSISTENT_DETECTIONS}/{POSITION_HISTORY_SIZE}")
    print(f"   • GPS: Trilatération pondérée RSSI")
    print(f"   • Buildings pour trilatération: {N_BUILDINGS_FOR_TRILATERATION}")
    print(f"📡 MQTT:")
    print(f"   • Broker: {MQTT_BROKER}:{MQTT_PORT}")
    print(f"   • Topic Position: {MQTT_TOPIC_POSITION}")
    print(f"   • Topic Networks: {MQTT_TOPIC_NETWORKS}")
    print(f"   • Topic Status: {MQTT_TOPIC_STATUS}")
    print("=" * 80)

    # Publier statut démarrage sur MQTT
    publish_to_mqtt(MQTT_TOPIC_STATUS, {
        "status": "starting",
        "timestamp": datetime.now().isoformat()
    })

    if ser:
        threading.Thread(target=scan_loop, daemon=True).start()
        
    # Publier statut actif sur MQTT
    publish_to_mqtt(MQTT_TOPIC_STATUS, {
        "status": "active",
        "timestamp": datetime.now().isoformat()
    })

    uvicorn.run(app, host="0.0.0.0", port=8000)