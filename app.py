# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "litestar",
#     "python-multipart",
#     "sniffio",
#     "uvicorn",
# ]
# ///

import asyncio
import http.server
import json
import os
import re
import shutil
import socketserver
import subprocess
import sys
import threading
import uuid
import zipfile
from pathlib import Path
from threading import Lock
from typing import Annotated, AsyncGenerator

from litestar import Litestar, get, post
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

# --- GIT URL CONVERSION ---


def convert_git_to_raw_url(
    repo_url: str, branch: str = "main", entry_path: str = "index.html"
) -> str:
    """
    Convert a Git repository URL to a served URL for live loading.
    Uses raw.githack.com for GitHub (serves with correct MIME types as web pages).
    """
    import urllib.parse

    parsed = urllib.parse.urlparse(repo_url)
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/")

    # Remove .git suffix if present
    if path.endswith(".git"):
        path = path[:-4]

    # Extract user/repo from path
    parts = path.strip("/").split("/")
    if len(parts) < 2:
        raise ValueError(f"Invalid repository URL: {repo_url}")

    user = parts[0]
    repo = parts[1]

    # Normalize entry path
    entry_path = entry_path.lstrip("/")

    # Convert based on host
    if "github.com" in host:
        # raw.githack.com serves GitHub files with correct MIME types
        # Format: https://raw.githack.com/user/repo/branch/path
        return f"https://raw.githack.com/{user}/{repo}/{branch}/{entry_path}"

    elif "gitlab.com" in host:
        # GitLab Pages (if enabled)
        return f"https://{user}.gitlab.io/{repo}/{entry_path}"

    elif "codeberg.org" in host:
        # Codeberg Pages
        return f"https://{user}.codeberg.page/{repo}/{entry_path}"

    else:
        # Generic: try raw URL
        return (
            f"{parsed.scheme}://{host}/{user}/{repo}/raw/branch/{branch}/{entry_path}"
        )


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

