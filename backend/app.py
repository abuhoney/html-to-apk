"""
html_to_apk backend — Cloud build service.

Receives an HTML payload + app_name + (optional) package_name/version,
generates a fresh Android project on disk, compiles it to a real
installable APK using aapt2 + javac + d8 + apksigner, and returns
the signed APK file.

Endpoints:
    GET  /                     — health check + UI
    GET  /api/health           — JSON health
    POST /api/build-apk        — multipart: html_file, app_name, package_name, version
    GET  /api/apks             — list previously built APKs
    GET  /download/<filename>  — download a built APK
    GET  /api/version          — version + tool info

Run:
    python app.py
    # → http://0.0.0.0:10000
"""
from __future__ import annotations

import os
import io
import re
import json
import time
import shutil
import struct
import hashlib
import zipfile
import datetime
import subprocess
from pathlib import Path
from typing import Optional

from flask import (
    Flask, request, jsonify, send_file,
    render_template_string, abort, Response
)

# ─────────────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent
BUILD_TOOLS = PROJECT_ROOT / "build_tools"
AAPT2 = BUILD_TOOLS / "aapt2"
D8_JAR = BUILD_TOOLS / "d8.jar"
APKSIGNER_JAR = BUILD_TOOLS / "apksigner-lib.jar"
ANDROID_JAR = BUILD_TOOLS / "android.jar"
KEYSTORE = BUILD_TOOLS / "debug.keystore"
KEYSTORE_ALIAS = "bardom-debug"
KEYSTORE_PASS = "bardom123"

WORK_DIR = PROJECT_ROOT / "_work"
OUTPUT_DIR = PROJECT_ROOT / "_output"
WORK_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# JDK bootstrap (auto-download portable Temurin 21 if javac is missing)
# ─────────────────────────────────────────────────────────────────────────────
_JDK_DIR: Optional[Path] = None


def _ensure_jdk() -> bool:
    """Make javac available on PATH. Download portable Temurin 21 if needed."""
    global _JDK_DIR
    # 1) try system javac
    try:
        r = subprocess.run(["javac", "-version"], capture_output=True, timeout=10)
        if r.returncode == 0:
            return True
    except Exception:
        pass

    # 2) try cached JDK
    cache_marker = WORK_DIR / "jdk_path.txt"
    if cache_marker.exists():
        saved = cache_marker.read_text().strip()
        if saved and Path(saved).exists():
            os.environ["PATH"] = saved + os.pathsep + os.environ.get("PATH", "")
            try:
                r = subprocess.run(["javac", "-version"], capture_output=True, timeout=10)
                if r.returncode == 0:
                    _JDK_DIR = Path(saved).parent
                    return True
            except Exception:
                pass

    # 3) try JAVA_HOME if set
    jh = os.environ.get("JAVA_HOME")
    if jh and (Path(jh) / "bin" / "javac").exists():
        os.environ["PATH"] = str(Path(jh) / "bin") + os.pathsep + os.environ.get("PATH", "")
        _JDK_DIR = Path(jh)
        cache_marker.write_text(str(Path(jh) / "bin"))
        return True

    # 4) download Temurin 21 portable
    import urllib.request, tarfile
    jdk_base = WORK_DIR / "jdk"
    jdk_base.mkdir(parents=True, exist_ok=True)
    tar_path = jdk_base / "jdk.tar.gz"
    url = ("https://github.com/adoptium/temurin21-binaries/releases/download/"
           "jdk-21.0.5%2B11/OpenJDK21U-jdk_x64_linux_hotspot_21.0.5_11.tar.gz")
    try:
        if not tar_path.exists() or tar_path.stat().st_size < 1_000_000:
            print("[JDK] Downloading Temurin 21 (200MB)…", flush=True)
            urllib.request.urlretrieve(url, tar_path)
        with tarfile.open(tar_path, "r:gz") as tf:
            tf.extractall(jdk_base)
        for d in jdk_base.iterdir():
            if d.is_dir() and (d / "bin" / "javac").exists():
                _JDK_DIR = d
                cache_marker.write_text(str(d / "bin"))
                os.environ["PATH"] = str(d / "bin") + os.pathsep + os.environ.get("PATH", "")
                os.environ["JAVA_HOME"] = str(d)
                print(f"[JDK] Ready: {d}", flush=True)
                return True
    except Exception as e:
        print(f"[JDK] download failed: {e}", flush=True)
    return False


