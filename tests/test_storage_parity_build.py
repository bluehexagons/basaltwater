"""New parity cannot become active before a stable, independently verified build."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sync.service_tools import scrub_par2 as scrub
from sync.service_tools import parity_sets


class ParityBuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source'
        self.database = self.root / 'database'
        self.source.mkdir()
        self.database.mkdir()
        self.file = self.source / 'file'
        self.file.write_bytes(b'original')
        self.active = self.database / 'file.par2'

    def create(self, effect):
        with patch.object(scrub.subprocess, 'run', side_effect=effect) as run, patch.object(scrub, 'log'), patch.object(scrub.time, 'sleep'):
            result = scrub.create_par2(str(self.file), str(self.source), str(self.database), 10, 'unused')
        return result, run

    def test_build_and_verify_are_staged_before_publication(self):
        def run(command, **kwargs):
            self.assertFalse(self.active.exists())
            self.assertEqual(kwargs['timeout'], 14400)
            if command[1] == 'create':
                Path(command[-2]).write_bytes(b'new parity')
            return subprocess.CompletedProcess(command, 0)
        result, run = self.create(run)
        self.assertTrue(result)
        self.assertEqual([call.args[0][1] for call in run.call_args_list], ['create', 'verify'])
        paths = scrub.locate(str(self.file), str(self.source), str(self.database))[1]
        self.assertEqual(Path(paths[0]).read_bytes(), b'new parity')
        self.assertEqual(list((self.database / scrub.METADATA_DIR).glob('build-*')), [])

    def test_timeout_and_failed_verification_leave_no_active_parity(self):
        for timeout in (True, False):
            def run(command, **kwargs):
                if command[1] == 'create':
                    Path(command[-2]).write_bytes(b'partial parity')
                    if timeout:
                        raise subprocess.TimeoutExpired(command, 14400)
                    return subprocess.CompletedProcess(command, 0)
                return subprocess.CompletedProcess(command, 2)
            result, _ = self.create(run)
            self.assertFalse(result)
            self.assertFalse(self.active.exists())
        self.assertEqual(len(list((self.database / scrub.METADATA_DIR).glob('build-*'))), 2)

    def test_source_change_or_concurrent_parity_prevents_publication(self):
        for change_source in (True, False):
            def run(command, **kwargs):
                if command[1] == 'create':
                    Path(command[-2]).write_bytes(b'new parity')
                elif change_source:
                    self.file.write_bytes(b'concurrent contents')
                else:
                    self.active.write_bytes(b'other writer')
                return subprocess.CompletedProcess(command, 0)
            result, _ = self.create(run)
            self.assertFalse(result)
            if change_source:
                self.assertFalse(self.active.exists())
            else:
                self.assertEqual(self.active.read_bytes(), b'other writer')

    def test_publication_failure_retains_verified_staging_manifest(self):
        def run(command, **kwargs):
            if command[1] == 'create':
                Path(command[-2]).write_bytes(b'verified parity')
            return subprocess.CompletedProcess(command, 0)
        with patch.object(scrub, 'publish', side_effect=OSError('disk full')):
            result, _ = self.create(run)
        self.assertFalse(result)
        self.assertFalse(self.active.exists())
        manifest = next((self.database / scrub.METADATA_DIR).glob('build-*/operation.json'))
        self.assertEqual(json.loads(manifest.read_text())['state'], 'publishing')
        self.assertEqual((manifest.parent / 'parity/set.par2').read_bytes(), b'verified parity')

    def test_interrupted_generation_switch_keeps_previous_active_set(self):
        first, second = self.root / 'first.par2', self.root / 'second.par2'
        first.write_bytes(b'first')
        second.write_bytes(b'second')
        args = (str(self.file), str(self.source), str(self.database))
        parity_sets.publish(*args, [str(first)])
        previous = parity_sets.locate(*args)
        with patch.object(parity_sets, 'write_json_atomic', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                parity_sets.publish(*args, [str(second)])
        self.assertEqual(parity_sets.locate(*args), previous)
        self.assertEqual(Path(previous[1][0]).read_bytes(), b'first')
        parity_sets.publish(*args, [str(second)])
        self.assertEqual(Path(parity_sets.locate(*args)[1][0]).read_bytes(), b'second')
        self.assertEqual(Path(previous[1][0]).read_bytes(), b'first')

    def test_new_generation_supports_par2_and_volume_like_source_names(self):
        parity = self.root / 'set.par2'
        parity.write_bytes(b'parity')
        for name in ('file', 'file.par2', 'file.vol00+01'):
            target = self.source / name
            target.write_bytes(b'content')
            args = (str(target), str(self.source), str(self.database))
            parity_sets.publish(*args, [str(parity)])
            self.assertEqual(Path(parity_sets.locate(*args)[1][0]).read_bytes(), b'parity')
        self.assertEqual(set(parity_sets.protected_files(str(self.database))), {'file', 'file.par2', 'file.vol00+01'})

    def test_missing_generation_volume_fails_closed(self):
        index, volume = self.root / 'set.par2', self.root / 'set.vol00+01.par2'
        index.write_bytes(b'index')
        volume.write_bytes(b'volume')
        args = (str(self.file), str(self.source), str(self.database))
        parity_sets.publish(*args, [str(index), str(volume)])
        Path(parity_sets.locate(*args)[1][1]).unlink()
        with self.assertRaisesRegex(ValueError, 'generation is missing'):
            parity_sets.locate(*args)

    def test_missing_source_keeps_generation_and_is_recorded(self):
        parity = self.root / 'set.par2'
        parity.write_bytes(b'parity')
        args = (str(self.file), str(self.source), str(self.database))
        parity_sets.publish(*args, [str(parity)])
        self.file.unlink()
        with patch.object(scrub, 'log'):
            scrub._record_missing_files(str(self.source), str(self.database), set(), 'unused')
        self.assertTrue(scrub.Findings(str(self.source), str(self.database)).active('file'))
        self.assertTrue(Path(parity_sets.locate(*args)[1][0]).exists())
