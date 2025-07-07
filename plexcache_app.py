"""
Main PlexCache application.
Orchestrates all components and provides the main business logic.
"""

import sys
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Set

from config import ConfigManager
from logging_config import LoggingManager
from system_utils import SystemDetector, PathConverter, FileUtils
from plex_api import PlexManager, CacheManager
from file_operations import FilePathModifier, SubtitleFinder, FileFilter, FileMover


class PlexCacheApp:
    """Main PlexCache application class."""
    
    def __init__(self, config_file: str, skip_cache: bool = False, debug: bool = False):
        self.config_file = config_file
        self.skip_cache = skip_cache
        self.debug = debug
        self.start_time = time.time()
        
        # Initialize components
        self.config_manager = ConfigManager(config_file)
        self.system_detector = SystemDetector()
        self.path_converter = PathConverter(self.system_detector.is_linux)
        self.file_utils = FileUtils(self.system_detector.is_linux)
        
        # Will be initialized after config loading
        self.logging_manager = None
        self.plex_manager = None
        self.file_path_modifier = None
        self.subtitle_finder = None
        self.file_filter = None
        self.file_mover = None
        
        # State variables
        self.files_to_skip = []
        self.media_to_cache = []
        self.media_to_array = []
        
    def run(self) -> None:
        """Run the main application."""
        try:
            print("*** PlexCache ***")
            
            # Load configuration
            self.config_manager.load_config()
            
            # Setup logging
            self._setup_logging()
            
            # Initialize components that depend on config
            self._initialize_components()
            
            # Log system information
            logging.info(self.system_detector.get_system_info())
            
            # Check paths
            self._check_paths()
            
            # Connect to Plex
            self._connect_to_plex()
            
            # Check for active sessions
            self._check_active_sessions()
            
            # Set debug mode
            self._set_debug_mode()
            
            # Process media
            self._process_media()
            
            # Move files
            self._move_files()
            
            # Log summary and cleanup
            self._finish()
            
        except Exception as e:
            if self.logging_manager:
                logging.critical(f"Application error: {e}")
            else:
                print(f"Application error: {e}")
            raise
    
    def _setup_logging(self) -> None:
        """Set up logging system."""
        self.logging_manager = LoggingManager(
            logs_folder=self.config_manager.paths.logs_folder,
            log_level="",  # Will be set from config
            max_log_files=5
        )
        self.logging_manager.setup_logging()
        self.logging_manager.setup_notification_handlers(
            self.config_manager.notification,
            self.system_detector.is_unraid,
            self.system_detector.is_docker
        )
        logging.info("*** PlexCache ***")
    
    def _initialize_components(self) -> None:
        """Initialize components that depend on configuration."""
        # Initialize Plex manager
        self.plex_manager = PlexManager(
            plex_url=self.config_manager.plex.plex_url,
            plex_token=self.config_manager.plex.plex_token,
            retry_limit=self.config_manager.performance.retry_limit,
            delay=self.config_manager.performance.delay
        )
        
        # Initialize file operation components
        self.file_path_modifier = FilePathModifier(
            plex_source=self.config_manager.paths.plex_source,
            real_source=self.config_manager.paths.real_source,
            plex_library_folders=self.config_manager.paths.plex_library_folders,
            nas_library_folders=self.config_manager.paths.nas_library_folders
        )
        
        self.subtitle_finder = SubtitleFinder()
        
        # Get cache files
        watchlist_cache, watched_cache, mover_exclude = self.config_manager.get_cache_files()
        
        self.file_filter = FileFilter(
            real_source=self.config_manager.paths.real_source,
            cache_dir=self.config_manager.paths.cache_dir,
            is_unraid=self.system_detector.is_unraid,
            mover_cache_exclude_file=str(mover_exclude)
        )
        
        self.file_mover = FileMover(
            real_source=self.config_manager.paths.real_source,
            cache_dir=self.config_manager.paths.cache_dir,
            is_unraid=self.system_detector.is_unraid,
            file_utils=self.file_utils,
            debug=self.debug
        )
    
    def _check_paths(self) -> None:
        """Check that required paths exist and are accessible."""
        for path in [self.config_manager.paths.real_source, self.config_manager.paths.cache_dir]:
            self.file_utils.check_path_exists(path)
    
    def _connect_to_plex(self) -> None:
        """Connect to the Plex server."""
        self.plex_manager.connect()
    
    def _check_active_sessions(self) -> None:
        """Check for active Plex sessions."""
        sessions = self.plex_manager.get_active_sessions()
        if sessions:
            if self.config_manager.exit_if_active_session:
                logging.warning('There is an active session. Exiting...')
                sys.exit('There is an active session. Exiting...')
            else:
                self._process_active_sessions(sessions)
        else:
            logging.info('No active sessions found. Proceeding...')
    
    def _process_active_sessions(self, sessions: List) -> None:
        """Process active sessions and add files to skip list."""
        for session in sessions:
            try:
                media = str(session.source())
                media_id = media[media.find(":") + 1:media.find(":", media.find(":") + 1)]
                media_item = self.plex_manager.plex.fetchItem(int(media_id))
                media_title = media_item.title
                media_type = media_item.type
                
                if media_type == "episode":
                    show_title = media_item.grandparentTitle
                    print(f"Active session detected, skipping: {show_title} - {media_title}")
                    logging.warning(f"Active session detected, skipping: {show_title} - {media_title}")
                elif media_type == "movie":
                    print(f"Active session detected, skipping: {media_title}")
                    logging.warning(f"Active session detected, skipping: {media_title}")
                
                media_path = media_item.media[0].parts[0].file
                logging.info(f"Skipping: {media_path}")
                self.files_to_skip.append(media_path)
                
            except Exception as e:
                logging.error(f"Error occurred while processing session: {session} - {e}")
    
    def _set_debug_mode(self) -> None:
        """Set debug mode if enabled."""
        if self.debug:
            print("Debug mode is active, NO FILE WILL BE MOVED.")
            logging.getLogger().setLevel(logging.DEBUG)
            logging.warning("Debug mode is active, NO FILE WILL BE MOVED.")
            logging.info(f"Real source: {self.config_manager.paths.real_source}")
            logging.info(f"Cache dir: {self.config_manager.paths.cache_dir}")
            logging.info(f"Plex source: {self.config_manager.paths.plex_source}")
            logging.info(f"NAS folders: {self.config_manager.paths.nas_library_folders}")
            logging.info(f"Plex folders: {self.config_manager.paths.plex_library_folders}")
        else:
            logging.getLogger().setLevel(logging.INFO)
    
    def _process_media(self) -> None:
        """Process all media types (onDeck, watchlist, watched)."""
        # Fetch OnDeck Media
        self.media_to_cache.extend(
            self.plex_manager.get_on_deck_media(
                self.config_manager.plex.valid_sections,
                self.config_manager.plex.days_to_monitor,
                self.config_manager.plex.number_episodes,
                self.config_manager.plex.users_toggle,
                self.config_manager.plex.skip_ondeck
            )
        )

        # Edit file paths for the above fetched media
        self.media_to_cache = self.file_path_modifier.modify_file_paths(self.media_to_cache)

        # Fetches subtitles for the above fetched media
        self.media_to_cache.extend(
            self.subtitle_finder.get_media_subtitles(self.media_to_cache, files_to_skip=set(self.files_to_skip))
        )

        # Process watchlist
        if self.config_manager.cache.watchlist_toggle:
            self._process_watchlist()

        # Process watched media
        if self.config_manager.cache.watched_move:
            self._process_watched_media()
    
    def _process_watchlist(self) -> None:
        """Process watchlist media."""
        try:
            watchlist_cache, _, _ = self.config_manager.get_cache_files()
            watchlist_media_set, last_updated = CacheManager.load_media_from_cache(watchlist_cache)
            current_watchlist_set = set()

            if self.system_detector.is_connected():
                # Check if cache should be refreshed
                cache_expired = (
                    self.skip_cache or 
                    (not watchlist_cache.exists()) or 
                    self.debug or 
                    (datetime.now() - datetime.fromtimestamp(watchlist_cache.stat().st_mtime) > 
                     timedelta(hours=self.config_manager.cache.watchlist_cache_expiry))
                )
                
                if cache_expired:
                    logging.info("Fetching watchlist media...")
                    
                    # Fetch the watchlist media from Plex server
                    fetched_watchlist = list(self.plex_manager.get_watchlist_media(
                        self.config_manager.plex.valid_sections,
                        self.config_manager.cache.watchlist_episodes,
                        self.config_manager.plex.users_toggle,
                        self.config_manager.plex.skip_watchlist
                    ))

                    # Add new media paths to the cache
                    for file_path in fetched_watchlist:
                        current_watchlist_set.add(file_path)
                        if file_path not in watchlist_media_set:
                            self.media_to_cache.append(file_path)

                    # Remove media that no longer exists in the watchlist
                    watchlist_media_set.intersection_update(current_watchlist_set)

                    # Add new media to the watchlist media set
                    watchlist_media_set.update(self.media_to_cache)

                    # Modify file paths and add subtitles
                    self.media_to_cache = self.file_path_modifier.modify_file_paths(self.media_to_cache)
                    self.media_to_cache.extend(
                        self.subtitle_finder.get_media_subtitles(self.media_to_cache, files_to_skip=set(self.files_to_skip))
                    )

                    # Update the cache file
                    CacheManager.save_media_to_cache(watchlist_cache, self.media_to_cache)
                else:
                    logging.info("Loading watchlist media from cache...")
                    self.media_to_cache.extend(watchlist_media_set)
            else:
                logging.warning("Unable to connect to the internet, skipping fetching new watchlist media due to plexapi limitation.")
                logging.info("Loading watchlist media from cache...")
                self.media_to_cache.extend(watchlist_media_set)
                
        except Exception as e:
            logging.error(f"An error occurred while processing the watchlist: {str(e)}")
    
    def _process_watched_media(self) -> None:
        """Process watched media."""
        try:
            _, watched_cache, _ = self.config_manager.get_cache_files()
            watched_media_set, last_updated = CacheManager.load_media_from_cache(watched_cache)
            current_media_set = set()

            # Check if cache should be refreshed
            cache_expired = (
                self.skip_cache or 
                not watched_cache.exists() or 
                self.debug or 
                (datetime.now() - datetime.fromtimestamp(watched_cache.stat().st_mtime) > 
                 timedelta(hours=self.config_manager.cache.watched_cache_expiry))
            )
            
            if cache_expired:
                logging.info("Fetching watched media...")

                # Get watched media from Plex server
                fetched_media = list(self.plex_manager.get_watched_media(
                    self.config_manager.plex.valid_sections,
                    last_updated,
                    self.config_manager.plex.users_toggle
                ))
                
                # Add fetched media to the current media set
                for file_path in fetched_media:
                    current_media_set.add(file_path)

                    # Check if file is not already in the watched media set
                    if file_path not in watched_media_set:
                        self.media_to_array.append(file_path)

                # Add new media to the watched media set
                watched_media_set.update(self.media_to_array)
                
                # Modify file paths and add subtitles
                self.media_to_array = self.file_path_modifier.modify_file_paths(self.media_to_array)
                self.media_to_array.extend(
                    self.subtitle_finder.get_media_subtitles(self.media_to_array, files_to_skip=set(self.files_to_skip))
                )

                # Save updated watched media set to cache file
                CacheManager.save_media_to_cache(watched_cache, self.media_to_array)

            else:
                logging.info("Loading watched media from cache...")
                # Add watched media from cache to the media array
                self.media_to_array.extend(watched_media_set)

        except Exception as e:
            logging.error(f"An error occurred while processing the watched media: {str(e)}")
    
    def _move_files(self) -> None:
        """Move files to their destinations."""
        # Move watched files to array
        if self.config_manager.cache.watched_move:
            try:
                self._check_free_space_and_move_files(
                    self.media_to_array, 'array', 
                    self.config_manager.paths.real_source, 
                    self.config_manager.paths.cache_dir
                )
            except Exception as e:
                if not self.debug:
                    logging.critical(f"Error checking free space and moving media files to the array: {str(e)}")
                    sys.exit(f"Error: {str(e)}")
                else:
                    logging.error(f"Error checking free space and moving media files to the array: {str(e)}")
                    print(f"Error: {str(e)}")

        # Move files to cache
        try:
            self._check_free_space_and_move_files(
                self.media_to_cache, 'cache', 
                self.config_manager.paths.real_source, 
                self.config_manager.paths.cache_dir
            )
        except Exception as e:
            if not self.debug:
                logging.critical(f"Error checking free space and moving media files to the cache: {str(e)}")
                sys.exit(f"Error: {str(e)}")
            else:
                logging.error(f"Error checking free space and moving media files to the cache: {str(e)}")
                print(f"Error: {str(e)}")
    
    def _check_free_space_and_move_files(self, media_files: List[str], destination: str, 
                                        real_source: str, cache_dir: str) -> None:
        """Check free space and move files."""
        media_files_filtered = self.file_filter.filter_files(
            media_files, destination, self.media_to_cache, set(self.files_to_skip)
        )
        
        total_size, total_size_unit = self.file_utils.get_total_size_of_files(media_files_filtered)
        
        if total_size > 0:
            logging.info(f"Total size of media files to be moved to {destination}: {total_size:.2f} {total_size_unit}")
            print(f"Total size of media files to be moved to {destination}: {total_size:.2f} {total_size_unit}")
            
            self.logging_manager.add_summary_message(
                f"Total size of media files moved to {destination}: {total_size:.2f} {total_size_unit}"
            )
            
            free_space, free_space_unit = self.file_utils.get_free_space(
                cache_dir if destination == 'cache' else real_source
            )
            print(f"Free space on the {destination}: {free_space:.2f} {free_space_unit}")
            logging.info(f"Free space on the {destination}: {free_space:.2f} {free_space_unit}")
            
            # Check if enough space
            size_multipliers = {'KB': 0, 'MB': 1, 'GB': 2, 'TB': 3}
            total_size_bytes = total_size * (1024 ** size_multipliers[total_size_unit])
            free_space_bytes = free_space * (1024 ** size_multipliers[free_space_unit])
            
            if total_size_bytes > free_space_bytes:
                if not self.debug:
                    sys.exit(f"Not enough space on {destination} drive.")
                else:
                    print(f"Not enough space on {destination} drive.")
                    logging.error(f"Not enough space on {destination} drive.")
            
            logging.info(f"Moving media to {destination}...")
            print(f"Moving media to {destination}...")
            
            self.file_mover.move_media_files(
                media_files_filtered, destination,
                self.config_manager.performance.max_concurrent_moves_array,
                self.config_manager.performance.max_concurrent_moves_cache
            )
        else:
            print(f"Nothing to move to {destination}")
            logging.info(f"Nothing to move to {destination}")
            if not self.logging_manager.files_moved:
                self.logging_manager.summary_messages = ["There were no files to move to any destination."]
    
    def _finish(self) -> None:
        """Finish the application and log summary."""
        end_time = time.time()
        execution_time_seconds = end_time - self.start_time
        execution_time = self._convert_time(execution_time_seconds)

        self.logging_manager.add_summary_message(f"The script took approximately {execution_time} to execute.")
        self.logging_manager.log_summary()

        print(f"Execution time of the script: {execution_time}")
        logging.info(f"Execution time of the script: {execution_time}")

        print("Thank you for using bexem's script: \nhttps://github.com/bexem/PlexCache")
        logging.info("Thank you for using bexem's script: https://github.com/bexem/PlexCache")
        logging.info("Also special thanks to: - /u/teshiburu2020 - /u/planesrfun - /u/trevski13 - /u/extrobe - /u/dsaunier-sunlight")
        logging.info("*** The End ***")
        
        self.logging_manager.shutdown()
        print("*** The End ***")
    
    def _convert_time(self, execution_time_seconds: float) -> str:
        """Convert execution time to human-readable format."""
        days, remainder = divmod(execution_time_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)

        result_str = ""
        if days > 0:
            result_str += f"{int(days)} day{'s' if days > 1 else ''}, "
        if hours > 0:
            result_str += f"{int(hours)} hour{'s' if hours > 1 else ''}, "
        if minutes > 0:
            result_str += f"{int(minutes)} minute{'s' if minutes > 1 else ''}, "
        if seconds > 0:
            result_str += f"{int(seconds)} second{'s' if seconds > 1 else ''}"

        return result_str.rstrip(", ")


def main():
    """Main entry point."""
    skip_cache = "--skip-cache" in sys.argv
    debug = "--debug" in sys.argv
    
    # Default config file location
    config_file = "/mnt/user/system/plexcache/plexcache_settings.json"
    
    app = PlexCacheApp(config_file, skip_cache, debug)
    app.run()


if __name__ == "__main__":
    main() 