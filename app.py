import os
import subprocess
import sys
import re
import threading
import shutil
import zipfile
import uuid
import asyncio
import json
import http.server
import socketserver
from pathlib import Path
from typing import Annotated, AsyncGenerator
from threading import Lock

from litestar import Litestar, post, get
from litestar.config.cors import CORSConfig
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import File, Stream

# --- CONFIGURATION ---
BASE_DIR = Path(__file__).parent.absolute()
ANDROID_DIR = BASE_DIR / "android_source"
OUTPUT_DIR = BASE_DIR / "output"
CACHE_DIR = BASE_DIR / "cache"
WEB_DIR = BASE_DIR / "web_ui"
MAKE_SH_PATH = BASE_DIR / "make.sh"
ICON_FILENAME = "icon.png"
CONF_FILENAME = "webapk.conf"

OUTPUT_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(exist_ok=True)

# --- TEMPLATES (ROBUST) ---

# 1. Gradle: Ensures WebKit is included for Virtual Domain support
BUILD_GRADLE_TEMPLATE = """buildscript {
    repositories {
        google()
        mavenCentral()
    }
    dependencies {
        classpath 'com.android.tools.build:gradle:7.3.0'
    }
}

allprojects {
    repositories {
        google()
        mavenCentral()
    }
}

apply plugin: 'com.android.application'

android {
    namespace 'com.APP_ID_PLACEHOLDER.htpk'

    compileSdkVersion 33
    defaultConfig {
        applicationId "com.APP_ID_PLACEHOLDER.htpk"
        minSdkVersion 24
        targetSdkVersion 33
        versionCode 1
        versionName "1.0"
    }

    lintOptions {
        checkReleaseBuilds false
        abortOnError false
    }

    compileOptions {
        sourceCompatibility JavaVersion.VERSION_11
        targetCompatibility JavaVersion.VERSION_11
    }

    signingConfigs {
        release {
            storeFile file("my-release-key.jks")
            storePassword "123456"
            keyAlias "my"
            keyPassword "123456"
        }
    }

    buildTypes {
        release {
            minifyEnabled false
            proguardFiles getDefaultProguardFile('proguard-android-optimize.txt'), 'proguard-rules.pro'
            signingConfig signingConfigs.release
        }
    }
}

dependencies {
    implementation 'androidx.appcompat:appcompat:1.6.1'
    implementation 'androidx.core:core:1.9.0'
    implementation 'org.unifiedpush.android:connector:3.0.10'
    implementation 'androidx.media:media:1.6.0'
    implementation 'androidx.localbroadcastmanager:localbroadcastmanager:1.1.0'

    // REQUIRED: WebKit for WebViewAssetLoader (Fixes ES Modules & CORS)
    implementation 'androidx.webkit:webkit:1.6.0'
}
"""

# 2. MainActivity: Uses AssetLoader + Mixed Content Fixes
MAIN_ACTIVITY_TEMPLATE = """package com.APP_ID_PLACEHOLDER.htpk;

import androidx.appcompat.app.AppCompatActivity;
import android.os.Bundle;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.view.View;
import android.widget.ProgressBar;
import android.os.Build;
import android.graphics.Color;
import androidx.core.view.WindowCompat;
import android.content.Intent;
import android.net.Uri;

// AssetLoader Import
import androidx.webkit.WebViewAssetLoader;

public class MainActivity extends AppCompatActivity {
    private WebView webview;
    private ProgressBar spinner;
    private WebViewAssetLoader assetLoader;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        WindowCompat.setDecorFitsSystemWindows(getWindow(), false);
        getWindow().setStatusBarColor(Color.TRANSPARENT);
        getWindow().setNavigationBarColor(Color.TRANSPARENT);

        setContentView(R.layout.activity_main);

        webview = findViewById(R.id.webView);
        spinner = findViewById(R.id.progressBar1);

        // Initialize AssetLoader (Maps "https://appassets.androidplatform.net/assets/" to local files)
        assetLoader = new WebViewAssetLoader.Builder()
            .addPathHandler("/assets/", new WebViewAssetLoader.AssetsPathHandler(this))
            .build();

        webview.setWebViewClient(new WebViewClient() {
            // INTERCEPT REQUESTS: Serve local files via virtual HTTPS domain
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                WebResourceResponse response = assetLoader.shouldInterceptRequest(request.getUrl());
                if (response != null) return response;
                return super.shouldInterceptRequest(view, request);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                spinner.setVisibility(View.GONE);
                webview.setVisibility(View.VISIBLE);
            }

            // Handle external links
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                String url = request.getUrl().toString();
                // Allow our virtual domain
                if (url.startsWith("https://appassets.androidplatform.net")) return false;
                return false;
            }
        });

        webview.setWebChromeClient(new WebChromeClient());

        WebSettings settings = webview.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);

        // --- CRITICAL FIXES ---

        // 1. Allow HTTP (Videos) on HTTPS (App)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        }

        // 2. Allow Autoplay
        settings.setMediaPlaybackRequiresUserGesture(false);

        // 3. File Access (Backup)
        settings.setAllowFileAccess(true);
        settings.setAllowUniversalAccessFromFileURLs(true);

        webview.loadUrl("MAIN_URL_PLACEHOLDER");
    }

    @Override
    public void onBackPressed() {
        if (webview.canGoBack()) webview.goBack();
        else super.onBackPressed();
    }
}
"""

