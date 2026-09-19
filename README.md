# HTML to APK

**Convert any HTML file to a real, installable Android APK — directly from your phone.**

This package contains:

1. **`html_to_apk.apk`** — Android app (25 KB) with an English UI. Install it on your phone, pick an HTML file, tap Build APK, and the resulting APK is downloaded and the installer launches automatically.

2. **Cloud backend** — Flask service that compiles real APKs from HTML using `aapt2 + javac + d8 + apksigner`. Self-hosted or deploy to Render.com (free tier).

3. **Build tools** — Bundled `aapt2`, `d8.jar`, `apksigner-lib.jar`, `android.jar` (API 34), and a debug keystore.

---

## Architecture

```
┌──────────────────────────┐         ┌──────────────────────────┐
│  Phone (Android)         │         │  Cloud Backend           │
│  html_to_apk.apk (25KB) │  HTTPS  │  Flask + aapt2 + d8 +    │
│  ┌────────────────────┐  │ ──────> │  apksigner + javac +     │
│  │ WebView (UI)       │  │  HTML   │  android.jar (47 MB)     │
│  │  • File picker     │  │ ──────> │                          │
│  │  • App name field  │  │  APK    │  Build pipeline:         │
│  │  • Build history   │  │ <────── │  aapt2 compile → link →  │
│  └────────────────────┘  │         │  javac → d8 → apksigner  │
│  DownloadManager +       │         │                          │
│  system installer        │         │  Output: signed APK      │
└──────────────────────────┘         └──────────────────────────┘
```

---

## Quick start

### Option A — Use the pre-built APK

1. **Install `html_to_apk.apk`** on your Android phone (Android 7.0+).
2. Make sure the backend is reachable — see below.
3. Open the app, tap the file-picker card, choose any `.html` file.
4. Enter an app name, tap **Build APK**.
5. The APK downloads and the installer opens automatically.

### Option B — Run your own backend

```bash
# Local
cd backend
pip install -r requirements.txt
python app.py        # → http://localhost:10000

# Docker (recommended for production)
docker compose up -d # → http://localhost:10000
```

Then rebuild the APK pointing at your backend:

```bash
cd /home/z/my-project
BACKEND_URL=https://your-backend.example.com python scripts/build_apk.py
# → /home/z/my-project/html_to_apk/download/html_to_apk.apk
```

### Option C — Deploy to Render.com (free tier)

1. Push this folder to a new GitHub repo.
2. On Render, create a new **Web Service** from the repo.
3. Render auto-detects the `render.yaml` and uses the Dockerfile.
4. Wait ~5 min for first deploy.
5. Get your URL like `https://your-app.onrender.com`.
6. Rebuild the APK with that URL (see Option B).

---

## Project structure

```
html_to_apk/
├── html_to_apk.apk                  # ← Pre-built Android app (25 KB)
├── README.md                        # ← This file
├── Dockerfile                       # ← Backend Docker image
├── docker-compose.yml               # ← Local Docker deploy
├── render.yaml                      # ← Render.com config
│
├── backend/                         # ← Cloud build service
│   ├── app.py                       # ← Flask app + build pipeline
│   ├── requirements.txt             # ← Flask + gunicorn
│   ├── build_tools/                 # ← All build tools (47 MB)
│   │   ├── aapt2                    #    Android resource compiler
│   │   ├── d8.jar                   #    .class → .dex compiler
│   │   ├── apksigner-lib.jar        #    APK signer
│   │   ├── apksigner                #    Shell wrapper
│   │   ├── android.jar              #    API 34 framework
│   │   └── debug.keystore           #    Signing key
│   ├── _output/                     # ← Built APKs (auto-created)
│   └── _work/                       # ← Build workspace (auto-created)
│
├── android/                         # ← Source for html_to_apk.apk
│   ├── app/
│   │   ├── build.gradle             # ← Gradle build (optional — we use build_apk.py)
│   │   └── src/main/
│   │       ├── AndroidManifest.xml
│   │       ├── assets/webapp/index.html  # ← The English UI
│   │       ├── java/com/htmltoapk/main/
│   │       │   ├── MainActivity.java
│   │       │   ├── HtmlToApkBridge.java       # ← JS ↔ Native bridge
│   │       │   ├── HtmlToApkWebViewClient.java
│   │       │   └── ApkDownloadReceiver.java
│   │       └── res/                 # ← Icons, strings, layouts
│   ├── build.gradle
│   ├── settings.gradle
│   └── gradle.properties
│
└── download/
    └── html_to_apk.apk             # ← Final installable APK
```

---

## API reference

