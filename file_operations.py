"""
File operations for PlexCache.
Handles file moving, filtering, subtitle operations, and path modifications.
"""

import os
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import List, Set, Optional, Tuple


class FilePathModifier:
    """Handles file path modifications and conversions."""
    
    def __init__(self, plex_source: str, real_source: str, 
                 plex_library_folders: List[str], nas_library_folders: List[str]):
        self.plex_source = plex_source
        self.real_source = real_source
        self.plex_library_folders = plex_library_folders
        self.nas_library_folders = nas_library_folders
    
    def modify_file_paths(self, files: List[str]) -> List[str]:
        """Modify file paths from Plex paths to real system paths."""
        if files is None:
            return []

        logging.info("Editing file paths...")
        
        # Filter the files based on those that start with the plex_source path
        files = [file_path for file_path in files if file_path.startswith(self.plex_source)]

        # Iterate over each file path and modify it accordingly
        for i, file_path in enumerate(files):
            logging.info(f"Original path: {file_path}")

            # Replace the plex_source with the real_source in the file path
            file_path = file_path.replace(self.plex_source, self.real_source, 1)

            # Determine which library folder is in the file path
            for j, folder in enumerate(self.plex_library_folders):
                if folder in file_path:
                    # Replace the plex library folder with the corresponding NAS library folder
                    file_path = file_path.replace(folder, self.nas_library_folders[j])
                    break

            # Update the modified file path in the files list
            files[i] = file_path
            logging.info(f"Edited path: {file_path}")

        return files or []


class SubtitleFinder:
    """Handles subtitle file discovery and operations."""
    
    def __init__(self, subtitle_extensions: Optional[List[str]] = None):
        if subtitle_extensions is None:
            subtitle_extensions = [".srt", ".vtt", ".sbv", ".sub", ".idx"]
        self.subtitle_extensions = subtitle_extensions
    
    def get_media_subtitles(self, media_files: List[str], files_to_skip: Optional[Set[str]] = None) -> List[str]:
        """Get subtitle files for media files."""
        logging.info("Fetching subtitles...")
        
        files_to_skip = set() if files_to_skip is None else set(files_to_skip)
        processed_files = set()
        all_media_files = media_files.copy()
        
        for file in media_files:
            if file in files_to_skip or file in processed_files:
                continue
            processed_files.add(file)
            
            directory_path = os.path.dirname(file)
            if os.path.exists(directory_path):
                subtitle_files = self._find_subtitle_files(directory_path, file)
                all_media_files.extend(subtitle_files)
                for subtitle_file in subtitle_files:
                    logging.info(f"Subtitle found: {subtitle_file}")
        
        return all_media_files or []
    
    def _find_subtitle_files(self, directory_path: str, file: str) -> List[str]:
        """Find subtitle files in a directory for a given media file."""
        file_name, _ = os.path.splitext(os.path.basename(file))

        try:
            subtitle_files = [
                entry.path
                for entry in os.scandir(directory_path)
                if entry.is_file() and entry.name.startswith(file_name) and 
                   entry.name != file and entry.name.endswith(tuple(self.subtitle_extensions))
            ]
        except PermissionError as e:
            logging.error(f"Cannot access directory {directory_path}. Permission denied. Error: {e}")
            subtitle_files = []
        except OSError as e:
            logging.error(f"Cannot access directory {directory_path}. Error: {e}")
            subtitle_files = []

        return subtitle_files or []


class FileFilter:
    """Handles file filtering based on destination and conditions."""
    
    def __init__(self, real_source: str, cache_dir: str, is_unraid: bool, 
                 mover_cache_exclude_file: str):
        self.real_source = real_source
        self.cache_dir = cache_dir
        self.is_unraid = is_unraid
        self.mover_cache_exclude_file = mover_cache_exclude_file
    
    def filter_files(self, files: List[str], destination: str, 
                    media_to_cache: Optional[List[str]] = None, 
                    files_to_skip: Optional[Set[str]] = None) -> List[str]:
        """Filter files based on destination and conditions."""
        logging.info(f"Filtering media files for {destination}...")

        if media_to_cache is None:
            media_to_cache = []

        processed_files = set()
        media_to = []
        cache_files_to_exclude = []

        if not files:
            return []

        for file in files:
            if file in processed_files or (files_to_skip and file in files_to_skip):
                continue
            processed_files.add(file)
            
            cache_file_name = self._get_cache_paths(file)[1]
            cache_files_to_exclude.append(cache_file_name)
            
            if destination == 'array':
                if self._should_add_to_array(file, cache_file_name, media_to_cache):
                    media_to.append(file)
                    logging.info(f"Adding file to array: {file}")

            elif destination == 'cache':
                if self._should_add_to_cache(file, cache_file_name):
                    media_to.append(file)
                    logging.info(f"Adding file to cache: {file}")

        if self.is_unraid:
            with open(self.mover_cache_exclude_file, "w") as file:
                for item in cache_files_to_exclude:
                    file.write(str(item) + "\n")

        return media_to or []
    
    def _should_add_to_array(self, file: str, cache_file_name: str, media_to_cache: List[str]) -> bool:
        """Determine if a file should be added to the array."""
        if file in media_to_cache:
            return False

        array_file = file.replace("/mnt/user/", "/mnt/user0/", 1) if self.is_unraid else file

        if os.path.isfile(array_file):
            # File already exists in the array
            if os.path.isfile(cache_file_name):
                os.remove(cache_file_name)
                logging.info(f"Removed cache version of file: {cache_file_name}")
            return False  # No need to add to array
        return True  # Otherwise, the file should be added to the array

    def _should_add_to_cache(self, file: str, cache_file_name: str) -> bool:
        """Determine if a file should be added to the cache."""
        array_file = file.replace("/mnt/user/", "/mnt/user0/", 1) if self.is_unraid else file

        if os.path.isfile(cache_file_name) and os.path.isfile(array_file):
            # Uncomment the following line if you want to remove the array version when the file exists in the cache
            os.remove(array_file)
            logging.info(f"Removed array version of file: {array_file}")
            return False
        return not os.path.isfile(cache_file_name)
    
    def _get_cache_paths(self, file: str) -> Tuple[str, str]:
        """Get cache path and filename for a given file."""
        # Get the cache path by replacing the real source directory with the cache directory
        cache_path = os.path.dirname(file).replace(self.real_source, self.cache_dir, 1)
        
        # Get the cache file name by joining the cache path with the base name of the file
        cache_file_name = os.path.join(cache_path, os.path.basename(file))
        
        return cache_path, cache_file_name


