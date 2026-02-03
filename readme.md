# 📍 Projet IOT - Géolocalisation WiFi Campus Sorbonne Université

Application de géolocalisation pour le campus de Sorbonne Université utilisant les signaux WiFi existants. Le système calcule la position précise de l'utilisateur sans GPS en analysant les réseaux WiFi environnants.

**Principe :** Comparaison des signatures WiFi en temps réel avec une base de référence + calcul GPS par trilatération pondérée.


## 📁 Structure du Projet

### **Fichiers principaux**

- **`fastapi_server.py`** : Serveur backend FastAPI
  - Algorithme de géolocalisation (fingerprinting + trilatération)
  - APIs REST (GET /position/estimate, /networks/current, /locations/all)
  - WebSocket temps réel (WS /ws/position)
  - Publication MQTT vers broker externe

- **`Projet_IOT.ino`** : Code Arduino pour ESP32
  - Scan des réseaux WiFi toutes les 3 secondes
  - Mesure RSSI (force du signal)
  - Communication série vers le serveur

- **`wifi_data.csv`** : Base de données de référence
  - Empreintes WiFi de chaque bâtiment du campus
  - Format : timestamp, location, ssid, bssid, rssi, channel

- **`ap_reference.csv`** : Coordonnées GPS des bâtiments
  - Position GPS exacte du centre de chaque tour
  - Format : salle, latitude, longitude

- **`ap_database.csv`** : Scans temps réel
  - Stockage temporaire des scans WiFi en cours
  - Nettoyé automatiquement après 1000 scans

### **Dossier static/**
Interface web (HTML/CSS/JavaScript)
  - Carte interactive Leaflet.js
  - Affichage position temps réel via WebSocket
  - Statistiques (confiance, réseaux détectés, alternatives)


## ⚙️ Fonctionnement

### **1. Acquisition (ESP32)**
- ESP32 scanne les réseaux WiFi environnants
- Mesure le RSSI de chaque réseau
- Envoie les données au serveur via port série (USB)

### **2. Traitement (Serveur FastAPI)**
- **Fingerprinting** : Compare les RSSI détectés avec wifi_data.csv
- **Scoring** : Calcule un score pour chaque bâtiment
- **Confiance** : Vérifie que la détection est fiable (≥ 70%)
- **Trilatération GPS** : Convertit RSSI → Distance → GPS précis
- **Stabilisation** : Vote sur 12 scans pour éviter les sauts erratiques

### **3. Affichage (Interface Web)**
- WebSocket reçoit la position toutes les 3 secondes
- Repère GPS mis à jour en temps réel sur la carte
- Affichage du bâtiment détecté et niveau de confiance

### **4. Diffusion (MQTT)**
- Publication sur broker MQTT (broker.hivemq.com)
- Topics : position, réseaux, statut système
- Permet à d'autres applications de s'abonner aux données

## 🚀 Installation

### **Prérequis**
- Python 3.8+
- ESP32 avec module WiFi
- Port série disponible (COM3 par défaut)

### **Installation des dépendances**

pip install -r requirements.txt


### **Upload du code Arduino**
1. Ouvrir Projet_IOT.ino dans Arduino IDE
2. Sélectionner carte ESP32
3. Téléverser sur l'ESP32


## 🎮 Utilisation

### **1. Lancer le serveur**

python fastapi_server.py

Le serveur démarre sur http://localhost:8000

### **2. Ouvrir l'interface web**
Accéder à http://localhost:8000 dans un navigateur

### **3. Voir la position en temps réel**
- Le repère bleu sur la carte montre ta position GPS calculée
- Le texte en haut affiche le bâtiment détecté
- La confiance indique la fiabilité de la détection (70-100%)


## 📊 Paramètres Configurables

Dans fastapi_server.py :

# Confiance minimum pour affichage
MIN_CONFIDENCE_TO_DISPLAY = 70  # 70%

# Stabilisation
POSITION_HISTORY_SIZE = 12  # Historique de 12 scans
MIN_CONSISTENT_DETECTIONS = 8  # 8/12 requis pour changer

# MQTT
MQTT_BROKER = "broker.hivemq.com"
MQTT_TOPIC_POSITION = "sorbonne/wifi/geolocation/position"

## 🔧 Enrichir la Base de Données

Pour améliorer la précision (confiance > 80%) :

- Lancer sur cmd : python scan_wifi.py

## Technologies Utilisées

**Backend :**
- Python 3
- FastAPI (serveur web)
- Pandas/NumPy (traitement données)
- Paho-MQTT (publication)

**Frontend :**
- HTML/CSS/JavaScript
- Leaflet.js (carte interactive)
- WebSocket API

**Hardware :**
- ESP32 (microcontrôleur)
- Module WiFi intégré

**Protocoles :**
- HTTP REST
- WebSocket
- MQTT
- Série USB

## Performances

- ✅ Précision GPS : < 5 mètres
- ✅ Confiance : 70-100%
- ✅ Temps détection initiale : 3-6 secondes
- ✅ Temps changement bâtiment : ~21 secondes
- ✅ Fréquence mise à jour : 3 secondes
- ✅ Stabilité : Aucun saut erratique