# --- HELPERS ---

build_states = {}
build_states_lock = Lock()

def run_command(command: list[str], cwd: Path, output_target_dir: Path = None) -> None:
    env = os.environ.copy()
    env["ANDROID_PROJECT_ROOT"] = str(ANDROID_DIR)
    env["CACHE_DIR"] = str(CACHE_DIR)
    if output_target_dir: env["OUTPUT_DIR"] = str(output_target_dir)

    subprocess.run(command, cwd=cwd, env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

def write_conf(app_id: str, name: str, target_path: Path) -> None:
    content = f"id = {app_id}\nname = {name}\nicon = {ICON_FILENAME}\n"
    target_path.write_text(content, encoding="utf-8")

def overwrite_android_files(app_id: str, main_url: str) -> None:
    """Completely regenerates the Android source files to prevent corruption."""
    print(f"[BUILDER] Overwriting Android Source Files for {app_id}...")

    # 1. Overwrite build.gradle
    gradle_path = ANDROID_DIR / "app/build.gradle"
    gradle_content = BUILD_GRADLE_TEMPLATE.replace("APP_ID_PLACEHOLDER", app_id)
    gradle_path.write_text(gradle_content, encoding="utf-8")

    # 2. Overwrite MainActivity.java
    package_dir = ANDROID_DIR / "app/src/main/java/com" / app_id / "htpk"
    package_dir.mkdir(parents=True, exist_ok=True)

    # Clean old files to prevent "Duplicate Class" errors
    src_root = ANDROID_DIR / "app/src/main/java"
    for f in src_root.glob("**/MainActivity.java"):
        if f.parent.resolve() != package_dir.resolve():
            f.unlink()

    java_file = package_dir / "MainActivity.java"
    java_content = MAIN_ACTIVITY_TEMPLATE.replace("APP_ID_PLACEHOLDER", app_id).replace("MAIN_URL_PLACEHOLDER", main_url)
    java_file.write_text(java_content, encoding="utf-8")

def execute_build_async(build_id: str, data: dict) -> None:
    def update(progress, msg, status="in_progress", **kwargs):
        with build_states_lock:
            build_states[build_id].update({"progress": progress, "message": msg, "status": status, **kwargs})

    try:
        app_id, name = data["app_id"], data["name"]
        app_output_dir = OUTPUT_DIR / app_id
        app_output_dir.mkdir(parents=True, exist_ok=True)

        update(5, "Preparing assets...")
        (app_output_dir / ICON_FILENAME).write_bytes(data["icon_data"])

        if data["zip_data"]:
            # Local File Mode
            assets_dir = ANDROID_DIR / "app/src/main/assets"
            if assets_dir.exists(): shutil.rmtree(assets_dir)
            assets_dir.mkdir(parents=True, exist_ok=True)

            zip_path = app_output_dir / "assets.zip"
            zip_path.write_bytes(data["zip_data"])
            with zipfile.ZipFile(zip_path, 'r') as z: z.extractall(assets_dir)
            zip_path.unlink()

            # Find index.html
            index_file = next((p for p in assets_dir.rglob("index.htm*")), None)
            if not index_file: raise RuntimeError("No index.html found in zip")

            rel_path = str(index_file.relative_to(assets_dir)).replace("\\", "/")

            # KEY FIX: Virtual Domain for ES Modules support
            final_url = f"https://appassets.androidplatform.net/assets/{rel_path}"
        else:
            # URL Mode
            final_url = data["main_url"]

        update(15, "Cleaning previous builds...")
        # CRITICAL FIX: Clean build artifacts to prevent crashes from stale cache
        # We ignore errors here in case clean fails on a fresh run
        try:
            run_command(["bash", str(MAKE_SH_PATH), "clean"], cwd=BASE_DIR)
        except:
            pass

        update(25, "Configuring project...")
        conf_path = app_output_dir / CONF_FILENAME
        write_conf(app_id, name, conf_path)

        # Run make.sh apply_config (handles Manifest updates)
        run_command(["bash", str(MAKE_SH_PATH), "apply_config", str(conf_path)], cwd=BASE_DIR)

        # Overwrite source code with correct templates (AssetLoader + Mixed Content)
        update(40, "Injecting source code...")
        overwrite_android_files(app_id, final_url)

        update(60, "Building APK (this takes a minute)...")
        run_command(["bash", str(MAKE_SH_PATH), "apk"], cwd=BASE_DIR, output_target_dir=app_output_dir)

        final_apk = app_output_dir / f"{app_id}.apk"
        if not final_apk.exists(): raise FileNotFoundError("APK build failed")

        update(100, "Done!", "complete", apk_path=str(final_apk), apk_filename=f"{app_id}_release.apk")

    except Exception as e:
        print(f"Build Error: {e}")
        update(0, f"Error: {str(e)}", "error", error=str(e))

# --- ROUTES ---

@post("/build-app")
async def build_apk(data: Annotated[dict, Body(media_type=RequestEncodingType.MULTI_PART)]) -> dict:
    build_id = str(uuid.uuid4())
    with build_states_lock:
        build_states[build_id] = {"status": "in_progress", "progress": 0, "message": "Starting..."}

    thread_data = {
        "app_id": data["app_id"], "name": data["name"],
        "icon_data": await data["icon"].read(),
        "main_url": data.get("main_url"),
        "zip_data": await data["zip_file"].read() if data.get("zip_file") else None
    }
    threading.Thread(target=execute_build_async, args=(build_id, thread_data), daemon=True).start()
    return {"build_id": build_id}

@get("/build-progress/{build_id:str}")
async def stream_progress(build_id: str) -> Stream:
    async def generator():
        last = None
        while True:
            with build_states_lock: state = build_states.get(build_id)
            if not state: yield f"event: error\ndata: {json.dumps({'error': 'Invalid ID'})}\n\n"; break

            if state != last:
                yield f"data: {json.dumps(state)}\n\n"
                last = state.copy()

            if state["status"] == "complete":
                yield f"event: complete\ndata: {json.dumps({'build_id': build_id})}\n\n"
                break
            if state["status"] == "error":
                yield f"event: error\ndata: {json.dumps({'error': state.get('error')})}\n\n"
                break
            await asyncio.sleep(0.5)

    return Stream(generator(), media_type="text/event-stream")

@get("/download-apk/{build_id:str}")
async def download(build_id: str) -> File:
    with build_states_lock: state = build_states.get(build_id)
    if not state or state["status"] != "complete": raise RuntimeError("Not ready")
    return File(path=Path(state["apk_path"]), filename=state["apk_filename"], media_type="application/vnd.android.package-archive")

# --- RUN ---
cors = CORSConfig(allow_origins=["*"])
app = Litestar(route_handlers=[build_apk, stream_progress, download], cors_config=cors)

class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

if __name__ == "__main__":
    import uvicorn
    os.chdir(WEB_DIR)

    def serve_front():
        handler = http.server.SimpleHTTPRequestHandler
        with ReusableTCPServer(("", 8001), handler) as httpd:
            print(f"[FRONTEND] http://localhost:8001")
            httpd.serve_forever()

    threading.Thread(target=serve_front, daemon=True).start()
    print("[BACKEND] http://0.0.0.0:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="error")