def _ensure_aapt2() -> bool:
    if not AAPT2.exists():
        return False
    try:
        os.chmod(AAPT2, 0o755)
    except Exception:
        pass
    try:
        r = subprocess.run([str(AAPT2), "version"], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def _ensure_keystore() -> None:
    """Create the debug keystore if it doesn't exist (needs keytool from JDK)."""
    if KEYSTORE.exists():
        return
    KEYSTORE.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "keytool", "-genkeypair",
        "-keystore", str(KEYSTORE),
        "-alias", KEYSTORE_ALIAS,
        "-keypass", KEYSTORE_PASS,
        "-storepass", KEYSTORE_PASS,
        "-keyalg", "RSA", "-keysize", "2048",
        "-validity", "10000",
        "-dname", "CN=HTML to APK, OU=Mobile Apps, O=HTMLtoAPK, L=Riyadh, ST=Riyadh, C=SA",
    ], check=True, capture_output=True)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — package name, icon, manifest
# ─────────────────────────────────────────────────────────────────────────────
def _sanitize_app_name(s: str) -> str:
    s = (s or "").strip() or "MyApp"
    s = re.sub(r"[^A-Za-z0-9 _-]", "", s)
    return s[:30] or "MyApp"


def _sanitize_package(s: str) -> str:
    s = (s or "").strip().lower() or "com.htmltoapk.app"
    s = re.sub(r"[^a-z0-9_.]", "", s)
    if not re.match(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$", s):
        s = "com.htmltoapk.app"
    return s


def _sanitize_filename(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]", "_", s)
    return s or "app"


def _generate_icon_png(seed: str, size: int = 192) -> bytes:
    """Pure-Python PNG icon — solid color square with white circle."""
    import zlib
    h = hashlib.md5(seed.encode()).digest()
    r, g, b = min(255, h[0] + 60), min(255, h[1] + 60), min(255, h[2] + 60)

    def _chunk(t: bytes, d: bytes) -> bytes:
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
    raw = bytearray()
    cx = cy = size // 2
    radius = size // 3
    for y in range(size):
        raw.append(0)
        for x in range(size):
            if ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 < radius:
                raw.extend([255, 255, 255])
            else:
                raw.extend([r, g, b])
    idat = _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    iend = _chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


def _write_manifest(src_dir: Path, package: str, version_code: int,
                    version_name: str, app_name: str) -> None:
    manifest = f'''<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="{package}"
    android:versionCode="{version_code}"
    android:versionName="{version_name}">

    <uses-permission android:name="android.permission.INTERNET" />

    <application
        android:label="@string/app_name"
        android:icon="@mipmap/ic_launcher"
        android:allowBackup="true"
        android:usesCleartextTraffic="true"
        android:theme="@android:style/Theme.Material.NoActionBar"
        android:hardwareAccelerated="true">

        <activity
            android:name=".MainActivity"
            android:exported="true"
            android:configChanges="orientation|screenSize|keyboardHidden">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
'''
    (src_dir / "AndroidManifest.xml").write_text(manifest, encoding="utf-8")


def _write_strings(res_dir: Path, app_name: str) -> None:
    vals = res_dir / "values"
    vals.mkdir(parents=True, exist_ok=True)
    (vals / "strings.xml").write_text(
        f'<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
        f'    <string name="app_name">{app_name}</string>\n'
        f'</resources>\n', encoding="utf-8")


def _write_layout(res_dir: Path) -> None:
    layout = res_dir / "layout"
    layout.mkdir(parents=True, exist_ok=True)
    (layout / "activity_main.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<FrameLayout xmlns:android="http://schemas.android.com/apk/res/android"\n'
        '    android:layout_width="match_parent"\n'
        '    android:layout_height="match_parent">\n'
        '    <WebView\n'
        '        android:id="@+id/webview"\n'
        '        android:layout_width="match_parent"\n'
        '        android:layout_height="match_parent" />\n'
        '</FrameLayout>\n', encoding="utf-8")


def _write_icons(res_dir: Path, seed: str) -> None:
    densities = {
        "mipmap-mdpi": 48, "mipmap-hdpi": 72, "mipmap-xhdpi": 96,
        "mipmap-xxhdpi": 144, "mipmap-xxxhdpi": 192,
    }
    for folder, size in densities.items():
        d = res_dir / folder
        d.mkdir(parents=True, exist_ok=True)
        (d / "ic_launcher.png").write_bytes(_generate_icon_png(seed, size))


JAVA_SOURCE = '''package {package_name};

import android.app.Activity;
import android.os.Bundle;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.WebSettings;
import android.view.KeyEvent;

public class MainActivity extends Activity {{
    private WebView webView;

    @Override
    protected void onCreate(Bundle savedInstanceState) {{
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        webView = findViewById(R.id.webview);
        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setAllowFileAccess(true);
        s.setAllowContentAccess(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
        webView.setWebViewClient(new WebViewClient());
        webView.loadUrl("file:///android_asset/webapp/index.html");
    }}

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {{
        if (keyCode == KeyEvent.KEYCODE_BACK && webView != null && webView.canGoBack()) {{
            webView.goBack();
            return true;
        }}
        return super.onKeyDown(keyCode, event);
    }}

    @Override protected void onResume() {{ super.onResume(); if (webView != null) webView.onResume(); }}
    @Override protected void onPause()   {{ if (webView != null) webView.onPause(); super.onPause(); }}
}}
'''


# ─────────────────────────────────────────────────────────────────────────────
# Build pipeline
# ─────────────────────────────────────────────────────────────────────────────
def _run(cmd: list[str], cwd: Optional[Path] = None,
         timeout: int = 180) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def _prepare_project(work_dir: Path, app_name: str, package: str,
                     version_code: int, version_name: str,
                     html_content: str) -> Path:
    """Stage a fresh Android project tree for compilation."""
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)

    src_main = work_dir / "src" / "main"
    res_dir = src_main / "res"
    java_dir = src_main / "java" / package.replace(".", "/")
    assets_dir = src_main / "assets" / "webapp"

    java_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)
    res_dir.mkdir(parents=True, exist_ok=True)

    _write_manifest(src_main, package, version_code, version_name, app_name)
    _write_strings(res_dir, app_name)
    _write_layout(res_dir)
    _write_icons(res_dir, app_name)

    (java_dir / "MainActivity.java").write_text(
        JAVA_SOURCE.format(package_name=package), encoding="utf-8")

    (assets_dir / "index.html").write_text(html_content, encoding="utf-8")

    return work_dir


