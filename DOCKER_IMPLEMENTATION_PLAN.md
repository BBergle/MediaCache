# PlexCache-R Docker Implementation Plan

## Overview

This document outlines the comprehensive plan for containerizing PlexCache-R for deployment on Unraid, with the goal of eventual inclusion in Unraid Community Apps.

### Confirmed Decisions

| Decision | Choice |
|----------|--------|
| Docker Hub | `brandonhaney/plexcache-r` |
| Image Name | `plexcache-r` |
| Default Port | `5757` |
| Base Image | `python:3.11-slim` |
| ARM64 Support | Deferred (amd64 only initially) |
| Mover Exclude List | Write to `/config/`, user configures CA Mover plugin to read from there |
| Schedule Default | Every 4 hours (`0 */4 * * *`) |
| First Run | Setup wizard for library/user selection |
| CA Mover Plugin | Optional (not required) |

### Goals

1. **Seamless Unraid Integration** - Work naturally with Unraid's filesystem, mover, and Docker ecosystem
2. **User-Friendly Setup** - Minimal configuration required, sensible defaults
3. **Feature Parity** - All CLI and Web UI features available in container
4. **Community Apps Ready** - Meet all requirements for CA submission
5. **Maintainability** - Easy to update and debug

---

## Architecture Decision: Unified Container

We will use a **single unified container** that provides:

- Web UI (primary interface) on configurable port
- Internal scheduler for automated runs
- CLI access via `docker exec` for manual operations
- Health checks for container monitoring

```
┌─────────────────────────────────────────────────────────┐
│                  PlexCache-R Container                   │
├─────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │
│  │   Web UI    │  │  Scheduler  │  │    CLI Tools    │  │
│  │  (FastAPI)  │  │   (APScheduler)   │  (plexcache.py) │  │
│  │  Port 5000  │  │  Cron-like  │  │  docker exec    │  │
│  └─────────────┘  └─────────────┘  └─────────────────┘  │
├─────────────────────────────────────────────────────────┤
│                    Shared Core Modules                   │
│         (config, plex_api, file_operations, etc.)        │
└─────────────────────────────────────────────────────────┘
          │              │              │
          ▼              ▼              ▼
    /mnt/cache     /mnt/user0      /config
```

---

## Filesystem & Volume Mounts

### Required Mounts

| Container Path | Host Path | Purpose | Mode |
|----------------|-----------|---------|------|
| `/mnt/cache` | `/mnt/cache` | Cache drive access | rw |
| `/mnt/user0` | `/mnt/user0` | Array-only access | rw |
| `/mnt/user` | `/mnt/user` | Merged view (for Plex path compatibility) | ro |
| `/config` | User choice (e.g., `/mnt/user/appdata/plexcache`) | Persistent config & data | rw |

### Path Consistency Requirement

**Critical**: Container paths MUST match host paths for `/mnt/*` mounts.

Plex reports paths like `/mnt/user/Media/Movies/...`. Our path conversion logic expects these exact paths. Remapping (e.g., `/mnt/cache:/data`) would break path resolution.

### Mover Exclude List

PlexCache-R writes `plexcache_mover_files_to_exclude.txt` to prevent the Unraid mover from moving cached files back to the array.

**Approach**: Container writes the exclude file to `/config/` (mapped to appdata folder). Users configure CA Mover Tuning plugin to read from this location.

```
Container: /config/plexcache_mover_files_to_exclude.txt
   ↓ (volume mount)
Host: /mnt/user/appdata/plexcache/plexcache_mover_files_to_exclude.txt
   ↓ (user configures in CA Mover Tuning)
Mover: Reads exclude list from user-specified path
```

**User Setup** (documented in template):
1. Install CA Mover Tuning plugin (if not already installed)
2. Go to Settings → Mover Tuning
3. Set "File exclusion path" to `/mnt/user/appdata/plexcache/plexcache_mover_files_to_exclude.txt`

**Benefits**:
- No `/boot` mount required
- Matches current non-Docker behavior
- User has full control
- CA Mover plugin is optional (users without it just skip this step)