| Method | Endpoint                    | Purpose                              |
|--------|------------------------------|--------------------------------------|
| GET    | `/`                          | Service info                         |
| GET    | `/api/health`                | Health + tool availability           |
| GET    | `/api/version`               | Version + min/target SDK             |
| POST   | `/api/build-apk`             | Build APK from HTML (multipart)      |
| GET    | `/api/apks`                  | List built APKs                      |
| GET    | `/download/<id>/<filename>`  | Download a built APK                 |

### Build APK request

```bash
curl -X POST https://your-backend/api/build-apk \
  -F "html_file=@myapp.html" \
  -F "app_name=My Cool App" \
  -F "package_name=com.example.mycoolapp"   # optional
  -F "version_name=1.0.0"                   # optional, default 1.0.0
```

### Response (success)

```json
{
  "success": true,
  "build_id": "20260918_234800_cac66c",
  "apk_name": "My_Cool_App.apk",
  "apk_url": "/download/20260918_234800_cac66c/My_Cool_App.apk",
  "apk_size": 17337,
  "package_name": "com.example.mycoolapp",
  "app_name": "My Cool App",
  "version_code": 1,
  "version_name": "1.0.0",
  "duration_sec": 2.94,
  "build_mode": "apk-v3-signed",
  "built_at": "2026-09-18T23:48:03Z"
}
```

---

## How the APK build works

The backend uses the same toolchain as Android Studio, but without Gradle:

1. **Stage** — Create a temp Android project: `AndroidManifest.xml`, `MainActivity.java` (WebView shell), `res/`, `assets/webapp/index.html` (your HTML).
2. **aapt2 compile** — Compile `res/` into a `.zip` of binary resources.
3. **aapt2 link** — Link the manifest + compiled resources + android.jar framework → `linked.apk` skeleton. Also generates `R.java` with resource IDs.
4. **javac** — Compile `MainActivity.java + R.java + BuildConfig.java` against `android.jar` → `.class` files.
5. **d8** — Convert `.class` files to `classes.dex` (Dalvik bytecode). min-api 24.
6. **inject dex** — Add `classes.dex` into the APK (re-zip).
7. **apksigner** — Sign with the bundled debug keystore (v1 + v2 + v3 schemes). Verify.

Total time per build: **2–4 seconds** for a typical HTML page.

---

## Features

### English UI

All UI text is in English (no Arabic, no RTL). GitHub Dark theme.

### File picker

Native Android file picker (SAF). Supports `.html`, `.htm`. Drag & drop is supported on tablets/desktops.

### Build history

Lists all previously built APKs on this device. Tap **Download** to fetch any past build from the backend.

### Auto-install

When a build completes, the APK is downloaded to `Downloads/` and the system installer launches automatically. No file manager needed.

### Backend URL embedded

The backend URL is baked into the APK at build time via `BuildConfig.BACKEND_URL`. To change it, rebuild the APK with `BACKEND_URL=https://...` env var.

### Health polling

The UI polls `/api/health` every 30 seconds and shows a green/red dot. If the backend is down, the Build button is disabled.

### Build log

Real-time log inside the app shows upload → compile → sign → done stages with timestamps.

---

## Customization

### Change the app name

Edit `android/app/src/main/res/values/strings.xml`:

```xml
<string name="app_name">My Custom Brand</string>
```

Then rebuild.

### Change the icon

Replace the PNG files in `android/app/src/main/res/mipmap-*/ic_launcher.png`. Or run:

```bash
python /home/z/my-project/scripts/gen_icons.py
```

### Change the theme color

Edit `android/app/src/main/assets/webapp/index.html` — the `:root` CSS variables at the top.

### Add new build options (e.g., orientation, min-sdk)

1. Add a form field in `assets/webapp/index.html`.
2. Pass it in the FormData in `buildApk()`.
3. Read it in `backend/app.py → build_apk_from_html()`.
4. Pass to `_write_manifest()` and use it in the manifest template.

---

## Troubleshooting

### "Backend offline" message

- Check that the backend is running (`curl https://your-backend/api/health`).
- The APK is hardcoded with `BACKEND_URL = "https://html-to-apk.onrender.com"`. Rebuild with your own URL.

### "Build failed" in the app

- Open the build log — it shows the raw error from the backend.
- Common causes: HTML file is empty, app name has special characters (sanitized automatically), backend's JDK or aapt2 is missing.

### APK won't install on Android

- Make sure "Install unknown apps" is enabled for "HTML to APK" in Android settings.
- The APK is signed with a debug key. For Play Store distribution, replace `debug.keystore` with your own release key.

### d8 crashes with NPE

- This is a known d8 8.2.2-dev bug with anonymous inner classes. All Java source files in this project have been refactored to use named static classes instead of anonymous ones. If you add new code, follow the same pattern.

---

## License

MIT. The bundled `aapt2`, `d8.jar`, `apksigner`, and `android.jar` are © Google/AOSP under their respective licenses.
