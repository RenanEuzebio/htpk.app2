# HTPK - Website-to-APK Builder

A minimal, high-performance tool to convert any static website or URL into an Android APK. It features a local web interface for configuration and a dedicated build backend that orchestrates the Android compilation process.

## Key Features

* **Unified Runtime:** A single script launches both the Build [Litestar](https://litestar.dev/) API and the Web UI.
* **Automated Setup:** Automatically checks for and downloads Java 17 JDK and Android Command Line Tools if they are missing.
* **Optimized Building:** Configured for maximum Gradle speed (Daemon enabled, parallel execution, and caching).
* **Auto-Patching:** Automatically updates the Android source code, package names, and permissions to match your input configuration before every build.
* **Self-Healing:** Detects and repairs project structure mismatches if a build is interrupted.

## Prerequisites

* **OS:** Linux or WSL2 (Windows Subsystem for Linux).
* **Python:** 3.12+
* **Package Manager:** `uv` (Recommended) or `pip`.

## Installation

### 1. Clone the Repository
```bash
git clone https://github.com/RenanEuzebio/htpk.app/ 
cd htpk.app/
````

### 2\. Install Dependencies

We recommend using `uv` for fast virtual environment management.

```bash
# Create virtual environment
uv venv
source .venv/bin/activate

# Install Python requirements
uv pip install litestar uvicorn python-multipart
```

### 3\. Initial Setup (Run Once)

Run the setup script to download the required build tools (\~150MB). This generates your signing keystore and sets up the Android SDK environment.

```bash
python setup.py
```

## Usage

### 1\. Start the Application

Run the main application script. This handles both the backend logic and serves the frontend interface.

```bash
python app.py
```

### 2\. Build Your App

1.  Open your browser to **[http://localhost:8001](https://www.google.com/search?q=http://localhost:8001)**.
2.  Enter the **App ID**, **App Name**, and **URL**.
3.  Upload a **PNG Icon**.
4.  Click **Build APK**.

*The first build may take 1-2 minutes to initialize Gradle. Subsequent builds typically complete in 5-30 seconds.*

### 3\. Locate Your APK

Once finished, the APK will download automatically in your browser. You can also find saved copies in the `output_apks/` folder.

## Directory Structure

The project is organized into modular components:

  * **`app.py`**: The main entry point. Orchestrates the API, serves the frontend, and manages the build queue.
  * **`setup.py`**: Handles environment initialization (downloading Java/SDKs).
  * **`web_interface/`**: Contains the HTML/JS frontend code.
  * **`build_scripts/`**: Contains the low-level Shell scripts (`make.sh`) that wrap Gradle commands.
  * **`android_source/`**: The complete Android project source code (`app`, `gradle`, manifest, etc.).
  * **`output_apks/`**: The destination folder for all successfully built APKs.

## Troubleshooting

  * **"Port 8000/8001 already in use":** Ensure no other instances of the script are running. You can check with `lsof -i :8000`.
  * **Build Fails immediately:** Check the terminal output. If you see errors about missing files, try running `python setup.py` again to ensure the SDK is complete.

<!-- end list -->