---

## Configuration Strategy

### Simplified Approach: Web UI Configuration

After implementation review, we simplified to use **Web UI for all app configuration** with only Docker-specific settings as environment variables.

**Environment Variables** (Docker-specific only):
- `TZ` - Timezone (default: `America/Los_Angeles`)
- `PUID` / `PGID` - User/Group IDs (default: `99`/`100` for Unraid)
- `WEB_PORT` - Web UI port (default: `5757`)
- `LOG_LEVEL` - Logging verbosity (default: `INFO`)

**Config File** (`/config/plexcache_settings.json`):
- All application settings configured via Web UI:
  - Plex URL and Token
  - Libraries and Users
  - Schedule settings (cron, enabled/disabled)
  - Retention periods, thread counts, etc.
- Generated on first run via Web UI setup
- Editable via Web UI Settings page

### Why This Approach?

1. **Single source of truth** - All settings in one place (config file)
2. **No duplication** - No need to sync env vars with config file
3. **Better UX** - Web UI provides validation, dropdowns, and guided setup
4. **Simpler Docker setup** - Fewer env vars to configure

### First-Run Behavior

```
IF config file doesn't exist:
    Start Web UI
    User configures via Settings page:
        - Plex URL and Token
        - Select Libraries
        - Select Users
        - Configure Schedule
    Settings saved to config file
```

---

## Scheduling Implementation

### Internal Scheduler (APScheduler)

The Web UI includes a built-in scheduler (already implemented in `web/services/scheduler_service.py`).

Schedule is configured via the Web UI Settings page and saved to `plexcache_settings.json`.

### Scheduler Features

- **Configurable via Web UI** - Enable/disable, interval or cron mode
- **Run Now** button in Web UI triggers immediate execution
- **Next Run** display in Dashboard
- **Run History** tracking (Recent Activity)
- **Prevent Overlapping** - Skip if previous run still in progress
- **Graceful Shutdown** - Complete current operation on container stop

### Manual Execution Options

```bash
# Via docker exec (CLI mode)
docker exec plexcache python3 plexcache.py --dry-run
docker exec plexcache python3 plexcache.py --verbose

# Via Web UI
# "Run Now" button triggers immediate execution
```

---

## Web UI Enhancements for Docker

### New Features for Container Mode

1. **Setup Wizard** (first-run)
   - Plex connection configuration
   - Library selection
   - User selection
   - Basic settings

2. **Scheduler Management**
   - Enable/disable scheduler
   - Configure cron schedule (with presets)
   - View next scheduled run
   - Run history with status

3. **Container Info Panel**
   - Container uptime
   - Resource usage (if available)
   - Version info
   - Update check (optional)

4. **Health Dashboard Enhancements**
   - Scheduler status
   - Last run result (success/failure/warnings)
   - Plex connection status (real-time)

### API Endpoints for Docker

```
POST /api/run          - Trigger immediate run
GET  /api/schedule     - Get schedule info
POST /api/schedule     - Update schedule
GET  /api/health       - Health check endpoint (for Docker)
GET  /api/status       - Detailed status for monitoring
```

---

## Dockerfile

See `docker/Dockerfile` for the actual implementation. Key points:

- Base image: `python:3.11-slim`
- Uses `tini` as init system and `gosu` for user switching
- Environment variables: `PUID`, `PGID`, `TZ`, `WEB_PORT`, `LOG_LEVEL`
- Health check via `/api/health` endpoint
- Entrypoint handles user creation and config symlinks

### docker-entrypoint.sh

See `docker/docker-entrypoint.sh` for the actual implementation. Key points:

- Creates user/group based on `PUID`/`PGID`
- Sets up symlinks: `/app/plexcache_settings.json` → `/config/plexcache_settings.json`
- Sets up symlinks: `/app/data` → `/config/data`, `/app/logs` → `/config/logs`
- Drops privileges and runs `plexcache_web.py --host 0.0.0.0 --port ${WEB_PORT}`

### requirements-docker.txt

**Not needed** - `apscheduler` is already in the main `requirements.txt`.

---

