"""
Example tests for the refactored PlexCache components.
This demonstrates how the modular architecture enables easy testing.
"""

import unittest
from unittest.mock import Mock, patch
from pathlib import Path

from config import ConfigManager
from system_utils import SystemDetector, PathConverter, FileUtils
from file_operations import FilePathModifier, SubtitleFinder, FileFilter


class TestSystemDetector(unittest.TestCase):
    """Test the SystemDetector class."""
    
    def setUp(self):
        self.detector = SystemDetector()
    
    def test_system_detection(self):
        """Test that system detection works correctly."""
        # This test will depend on the actual system
        self.assertIsInstance(self.detector.os_name, str)
        self.assertIsInstance(self.detector.is_linux, bool)
        self.assertIsInstance(self.detector.is_unraid, bool)
        self.assertIsInstance(self.detector.is_docker, bool)
    
    def test_system_info(self):
        """Test that system info is properly formatted."""
        info = self.detector.get_system_info()
        self.assertIsInstance(info, str)
        self.assertIn(self.detector.os_name, info)


class TestPathConverter(unittest.TestCase):
    """Test the PathConverter class."""
    
    def setUp(self):
        self.converter = PathConverter(is_linux=True)
    
    def test_add_trailing_slashes(self):
        """Test adding trailing slashes to paths."""
        # Test Linux path
        result = self.converter.add_trailing_slashes("path/to/dir")
        self.assertEqual(result, "/path/to/dir/")
        
        # Test already has slashes
        result = self.converter.add_trailing_slashes("/path/to/dir/")
        self.assertEqual(result, "/path/to/dir/")
    
    def test_remove_trailing_slashes(self):
        """Test removing trailing slashes from paths."""
        result = self.converter.remove_trailing_slashes("/path/to/dir/")
        self.assertEqual(result, "/path/to/dir")
        
        result = self.converter.remove_trailing_slashes("/path/to/dir")
        self.assertEqual(result, "/path/to/dir")
    
    def test_remove_all_slashes(self):
        """Test removing all slashes from a list of paths."""
        paths = ["/path/to/dir/", "/another/path\\", "\\windows\\path\\"]
        result = self.converter.remove_all_slashes(paths)
        expected = ["path/to/dir", "another/path", "windows/path"]
        self.assertEqual(result, expected)


class TestFilePathModifier(unittest.TestCase):
    """Test the FilePathModifier class."""
    
    def setUp(self):
        self.modifier = FilePathModifier(
            plex_source="/media/",
            real_source="/mnt/user/",
            plex_library_folders=["movies", "tv"],
            nas_library_folders=["movies", "tv"]
        )
    
    def test_modify_file_paths(self):
        """Test file path modification."""
        files = [
            "/media/movies/test.mkv",
            "/media/tv/show/s01e01.mkv",
            "/other/path/file.mkv"  # Should be filtered out
        ]
        
        result = self.modifier.modify_file_paths(files)
        expected = [
            "/mnt/user/movies/test.mkv",
            "/mnt/user/tv/show/s01e01.mkv"
        ]
        
        self.assertEqual(result, expected)
    
    def test_modify_file_paths_none(self):
        """Test handling of None input."""
        result = self.modifier.modify_file_paths(None)  # type: ignore
        self.assertEqual(result, [])
    
    def test_modify_file_paths_empty(self):
        """Test handling of empty list."""
        result = self.modifier.modify_file_paths([])
        self.assertEqual(result, [])


class TestSubtitleFinder(unittest.TestCase):
    """Test the SubtitleFinder class."""
    
    def setUp(self):
        self.finder = SubtitleFinder()
    
    def test_subtitle_extensions(self):
        """Test that subtitle extensions are properly set."""
        expected = [".srt", ".vtt", ".sbv", ".sub", ".idx"]
        self.assertEqual(self.finder.subtitle_extensions, expected)
    
    def test_custom_subtitle_extensions(self):
        """Test custom subtitle extensions."""
        custom_finder = SubtitleFinder([".srt", ".ass"])
        self.assertEqual(custom_finder.subtitle_extensions, [".srt", ".ass"])


class TestFileFilter(unittest.TestCase):
    """Test the FileFilter class."""
    
    def setUp(self):
        self.filter = FileFilter(
            real_source="/mnt/user/",
            cache_dir="/mnt/cache/",
            is_unraid=True,
            mover_cache_exclude_file="/tmp/exclude.txt"
        )
    
    def test_filter_files_empty(self):
        """Test filtering empty file list."""
        result = self.filter.filter_files([], "cache")
        self.assertEqual(result, [])
    
    def test_get_cache_paths(self):
        """Test cache path generation."""
        file_path = "/mnt/user/movies/test.mkv"
        cache_path, cache_file = self.filter._get_cache_paths(file_path)
        
        self.assertEqual(cache_path, "/mnt/cache/movies")
        self.assertEqual(cache_file, "/mnt/cache/movies/test.mkv")


class TestConfigManager(unittest.TestCase):
    """Test the ConfigManager class."""
    
    def setUp(self):
        # Create a temporary config file for testing
        self.config_file = "/tmp/test_config.json"
        self.config_manager = ConfigManager(self.config_file)
    
    def test_config_validation(self):
        """Test configuration validation."""
        # Test with missing required fields
        with self.assertRaises(FileNotFoundError):
            self.config_manager.load_config()
    
    def test_path_conversion_methods(self):
        """Test path conversion utility methods."""
        # Test add_trailing_slashes
        result = ConfigManager._add_trailing_slashes("path/to/dir")
        self.assertEqual(result, "/path/to/dir/")
        
        # Test remove_all_slashes
        paths = ["/path/to/dir/", "/another/path\\"]
        result = ConfigManager._remove_all_slashes(paths)
        expected = ["path/to/dir", "another/path"]
        self.assertEqual(result, expected)


class TestFileUtils(unittest.TestCase):
    """Test the FileUtils class."""
    
    def setUp(self):
        self.file_utils = FileUtils(is_linux=True)
    
    def test_convert_bytes_to_readable_size(self):
        """Test byte conversion to human readable format."""
        # Test KB
        size, unit = self.file_utils._convert_bytes_to_readable_size(1024)
        self.assertEqual(unit, "KB")
        self.assertEqual(size, 1.0)
        
        # Test MB
        size, unit = self.file_utils._convert_bytes_to_readable_size(1024 * 1024)
        self.assertEqual(unit, "MB")
        self.assertEqual(size, 1.0)
        
        # Test GB
        size, unit = self.file_utils._convert_bytes_to_readable_size(1024 * 1024 * 1024)
        self.assertEqual(unit, "GB")
        self.assertEqual(size, 1.0)


if __name__ == "__main__":
    unittest.main() 