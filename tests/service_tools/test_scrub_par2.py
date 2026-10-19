"""Tests for par2 scrub operations."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from sync.service_tools.scrub_par2 import _par2_base_from_parity_file


class TestPar2BaseFromParityFile(unittest.TestCase):
    """Tests for _par2_base_from_parity_file function.
    
    This function extracts the base par2 filename from various par2 file formats.
    Critical for orphan detection to avoid false positives that delete valid par2 files.
    """
    
    def test_volume_only_file(self):
        """Test extraction of base path from volume-only par2 files (created with -n1).
        
        Volume-only files have pattern: filename.vol000+100.par2
        This was the bug: the old code didn't recognize this pattern and treated
        these as orphans, causing a delete/recreate cycle.
        """
        self.assertEqual(
            _par2_base_from_parity_file('/database/Blender/lampshade_1b.blend.vol000+100.par2'),
            '/database/Blender/lampshade_1b.blend.par2'
        )
        self.assertEqual(
            _par2_base_from_parity_file('/database/file.vol00+01.par2'),
            '/database/file.par2'
        )
        self.assertEqual(
            _par2_base_from_parity_file('/database/test.vol999+999.par2'),
            '/database/test.par2'
        )
    
    def test_base_plus_volume_file(self):
        """Test extraction of base path from base+volume par2 files (created with -n2+).
        
        Base+volume files have pattern: filename.par2.vol00+01.par2
        This format was already working correctly.
        """
        self.assertEqual(
            _par2_base_from_parity_file('/database/file.par2.vol00+01.par2'),
            '/database/file.par2'
        )
        self.assertEqual(
            _par2_base_from_parity_file('/database/test.par2.vol000+100.par2'),
            '/database/test.par2'
        )
    
    def test_base_file(self):
        """Test that base par2 files are returned unchanged."""
        self.assertEqual(
            _par2_base_from_parity_file('/database/file.par2'),
            '/database/file.par2'
        )
        self.assertEqual(
            _par2_base_from_parity_file('/database/Blender/project.blend.par2'),
            '/database/Blender/project.blend.par2'
        )
    
    def test_edge_case_vol_in_filename(self):
        """Test that files with .vol in their actual name are handled correctly.
        
        A file named "data.vol.txt" should have its base par2 as "data.vol.txt.par2"
        and should NOT be confused with a volume file.
        """
        # Base file with .vol in the data filename
        self.assertEqual(
            _par2_base_from_parity_file('/database/data.vol.txt.par2'),
            '/database/data.vol.txt.par2'
        )
        # Volume file for a data file that has .vol in its name
        self.assertEqual(
            _par2_base_from_parity_file('/database/data.vol.txt.vol001+050.par2'),
            '/database/data.vol.txt.par2'
        )
    
    def test_unusual_patterns(self):
        """Test edge cases with unusual but valid patterns."""
        # Multiple dots in filename
        self.assertEqual(
            _par2_base_from_parity_file('/db/archive.tar.gz.vol001+010.par2'),
            '/db/archive.tar.gz.par2'
        )
        # Very long volume numbers
        self.assertEqual(
            _par2_base_from_parity_file('/db/file.vol12345+67890.par2'),
            '/db/file.par2'
        )
        # Nested directories
        self.assertEqual(
            _par2_base_from_parity_file('/db/a/b/c/d/file.vol00+01.par2'),
            '/db/a/b/c/d/file.par2'
        )


if __name__ == '__main__':
    unittest.main()