## Docker Compose

See `docker/docker-compose.yml` for the production configuration.

```yaml
version: "3.8"

services:
  plexcache:
    image: brandonhaney/plexcache-r:latest
    container_name: plexcache-r
    restart: unless-stopped

    environment:
      - PUID=99
      - PGID=100
      - TZ=America/Los_Angeles
      - LOG_LEVEL=INFO
      # Note: Plex and Schedule settings configured via Web UI

    volumes:
      - /mnt/user/appdata/plexcache:/config
      - /mnt/cache:/mnt/cache
      - /mnt/user0:/mnt/user0
      - /mnt/user:/mnt/user:ro

    ports:
      - 5757:5757
```

Also see `docker/docker-compose.dev.yml` for local development builds.

---

## Unraid Template XML

See `docker/plexcache-r.xml` for the actual template. Key configuration:

**Ports:**
- Web UI Port: `5757` (configurable)

**Paths:**
- `/config` → `/mnt/user/appdata/plexcache` (config, data, logs, mover exclude file)
- `/mnt/cache` → `/mnt/cache` (cache drive, must match exactly)
- `/mnt/user0` → `/mnt/user0` (array-only view, must match exactly)
- `/mnt/user` → `/mnt/user` (read-only, for Plex path compatibility)

**Environment Variables:**
- `PUID` / `PGID` - File permissions (default: 99/100)
- `TZ` - Timezone
- `LOG_LEVEL` - Logging verbosity

**Note:** Plex URL/Token and Schedule settings are configured via Web UI, not environment variables.

---

## Community Apps Submission Requirements

### Prerequisites

1. **Docker Hub Repository**
   - Image: `brandonhaney/plexcache-r`
   - Tags: `latest`, semantic versions (`v3.0.0`, `v3.0.1`, etc.)
   - Architecture: `linux/amd64` (ARM64 deferred)

2. **GitHub Repository**
   - Public repository
   - README with clear documentation
   - LICENSE file (already have MIT)
   - Releases with changelogs

3. **Template Repository**
   - Fork `Squidly271/community.applications`
   - Add template XML to appropriate folder
   - Submit PR

### Template Checklist

- [ ] Valid XML structure
- [ ] All required fields populated
- [ ] WebUI URL correct
- [ ] Icon URL accessible (PNG, 512x512 recommended)
- [ ] Support/Project URLs valid
- [ ] Category appropriate
- [ ] Description clear and informative
- [ ] Default values sensible
- [ ] Required vs optional fields marked correctly

### Testing Before Submission

1. Install via "Add Container" with template XML URL
2. Verify all paths mount correctly
3. Test first-run setup wizard
4. Verify scheduled operations work
5. Test Web UI accessibility
6. Verify mover exclude list integration
7. Test container restart persistence
8. Verify health checks work

---

## Implementation Phases

### Phase 1: Core Docker Support ✅ COMPLETE

**Status:** Fully complete and tested

**Tasks:**
- [x] Create `Dockerfile`
- [x] Create `docker-entrypoint.sh`
- [x] ~~Create `requirements-docker.txt`~~ (not needed - apscheduler already in requirements.txt)
- [x] ~~Create `plexcache_docker.py`~~ (not needed - use `plexcache_web.py` directly)
- [x] ~~Implement environment variable configuration loading~~ (simplified - Web UI handles config)
- [x] ~~Add config file generation from env vars~~ (simplified - Web UI handles config)
- [x] APScheduler integration (already exists in web/services/scheduler_service.py)
- [x] Add `/api/health` endpoint
- [x] Add `/api/status` endpoint (detailed status)
- [x] Add `/api/run` endpoint for manual triggers
- [x] Create `docker-compose.yml`
- [x] Create `docker-compose.dev.yml` (for local development)
- [x] Create `.dockerignore`
- [x] Create `build.sh` helper script
- [x] Create Unraid template XML (`plexcache-r.xml`)
- [x] Test basic container operation

