import unittest

from scripts.quality_gate import voice_verdict


class VoiceVerdictTests(unittest.TestCase):
    def test_pronunciation_failure_blocks(self):
        verdict = voice_verdict(
            listener_assertions=[{"passed": True}],
            semantic_checks=[{"passed": True}],
            pronunciation_passed=False,
            voice_preservation_probability=0.99,
            threshold=0.85,
        )

        self.assertFalse(verdict["voice_passed"])
        self.assertFalse(verdict["passed"])

    def test_listener_assertion_failure_blocks(self):
        verdict = voice_verdict(
            listener_assertions=[{"passed": False}],
            semantic_checks=[{"passed": True}],
            pronunciation_passed=True,
            voice_preservation_probability=0.99,
            threshold=0.85,
        )

        self.assertFalse(verdict["passed"])

    def test_semantic_spoken_fidelity_failure_blocks(self):
        verdict = voice_verdict(
            listener_assertions=[{"passed": True}],
            semantic_checks=[{"passed": False}],
            pronunciation_passed=True,
            voice_preservation_probability=0.2,
            threshold=0.85,
        )

        self.assertFalse(verdict["passed"])

    def test_faithful_voice_passes(self):
        verdict = voice_verdict(
            listener_assertions=[{"passed": True}],
            semantic_checks=[{"passed": True}],
            pronunciation_passed=True,
            voice_preservation_probability=0.95,
            threshold=0.85,
        )

        self.assertTrue(verdict["voice_passed"])
        self.assertTrue(verdict["passed"])


if __name__ == "__main__":
    unittest.main()