# 3. Git Mode MainActivity: Downloads repo on first launch, works offline, auto-updates
MAIN_ACTIVITY_GIT_TEMPLATE = """package com.APP_ID_PLACEHOLDER.htpk;

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
import android.widget.TextView;
import android.os.Build;
import android.graphics.Color;
import androidx.core.view.WindowCompat;
import android.content.SharedPreferences;
import android.os.AsyncTask;
import android.util.Log;

import androidx.webkit.WebViewAssetLoader;

import java.io.*;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

public class MainActivity extends AppCompatActivity {
    private static final String TAG = "GitWebApp";
    private static final String PREFS_NAME = "GitWebAppPrefs";
    private static final String KEY_COMMIT_SHA = "commit_sha";
    private static final String KEY_DOWNLOADED = "content_downloaded";

    // Git configuration (injected at build time)
    private static final String GIT_USER = "GIT_USER_PLACEHOLDER";
    private static final String GIT_REPO = "GIT_REPO_PLACEHOLDER";
    private static final String GIT_BRANCH = "GIT_BRANCH_PLACEHOLDER";
    private static final String GIT_ENTRY = "GIT_ENTRY_PLACEHOLDER";

    private WebView webview;
    private ProgressBar spinner;
    private TextView statusText;
    private File contentDir;
    private SharedPreferences prefs;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        WindowCompat.setDecorFitsSystemWindows(getWindow(), false);
        getWindow().setStatusBarColor(Color.TRANSPARENT);
        getWindow().setNavigationBarColor(Color.TRANSPARENT);

        setContentView(R.layout.activity_main);

        webview = findViewById(R.id.webView);
        spinner = findViewById(R.id.progressBar1);
        statusText = findViewById(R.id.statusText);

        contentDir = new File(getFilesDir(), "web_content");
        prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);

        setupWebView();

        // Check if content exists locally
        if (isContentDownloaded()) {
            // Load cached content immediately
            loadLocalContent();
            // Check for updates in background
            new CheckUpdateTask().execute();
        } else {
            // First launch - download content
            showStatus("Downloading content...");
            new DownloadContentTask().execute();
        }
    }

    private void setupWebView() {
        WebViewAssetLoader assetLoader = new WebViewAssetLoader.Builder()
            .setDomain("app.local")
            .addPathHandler("/", new WebViewAssetLoader.InternalStoragePathHandler(this, contentDir))
            .build();

        webview.setWebViewClient(new WebViewClient() {
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                return assetLoader.shouldInterceptRequest(request.getUrl());
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                hideLoading();
            }
        });

        webview.setWebChromeClient(new WebChromeClient());

        WebSettings settings = webview.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setAllowFileAccess(true);

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        }
        settings.setMediaPlaybackRequiresUserGesture(false);
    }

    private boolean isContentDownloaded() {
        return prefs.getBoolean(KEY_DOWNLOADED, false) && contentDir.exists() && contentDir.list().length > 0;
    }

    private void loadLocalContent() {
        String entryPath = GIT_ENTRY.isEmpty() ? "index.html" : GIT_ENTRY;
        webview.loadUrl("https://app.local/" + entryPath);
    }

    private void showStatus(String msg) {
        runOnUiThread(() -> {
            if (statusText != null) {
                statusText.setText(msg);
                statusText.setVisibility(View.VISIBLE);
            }
            spinner.setVisibility(View.VISIBLE);
            webview.setVisibility(View.GONE);
        });
    }

    private void hideLoading() {
        runOnUiThread(() -> {
            spinner.setVisibility(View.GONE);
            if (statusText != null) statusText.setVisibility(View.GONE);
            webview.setVisibility(View.VISIBLE);
        });
    }

    // Download repo ZIP from GitHub
    private class DownloadContentTask extends AsyncTask<Void, String, Boolean> {
        @Override
        protected Boolean doInBackground(Void... voids) {
            try {
                String zipUrl = "https://github.com/" + GIT_USER + "/" + GIT_REPO + "/archive/refs/heads/" + GIT_BRANCH + ".zip";
                Log.d(TAG, "Downloading: " + zipUrl);
                publishProgress("Downloading...");

                URL url = new URL(zipUrl);
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setInstanceFollowRedirects(true);

                if (conn.getResponseCode() != 200) {
                    Log.e(TAG, "Download failed: " + conn.getResponseCode());
                    return false;
                }

                // Clear old content
                deleteRecursive(contentDir);
                contentDir.mkdirs();

                publishProgress("Extracting...");

                // Extract ZIP
                ZipInputStream zis = new ZipInputStream(conn.getInputStream());
                ZipEntry entry;
                String rootFolder = null;

                while ((entry = zis.getNextEntry()) != null) {
                    String name = entry.getName();

                    // GitHub ZIP has a root folder like "repo-branch/"
                    if (rootFolder == null && name.contains("/")) {
                        rootFolder = name.substring(0, name.indexOf("/") + 1);
                    }

                    // Remove root folder prefix
                    if (rootFolder != null && name.startsWith(rootFolder)) {
                        name = name.substring(rootFolder.length());
                    }

                    if (name.isEmpty()) continue;

                    File outFile = new File(contentDir, name);

                    if (entry.isDirectory()) {
                        outFile.mkdirs();
                    } else {
                        outFile.getParentFile().mkdirs();
                        FileOutputStream fos = new FileOutputStream(outFile);
                        byte[] buffer = new byte[4096];
                        int len;
                        while ((len = zis.read(buffer)) > 0) {
                            fos.write(buffer, 0, len);
                        }
                        fos.close();
                    }
                    zis.closeEntry();
                }
                zis.close();

                // Get current commit SHA for version tracking
                String sha = fetchLatestCommitSha();
                if (sha != null) {
                    prefs.edit().putString(KEY_COMMIT_SHA, sha).apply();
                }

                prefs.edit().putBoolean(KEY_DOWNLOADED, true).apply();
                return true;

            } catch (Exception e) {
                Log.e(TAG, "Download error", e);
                return false;
            }
        }

        @Override
        protected void onProgressUpdate(String... values) {
            showStatus(values[0]);
        }

        @Override
        protected void onPostExecute(Boolean success) {
            if (success) {
                loadLocalContent();
            } else {
                showStatus("Download failed. Check internet connection.");
            }
        }
    }

    // Check for updates via GitHub API
    private class CheckUpdateTask extends AsyncTask<Void, Void, Boolean> {
        private String newSha;

        @Override
        protected Boolean doInBackground(Void... voids) {
            try {
                String currentSha = prefs.getString(KEY_COMMIT_SHA, "");
                newSha = fetchLatestCommitSha();

                if (newSha != null && !newSha.equals(currentSha)) {
                    Log.d(TAG, "Update available: " + currentSha + " -> " + newSha);
                    return true;
                }
            } catch (Exception e) {
                Log.d(TAG, "Update check failed (offline?)", e);
            }
            return false;
        }

        @Override
        protected void onPostExecute(Boolean updateAvailable) {
            if (updateAvailable) {
                // Download update in background
                new DownloadContentTask() {
                    @Override
                    protected void onPostExecute(Boolean success) {
                        if (success) {
                            // Reload page with new content
                            loadLocalContent();
                        }
                    }
                }.execute();
            }
        }
    }

    private String fetchLatestCommitSha() {
        try {
            String apiUrl = "https://api.github.com/repos/" + GIT_USER + "/" + GIT_REPO + "/commits/" + GIT_BRANCH;
            URL url = new URL(apiUrl);
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setRequestProperty("Accept", "application/vnd.github.v3+json");

            if (conn.getResponseCode() == 200) {
                BufferedReader reader = new BufferedReader(new InputStreamReader(conn.getInputStream()));
                StringBuilder response = new StringBuilder();
                String line;
                while ((line = reader.readLine()) != null) {
                    response.append(line);
                }
                reader.close();

                // Simple JSON parse for "sha" field
                String json = response.toString();
                int shaIndex = json.indexOf('"' + "sha" + '"');
                if (shaIndex != -1) {
                    int start = json.indexOf('"', shaIndex + 5) + 1;
                    int end = json.indexOf('"', start);
                    return json.substring(start, end);
                }
            }
        } catch (Exception e) {
            Log.d(TAG, "SHA fetch failed", e);
        }
        return null;
    }

    private void deleteRecursive(File file) {
        if (file.isDirectory()) {
            File[] children = file.listFiles();
            if (children != null) {
                for (File child : children) {
                    deleteRecursive(child);
                }
            }
        }
        file.delete();
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

# Gradle task patterns and their progress weights
# These tasks appear in order during an Android build
GRADLE_TASKS = [
    (r"preBuild", 5),
    (r"preReleaseBuild", 8),
    (r"compileReleaseAidl", 10),
    (r"compileReleaseRenderscript", 12),
    (r"generateReleaseBuildConfig", 15),
    (r"generateReleaseResValues", 18),
    (r"generateReleaseResources", 20),
    (r"mergeReleaseResources", 25),
    (r"processReleaseResources", 30),
    (r"compileReleaseJavaWithJavac", 45),
    (r"compileReleaseSources", 50),
    (r"mergeReleaseJavaResource", 55),
    (r"dexBuilderRelease", 60),
    (r"mergeDexRelease", 70),
    (r"mergeReleaseJniLibFolders", 72),
    (r"mergeReleaseNativeLibs", 75),
    (r"packageRelease", 85),
    (r"assembleRelease", 90),
    (r"signReleaseBundle", 92),
    (r"BUILD SUCCESSFUL", 95),
]


def run_command(command: list[str], cwd: Path, output_target_dir: Path = None) -> None:
    env = os.environ.copy()
    env["ANDROID_PROJECT_ROOT"] = str(ANDROID_DIR)
    env["CACHE_DIR"] = str(CACHE_DIR)
    if output_target_dir:
        env["OUTPUT_DIR"] = str(output_target_dir)

    subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def run_gradle_with_progress(
    command: list[str],
    cwd: Path,
    build_id: str,
    base_progress: int = 60,
    output_target_dir: Path = None,
) -> None:
    """Run Gradle command with real-time progress tracking."""
    env = os.environ.copy()
    env["ANDROID_PROJECT_ROOT"] = str(ANDROID_DIR)
    env["CACHE_DIR"] = str(CACHE_DIR)
    if output_target_dir:
        env["OUTPUT_DIR"] = str(output_target_dir)

    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    current_task = "Starting Gradle..."
    last_progress = base_progress
    output_lines = []

    for line in process.stdout:
        output_lines.append(line)
        line_stripped = line.strip()

        # Check for task patterns
        for pattern, progress_value in GRADLE_TASKS:
            if pattern in line_stripped:
                # Scale progress: base_progress to 95
                scaled_progress = base_progress + int(
                    (progress_value / 100) * (95 - base_progress)
                )
                if scaled_progress > last_progress:
                    last_progress = scaled_progress
                    # Extract a cleaner task name
                    if ">" in line_stripped:
                        task_part = line_stripped.split(">")[-1].strip()
                        current_task = (
                            task_part[:50] if len(task_part) > 50 else task_part
                        )
                    else:
                        current_task = pattern

                    with build_states_lock:
                        build_states[build_id].update(
                            {
                                "progress": last_progress,
                                "message": f"Building: {current_task}",
                                "status": "in_progress",
                            }
                        )
                break

        # Also check for downloading dependencies (first build)
        if "Downloading" in line_stripped or "Download" in line_stripped:
            with build_states_lock:
                build_states[build_id].update(
                    {
                        "message": "Downloading dependencies...",
                        "status": "in_progress",
                    }
                )
        elif "Compiling" in line_stripped:
            with build_states_lock:
                build_states[build_id].update(
                    {
                        "message": "Compiling source code...",
                        "status": "in_progress",
                    }
                )

    process.wait()

    if process.returncode != 0:
        output_text = "".join(output_lines)
        raise subprocess.CalledProcessError(
            process.returncode, command, output=output_text.encode()
        )


def write_conf(app_id: str, name: str, target_path: Path) -> None:
    content = f"id = {app_id}\nname = {name}\nicon = {ICON_FILENAME}\n"
    target_path.write_text(content, encoding="utf-8")


def overwrite_android_files(
    app_id: str, main_url: str, app_name: str = None, git_info: dict = None
) -> None:
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

    if git_info:
        # Use Git template with download/cache/update functionality
        java_content = MAIN_ACTIVITY_GIT_TEMPLATE.replace("APP_ID_PLACEHOLDER", app_id)
        java_content = java_content.replace("GIT_USER_PLACEHOLDER", git_info["user"])
        java_content = java_content.replace("GIT_REPO_PLACEHOLDER", git_info["repo"])
        java_content = java_content.replace(
            "GIT_BRANCH_PLACEHOLDER", git_info["branch"]
        )
        java_content = java_content.replace("GIT_ENTRY_PLACEHOLDER", git_info["entry"])
        print(f"[BUILDER] Using Git template for {git_info['user']}/{git_info['repo']}")
    else:
        # Standard template for URL/ZIP mode
        java_content = MAIN_ACTIVITY_TEMPLATE.replace(
            "APP_ID_PLACEHOLDER", app_id
        ).replace("MAIN_URL_PLACEHOLDER", main_url)

    java_file.write_text(java_content, encoding="utf-8")

    # 3. Write strings.xml with the actual app name
    strings_path = ANDROID_DIR / "app/src/main/res/values/strings.xml"
    strings_path.parent.mkdir(parents=True, exist_ok=True)
    display_name = app_name if app_name else app_id
    strings_path.write_text(
        f'<resources>\n    <string name="app_name">{display_name}</string>\n</resources>',
        encoding="utf-8",
    )


def execute_build_async(build_id: str, data: dict) -> None:
    def update(progress, msg, status="in_progress", **kwargs):
        with build_states_lock:
            build_states[build_id].update(
                {"progress": progress, "message": msg, "status": status, **kwargs}
            )

    try:
        app_id, name = data["app_id"], data["name"]
        app_output_dir = OUTPUT_DIR / app_id
        app_output_dir.mkdir(parents=True, exist_ok=True)

        update(5, "Preparing assets...")
        (app_output_dir / ICON_FILENAME).write_bytes(data["icon_data"])

        if data["zip_data"]:
            # Local File Mode
            assets_dir = ANDROID_DIR / "app/src/main/assets"
            if assets_dir.exists():
                shutil.rmtree(assets_dir)
            assets_dir.mkdir(parents=True, exist_ok=True)

            zip_path = app_output_dir / "assets.zip"
            zip_path.write_bytes(data["zip_data"])
            with zipfile.ZipFile(zip_path, "r") as z:
                z.extractall(assets_dir)
            zip_path.unlink()

            # Find index.html
            index_file = next((p for p in assets_dir.rglob("index.htm*")), None)
            if not index_file:
                raise RuntimeError("No index.html found in zip")

            rel_path = str(index_file.relative_to(assets_dir)).replace("\\", "/")

            # KEY FIX: Virtual Domain for ES Modules support
            final_url = f"https://appassets.androidplatform.net/assets/{rel_path}"
            git_info = None
        elif data.get("git_url"):
            # Git Repository Mode - Parse URL for offline-capable build
            update(10, "Configuring Git repository...")
            import urllib.parse

            parsed = urllib.parse.urlparse(data["git_url"])
            path = parsed.path.rstrip("/")
            if path.endswith(".git"):
                path = path[:-4]
            parts = path.strip("/").split("/")
            if len(parts) < 2:
                raise ValueError(f"Invalid repository URL: {data['git_url']}")

            git_info = {
                "user": parts[0],
                "repo": parts[1],
                "branch": data.get("git_branch", "main"),
                "entry": data.get("git_entry", "index.html"),
            }
            final_url = ""  # Not used for Git mode (app downloads content itself)
            print(
                f"[BUILDER] Git mode configured for {git_info['user']}/{git_info['repo']}"
            )
        else:
            # URL Mode
            final_url = data["main_url"]
            git_info = None

        update(20, "Cleaning previous builds...")
        # CRITICAL FIX: Clean build artifacts to prevent crashes from stale cache
        # We ignore errors here in case clean fails on a fresh run
        try:
            run_command(["bash", str(MAKE_SH_PATH), "clean"], cwd=BASE_DIR)
        except:
            pass

        update(30, "Configuring project...")
        conf_path = app_output_dir / CONF_FILENAME
        write_conf(app_id, name, conf_path)

        # Run make.sh apply_config (handles Manifest updates)
        run_command(
            ["bash", str(MAKE_SH_PATH), "apply_config", str(conf_path)], cwd=BASE_DIR
        )

        # Overwrite source code with correct templates (AssetLoader + Mixed Content)
        update(45, "Injecting source code...")
        overwrite_android_files(app_id, final_url, name, git_info)

        update(50, "Building APK...")
        run_gradle_with_progress(
            ["bash", str(MAKE_SH_PATH), "apk"],
            cwd=BASE_DIR,
            build_id=build_id,
            base_progress=50,
            output_target_dir=app_output_dir,
        )

        update(96, "Verifying APK...")
        final_apk = app_output_dir / f"{app_id}.apk"
        if not final_apk.exists():
            raise FileNotFoundError("APK build failed")

        update(98, "Finalizing...")
        update(
            100,
            "Done!",
            "complete",
            apk_path=str(final_apk),
            apk_filename=f"{app_id}_release.apk",
        )

    except subprocess.CalledProcessError as e:
        error_msg = f"Command failed: {e.cmd}\nOutput:\n{e.stdout.decode('utf-8', errors='replace')}"
        print(f"Build Error: {error_msg}")
        update(0, "Build failed. Check console for details.", "error", error=error_msg)
    except Exception as e:
        print(f"Build Error: {e}")
        update(0, f"Error: {str(e)}", "error", error=str(e))


# --- ROUTES ---


@post("/build-app")
async def build_apk(
    data: Annotated[dict, Body(media_type=RequestEncodingType.MULTI_PART)],
) -> dict:
    build_id = str(uuid.uuid4())
    with build_states_lock:
        build_states[build_id] = {
            "status": "in_progress",
            "progress": 0,
            "message": "Starting...",
        }

    thread_data = {
        "app_id": data["app_id"],
        "name": data["name"],
        "icon_data": await data["icon"].read(),
        "main_url": data.get("main_url"),
        "zip_data": await data["zip_file"].read() if data.get("zip_file") else None,
        "git_url": data.get("git_url"),
        "git_branch": data.get("git_branch", "main"),
        "git_entry": data.get("git_entry", "index.html"),
    }
    threading.Thread(
        target=execute_build_async, args=(build_id, thread_data), daemon=True
    ).start()
    return {"build_id": build_id}


@get("/build-progress/{build_id:str}")
async def stream_progress(build_id: str) -> Stream:
    async def generator():
        last = None
        while True:
            with build_states_lock:
                state = build_states.get(build_id)
            if not state:
                yield f"event: error\ndata: {json.dumps({'error': 'Invalid ID'})}\n\n"
                break

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
    with build_states_lock:
        state = build_states.get(build_id)
    if not state or state["status"] != "complete":
        raise RuntimeError("Not ready")
    return File(
        path=Path(state["apk_path"]),
        filename=state["apk_filename"],
        media_type="application/vnd.android.package-archive",
    )


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
        with ReusableTCPServer(("", 9742), handler) as httpd:
            print(f"[FRONTEND] http://localhost:9742")
            httpd.serve_forever()

    threading.Thread(target=serve_front, daemon=True).start()
    print("[BACKEND] http://0.0.0.0:9741")
    uvicorn.run(app, host="0.0.0.0", port=9741, log_level="error")
