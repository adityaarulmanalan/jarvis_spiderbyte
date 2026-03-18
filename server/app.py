from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import threading, time, requests, json, os, math

DIR           = os.path.dirname(os.path.abspath(__file__))
OWM_KEY       = "your_openweather_api"
OFFLINE_SECS  = 10
TEMP_CRITICAL = 75.0
TEMP_SAFE     = 45.0

app = Flask(__name__, static_folder=DIR, template_folder=DIR)
CORS(app)
lock = threading.Lock()

state = {
    "status": "Waiting", "state": "idle", "sub_state": "idle",
    "battery": -1.0,    # -1 = never read yet, avoids false "low battery" on boot
    "tank": 100.0, "temp": 0.0,
    "lat": 12.9998, "lon": 80.2209,
    "sprays_today": 0, "scans_today": 0,
    "spray_log": [], "scan_log": [], "alerts": [],
    "last_plant_result": "—",
    "temp_shutdown": False,
    "rain_percent": 0, "is_raining": False,
    "weather_temp": None, "weather_desc": "Loading...",
    "humidity": 0, "wind_kmh": 0,
    "calibrating": False, "calib_start": None, "calib_end": None,
    "calib_replay": False, "calib_replay_idx": 0,
    # Internal
    "_last_hb": 0.0, "_cmd": "NONE", "_steer": "STOP",
    "_scan_pending": False, "_scan_result": None, "_scan_t": 0.0,
    "_calib_path": [],
    "_phone_last_seen": 0.0,   # timestamp of last phone GPS/scan post
}

# ── Helpers ───────────────────────────────────────────────────────────────────
def pub():
    with lock:
        s = {k: v for k, v in state.items() if not k.startswith("_")}
        s["calib_waypoints"] = len(state["_calib_path"])
        # Phone is connected if it sent GPS or scan data in the last 15 seconds
        s["phone_connected"] = (time.time() - state["_phone_last_seen"]) < 15
        return s

def hav(lat1, lon1, lat2, lon2):
    R = 6371000
    p1, p2, dp, dl = map(math.radians, [lat1, lat2, lat2-lat1, lon2-lon1])
    return R * 2 * math.atan2(math.sqrt(math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2),
                               math.sqrt(1 - math.sin(dp/2)**2 - math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2))

def alerts():
    a = []
    if state["status"] == "Offline":              a.append("⚡ Rover offline")
    if state["temp_shutdown"]:                     a.append(f"🌡 THERMAL SHUTDOWN {state['temp']:.1f}°C")
    elif state["temp"] > 60:                       a.append(f"⚠ High temp {state['temp']:.1f}°C")
    if state["status"] == "Online":
        if state["battery"] >= 0 and state["battery"] < 15:  # -1 = not yet read
            a.append(f"🔋 Low battery {state['battery']:.1f}%")
        if state["tank"] < 10:                    a.append(f"💧 Tank critical {state['tank']:.1f}%")
    if state["is_raining"]:                        a.append("🌧 Raining — spray paused")
    elif state["rain_percent"] > 70:               a.append(f"☔ Rain likely {state['rain_percent']}%")
    if state["calibrating"]:                       a.append("🎯 CALIBRATION MODE")
    if state["calib_replay"]:
        a.append(f"▶ Replay {state['calib_replay_idx']}/{len(state['_calib_path'])}")
    return a

# ── Background threads ────────────────────────────────────────────────────────
def _offline():
    while True:
        time.sleep(3)
        with lock:
            if state["_last_hb"] and time.time() - state["_last_hb"] > OFFLINE_SECS:
                if state["status"] == "Online":
                    state["status"] = "Offline"
                    state["battery"] = -1.0   # reset so stale reading doesn't show on reconnect
                    state["alerts"] = alerts()
                    print("[Watch] Offline")

def _thermal():
    while True:
        time.sleep(3)
        with lock:
            t = state["temp"]
            if t >= TEMP_CRITICAL and not state["temp_shutdown"]:
                state.update({"temp_shutdown":True,"state":"idle","sub_state":"cooling",
                              "_cmd":"STOP","calibrating":False,"calib_replay":False})
                state["alerts"] = alerts(); print(f"[Thermal] SHUTDOWN {t}°C")
            elif state["temp_shutdown"] and t <= TEMP_SAFE:
                state.update({"temp_shutdown":False,"sub_state":"idle"})
                state["alerts"] = alerts(); print(f"[Thermal] Clear {t}°C")

def _weather():
    while True:
        try:
            with lock: lat, lon = state["lat"], state["lon"]
            r = requests.get(f"https://api.openweathermap.org/data/2.5/weather"
                             f"?lat={lat}&lon={lon}&appid={OWM_KEY}&units=metric", timeout=10)
            if r.status_code == 200:
                d = r.json()
                with lock:
                    state.update({
                        "is_raining": "rain" in d,
                        "rain_percent": 100 if "rain" in d else d.get("clouds",{}).get("all",0),
                        "weather_temp": round(d["main"]["temp"],1),
                        "weather_desc": d["weather"][0]["description"].title(),
                        "humidity": d["main"]["humidity"],
                        "wind_kmh": round(d["wind"]["speed"]*3.6),
                    })
                    state["alerts"] = alerts()
        except Exception as e: print(f"[Weather] {e}")
        time.sleep(600)

