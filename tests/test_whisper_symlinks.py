import os
import unittest

from shorts_clipper.transcription import whisper


class WhisperSymlinkTests(unittest.TestCase):
    def test_disables_hf_symlinks_env(self):
        whisper._disable_hf_symlinks()
        self.assertEqual(os.environ.get("HF_HUB_DISABLE_SYMLINKS"), "1")
        self.assertEqual(os.environ.get("HF_HUB_DISABLE_SYMLINKS_WARNING"), "1")

    def test_patches_frozen_hf_constants(self):
        whisper._disable_hf_symlinks()
        try:
            import huggingface_hub.constants as hf_constants
        except Exception:
            self.skipTest("huggingface_hub not installed")
        self.assertTrue(hf_constants.HF_HUB_DISABLE_SYMLINKS)


if __name__ == "__main__":
    unittest.main()