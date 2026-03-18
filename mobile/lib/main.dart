import 'dart:async';
import 'dart:io';
import 'dart:convert';
import 'dart:typed_data';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:camera/camera.dart';
import 'package:image/image.dart' as img;
import 'package:geolocator/geolocator.dart';

const String kServer = "http://<your_pc_ip>:5000";
late List<CameraDescription> cameras;

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  cameras = await availableCameras();
  runApp(MaterialApp(
    debugShowCheckedModeBanner: false,
    theme: ThemeData.dark().copyWith(
      scaffoldBackgroundColor: const Color(0xFF080B0D),
      appBarTheme: const AppBarTheme(backgroundColor: Color(0xFF0E1215), elevation: 0),
    ),
    home: const _Home(),
  ));
}

// Top-level so compute() can find it
String analyseColour(Uint8List bytes) {
  final im = img.decodeImage(bytes);
  if (im == null) return "UNKNOWN";
  int g = 0, y = 0, b = 0, t = 0;
  final sy = im.height ~/ 3, ey = (im.height * 2) ~/ 3;
  for (int row = sy; row < ey; row += 6) {
    for (int col = 0; col < im.width; col += 6) {
      final p = im.getPixel(col, row);
      final r = p.r.toInt(), gv = p.g.toInt(), bv = p.b.toInt();
      if (2 * gv - r - bv > 20 && gv > 50)              g++;
      if (r > 150 && gv > 130 && bv < 100 && r > gv)    y++;
      if (r > 100 && gv < 90  && bv < 80  && r > bv)    b++;
      t++;
    }
  }
  if (t == 0) return "UNKNOWN";
  return (b / t > 0.04 || y / t > 0.07 || g / t < 0.25) ? "DEFECTIVE" : "HEALTHY";
}

class _Home extends StatefulWidget {
  const _Home();
  @override
  State<_Home> createState() => _HomeState();
}

class _HomeState extends State<_Home> {
  CameraController? _cam;
  bool _camReady = false, _camLocked = false;

  StreamSubscription<Position>? _gpsSub;
  String _gpsText = "Acquiring...", _gpsMsg = "—";
  Color  _gpsCol  = Colors.grey;

  Timer? _pollTimer;
  bool   _scanning   = false;
  String _lastResult = "—";
  Color  _resultCol  = Colors.grey;
  int    _scanCount  = 0;

  bool   _serverOk   = false;
  String _status     = "Starting...";
  Color  _statusCol  = Colors.grey;

  @override
  void initState() {
    super.initState();
    _initCam();
    _initGPS();
    _ping();
    _pollTimer = Timer.periodic(const Duration(milliseconds: 300), (_) => _poll());
  }

  @override
  void dispose() {
    _cam?.dispose();
    _gpsSub?.cancel();
    _pollTimer?.cancel();
    super.dispose();
  }

  // ── Camera ────────────────────────────────────────────────────────────────
  Future<void> _initCam() async {
    if (cameras.isEmpty) { _setStatus("No camera", Colors.red); return; }
    _cam = CameraController(cameras[0], ResolutionPreset.low, enableAudio: false);
    try {
      await _cam!.initialize();
      if (mounted) setState(() => _camReady = true);
      _setStatus("Ready", Colors.green);
    } catch (e) { _setStatus("Cam: $e", Colors.red); }
  }

  Future<Uint8List?> _photo() async {
    if (!_camReady || _cam == null || _camLocked) return null;
    _camLocked = true;
    try {
      final f = await _cam!.takePicture();
      return await File(f.path).readAsBytes();
    } catch (e) { debugPrint("[Cam] $e"); return null; }
    finally     { _camLocked = false; }
  }

