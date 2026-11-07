"""
tests/test_gif_export.py
Unit tests for gamess_result_analyzer/gif_export.py (pure Python, no Qt stubs required).
"""

import os
import sys
import importlib.util
import tempfile
import unittest

from PIL import Image

_SRC = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__), "..", "gamess_result_analyzer", "gif_export.py"
    )
)


def _load_gif_export():
    spec = importlib.util.spec_from_file_location("gamess_gif_export_mod", _SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gamess_gif_export_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


_gif_export = _load_gif_export()
encode_frames_to_gif = _gif_export.encode_frames_to_gif


def _frames(n, mode="RGBA"):
    return [Image.new(mode, (4, 4), (10 * i, 0, 0, 255)) for i in range(n)]


class TestEncodeFramesToGif(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "out.gif")

    def test_a_transparent_high_quality_sequence_saves_a_readable_gif(self):
        encode_frames_to_gif(
            _frames(3), self.path, fps=10, transparent=True, use_hq=True
        )
        self.assertTrue(os.path.isfile(self.path))
        with Image.open(self.path) as saved:
            self.assertEqual(saved.n_frames, 3)
            self.assertEqual(saved.info.get("transparency"), 255)

    def test_an_opaque_high_quality_sequence_saves_without_transparency_key(self):
        encode_frames_to_gif(
            _frames(2, mode="RGB"), self.path, fps=10, transparent=False, use_hq=True
        )
        with Image.open(self.path) as saved:
            self.assertEqual(saved.n_frames, 2)
            self.assertNotIn("transparency", saved.info)

    def test_a_non_high_quality_transparent_sequence_still_saves(self):
        encode_frames_to_gif(
            _frames(2), self.path, fps=5, transparent=True, use_hq=False
        )
        with Image.open(self.path) as saved:
            self.assertEqual(saved.n_frames, 2)

    def test_the_frame_duration_follows_the_requested_fps(self):
        encode_frames_to_gif(
            _frames(2), self.path, fps=20, transparent=False, use_hq=False
        )
        with Image.open(self.path) as saved:
            self.assertEqual(saved.info.get("duration"), 50)

    def test_an_empty_frame_list_raises_instead_of_writing_a_corrupt_file(self):
        with self.assertRaises(IndexError):
            encode_frames_to_gif([], self.path, fps=10, transparent=True, use_hq=True)
        self.assertFalse(os.path.isfile(self.path))

    def test_a_frame_missing_the_alpha_channel_fails_encoding_when_transparent(self):
        # Caller asked for a transparent GIF but supplied RGB frames with no
        # alpha channel to split(); the encoder must surface this, not hide it.
        with self.assertRaises(IndexError):
            encode_frames_to_gif(
                _frames(2, mode="RGB"), self.path, fps=10, transparent=True, use_hq=True
            )


if __name__ == "__main__":
    unittest.main()
