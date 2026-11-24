import os
import tempfile
import unittest

from spare.core import config
from spare.core.ledger import Ledger


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.ledger_path = os.path.join(self.dir, "ledger.jsonl")

    def tearDown(self):
        self._tmp.cleanup()

    def ledger(self) -> Ledger:
        return Ledger(self.ledger_path)


UNIVERSE = config.load_universe()