  // ── GPS ───────────────────────────────────────────────────────────────────
  Future<void> _initGPS() async {
    if (!await Geolocator.isLocationServiceEnabled()) {
      if (mounted) setState(() => _gpsText = "GPS OFF"); return;
    }
    var p = await Geolocator.checkPermission();
    if (p == LocationPermission.denied) p = await Geolocator.requestPermission();
    if (p == LocationPermission.denied || p == LocationPermission.deniedForever) {
      if (mounted) setState(() => _gpsText = "No permission"); return;
    }
    try { final l = await Geolocator.getLastKnownPosition(); if (l != null) _onPos(l); } catch (_) {}
    _gpsSub = Geolocator.getPositionStream(
      locationSettings: const LocationSettings(accuracy: LocationAccuracy.best, distanceFilter: 1),
    ).listen((pos) { if (pos.accuracy <= 30) _onPos(pos); },
             onError: (e) => debugPrint("[GPS] $e"), cancelOnError: false);
  }

  void _onPos(Position pos) {
    if (!mounted) return;
    setState(() => _gpsText = "${pos.latitude.toStringAsFixed(5)}, ${pos.longitude.toStringAsFixed(5)}");
    _post("/location", {"lat": pos.latitude, "lon": pos.longitude}).then((ok) {
      if (!mounted) return;
      setState(() { _gpsMsg = ok ? "✓ Sent" : "✗ Failed"; _gpsCol = ok ? Colors.green : Colors.red; });
    });
  }

  // ── HTTP helpers ──────────────────────────────────────────────────────────
  Future<bool> _post(String path, Map body) async {
    try {
      final r = await http.post(Uri.parse("$kServer$path"),
        headers: {"Content-Type": "application/json"},
        body: jsonEncode(body),
      ).timeout(const Duration(seconds: 3));
      return r.statusCode == 200;
    } catch (_) { return false; }
  }

  Future<Map?> _get(String path, {Duration timeout = const Duration(seconds: 2)}) async {
    try {
      final r = await http.get(Uri.parse("$kServer$path")).timeout(timeout);
      if (r.statusCode == 200) return jsonDecode(r.body) as Map;
    } catch (e) { debugPrint("[GET $path] $e"); }
    return null;
  }

  // ── Scan poll ─────────────────────────────────────────────────────────────
  Future<void> _poll() async {
    if (_scanning || !_camReady) return;
    final res = await _get("/scan_request");
    if (res?["pending"] == true) {
      debugPrint("[Poll] Scan requested");
      await _scan();
    }
  }

  // ── Scan ──────────────────────────────────────────────────────────────────
  Future<void> _scan() async {
    if (_scanning) return;
    setState(() { _scanning = true; _status = "📸 Scanning..."; _statusCol = Colors.amber; });
    try {
      debugPrint("[Scan] Taking photo...");
      final bytes = await _photo();
      if (bytes == null) {
        _setStatus("⚠ Camera busy", Colors.orange);
        await _post("/scan_result", {"result": "UNKNOWN"});
        return;
      }
      debugPrint("[Scan] ${bytes.length}b — analysing...");
      final result = await compute(analyseColour, bytes);
      debugPrint("[Scan] → $result");
      final col = result == "HEALTHY" ? Colors.green : result == "DEFECTIVE" ? Colors.red : Colors.grey;
      if (mounted) setState(() { _lastResult = result; _resultCol = col; _scanCount++; });
      _setStatus("🎨 $result", col);
      final ok = await _post("/scan_result", {"result": result});
      if (!ok && mounted) _setStatus("⚠ Server unreachable", Colors.red);
    } finally {
      if (mounted) setState(() => _scanning = false);
    }
  }

  Future<void> _ping() async {
    final res = await _get("/data", timeout: const Duration(seconds: 4));
    if (res != null && mounted) {
      setState(() => _serverOk = true);
      _setStatus("Connected", Colors.green);
    } else if (mounted) {
      _setStatus("Cannot reach $kServer", Colors.red);
    }
  }

  void _setStatus(String m, Color c) {
    if (!mounted) return;
    setState(() { _status = m; _statusCol = c; });
  }

