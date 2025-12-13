# HTPK - Website-to-APK Builder

A minimal, high-performance tool to convert any static website or URL into an Android APK. It features a local web interface for configuration and a dedicated build backend that orchestrates the Android compilation process.

## Key Features

* **Unified Runtime:** A single script launches both the Build [Litestar](https://litestar.dev/) API and the Web UI.
* **Zero-Config Python:** Powered by `uv`, dependencies are managed automatically via inline script metadata.
* **Automated Setup:** Automatically checks for and downloads Java 17 JDK and Android Command Line Tools if they are missing.
* **Optimized Building:** Configured for maximum Gradle speed (Daemon enabled, parallel execution, and caching).
* **Auto-Patching:** Automatically updates the Android source code, package names, and permissions to match your input configuration before every build.
* **Simple UI:** Clean, minimal interface built with Tailwind CSS and Alpine.js.

## Prerequisites

* **OS:** Linux or WSL2 (Windows Subsystem for Linux).
* **Package Manager:** [uv](https://github.com/astral-sh/uv) (Required).

## Installation

### 1. Clone the Repository
```bash
git clone https://github.com/RenanEuzebio/htpk.app2.git
cd htpk.app2/
```

### 2. Initial Setup (Run Once)

Run the setup script to download the required build tools (~150MB). This generates your signing keystore and sets up the Android SDK environment.

```bash
uv run setup.py
```

## Usage

### 1. Start the Application

Run the main application script. `uv` will automatically install the required Python libraries (Litestar, Uvicorn, etc.) in a cached environment and start the server.

```bash
uv run app.py
```

This starts:
- **Backend API** on `http://localhost:9741`
- **Frontend UI** on `http://localhost:9742`

### 2. Build Your App

1. Open your browser to **http://localhost:9742**.
2. Enter the **App ID** and **App Name**.
3. Choose your source method:
   - **ZIP File**: Upload a ZIP containing your website (bundled into APK, works offline)
   - **Git**: Enter a GitHub repo URL (downloads on first launch, auto-updates when online)
   - **URL**: Enter any website URL (always loads live from the internet)
4. Upload a **PNG Icon**.
5. Click **Build APK**.

*The first build may take 1-2 minutes to initialize Gradle. Subsequent builds typically complete in 5-30 seconds.*

### 3. Locate Your APK

Once finished, the APK will download automatically in your browser. You can also find saved copies in the `output/` folder.

## Directory Structure

The project is organized into modular components:

* **`app.py`**: The main entry point. Orchestrates the API, serves the frontend, and manages the build queue.
* **`setup.py`**: Handles environment initialization (downloading Java/SDKs).
* **`make.sh`**: The low-level Shell script that wraps Gradle commands.
* **`web_ui/`**: Contains the HTML frontend interface (Tailwind CSS + Alpine.js).
* **`android_source/`**: The complete Android project source code (`app`, `gradle`, manifest, etc.).
* **`output/`**: The destination folder for all successfully built APKs.
* **`lib/`**: Contains the downloaded dependencies (JDK, Gradle, Android SDK).

## Troubleshooting

* **"Port 9741/9742 already in use":** Ensure no other instances of the script are running. You can check with `lsof -i :9741` or `lsof -i :9742`.
* **Build Fails immediately:** Check the terminal output. If you see errors about missing files, try running `uv run setup.py` again to ensure the SDK is complete.