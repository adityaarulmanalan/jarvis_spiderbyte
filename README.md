# J.A.R.V.I.S — AgroBot 🌱

> **Just Another Rover for Vegetation Inspection & Spraying**

An AI-powered autonomous agricultural rover that navigates crop rows, detects whitefly-infested and diseased seedlings using pixel-level colour analysis, and precision-sprays only the infected plants — without manual scouting, wasted chemicals, or unaffordable technology.

---

## 🚀 What It Does

- **Detects** diseased and whitefly-infested seedlings at single-plant level using the phone camera
- **Sprays** only infected plants — eliminating blanket pesticide waste
- **Navigates** crop rows autonomously via ESP32 with server-relayed steering commands
- **Monitors** rover status, GPS position, spray logs, battery, tank, and live weather on a web dashboard
- **Supports** GPS-based calibration and autonomous path replay

---

## 🧱 Architecture

```
[ESP32 Rover] ──heartbeat──► [Flask Server] ◄──poll──► [Web Dashboard]
                                    ▲
                           GPS + scan results
                                    │
                          [Flutter Mobile App]
                         (camera + GPS + analysis)
```

### Components

| Layer | Technology |
|-------|-----------|
| Hardware | ESP32, DC motors, spray nozzle, LiPo battery |
| Mobile App | Flutter (Dart), Camera, Geolocator |
| Backend | Flask (Python), REST API, in-memory state |
| Dashboard | Vanilla HTML/CSS/JS, Leaflet.js |
| AI / Vision | Pixel-level colour classifier (green/yellow/brown ratios) |
| Weather | OpenWeatherMap API |

---

## 📁 Project Structure

```
jarvis-agrobot/
├── server/
│   └── app.py          # Flask backend — rover state, commands, scan relay
├── dashboard/
│   └── index.html      # Web dashboard — live map, controls, alerts
├── mobile/
│   └── lib/
│       └── main.dart   # Flutter app — camera, GPS, plant scan
├── requirements.txt    # Python dependencies
└── README.md
```

---

## ⚙️ Setup & Running

### 1. Backend (Flask Server)

```bash
# Install dependencies
pip install -r requirements.txt

# Run the server
cd server
python app.py
```

Server starts at `http://0.0.0.0:5000`

> Make sure your PC and all devices (phone + ESP32) are on the **same WiFi network**.

---

### 2. Web Dashboard

Open `dashboard/index.html` in a browser, or the Flask server will serve it automatically at `http://<your-pc-ip>:5000`.

---

### 3. Flutter Mobile App

```bash
cd mobile

# Create all flutter files
flutter creaate .

# Install Flutter dependencies
flutter pub get

# Update the server IP in main.dart
# const String kServer = "http://<your-pc-ip>:5000";

# Build and run
flutter run
```

**Required Flutter packages** (add to `pubspec.yaml`):
```yaml
dependencies:
  flutter:
    sdk: flutter
  http: ^1.2.0
  camera: ^0.10.5
  image: ^4.1.3
  geolocator: ^11.0.0
```

**Android permissions** (add to `AndroidManifest.xml`):
```xml
<uses-permission android:name="android.permission.CAMERA"/>
<uses-permission android:name="android.permission.ACCESS_FINE_LOCATION"/>
<uses-permission android:name="android.permission.ACCESS_COARSE_LOCATION"/>
<uses-permission android:name="android.permission.INTERNET"/>
```

---

### 4. ESP32 Firmware

The ESP32 should:
- Connect to the same WiFi network
- POST to `/heartbeat` every 2 seconds with `battery`, `tank`, `temp`
- Read `command` and `steer` from the heartbeat response
- POST to `/scan_request` when stopping at a plant
- GET `/scan_result` every 300ms until result arrives
- POST to `/sprayed` after spraying a DEFECTIVE plant

---

## 🔌 API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/data` | Full rover state for dashboard |
| GET | `/weather` | Live weather data |
| POST | `/command` | Send `start`, `stop`, `dock` |
| POST | `/heartbeat` | ESP32 heartbeat — returns command + steer |
| POST | `/sprayed` | ESP32 reports a spray event |
| POST | `/scan_request` | ESP32 requests a plant scan |
| GET | `/scan_request` | Phone polls for pending scan |
| POST | `/scan_result` | Phone posts scan result |
| GET | `/scan_result` | ESP32 polls for scan result |
| POST | `/location` | Phone posts GPS coordinates |
| POST | `/calibrate/start` | Begin path calibration |
| POST | `/calibrate/steer` | Manual drive during calibration |
| POST | `/calibrate/done` | Finish and save calibration path |
| POST | `/calibrate/replay` | Start autonomous path replay |
| POST | `/calibrate/cancel` | Cancel calibration or replay |

---

## 🌿 Plant Detection Algorithm

The mobile app captures a photo and runs a pixel-level colour analysis:

```
For each pixel in the center third of the image:
  - Green pixel:  2G − R − B > 20  AND  G > 50
  - Yellow pixel: R > 150, G > 130, B < 100, R > G
  - Brown pixel:  R > 100, G < 90,  B < 80,  R > B

Result:
  DEFECTIVE  if brown > 4%  OR  yellow > 7%  OR  green < 25%
  HEALTHY    otherwise
```

---

## ⚠️ Configuration

Update these values before running:

**`server/app.py`**
```python
OWM_KEY = "your_openweathermap_api_key"
```

**`mobile/lib/main.dart`**
```dart
const String kServer = "http://<your-pc-ip>:5000";
```

---

## 🛠️ Built With

- [Flask](https://flask.palletsprojects.com/) — Python web framework
- [Flutter](https://flutter.dev/) — Cross-platform mobile framework
- [Leaflet.js](https://leafletjs.com/) — Interactive maps
- [OpenWeatherMap API](https://openweathermap.org/api) — Weather data
- ESP32 — Microcontroller for rover hardware

---

## 📄 License

MIT License — free to use, modify, and distribute.
