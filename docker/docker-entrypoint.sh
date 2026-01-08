#!/bin/bash
set -e

# PlexCache-R Docker Entrypoint
# Handles PUID/PGID user setup, config linking, and application startup

echo "----------------------------------------"
echo "  PlexCache-R Docker Container"
echo "----------------------------------------"

# Default values
PUID=${PUID:-99}
PGID=${PGID:-100}

echo "Starting with UID: $PUID, GID: $PGID"

# Handle PUID/PGID - create user/group if running as root
if [ "$(id -u)" = '0' ]; then
    # Check if GID already exists (common for system groups like 'users')
    EXISTING_GROUP=$(getent group ${PGID} | cut -d: -f1)

    if [ -n "${EXISTING_GROUP}" ]; then
        # GID exists, use the existing group name
        echo "Using existing group '${EXISTING_GROUP}' with GID ${PGID}"
        TARGET_GROUP="${EXISTING_GROUP}"
    else
        # Create new group with our name
        groupadd -g ${PGID} plexcache 2>/dev/null || true
        TARGET_GROUP="plexcache"
    fi

    # Create user if it doesn't exist
    if ! getent passwd plexcache > /dev/null 2>&1; then
        useradd -u ${PUID} -g ${TARGET_GROUP} -d /app -s /bin/bash plexcache 2>/dev/null || true
    fi

    # Ensure user has correct UID (in case it already existed)
    if [ "$(id -u plexcache 2>/dev/null)" != "${PUID}" ]; then
        usermod -u ${PUID} plexcache 2>/dev/null || true
    fi

    # Ensure config directory structure exists
    mkdir -p /config/data /config/logs /config/import

    # Set up symlinks for config and data persistence
    # Settings file
    if [ ! -L "/app/plexcache_settings.json" ]; then
        # If settings exist in app but not in config, move them
        if [ -f "/app/plexcache_settings.json" ] && [ ! -f "/config/plexcache_settings.json" ]; then
            mv /app/plexcache_settings.json /config/plexcache_settings.json
        fi
        rm -f /app/plexcache_settings.json 2>/dev/null || true
        ln -sf /config/plexcache_settings.json /app/plexcache_settings.json
    fi

    # Data directory (timestamps, trackers, etc.)
    if [ ! -L "/app/data" ]; then
        # If data exists in app but not in config, move it
        if [ -d "/app/data" ] && [ ! -d "/config/data" ]; then
            cp -r /app/data/* /config/data/ 2>/dev/null || true
        fi
        rm -rf /app/data 2>/dev/null || true
        ln -sf /config/data /app/data
    fi

    # Logs directory
    if [ ! -L "/app/logs" ]; then
        rm -rf /app/logs 2>/dev/null || true
        ln -sf /config/logs /app/logs
    fi

    # Mover exclude files (written to /config for user access)
    # Primary exclude list (managed by PlexCache)
    if [ ! -L "/app/plexcache_mover_files_to_exclude.txt" ]; then
        rm -f /app/plexcache_mover_files_to_exclude.txt 2>/dev/null || true
        ln -sf /config/plexcache_mover_files_to_exclude.txt /app/plexcache_mover_files_to_exclude.txt
    fi

    # Combined exclusions file (for advanced users)
    if [ ! -L "/app/unraid_mover_exclusions.txt" ]; then
        rm -f /app/unraid_mover_exclusions.txt 2>/dev/null || true
        ln -sf /config/unraid_mover_exclusions.txt /app/unraid_mover_exclusions.txt
    fi

    # Fix ownership of config and app directories
    chown -R plexcache:${TARGET_GROUP} /config
    chown -R plexcache:${TARGET_GROUP} /app

    echo "Configuration directory: /config"
    echo "Data directory: /config/data"
    echo "Logs directory: /config/logs"
    echo "Import directory: /config/import"
    echo "Mover exclude file: /config/plexcache_mover_files_to_exclude.txt"
    echo ""

    # Drop to plexcache user and re-exec
    exec gosu plexcache "$0" "$@"
fi

# Set timezone
if [ -n "${TZ}" ]; then
    export TZ
    echo "Timezone: ${TZ}"
fi

# Display configuration
echo ""
echo "Configuration:"
echo "  Web Port: ${WEB_PORT:-5757}"
echo "  Log Level: ${LOG_LEVEL:-INFO}"
echo ""

# Check if config file exists
if [ -f "/config/plexcache_settings.json" ]; then
    echo "Config file: Found"
else
    echo "Config file: Not found (configure via Web UI)"
fi

echo ""
echo "Starting PlexCache-R Web UI..."
echo "----------------------------------------"

# Start the web application
# --host 0.0.0.0 is required for Docker to expose the port
exec python3 /app/plexcache_web.py --host 0.0.0.0 --port ${WEB_PORT:-5757}
