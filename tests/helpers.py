"""Shared test infrastructure.

`RedirectsSeedConfig` was duplicated across three test suites — it points
`schema.SEED_CONFIG_PATH` at a temp file so the real household config
(`data/seed_config.json`) is never touched by a test. Lifted here so any
future change to the redirect (context-manager form, cache invalidation,
safety guards) lands in one place.

Uses `addCleanup` rather than `tearDown` so a partial setUp still cleans up
correctly — a plain `tearDown` would AttributeError on unset attributes and
mask the real failure.
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import schema


class RedirectsSeedConfig(unittest.TestCase):
    """Point schema.SEED_CONFIG_PATH at a temp file for the duration of the test.

    Override `_initial_seed_config()` to control the file's contents.
    """

    def _initial_seed_config(self) -> dict:
        return {"user_corrections": []}

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)

        self._cfg_path = Path(self._tmpdir) / "seed_config.json"
        self._cfg_path.write_text(json.dumps(self._initial_seed_config()))

        original = schema.SEED_CONFIG_PATH
        self.addCleanup(setattr, schema, "SEED_CONFIG_PATH", original)
        schema.SEED_CONFIG_PATH = self._cfg_path
