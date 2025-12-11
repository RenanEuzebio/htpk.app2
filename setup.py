import os
import sys
import shutil
import tarfile
import zipfile
import subprocess
import urllib.request
from pathlib import Path

# --- CONFIGURATION ---
BASE_DIR = Path(__file__).parent.absolute()
LIB_DIR = BASE_DIR / "lib"
ANDROID_DIR = BASE_DIR / "android_source"
CACHE_DIR = BASE_DIR / "cache"
MAKE_SH_PATH = BASE_DIR / "make.sh"

# Tool Versions & URLs
JDK_URL = "https://download.java.net/java/GA/jdk17.0.2/dfd4a8d0985749f896bed50d7138ee7f/8/GPL/openjdk-17.0.2_linux-x64_bin.tar.gz"
GRADLE_URL = "https://services.gradle.org/distributions/gradle-7.4-bin.zip"
CMDLINE_TOOLS_URL = "https://dl.google.com/android/repository/commandlinetools-linux-9477386_latest.zip"

# Paths inside lib/
JAVA_HOME = LIB_DIR / "jvm" / "jdk-17.0.2"
GRADLE_HOME = LIB_DIR / "gradle" / "gradle-7.4"
ANDROID_HOME = LIB_DIR / "cmdline-tools"
CMDLINE_TOOLS_BIN = ANDROID_HOME / "latest" / "bin"

def log(msg):
    print(f"[SETUP] {msg}")

def download_file(url, dest_path):
    if dest_path.exists():
        return
    log(f"Downloading {url}...")
    try:
        urllib.request.urlretrieve(url, dest_path)
    except Exception as e:
        print(f"Error downloading {url}: {e}")
        sys.exit(1)

def extract_archive(file_path, extract_to):
    log(f"Extracting {file_path.name}...")
    if file_path.name.endswith(".tar.gz"):
        with tarfile.open(file_path, "r:gz") as tar:
            tar.extractall(path=extract_to)
    elif file_path.name.endswith(".zip"):
        with zipfile.ZipFile(file_path, "r") as zip_ref:
            zip_ref.extractall(path=extract_to)

def setup_java():
    if JAVA_HOME.exists():
        log("JDK 17 is already installed.")
        # Ensure executable permissions even if already installed
        java_bin = JAVA_HOME / "bin" / "java"
        if java_bin.exists():
            os.chmod(java_bin, 0o755)
        return

    jvm_dir = LIB_DIR / "jvm"
    jvm_dir.mkdir(parents=True, exist_ok=True)
    
    archive = jvm_dir / "jdk.tar.gz"
    download_file(JDK_URL, archive)
    extract_archive(archive, jvm_dir)
    archive.unlink() # Cleanup

    # FIX: Ensure Java is executable
    java_bin = JAVA_HOME / "bin" / "java"
    if java_bin.exists():
        os.chmod(java_bin, 0o755)

def setup_gradle():
    if GRADLE_HOME.exists():
        log("Gradle 7.4 is already installed.")
        # FIX: Ensure executable permissions even if already installed
        gradle_bin = GRADLE_HOME / "bin" / "gradle"
        if gradle_bin.exists():
            os.chmod(gradle_bin, 0o755)
        return

    gradle_dir = LIB_DIR / "gradle"
    gradle_dir.mkdir(parents=True, exist_ok=True)

    archive = gradle_dir / "gradle.zip"
    download_file(GRADLE_URL, archive)
    extract_archive(archive, gradle_dir)
    archive.unlink()

    # FIX: Explicitly make Gradle executable
    gradle_bin = GRADLE_HOME / "bin" / "gradle"
    if gradle_bin.exists():
        os.chmod(gradle_bin, 0o755)

def setup_android_sdk():
    target_dir = ANDROID_HOME / "latest"
    if target_dir.exists():
        log("Android Command Line Tools are already installed.")
    else:
        ANDROID_HOME.mkdir(parents=True, exist_ok=True)
        archive = ANDROID_HOME / "tools.zip"
        download_file(CMDLINE_TOOLS_URL, archive)
        extract_archive(archive, ANDROID_HOME)
        archive.unlink()

        # Rename extracted 'cmdline-tools' to 'latest'
        extracted_folder = ANDROID_HOME / "cmdline-tools"
        if extracted_folder.exists():
            extracted_folder.rename(target_dir)
    
    # Install Platforms and Build Tools
    # OPTIMIZATION: Included 'build-tools;30.0.3' to prevent Gradle from downloading it during build
    log("Checking/Installing Android SDK packages...")
    
    # Set up environment for sdkmanager
    env = os.environ.copy()
    env["JAVA_HOME"] = str(JAVA_HOME)
    env["PATH"] = f"{JAVA_HOME}/bin:{env['PATH']}"
    env["ANDROID_HOME"] = str(ANDROID_HOME)
    
    sdkmanager = CMDLINE_TOOLS_BIN / "sdkmanager"
    # Ensure binary is executable
    if sdkmanager.exists():
        os.chmod(sdkmanager, 0o755)

    # Accept licenses and install
    cmd = [
        str(sdkmanager),
        "platform-tools",
        "platforms;android-33",
        "build-tools;33.0.0",
        "build-tools;30.0.3", 
        f"--sdk_root={ANDROID_HOME}"
    ]
    
    # Pipe 'yes' to accept licenses
    yes_proc = subprocess.Popen(["yes"], stdout=subprocess.PIPE)
    subprocess.run(cmd, stdin=yes_proc.stdout, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    yes_proc.stdout.close() # Clean up

def generate_keystore():
    keystore_path = ANDROID_DIR / "app" / "my-release-key.jks"
    if keystore_path.exists():
        log("Keystore already exists.")
        return

    log("Generating signing keystore...")
    keytool = JAVA_HOME / "bin" / "keytool"
    if keytool.exists():
        os.chmod(keytool, 0o755)
    
    cmd = [
        str(keytool), "-genkey", "-v",
        "-keystore", str(keystore_path),
        "-keyalg", "RSA",
        "-keysize", "2048",
        "-validity", "10000",
        "-alias", "my",
        "-storepass", "123456",
        "-keypass", "123456",
        "-dname", "CN=Developer, OU=Organization, O=Company, L=City, S=State, C=US"
    ]
    
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)

def main():
    print("=== HTPK SETUP (Python Native) ===")
    
    # Ensure directories
    LIB_DIR.mkdir(exist_ok=True)
    CACHE_DIR.mkdir(exist_ok=True)
    
    # Make make.sh executable
    if MAKE_SH_PATH.exists():
        os.chmod(MAKE_SH_PATH, 0o755)

    # Run Setup Steps
    try:
        setup_java()
        setup_gradle()
        setup_android_sdk()
        setup_keystore = generate_keystore()
    except Exception as e:
        print(f"\n[ERROR] Setup failed: {e}")
        sys.exit(1)

    print("\n=== SETUP COMPLETE ===")
    print("Environment ready. You can now run 'python app.py'")

if __name__ == "__main__":
    main()