def _compile_resources(project_dir: Path, compiled_zip: Path) -> bool:
    res_dir = project_dir / "src" / "main" / "res"
    res_files = [str(p) for p in sorted(res_dir.rglob("*")) if p.is_file()]
    if not res_files:
        return False
    cmd = [str(AAPT2), "compile", "-o", str(compiled_zip)] + res_files
    rc, _, err = _run(cmd, timeout=60)
    if rc != 0:
        print(f"[aapt2 compile] {err}", flush=True)
    return rc == 0


def _link_apk(project_dir: Path, compiled_zip: Path, output_apk: Path,
              package: str, version_code: int, version_name: str) -> bool:
    manifest = project_dir / "src" / "main" / "AndroidManifest.xml"
    assets_dir = project_dir / "src" / "main" / "assets"
    r_java_dir = project_dir / "build" / "gen" / package.replace(".", "/")
    r_java_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(AAPT2), "link",
        "-o", str(output_apk),
        "--manifest", str(manifest),
        "-I", str(ANDROID_JAR),
        "--min-sdk-version", "24",
        "--target-sdk-version", "34",
        "--version-code", str(version_code),
        "--version-name", version_name,
        "--rename-manifest-package", package,
        "--auto-add-overlay",
        "-A", str(assets_dir),
        "--java", str(r_java_dir.parent),
        str(compiled_zip),
    ]
    rc, _, err = _run(cmd, timeout=60)
    if rc != 0:
        print(f"[aapt2 link] {err}", flush=True)
    return rc == 0