**Files Created:**
- `docker/Dockerfile`
- `docker/docker-entrypoint.sh`
- `docker/docker-compose.yml`
- `docker/docker-compose.dev.yml`
- `docker/plexcache-r.xml`
- `docker/build.sh`
- `.dockerignore`

**Files Modified:**
- `web/routers/api.py` (added /api/health, /api/status, /api/run)

### Phase 2: Web UI Enhancements ✅ COMPLETE

**Status:** Complete - First-run setup wizard implemented

**Tasks:**
- [x] Add scheduler status to Dashboard (already exists)
- [x] Add "Run Now" button (already exists in Operations page)
- [x] Add schedule configuration UI (already exists in Settings page)
- [x] Add run history display (already exists - Recent Activity)
- [x] Add first-run setup wizard (6-step guided setup)
- [ ] Add container info panel (optional - deferred)

**Setup Wizard Steps:**
1. Welcome - Introduction to PlexCache-R
2. Plex Connection - URL/Token with OAuth support
3. Libraries & Paths - Library selection and path mappings
4. Users - Select which users to monitor
5. Behavior - OnDeck/Watchlist/retention settings
6. Complete - Summary and launch

**Files Created:**
- `web/routers/setup.py` - Setup wizard routes and OAuth handlers
- `web/templates/setup/base_setup.html` - Wizard base template
- `web/templates/setup/step1.html` through `step6.html` - Step templates

**Files Modified:**
- `web/main.py` - Added setup router and redirect middleware

### Phase 3: Unraid Integration ✅ COMPLETE

**Status:** Complete - Tested on Unraid, setup wizard fully functional

**Tasks:**
- [x] Create Unraid template XML
- [x] Test mover exclude list integration (code verified, symlinks configured)
- [x] Create container icon (512x512 PNG)
- [x] Write Unraid-specific documentation
- [x] Test on actual Unraid system
- [x] Fix line endings issue (CRLF → LF for entrypoint)
- [x] Add WebUI URL to template (`http://[IP]:[PORT:5757]`)

**Files Created:**
- `docker/icon.svg` - Container icon source (vector)
- `docker/icon.png` - Container icon (512x512 PNG for Unraid template)
- `docker/UNRAID_SETUP.md` - Comprehensive Unraid setup guide

**Files Modified:**
- `docker/docker-entrypoint.sh` - Added symlinks for both exclude files
- `docker/plexcache-r.xml` - Updated icon URL, WebUI format

**Mover Exclude Integration:**
The entrypoint creates symlinks so the app writes exclude files to `/config/`:
- `/app/plexcache_mover_files_to_exclude.txt` → `/config/plexcache_mover_files_to_exclude.txt`
- `/app/unraid_mover_exclusions.txt` → `/config/unraid_mover_exclusions.txt`

Users configure CA Mover Tuning to read from the mapped host path.

**Setup Wizard Improvements (tested on Unraid):**
- Plex-themed styling (dark gradient, gold accents matching plex-theme.css)
- OAuth server discovery - auto-detects Plex server URL after OAuth login
- Background user prefetch - speeds up Step 4 by fetching users in background
- Select All/Deselect All button for user selection
- RSS URL info box for remote user watchlist support
- Cacheable checkbox per library with explanation
- In-memory state until completion - no partial config if wizard abandoned
- Path Mappings section marked as "(Advanced)" with clearer help text

### Phase 4: Polish & Release

**Tasks:**
- [ ] Multi-architecture builds (amd64 + arm64)
- [ ] Automated Docker Hub publishing (GitHub Actions)
- [ ] Version tagging strategy
- [ ] Comprehensive testing
- [ ] User documentation
- [ ] Community Apps PR submission

---

## Migration Guide for Existing Users

### From User Scripts to Docker

1. **Backup Current Configuration**
   ```bash
   mkdir -p /mnt/user/appdata/plexcache/data
   cp /path/to/plexcache_settings.json /mnt/user/appdata/plexcache/
   cp -r /path/to/data/* /mnt/user/appdata/plexcache/data/
   ```

2. **Install Docker Container**
   - Add container via Unraid Docker tab or Community Apps
   - Configure volume mounts (paths must match host paths exactly)
   - Start container