class FileMover:
    """Handles file moving operations."""
    
    def __init__(self, real_source: str, cache_dir: str, is_unraid: bool, 
                 file_utils, debug: bool = False):
        self.real_source = real_source
        self.cache_dir = cache_dir
        self.is_unraid = is_unraid
        self.file_utils = file_utils
        self.debug = debug
    
    def move_media_files(self, files: List[str], destination: str, 
                        max_concurrent_moves_array: int, max_concurrent_moves_cache: int) -> None:
        """Move media files to the specified destination."""
        logging.info(f"Moving media files to {destination}...")
        
        processed_files = set()
        move_commands = []

        # Iterate over each file to move
        for file_to_move in files:
            if file_to_move in processed_files:
                continue
            
            processed_files.add(file_to_move)
            
            # Get the user path, cache path, cache file name, and user file name
            user_path, cache_path, cache_file_name, user_file_name = self._get_paths(file_to_move)
            
            # Get the move command for the current file
            move = self._get_move_command(destination, cache_file_name, user_path, user_file_name, cache_path)
            
            if move is not None:
                move_commands.append(move)
        
        # Execute the move commands
        self._execute_move_commands(move_commands, max_concurrent_moves_array, 
                                  max_concurrent_moves_cache, destination)
    
    def _get_paths(self, file_to_move: str) -> Tuple[str, str, str, str]:
        """Get all necessary paths for file moving."""
        # Get the user path
        user_path = os.path.dirname(file_to_move)
        
        # Get the relative path from the real source directory
        relative_path = os.path.relpath(user_path, self.real_source)
        
        # Get the cache path by joining the cache directory with the relative path
        cache_path = os.path.join(self.cache_dir, relative_path)
        
        # Get the cache file name by joining the cache path with the base name of the file to move
        cache_file_name = os.path.join(cache_path, os.path.basename(file_to_move))
        
        # Modify the user path if unraid is True
        if self.is_unraid:
            user_path = user_path.replace("/mnt/user/", "/mnt/user0/", 1)

        # Get the user file name by joining the user path with the base name of the file to move
        user_file_name = os.path.join(user_path, os.path.basename(file_to_move))
        
        return user_path, cache_path, cache_file_name, user_file_name
    
    def _get_move_command(self, destination: str, cache_file_name: str, 
                         user_path: str, user_file_name: str, cache_path: str) -> Optional[Tuple[str, str]]:
        """Get the move command for a file."""
        move = None
        if destination == 'array':
            self.file_utils.create_directory_with_permissions(user_path, cache_file_name)
            if os.path.isfile(cache_file_name):
                move = (cache_file_name, user_path)
        elif destination == 'cache':
            self.file_utils.create_directory_with_permissions(cache_path, user_file_name)
            if not os.path.isfile(cache_file_name):
                move = (user_file_name, cache_path)
        return move
    
    def _execute_move_commands(self, move_commands: List[Tuple[str, str]], 
                             max_concurrent_moves_array: int, max_concurrent_moves_cache: int, 
                             destination: str) -> None:
        """Execute the move commands."""
        if self.debug:
            for move_cmd in move_commands:
                print(move_cmd)
                logging.info(move_cmd)
        else:
            max_concurrent_moves = max_concurrent_moves_array if destination == 'array' else max_concurrent_moves_cache
            with ThreadPoolExecutor(max_workers=max_concurrent_moves) as executor:
                results = list(executor.map(self._move_file, move_commands))
                errors = [result for result in results if result != 0]
                print(f"Finished moving files with {len(errors)} errors.")
                logging.info(f"Finished moving files with {len(errors)} errors.")
    
    def _move_file(self, move_cmd: Tuple[str, str]) -> int:
        """Move a single file."""
        src, dest = move_cmd
        try:
            self.file_utils.move_file(src, dest)
            logging.info(f"Moved file from {src} to {dest} with original permissions and owner.")
            return 0
        except Exception as e:
            logging.error(f"Error moving file: {str(e)}")
            return 1 