  // ── UI ────────────────────────────────────────────────────────────────────
  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: const Text("J.A.R.V.I.S AgroBot",
          style: TextStyle(fontWeight: FontWeight.bold, letterSpacing: 1.5, color: Color(0xFFFF2D2D))),
      actions: [
        Padding(padding: const EdgeInsets.only(right: 14),
          child: Icon(_serverOk ? Icons.wifi : Icons.wifi_off,
              color: _serverOk ? Colors.green : Colors.red)),
      ],
    ),
    body: Column(children: [
      // Camera
      Expanded(flex: 3, child: ClipRRect(
        borderRadius: const BorderRadius.vertical(bottom: Radius.circular(16)),
        child: Stack(fit: StackFit.expand, children: [
          _camReady ? CameraPreview(_cam!)
                    : const Center(child: CircularProgressIndicator(color: Color(0xFFFF2D2D))),
          if (_scanning) Container(color: Colors.black54,
            child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
              const CircularProgressIndicator(color: Colors.amber, strokeWidth: 3),
              const SizedBox(height: 12),
              Text("Analysing...", style: TextStyle(color: Colors.amber.shade200,
                  fontSize: 16, fontWeight: FontWeight.bold)),
            ])),
        ]),
      )),

      // Info panel
      Expanded(flex: 2, child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 14, 16, 16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          _row("📡", "Server", kServer),
          const SizedBox(height: 5),
          _row("📍", "GPS", _gpsText),
          Padding(padding: const EdgeInsets.only(left: 22, top: 1, bottom: 10),
            child: Text(_gpsMsg, style: TextStyle(fontSize: 11, color: _gpsCol))),

          // Result badge
          Row(children: [
            const Text("🎨  Last Scan  ", style: TextStyle(color: Colors.grey, fontSize: 13)),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
              decoration: BoxDecoration(
                color: _resultCol.withOpacity(0.15),
                border: Border.all(color: _resultCol.withOpacity(0.6)),
                borderRadius: BorderRadius.circular(8)),
              child: Text(_lastResult, style: TextStyle(color: _resultCol,
                  fontWeight: FontWeight.bold, fontSize: 14)),
            ),
            const Spacer(),
            Text("$_scanCount scans", style: const TextStyle(color: Colors.white38, fontSize: 11)),
          ]),
          const SizedBox(height: 10),

          // Status
          Container(
            width: double.infinity,
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 9),
            decoration: BoxDecoration(
              color: _statusCol.withOpacity(0.08),
              border: Border.all(color: _statusCol.withOpacity(0.35)),
              borderRadius: BorderRadius.circular(10)),
            child: Text(_status, textAlign: TextAlign.center,
                style: TextStyle(color: _statusCol, fontWeight: FontWeight.w600, fontSize: 13)),
          ),
          const Spacer(),

          // Manual scan button
          SizedBox(width: double.infinity, child: ElevatedButton.icon(
            onPressed: _scanning ? null : _scan,
            icon: _scanning
                ? const SizedBox(width: 16, height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                : const Icon(Icons.colorize, size: 20),
            label: Text(_scanning ? "Scanning..." : "Manual Scan",
                style: const TextStyle(fontSize: 15, fontWeight: FontWeight.bold)),
            style: ElevatedButton.styleFrom(
              backgroundColor: const Color(0xFF1E7B1E),
              foregroundColor: Colors.white,
              disabledBackgroundColor: const Color(0xFF1E7B1E).withOpacity(0.4),
              padding: const EdgeInsets.symmetric(vertical: 14),
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12))),
          )),
        ]),
      )),
    ]),
  );

  Widget _row(String emoji, String label, String val) => Padding(
    padding: const EdgeInsets.only(bottom: 2),
    child: Row(children: [
      Text("$emoji  ", style: const TextStyle(fontSize: 14)),
      Text("$label  ", style: const TextStyle(color: Colors.grey, fontSize: 13)),
      Expanded(child: Text(val, overflow: TextOverflow.ellipsis,
          style: const TextStyle(fontFamily: "monospace", fontSize: 13, color: Colors.white70))),
    ]),
  );
}