3. **Configure via Web UI**
   - Open Web UI at `http://[IP]:5757`
   - Verify Plex connection in Settings
   - Enable scheduler in Settings (replaces User Scripts cron)

4. **Disable User Script**
   - Remove or disable the User Scripts schedule
   - Container scheduler handles this now

5. **Verify Operation**
   - Check Dashboard shows Plex connected
   - Run a dry-run test via Operations page
   - Verify scheduler shows next run time

### Data Compatibility

- `plexcache_settings.json` - Fully compatible, no changes needed
- `data/*.json` files - Fully compatible
- `logs/` - Fresh start in container (old logs preserved on host)

---

## Security Considerations

### Container Permissions

- Runs as non-root user (`plexcache`, PUID/PGID configurable)
- Only requires access to media paths and config
- No privileged mode required
- Network access needed only for Plex API

### Sensitive Data

- Plex token stored in config file (not logged)
- Environment variable masking in Unraid UI
- No external telemetry or data collection

### File System Access

- Cache path (`/mnt/cache`): Read/Write (required for file operations)
- Array path (`/mnt/user0`): Read/Write (required for file operations)
- User share (`/mnt/user`): Read-only (for Plex path resolution)
- Config (`/config`): Read/Write (settings, data, exclude list)
- No `/boot` access required

---

## Remaining Considerations

1. **Update Mechanism**: Include Watchtower-compatible labels? Auto-update notifications in Web UI?

2. **Resource Limits**: Suggest default memory/CPU limits in template?

3. ~~**Container Icon**: Need to create a 512x512 PNG icon for the Unraid template~~ ✅ DONE

4. ~~**First-Run Setup Wizard**: Design needed for the Web UI setup flow~~ ✅ DONE

---

## References