def _compile_java(project_dir: Path, package: str) -> bool:
    if not _ensure_jdk():
        return False
    java_dir = project_dir / "src" / "main" / "java"
    gen_dir = project_dir / "build" / "gen"
    out_classes = project_dir / "build" / "classes"
    out_classes.mkdir(parents=True, exist_ok=True)
    java_files = list(java_dir.rglob("*.java"))
    if gen_dir.exists():
        java_files.extend(gen_dir.rglob("*.java"))
    if not java_files:
        return False
    cmd = [
        "javac", "-source", "17", "-target", "17",
        "-classpath", str(ANDROID_JAR),
        "-d", str(out_classes),
    ] + [str(f) for f in java_files]
    rc, _, err = _run(cmd, timeout=60)
    if rc != 0:
        print(f"[javac] {err}", flush=True)
    return rc == 0


def _make_dex(project_dir: Path, dex_path: Path) -> bool:
    if not _ensure_jdk():
        return False
    classes_dir = project_dir / "build" / "classes"
    class_files = list(classes_dir.rglob("*.class"))
    if not class_files:
        return False
    cmd = [
        "java", "-cp", str(D8_JAR),
        "com.android.tools.r8.D8",
        "--release", "--min-api", "24",
        "--output", str(dex_path.parent),
    ] + [str(f) for f in class_files]
    rc, _, err = _run(cmd, timeout=120)
    if rc != 0:
        print(f"[d8] {err}", flush=True)
    return dex_path.exists()


def _inject_dex(apk_path: Path, dex_path: Path) -> None:
    tmp = apk_path.parent / "tmp_with_dex.apk"
    with zipfile.ZipFile(apk_path, "r") as src, \
         zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            if item.filename == "classes.dex":
                continue
            dst.writestr(item, src.read(item.filename))
        dst.write(dex_path, "classes.dex")
    tmp.replace(apk_path)


def _zipalign(in_apk: Path, out_apk: Path, alignment: int = 4) -> None:
    """Binary-level zipalign — rewrites the ZIP so STORED entries are aligned.

    MANDATORY before signing — Android 11+ silently rejects unaligned APKs
    with the misleading "App not installed" message.

    Uses the binary-level implementation in zipalign.py (sibling module)
    because Python's zipfile module doesn't expose enough control over
    local-header offsets to achieve proper 4-byte alignment.
    """
    # Import the binary-level zipalign from the sibling module
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "zipalign", Path(__file__).parent / "zipalign.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.zipalign(in_apk, out_apk, alignment=alignment)


def _sign_apk(apk_path: Path) -> bool:
    if not APKSIGNER_JAR.exists():
        return False
    _ensure_keystore()
    cmd = [
        "java", "-jar", str(APKSIGNER_JAR), "sign",
        "--ks", str(KEYSTORE),
        "--ks-key-alias", KEYSTORE_ALIAS,
        "--ks-pass", f"pass:{KEYSTORE_PASS}",
        "--key-pass", f"pass:{KEYSTORE_PASS}",
        "--v1-signing-enabled", "true",
        "--v2-signing-enabled", "true",
        "--v3-signing-enabled", "true",
        "--v4-signing-enabled", "false",
        str(apk_path),
    ]
    rc, _, err = _run(cmd, timeout=120)
    if rc != 0:
        print(f"[apksigner sign] {err}", flush=True)
        return False
    rc, _, _ = _run([
        "java", "-jar", str(APKSIGNER_JAR), "verify", "--verbose", str(apk_path)
    ], timeout=60)
    return rc == 0