for fn in [_offline, _thermal, _weather]:
    threading.Thread(target=fn, daemon=True).start()

# ── Routes — Dashboard ────────────────────────────────────────────────────────
@app.route("/")
def index(): return send_from_directory(DIR, "index.html")

@app.route("/data")
def data(): return jsonify(pub())

@app.route("/weather")
def weather():
    with lock:
        icon = "🌧" if state["is_raining"] else "☁️" if state["rain_percent"]>60 else "⛅" if state["rain_percent"]>30 else "☀️"
        return jsonify({"ok":True,"temp":state["weather_temp"],"desc":state["weather_desc"],
                        "humidity":state["humidity"],"wind":state["wind_kmh"],
                        "rain_percent":state["rain_percent"],"is_raining":state["is_raining"],"icon":icon})

@app.route("/command", methods=["POST"])
def command():
    action = (request.get_json(silent=True) or {}).get("action","").lower()
    with lock:
        if state["temp_shutdown"]: return jsonify({"error":"Thermal shutdown"}), 403
        if state["calibrating"]:   return jsonify({"error":"Calibration active"}), 403
        if action == "start":
            state.update({"state":"working","sub_state":"moving","_cmd":"START"})
        elif action in ("pause","stop","dock"):
            state.update({"state":"idle","sub_state":"idle","_cmd":"STOP","calib_replay":False})
        else: return jsonify({"error":f"Unknown: {action}"}), 400
        state["alerts"] = alerts()
    return jsonify({"ok":True})

# ── Routes — ESP32 ────────────────────────────────────────────────────────────
@app.route("/heartbeat", methods=["POST"])
def heartbeat():
    body = request.get_json(silent=True)
    if not body: return jsonify({"error":"no body"}), 400
    with lock:
        for k in ("battery","tank","temp"):
            if k in body: state[k] = float(body[k])
        if "sub_state" in body: state["sub_state"] = body["sub_state"]
        state["status"] = "Online"; state["_last_hb"] = time.time()

        # Advance replay waypoints
        if state["calib_replay"] and state["_calib_path"]:
            path = state["_calib_path"]; idx = state["calib_replay_idx"]
            if idx < len(path):
                if hav(state["lat"],state["lon"],path[idx]["lat"],path[idx]["lon"]) < 3.0 and idx < len(path)-1:
                    state["calib_replay_idx"] += 1; idx += 1
                    print(f"[Replay] WP {idx}/{len(path)}")
                state["_steer"] = path[idx].get("steer","CENTER") if idx < len(path) else "STOP"
            if idx >= len(path):
                state.update({"calib_replay":False,"state":"idle","sub_state":"idle","_cmd":"STOP","_steer":"STOP"})

        state["alerts"] = alerts()
        cmd = state["_cmd"]; state["_cmd"] = "NONE"
        scan_en = not (state["calibrating"] or state["calib_replay"])

    print(f"[HB] bat={body.get('battery','?')}% cmd={cmd} steer={state['_steer']}")
    return jsonify({"command":cmd,"steer":state["_steer"],"scan_enabled":scan_en,"calibrating":state["calibrating"]})

@app.route("/sprayed", methods=["POST"])
def sprayed():
    body = request.get_json(silent=True) or {}
    with lock:
        state["sprays_today"] += 1; state["tank"] = max(0.0, state["tank"]-1.0)
        state["spray_log"].append({"lat":state["lat"],"lon":state["lon"],
                                   "nozzle":body.get("nozzle",1),"time":time.strftime("%H:%M:%S")})
    return jsonify({"ok":True})

# ── Routes — Scan handshake ───────────────────────────────────────────────────
@app.route("/scan_request", methods=["POST"])
def scan_req_post():
    with lock: state.update({"_scan_pending":True,"_scan_result":None,"_scan_t":time.time(),"sub_state":"scanning"})
    print("[Scan] ESP32 requested"); return jsonify({"ok":True})

@app.route("/scan_request", methods=["GET"])
def scan_req_get():
    with lock:
        p = state["_scan_pending"]
        if p: state["_scan_pending"] = False
    return jsonify({"pending":p})

@app.route("/scan_result", methods=["POST"])
def scan_res_post():
    result = (request.get_json(silent=True) or {}).get("result","UNKNOWN").upper()
    if result not in ("HEALTHY","DEFECTIVE","UNKNOWN"): result = "UNKNOWN"
    with lock:
        state["_scan_result"] = result; state["last_plant_result"] = result
        state["_phone_last_seen"] = time.time()
        state["scans_today"] += 1
        state["scan_log"].append({"lat":state["lat"],"lon":state["lon"],
                                  "result":result,"time":time.strftime("%H:%M:%S")})
    print(f"[Scan] result={result}"); return jsonify({"ok":True})

