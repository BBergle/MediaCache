# PlexCache - Refactored Version

A refactored version of the PlexCache script with improved architecture, maintainability, and testability.

## Overview

This refactored version maintains all the original functionality while providing:

- **Modular Architecture**: Code is organized into logical modules with clear separation of concerns
- **Better Error Handling**: Proper exception handling without using `exit()` calls
- **Type Hints**: Full type annotations for better code documentation and IDE support
- **Testability**: Components can be easily unit tested
- **Configuration Management**: Centralized configuration handling
- **Logging**: Improved logging with proper handlers and rotation

## Architecture

The refactored code is organized into the following modules:

### Core Modules

- **`config.py`**: Configuration management with dataclasses for type safety
- **`logging_config.py`**: Logging setup, rotation, and notification handlers
- **`system_utils.py`**: OS detection, path conversions, and file utilities
- **`plex_api.py`**: Plex server interactions and cache management
- **`file_operations.py`**: File moving, filtering, and subtitle operations
- **`plexcache_app.py`**: Main application orchestrator

### Key Improvements

1. **Separation of Concerns**: Each module has a single responsibility
2. **Dependency Injection**: Components are injected rather than created internally
3. **Error Handling**: Proper exception handling with meaningful error messages
4. **Type Safety**: Full type annotations throughout the codebase
5. **Testability**: Each component can be tested in isolation
6. **Configuration**: Centralized configuration with validation

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Run the setup script to configure PlexCache:
```bash
python plexcache_setup.py
```

3. Run the main application:
```bash
python plexcache_app.py
```

## Usage

### Command Line Options

- `--debug`: Run in debug mode (no files will be moved)
- `--skip-cache`: Skip using cached data and fetch fresh from Plex

### Examples

```bash
# Normal run
python plexcache_app.py

# Debug mode
python plexcache_app.py --debug

# Skip cache
python plexcache_app.py --skip-cache

# Both options
python plexcache_app.py --debug --skip-cache
```

## Configuration

The configuration is stored in `plexcache_settings.json` and includes:

- **Plex Settings**: Server URL, token, library sections
- **Media Settings**: Number of episodes, days to monitor, user preferences
- **Path Settings**: Source and cache directories
- **Performance Settings**: Concurrent operations, retry limits
- **Notification Settings**: Webhook URLs, notification levels

## Testing

The modular architecture makes it easy to test individual components:

```python
# Example test for FilePathModifier
from file_operations import FilePathModifier

modifier = FilePathModifier(
    plex_source="/media/",
    real_source="/mnt/user/",
    plex_library_folders=["movies"],
    nas_library_folders=["movies"]
)

result = modifier.modify_file_paths(["/media/movies/test.mkv"])
assert result == ["/mnt/user/movies/test.mkv"]
```

## Migration from Original

The refactored version maintains full compatibility with the original:

1. **Same Configuration**: Uses the same `plexcache_settings.json` format
2. **Same Functionality**: All original features are preserved
3. **Same Output**: Logging and notifications work identically
4. **Same Performance**: No performance degradation

## Benefits of Refactoring

### For Developers

- **Easier Maintenance**: Clear module boundaries and responsibilities
- **Better Debugging**: Proper error handling and logging
- **Type Safety**: IDE support and compile-time error detection
- **Testing**: Unit tests can be written for each component

### For Users

- **Reliability**: Better error handling and recovery
- **Maintainability**: Easier to fix bugs and add features
- **Documentation**: Better code documentation and type hints
- **Future-Proof**: Easier to extend and modify

## Contributing

The refactored architecture makes it much easier to contribute:

1. **Clear Module Boundaries**: Know exactly where to make changes
2. **Type Safety**: IDE will catch many errors before runtime
3. **Testing**: Write tests for your changes
4. **Documentation**: Code is self-documenting with type hints

## License

Same as the original PlexCache project.

## Credits

Original PlexCache by bexem: https://github.com/bexem/PlexCache

Special thanks to the original contributors:
- /u/teshiburu2020
- /u/planesrfun  
- /u/trevski13
- /u/extrobe
- /u/dsaunier-sunlight 