# ─────────────────────────────────────────────────────────────────────────────
# Public build function
# ─────────────────────────────────────────────────────────────────────────────
def build_apk_from_html(html_content: str, app_name: str,
                         package_name: Optional[str] = None,
                         version_code: int = 1,
                         version_name: str = "1.0.0") -> dict:
    """Build a real signed APK from raw HTML. Returns a dict with status info."""
    t0 = time.time()
    app_name = _sanitize_app_name(app_name)
    package = _sanitize_package(package_name or f"com.htmltoapk.{app_name.lower().replace(' ', '_')}")
    version_code = max(1, int(version_code or 1))
    version_name = (version_name or "1.0.0").strip() or "1.0.0"

    # Pre-flight checks
    if not _ensure_jdk():
        return {"success": False, "error": "Java JDK not available and could not be installed."}
    if not _ensure_aapt2():
        return {"success": False, "error": "aapt2 binary not executable on this server."}
    if not ANDROID_JAR.exists():
        return {"success": False, "error": "android.jar framework missing."}

    build_id = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S") + "_" + hashlib.md5(
        (app_name + package + str(t0)).encode()).hexdigest()[:6]
    work_dir = WORK_DIR / f"build_{build_id}"
    out_dir = OUTPUT_DIR / build_id
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        # 1) stage project
        _prepare_project(work_dir, app_name, package, version_code, version_name, html_content)

        # 2) compile resources
        compiled_zip = work_dir / "build" / "compiled.zip"
        compiled_zip.parent.mkdir(parents=True, exist_ok=True)
        if not _compile_resources(work_dir, compiled_zip):
            return {"success": False, "error": "aapt2 compile failed",
                    "build_id": build_id, "duration_sec": time.time() - t0}

        # 3) link → APK skeleton
        linked_apk = work_dir / "build" / "linked.apk"
        if not _link_apk(work_dir, compiled_zip, linked_apk,
                         package, version_code, version_name):
            return {"success": False, "error": "aapt2 link failed",
                    "build_id": build_id, "duration_sec": time.time() - t0}

        # 4) javac
        if not _compile_java(work_dir, package):
            return {"success": False, "error": "javac failed",
                    "build_id": build_id, "duration_sec": time.time() - t0}

        # 5) d8 → classes.dex
        dex_path = work_dir / "build" / "dex" / "classes.dex"
        dex_path.parent.mkdir(parents=True, exist_ok=True)
        if not _make_dex(work_dir, dex_path):
            return {"success": False, "error": "d8 failed",
                    "build_id": build_id, "duration_sec": time.time() - t0}

        # 6) inject dex
        final_apk = out_dir / f"{_sanitize_filename(app_name)}.apk"
        shutil.copy2(linked_apk, final_apk)
        _inject_dex(final_apk, dex_path)

        # 6b) zipalign (MANDATORY before signing — Android 11+ rejects unaligned APKs)
        aligned_tmp = final_apk.parent / "_aligned.apk"
        _zipalign(final_apk, aligned_tmp)
        shutil.copy2(aligned_tmp, final_apk)
        aligned_tmp.unlink()

        # 7) sign
        if not _sign_apk(final_apk):
            return {"success": False, "error": "apksigner failed",
                    "build_id": build_id, "duration_sec": time.time() - t0}

        return {
            "success": True,
            "build_id": build_id,
            "apk_name": final_apk.name,
            "apk_path": str(final_apk),
            "apk_url": f"/download/{build_id}/{final_apk.name}",
            "apk_size": final_apk.stat().st_size,
            "package_name": package,
            "app_name": app_name,
            "version_code": version_code,
            "version_name": version_name,
            "duration_sec": round(time.time() - t0, 2),
            "build_mode": "apk-v3-signed",
            "built_at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }

    except Exception as e:
        import traceback
        return {"success": False, "error": str(e),
                "traceback": traceback.format_exc(),
                "build_id": build_id,
                "duration_sec": time.time() - t0}


def list_built_apks() -> list[dict]:
    out = []
    for p in sorted(OUTPUT_DIR.rglob("*.apk")):
        out.append({
            "name": p.name,
            "build_id": p.parent.name,
            "url": f"/download/{p.parent.name}/{p.name}",
            "size": p.stat().st_size,
            "modified": datetime.datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
        })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Flask app
# ─────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB upload


# ─────────────────────────────────────────────────────────────────────────────
# CORS — allow WebView apps to call this API cross-origin
# ─────────────────────────────────────────────────────────────────────────────
@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin", "*")
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Requested-With"
    response.headers["Access-Control-Max-Age"] = "86400"
    vary = response.headers.get("Vary", "")
    if "Origin" not in vary.split(", "):
        response.headers["Vary"] = (vary + ", Origin") if vary else "Origin"
    return response


@app.before_request
def handle_preflight():
    if request.method == "OPTIONS":
        return ("", 204)


@app.route("/")
def dashboard():
    return jsonify({
        "name": "HTML to APK",
        "version": "1.0.0",
        "status": "online",
        "endpoints": [
            "GET  /api/health",
            "POST /api/build-apk  (multipart: html_file, app_name, package_name?, version_name?)",
            "GET  /api/apks",
            "GET  /download/<build_id>/<filename>",
            "GET  /api/version",
        ],
    })


@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "version": "1.0.0",
        "tools": {
            "aapt2": AAPT2.exists(),
            "d8_jar": D8_JAR.exists(),
            "apksigner": APKSIGNER_JAR.exists(),
            "android_jar": ANDROID_JAR.exists() and ANDROID_JAR.stat().st_size > 1_000_000,
            "keystore": KEYSTORE.exists(),
            "jdk_javac": _ensure_jdk(),
        },
        "builds_count": len(list_built_apks()),
        "timestamp": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
    })


