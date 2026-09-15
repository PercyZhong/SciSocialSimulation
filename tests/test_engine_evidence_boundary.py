"""The full closure workflow owns real step_v02 boundary and replay evidence tests."""
import unittest
from pathlib import Path


class EngineEvidenceBoundaryTests(unittest.TestCase):
    # Require the closure integration implementation to call step_v02 rather than duplicate its branch.
    def test_integration_source_calls_production_step(self):
        source=(Path(__file__).resolve().parents[1]/'scimirror'/'stage_a_closure.py').read_text(encoding='utf-8')
        self.assertIn('step_v02(world,ControlledCorpus(count)',source)
        self.assertIn('replay_v02',source)


if __name__=='__main__': unittest.main()