@app.route("/scan_result", methods=["GET"])
def scan_res_get():
    with lock:
        r = state["_scan_result"]
        if r is None and time.time() - state["_scan_t"] > 15:
            state["_scan_result"] = "UNKNOWN"; r = "UNKNOWN"; print("[Scan] Timeout")
    return jsonify({"result":r})

# ── Routes — GPS ──────────────────────────────────────────────────────────────
@app.route("/location", methods=["POST"])
def location():
    body = request.get_json(silent=True) or {}
    lat, lon = body.get("lat"), body.get("lon")
    if lat is None or lon is None: return jsonify({"error":"missing lat/lon"}), 400
    with lock:
        state["lat"] = float(lat); state["lon"] = float(lon)
        state["status"] = "Online"; state["_last_hb"] = time.time()
        state["_phone_last_seen"] = time.time()
        state["alerts"] = alerts()
        if state["calibrating"]:
            path = state["_calib_path"]; last = path[-1] if path else None
            if last is None or hav(last["lat"],last["lon"],float(lat),float(lon)) > 0.5:
                path.append({"lat":float(lat),"lon":float(lon),"steer":state["_steer"],"t":time.time()})
                if len(path)==1: state["calib_start"] = {"lat":float(lat),"lon":float(lon)}
    return jsonify({"ok":True})

# ── Routes — Calibration ──────────────────────────────────────────────────────
@app.route("/calib_poll")
def calib_poll():
    with lock: return jsonify({"steer":state["_steer"],"done":not state["calibrating"]})

@app.route("/calibrate/start", methods=["POST"])
def calib_start():
    with lock:
        if state["temp_shutdown"]: return jsonify({"error":"Thermal shutdown"}), 403
        state.update({"calibrating":True,"calib_replay":False,"calib_replay_idx":0,
                      "_calib_path":[],"calib_start":None,"calib_end":None,
                      "state":"calibrating","sub_state":"manual","_cmd":"NONE","_steer":"STOP"})
        state["alerts"] = alerts()
    print("[Calib] Start"); return jsonify({"ok":True,"lat":state["lat"],"lon":state["lon"]})

@app.route("/calibrate/steer", methods=["POST"])
def calib_steer():
    body = request.get_json(silent=True) or {}
    d = body.get("steer","STOP").upper(); m = body.get("move","STOP").upper()
    if d not in ("LEFT","CENTER","RIGHT","STOP"): return jsonify({"error":"invalid"}), 400
    with lock:
        if not state["calibrating"]: return jsonify({"error":"not calibrating"}), 400
        state["_steer"] = d; state["_cmd"] = "START" if m=="FWD" else "STOP"
        state["sub_state"] = "moving" if m=="FWD" else "manual"
    return jsonify({"ok":True})

@app.route("/calibrate/done", methods=["POST"])
def calib_done():
    with lock:
        if not state["calibrating"]: return jsonify({"error":"not calibrating"}), 400
        state.update({"calibrating":False,"calib_end":{"lat":state["lat"],"lon":state["lon"]},
                      "state":"idle","sub_state":"idle","_cmd":"STOP","_steer":"STOP"})
        wps = len(state["_calib_path"]); state["alerts"] = alerts()
    print(f"[Calib] Done {wps} waypoints")
    return jsonify({"ok":True,"waypoints":wps,"start":state["calib_start"],"end":state["calib_end"]})

@app.route("/calibrate/path")
def calib_path():
    with lock: return jsonify({"path":state["_calib_path"],"start":state["calib_start"],"end":state["calib_end"]})

@app.route("/calibrate/replay", methods=["POST"])
def calib_replay():
    with lock:
        if not state["_calib_path"]:   return jsonify({"error":"No path"}), 400
        if state["temp_shutdown"]:      return jsonify({"error":"Thermal shutdown"}), 403
        if state["calibrating"]:        return jsonify({"error":"Still calibrating"}), 400
        state.update({"calib_replay":True,"calib_replay_idx":0,"state":"working",
                      "sub_state":"moving","_cmd":"START",
                      "_steer":state["_calib_path"][0].get("steer","CENTER")})
        state["alerts"] = alerts(); wps = len(state["_calib_path"])
    print(f"[Replay] Start {wps} waypoints"); return jsonify({"ok":True,"waypoints":wps})

@app.route("/calibrate/cancel", methods=["POST"])
def calib_cancel():
    with lock:
        state.update({"calibrating":False,"calib_replay":False,"state":"idle",
                      "sub_state":"idle","_cmd":"STOP","_steer":"STOP"})
        state["alerts"] = alerts()
    print("[Calib] Cancelled"); return jsonify({"ok":True})

if __name__ == "__main__":
    print("\n✅ AgroBot Server started — http://<your-pc-ip>:5000\n")
    app.run(host="0.0.0.0", port=5000, debug=False)