@app.route("/api/version")
def version():
    return jsonify({
        "name": "HTML to APK",
        "version": "1.0.0",
        "build_mode": "apk-v3-signed",
        "min_sdk": 24,
        "target_sdk": 34,
    })


@app.route("/api/build-apk", methods=["POST"])
def build_apk_route():
    # Accept both multipart and JSON
    if request.files:
        f = request.files.get("html_file") or request.files.get("file")
        if not f:
            return jsonify({"success": False, "error": "html_file required"}), 400
        try:
            html_content = f.read().decode("utf-8", errors="replace")
        except Exception as e:
            return jsonify({"success": False, "error": f"cannot read html_file: {e}"}), 400
        app_name = request.form.get("app_name", "").strip()
        package_name = request.form.get("package_name", "").strip() or None
        version_name = request.form.get("version_name", "1.0.0").strip() or "1.0.0"
        version_code = int(request.form.get("version_code", 1) or 1)
    else:
        data = request.get_json(force=True, silent=True) or {}
        html_content = data.get("html", "")
        if not html_content:
            return jsonify({"success": False, "error": "html required"}), 400
        app_name = data.get("app_name", "").strip()
        package_name = data.get("package_name")
        version_name = data.get("version_name", "1.0.0") or "1.0.0"
        version_code = int(data.get("version_code", 1) or 1)

    if not app_name:
        app_name = "MyApp"

    res = build_apk_from_html(html_content, app_name, package_name, version_code, version_name)
    code = 200 if res.get("success") else 500
    return jsonify(res), code


@app.route("/api/apks")
def list_apks():
    return jsonify({"apks": list_built_apks()})


@app.route("/download/<build_id>/<filename>")
def download_apk(build_id: str, filename: str):
    if "/" in filename or "\\" in filename or "/" in build_id or "\\" in build_id:
        return jsonify({"error": "invalid path"}), 400
    p = OUTPUT_DIR / build_id / filename
    if not p.exists():
        return jsonify({"error": "file not found"}), 404
    return send_file(p, as_attachment=True, download_name=filename,
                     mimetype="application/vnd.android.package-archive")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"╔══════════════════════════════════════════════╗", flush=True)
    print(f"║  HTML to APK · Cloud Build Service v1.0.0   ║", flush=True)
    print(f"║  Listening on http://0.0.0.0:{port:<5d}           ║", flush=True)
    print(f"╚══════════════════════════════════════════════╝", flush=True)
    print(f"\n  Pre-flight checks:", flush=True)
    print(f"  • aapt2:        {'OK' if _ensure_aapt2() else 'MISSING'}", flush=True)
    print(f"  • android.jar:  {'OK' if ANDROID_JAR.exists() else 'MISSING'}", flush=True)
    print(f"  • d8.jar:       {'OK' if D8_JAR.exists() else 'MISSING'}", flush=True)
    print(f"  • apksigner:    {'OK' if APKSIGNER_JAR.exists() else 'MISSING'}", flush=True)
    print(f"  • JDK (javac):  {'OK' if _ensure_jdk() else 'MISSING'}", flush=True)
    print(f"  • keystore:     {'OK' if KEYSTORE.exists() else 'will-create-on-demand'}", flush=True)
    print(f"\n  Endpoints:\n", flush=True)
    print(f"  • POST /api/build-apk  — build APK from HTML", flush=True)
    print(f"  • GET  /api/apks       — list built APKs", flush=True)
    print(f"  • GET  /download/<id>/<file> — download APK\n", flush=True)
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