- [Unraid Docker Documentation](https://docs.unraid.net/unraid-os/manual/docker-management/)
- [Community Applications Submission Guide](https://forums.unraid.net/topic/38582-plug-in-community-applications/)
- [Docker Best Practices](https://docs.docker.com/develop/develop-images/dockerfile_best-practices/)
- [APScheduler Documentation](https://apscheduler.readthedocs.io/)

---

## BUG FIX: Docker Host Path Translation for Exclude File ✅ COMPLETE

### Problem Statement

**Discovered:** 2026-01-08

When PlexCache runs in Docker with volume mounts that remap paths:

- **Docker container sees:** `/mnt/cache/Movies/...`
- **Unraid host has:** `/mnt/cache_downloads/Movies/...`

PlexCache writes container paths to the exclude file, but the Unraid mover runs on the HOST and can't match the paths. Result: files get moved despite being "excluded".

### Evidence

```bash
# Docker volume mount (host:container)
/mnt/cache_downloads:/mnt/cache

# PlexCache logs (container view)
Added to exclude file: /mnt/cache/Movies/A Merry Little Ex-Mas (2025)/...

# Unraid mover (host view) - CAN'T MATCH!
Skip file list: .../unraid_mover_exclusions.txt
# Looks for /mnt/cache/... but host has /mnt/cache_downloads/...
```

### User Impact

- Files moved off cache unexpectedly
- Stale entries in timestamps.json and exclude file
- Maintenance page shows files as "stale"

---

### Solution: Per-Path-Mapping Host Cache Path

#### Why Per-Mapping Instead of Global?

1. **Multiple cache pools** - Unraid is moving toward multiple cache pools/arrays
2. **Different libraries, different caches** - 4K content on NVMe, regular on SSD
3. **Future-proof** - Each mapping can have independent host path
4. **Setup wizard friendly** - Configure during path setup

#### Data Model Change

**Add `host_cache_path` to PathMapping:**

```python
@dataclass
class PathMapping:
    name: str = ""
    plex_path: str = ""
    real_path: str = ""
    cache_path: Optional[str] = None      # Container view (what Docker sees)
    host_cache_path: Optional[str] = None # Host view (what Unraid mover sees) - NEW
    cacheable: bool = True
    enabled: bool = True
```

#### Settings JSON Example

```json
{
  "path_mappings": [
    {
      "name": "TV Shows",
      "plex_path": "/data/TV Shows/",
      "real_path": "/mnt/user/TV Shows/",
      "cache_path": "/mnt/cache/TV Shows/",
      "host_cache_path": "/mnt/cache_downloads/TV Shows/",
      "cacheable": true,
      "enabled": true
    },
    {
      "name": "Movies 4K",
      "plex_path": "/nas/Movies UHD/",
      "real_path": "/mnt/user/Movies UHD/",
      "cache_path": "/mnt/cache_nvme/Movies UHD/",
      "host_cache_path": "/mnt/cache_nvme/Movies UHD/",
      "cacheable": true,
      "enabled": true
    }
  ]
}
```

---

### Implementation Tasks

#### Phase 1: Core Changes ✅

**1.1 Update PathMapping Dataclass**
- File: `core/config.py`
- Add `host_cache_path: Optional[str] = None` field

**1.2 Update Config Loading**
- File: `core/config.py` - `_load_path_config()`
- Load `host_cache_path` from settings JSON

**1.3 Update Config Saving**
- File: `core/config.py` - `save_settings()`
- Include `host_cache_path` when saving path mappings

**1.4 Update FileMover Translation Logic**
- File: `core/file_operations.py` - `FileMover` class
- Add `_translate_path_for_exclude()` method
- Pass path_mappings to FileMover

```python
def _translate_path_for_exclude(self, path: str) -> str:
    """Translate container cache path to host cache path using path mappings."""
    for mapping in self.path_mappings:
        if not mapping.cache_path or not mapping.host_cache_path:
            continue
        if mapping.cache_path == mapping.host_cache_path:
            continue  # No translation needed

        cache_prefix = mapping.cache_path.rstrip('/')
        if path.startswith(cache_prefix):
            host_prefix = mapping.host_cache_path.rstrip('/')
            translated = path.replace(cache_prefix, host_prefix, 1)
            logging.debug(f"Exclude path translation: {path} -> {translated}")
            return translated
    return path
```

**1.5 Update app.py**
- Pass `path_mappings` to FileMover constructor

#### Phase 2: Setup Wizard Changes ✅

**2.1 Update Step 3 (Libraries & Paths)**
- File: `web/templates/setup/step3_paths.html`
- Add `host_cache_path` input field (shown when Docker detected)
- Pre-fill with `cache_path` value as starting point

```
+----------------------------------------------------------+
| Library: Movies                                          |
+----------------------------------------------------------+
| Plex Path:       /data/Movies/          (from Plex)      |
| Real Path:       /mnt/user/Movies/      [__________]     |
| Cache Path:      /mnt/cache/Movies/     [__________]     |
|                                                          |
| [Docker Detected] Host Cache Path:                       |
| /mnt/cache_downloads/Movies/            [__________]     |
|                                                          |
| (i) The Unraid mover sees different paths than Docker.   |
|     Enter the actual host path to your cache pool.       |
+----------------------------------------------------------+
```

**2.2 Update Setup Router**
- File: `web/routers/setup.py`
- Pass `is_docker` flag to template
- Handle `host_cache_path` in form submission

**2.3 Update Setup Service**
- Include `host_cache_path` when building path mappings
- Default to `cache_path` if not provided (native installs)

#### Phase 3: Settings Page Changes ✅

**3.1 Update Paths Settings Tab**
- File: `web/templates/settings/paths.html`
- Add `host_cache_path` column (visible in Docker mode)

**3.2 Update Settings Router**
- Handle `host_cache_path` when saving

#### Phase 4: Logging & Testing ✅

**4.1 Verbose Logging**
```
DEBUG - Loaded path mapping: Movies (/data/Movies/ -> /mnt/user/Movies/)
DEBUG -   Cache: /mnt/cache/Movies/ -> Host: /mnt/cache_downloads/Movies/
...
DEBUG - Exclude path translation: /mnt/cache/Movies/Film.mkv -> /mnt/cache_downloads/Movies/Film.mkv
```

**4.2 Startup Validation**
```
INFO - Docker detected: exclude file will use host paths
INFO -   /mnt/cache/ -> /mnt/cache_downloads/
```

---

### Implementation Status: COMPLETE

The per-mapping `host_cache_path` feature has been implemented.

**Files Modified:**
- `core/config.py` - Added `host_cache_path` field to `PathMapping` dataclass, updated config loading/saving
- `core/file_operations.py` - Added `_translate_path_for_exclude()` method to `FileMover`, updated `_add_to_exclude_file()`, `_remove_from_exclude_file()`, and `_cleanup_stale_exclude_entries()` to use path translation
- `web/templates/setup/step3.html` - Added `host_cache_path` input field (shown when Docker detected)
- `web/routers/setup.py` - Handle `host_cache_path` in Step 3 form submission
- `web/templates/settings/paths.html` - Added `host_cache_path` field in "Add New Path Mapping" form
- `web/templates/settings/partials/path_mapping_card.html` - Added `host_cache_path` display and edit fields
- `web/routers/settings.py` - Handle `host_cache_path` in add/update/delete path mapping routes

---

### Migration Notes

**Existing Docker Users:**
1. Update to new version
2. Go to Settings > Paths (or re-run setup wizard)
3. Fill in `host_cache_path` for each mapping
4. Run PlexCache to regenerate exclude file with correct paths

**Native Installs:**
- No action needed
- `host_cache_path` defaults to same as `cache_path`

---

### Open Questions (Resolved)

1. **Auto-detection?** - No, user should configure explicitly. Auto-detection is risky and error-prone.
2. **Required field?** - No, defaults to `cache_path` if not provided. Only needed when Docker remaps cache paths.
3. **Maintenance page?** - Currently uses container paths for file existence checks, which works since the container can see the files. The exclude file is the only place that needs host paths (for Unraid mover).

---

## BUG FIX: Stop Button Not Working ✅ COMPLETE

### Problem Statement

**Discovered:** 2026-01-08

The Stop button on the Dashboard would:
1. Not appear for scheduled operations (only for manual "Run Now")
2. Cause the UI to freeze when clicked (deadlock)
3. Not actually stop the file moving operation

### Root Causes

**Issue 1: Deadlock in stop_operation()**
- `stop_operation()` acquired `self._lock` and then called `_add_log_message()`
- `_add_log_message()` also tries to acquire `self._lock`
- Python's `threading.Lock` is not reentrant → deadlock

**Issue 2: Dashboard didn't show Stop for scheduled runs**
- On page load with operation running, dashboard showed simple "Operation in progress..." message
- The full `operation_status.html` component (with Stop button) was only loaded when clicking "Run Now"

**Issue 3: FileMover didn't check stop flag**
- `FileMover._execute_move_commands()` used `executor.map()` which submits all tasks immediately
- No mechanism to check stop flag between file moves

### Solution

**Fix 1: Deadlock** (`web/services/operation_runner.py`)
- Move `_add_log_message()` and `app.request_stop()` calls outside the lock block
- Store app reference inside lock, signal outside

**Fix 2: Dashboard** (`web/templates/dashboard.html`)
- Add `hx-get="/operations/status"` with `hx-trigger="load"` when operation is running
- Dashboard now polls for full status component on load

**Fix 3: FileMover stop check** (`core/file_operations.py`, `core/app.py`)
- Added `stop_check` callback parameter to `FileMover.__init__()`
- Changed `_execute_move_commands()` to use `executor.submit()` with stop check between each file
- `PlexCacheApp` passes `lambda: self.should_stop` as the callback

### Files Modified

- `web/services/operation_runner.py` - Fixed deadlock in `stop_operation()`
- `web/templates/dashboard.html` - Load operation status component for scheduled runs
- `core/file_operations.py` - Added `stop_check` callback and stop logic in `_execute_move_commands()`
- `core/app.py` - Pass `stop_check` callback to `FileMover`

### Testing

Verified working:
- Ad-hoc run: Stop button appears, logs "Stop requested", operation stops after current phase
- Scheduled run: Stop button now appears (pending full test)
