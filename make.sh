#!/usr/bin/env bash
set -eu

# --- PATH CONFIGURATION ---
PROJECT_ROOT="${ANDROID_PROJECT_ROOT:-$PWD/android_source}"
DEPS_ROOT="${DEPENDENCIES_ROOT:-$PWD/lib}"
OUTPUT_DEST="${OUTPUT_DIR:-$PWD/output}"
CACHE_ROOT="${CACHE_DIR:-$PWD/cache}"

# --- ENV SETUP ---
# Defines where our local tools live (Must match setup.py)
export ANDROID_HOME="$DEPS_ROOT/cmdline-tools"
export JAVA_HOME="$DEPS_ROOT/jvm/jdk-17.0.2"
export GRADLE_HOME="$DEPS_ROOT/gradle/gradle-7.4"
export GRADLE_USER_HOME="$CACHE_ROOT/.gradle-cache"

# Add local tools to PATH
export PATH="$JAVA_HOME/bin:$GRADLE_HOME/bin:$PATH"

# Color definitions
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m'

log() { echo -e "${GREEN}[+]${NC} $1"; }
info() { echo -e "${BLUE}[*]${NC} $1"; }
error() { echo -e "${RED}[!]${NC} $1"; exit 1; }

# Navigate to project root
if [ ! -d "$PROJECT_ROOT" ]; then
     error "Android Source directory not found at: $PROJECT_ROOT"
else
    cd "$PROJECT_ROOT"
fi

try() {
    if ! "$@"; then
        error "Command failed: $*"
    fi
}

# --- BUILD COMMANDS ---

ensure_deps() {
    # Lightweight check to ensure setup.py was run
    [ -d "$ANDROID_HOME" ] || error "Android SDK not found. Please run 'python setup.py' first."
    [ -x "$GRADLE_HOME/bin/gradle" ] || error "Gradle not found. Please run 'python setup.py' first."
}

apk() {
    ensure_deps
    [ ! -f "app/my-release-key.jks" ] && error "Keystore not found. Run 'python setup.py'."
    
    rm -f app/build/outputs/apk/release/app-release.apk
    
    info "Building APK..."
    
    # Build using local Gradle and cache
    try gradle assembleRelease --quiet --project-cache-dir "$CACHE_ROOT/.gradle"
    
    if [ -f "app/build/outputs/apk/release/app-release.apk" ]; then
        log "APK Built Successfully!"
        cp "app/build/outputs/apk/release/app-release.apk" "$OUTPUT_DEST/$appname.apk"
        log "Saved to $OUTPUT_DEST/$appname.apk"
    else
        error "Build failed"
    fi
}

apply_config() {
    local config_file="${1:-webapk.conf}"
    [ ! -f "$config_file" ] && return
    
    export CONFIG_DIR="$(dirname "$config_file")"
    info "Applying config..."
    
    while IFS='=' read -r key value || [ -n "$key" ]; do
        [[ -z "$key" || "$key" =~ ^[[:space:]]*# ]] && continue
        key=$(echo "$key" | xargs)
        value=$(echo "$value" | xargs)
        
        case "$key" in
            "id") chid "$value" ;;
            "name") rename "$value" ;;
            "icon") set_icon "$value" ;;
            "scripts") ;; 
            *) set_var "$key = $value" ;;
        esac
    done < "$config_file"
}

set_var() {
    local java_file="app/src/main/java/com/$appname/htpk/MainActivity.java"
    if [ ! -f "$java_file" ]; then
        java_file=$(find app/src/main/java -name MainActivity.java | head -n 1)
    fi
    
    local pattern="$@"
    local var_name="${pattern%% =*}"
    local new_value="${pattern#*= }"

    if [ -z "$java_file" ] || [ ! -f "$java_file" ]; then return; fi
    if ! grep -q "$var_name *= *.*;" "$java_file"; then return; fi
    if [[ ! "$new_value" =~ ^(true|false)$ ]]; then new_value="\"$new_value\""; fi
    
    local tmp_file=$(mktemp)
    awk -v var="$var_name" -v val="$new_value" '
    {
        if (!found && $0 ~ var " *= *.*;" ) {
            match($0, "^.*" var " *=")
            before = substr($0, RSTART, RLENGTH)
            print before " " val ";"
            found = 1
        } else {
            print $0
        }
    }' "$java_file" > "$tmp_file"
    mv "$tmp_file" "$java_file"
}

clean() {
    info "Cleaning build files..."
    try rm -rf app/build
    try rm -rf "$CACHE_ROOT/.gradle"
}

chid() {
    [ -z "$1" ] && return
    [[ ! $1 =~ ^[a-zA-Z][a-zA-Z0-9_]*$ ]] && error "Invalid App ID"
    [ "$1" = "$appname" ] && return
    
    try find . -type f \( -name '*.gradle' -o -name '*.java' -o -name '*.xml' \) -exec sed -i "s/com\.\([a-zA-Z0-9_]*\)\.htpk/com.$1.htpk/g" {} +
    
    local current_dir=$(find app/src/main/java/com -maxdepth 1 -mindepth 1 -type d -not -name "$1" | head -n 1)
    if [ -n "$current_dir" ] && [ -d "$current_dir" ]; then
        if [ "$current_dir" != "app/src/main/java/com/$1" ]; then
            try mv "$current_dir" "app/src/main/java/com/$1"
        fi
    fi
    appname=$1
}

rename() {
    local new_name="$*"
    find app/src/main/res/values* -name "strings.xml" | while read xml_file; do
        escaped_name=$(echo "$new_name" | sed 's/[\/&]/\\&/g')
        try sed -i "s|<string name=\"app_name\">[^<]*</string>|<string name=\"app_name\">$escaped_name</string>|" "$xml_file"
    done
}

set_icon() {
    local icon_path="$@"
    local dest_file="app/src/main/res/mipmap/ic_launcher.png"
    [ -z "$icon_path" ] && return
    if [ -n "${CONFIG_DIR:-}" ] && [[ "$icon_path" != /* ]]; then icon_path="$CONFIG_DIR/$icon_path"; fi
    if [ -f "$icon_path" ]; then try cp "$icon_path" "$dest_file"; fi
}

appname=$(grep -Po '(?<=applicationId "com\.)[^.]*' app/build.gradle || echo "unknown")

if [ $# -eq 0 ]; then exit 1; fi
eval $@
