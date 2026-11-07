"""
tests/test_freq_vectors.py
Covers FrequencyDialog's 3D vector display: drawing displacement arrows for
the selected mode, the interaction with manual-displacement mode, and
redrawing the arrows at displaced positions during playback.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

F = gui_harness.load_isolated("freq_analysis")


def _frequencies():
    trivial = [
        {"freq": v, "ir": 0.0, "raman": 0.0, "vector": [[0.0, 0.0, 0.0]] * 3}
        for v in (0.01, 0.02, 0.03, 0.04, 0.05, 0.06)
    ]
    real = [
        {"freq": 1600.0, "ir": 55.0, "raman": 3.0, "vector": [[0.0, 0.1, 0.0]] * 3},
        {"freq": 3200.0, "ir": 5.0, "raman": 9.0, "vector": [[0.0, 0.0, 0.1]] * 3},
    ]
    return trivial + real


COORDS = [[0.0, 0.0, 0.0], [0.76, 0.59, 0.0], [-0.76, 0.59, 0.0]]


class _VectorCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

        saved = F.__file__
        F.__file__ = os.path.join(self.tmp, "freq_analysis.py")
        self.addCleanup(lambda: setattr(F, "__file__", saved))

        # update_view builds meshes with pyvista, bound at module load; other
        # test modules leave a partial stub there, so give it a working double.
        self.pv = MagicMock()
        pv_patch = patch.object(F, "pv", self.pv)
        pv_patch.start()
        self.addCleanup(pv_patch.stop)

        self.plotter = MagicMock()
        self.conf = MagicMock()
        # Reading back the displaced geometry needs a real atom count and
        # positions with numeric x/y/z.
        self.conf.GetNumAtoms.return_value = len(COORDS)
        self.conf.GetAtomPosition.side_effect = lambda i: MagicMock(
            x=COORDS[i][0], y=COORDS[i][1], z=COORDS[i][2]
        )
        self.mol = MagicMock()
        self.mol.GetConformer.return_value = self.conf
        self.mw = MagicMock()
        self.mw.plotter = self.plotter
        self.mw.current_mol = self.mol

        self.dlg = self._make(_frequencies())

    def _make(self, freqs):
        dlg = F.FrequencyDialog(self.mw, freqs, ["O", "H", "H"], COORDS, MagicMock())
        dlg.current_mode_idx = 6
        dlg.chk_vector.setChecked(True)
        dlg.chk_manual_displ.setChecked(False)
        return dlg


class TestUpdateView(_VectorCase):
    def test_selecting_a_mode_draws_its_arrows(self):
        self.dlg.update_view()
        self.plotter.add_mesh.assert_called_once()
        self.assertIsNotNone(self.dlg.vector_actor)

    def test_the_arrows_are_named_so_they_can_be_replaced(self):
        self.dlg.update_view()
        self.assertEqual(self.plotter.add_mesh.call_args.kwargs["name"], "vib_vectors")

    def test_the_arrows_use_the_chosen_colour(self):
        self.dlg.vector_color = "#00ff00"
        self.dlg.update_view()
        self.assertEqual(self.plotter.add_mesh.call_args.kwargs["color"], "#00ff00")

    def test_the_arrow_resolution_is_honoured(self):
        self.dlg.vector_res = 32
        self.dlg.update_view()
        self.assertEqual(self.pv.Arrow.call_args.kwargs["shaft_resolution"], 32)

    def test_redrawing_removes_the_previous_arrows(self):
        self.dlg.update_view()
        previous = self.dlg.vector_actor
        self.dlg.update_view()
        self.plotter.remove_actor.assert_called_once_with(previous)

    def test_a_stale_actor_that_cannot_be_removed_is_tolerated(self):
        self.dlg.vector_actor = object()
        self.plotter.remove_actor.side_effect = ValueError("gone")
        self.dlg.update_view()  # must not raise

    def test_the_geometry_is_reset_before_drawing(self):
        self.dlg.update_view()
        self.conf.SetAtomPosition.assert_called()

    def test_no_mode_means_no_arrows(self):
        self.dlg.current_mode_idx = -1
        self.dlg.update_view()
        self.plotter.add_mesh.assert_not_called()

    def test_disabling_vectors_leaves_the_scene_bare(self):
        self.dlg.chk_vector.setChecked(False)
        self.dlg.update_view()
        self.plotter.add_mesh.assert_not_called()

    def test_a_mode_without_displacements_draws_nothing(self):
        freqs = _frequencies()
        freqs[6]["vector"] = []
        dlg = self._make(freqs)
        dlg.update_view()
        self.plotter.add_mesh.assert_not_called()

    def test_manual_mode_displaces_instead_of_drawing_arrows(self):
        self.dlg.chk_manual_displ.setChecked(True)
        with patch.object(self.dlg, "apply_manual_displacement") as displace:
            self.dlg.update_view()
        displace.assert_called_once()
        self.plotter.add_mesh.assert_not_called()


class TestVectorsAtDisplacedPositions(_VectorCase):
    def test_arrows_are_redrawn_at_the_displaced_geometry(self):
        # The plotter offers add_arrows, which is preferred over the glyph
        # fallback; the positions come from the live conformer.
        self.dlg.update_vectors_at_displaced_position()
        self.plotter.add_arrows.assert_called_once()
        positions = self.plotter.add_arrows.call_args.args[0]
        self.assertEqual(len(positions), len(COORDS))

    def test_the_redrawn_arrows_use_the_chosen_colour(self):
        self.dlg.vector_color = "#00ff00"
        self.dlg.update_vectors_at_displaced_position()
        self.assertEqual(self.plotter.add_arrows.call_args.kwargs["color"], "#00ff00")

    def test_redrawing_removes_the_previous_arrows(self):
        previous = object()
        self.dlg.vector_actor = previous
        self.dlg.update_vectors_at_displaced_position()
        self.plotter.remove_actor.assert_called_once_with(previous)

    def test_a_mode_without_displacements_draws_nothing(self):
        freqs = _frequencies()
        freqs[6]["vector"] = []
        dlg = self._make(freqs)
        dlg.update_vectors_at_displaced_position()
        self.plotter.add_arrows.assert_not_called()

    def test_the_glyph_fallback_is_used_without_add_arrows(self):
        del self.plotter.add_arrows
        self.dlg.update_vectors_at_displaced_position()
        self.plotter.add_mesh.assert_called_once()


if __name__ == "__main__":
    unittest.main()
