# PlexCache Refactoring Summary

## Overview

The original PlexCache script has been completely refactored to improve maintainability, testability, and code organization while preserving all original functionality.

This refacting work was done by BBergle and I couldn't have progressed this project without his work - https://github.com/BBergle/PlexCache

The below is from his documentation, and covers the things that are still relevent in my own updates on this project. 

### Refactored Solution

The code has been split into 6 focused modules:

#### 1. `config.py` - Configuration Management
- **Purpose**: Handle all configuration loading, validation, and management
- **Key Features**:
  - Dataclasses for type-safe configuration
  - Validation of required fields
  - Path conversion utilities
  - Automatic cleanup of deprecated settings

#### 2. `logging_config.py` - Logging System
- **Purpose**: Set up logging, rotation, and notification handlers
- **Key Features**:
  - Rotating file handlers
  - Custom notification handlers (Unraid, Webhook)
  - Summary logging functionality
  - Proper log level management

#### 3. `system_utils.py` - System Operations
- **Purpose**: OS detection, path conversions, and file utilities
- **Key Features**:
  - System detection (Linux, Unraid, Docker)
  - Cross-platform path conversions
  - File operation utilities
  - Space calculation functions

#### 4. `plex_api.py` - Plex Integration
- **Purpose**: All Plex server interactions and cache management
- **Key Features**:
  - Plex server connections
  - Media fetching (onDeck, watchlist, watched)
  - Cache management
  - Rate limiting and retry logic

#### 5. `file_operations.py` - File Operations
- **Purpose**: File moving, filtering, and subtitle operations
- **Key Features**:
  - Path modification utilities
  - Subtitle discovery
  - File filtering logic
  - Concurrent file moving

#### 6. `plexcache_app.py` - Main Application
- **Purpose**: Orchestrate all components and provide main business logic
- **Key Features**:
  - Dependency injection
  - Error handling
  - Application flow control
  - Summary generation
