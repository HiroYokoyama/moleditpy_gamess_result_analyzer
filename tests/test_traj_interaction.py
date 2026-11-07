"""
tests/test_traj_interaction.py
Covers TrajectoryResultDialog's interactive surface: the NEB auto-load that
looks for a sibling MEP trajectory when the path summary carries no geometry,
the manual chooser it falls back to, and the graph interactions (scroll to
step, click to select, hover tooltip).
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

TR = gui_harness.load_isolated("traj_analysis")


def _xyz(frames):
    out = []
    for energy, x in frames:
        out.append("2")
        out.append(f"Coordinates from GAMESS-job E {energy:.8f}")
        out.append("O    0.000000    0.000000    0.000000")
        out.append(f"H    {x:.6f}    0.000000    0.000000")
    return "\n".join(out) + "\n"


def _summary_steps():
    """An NEB path summary: energies and distances but no geometry."""
    return [
        {"energy": -100.0, "type": "neb_image", "dist": 0.0},
        {"energy": -99.5, "type": "neb_image", "dist": 0.5},
    ]


def _full_steps():
    return [
        {
            "energy": -100.0,
            "type": "opt_cycle",
            "atoms": ["O", "H"],
            "coords": [[0.0, 0.0, 0.0], [0.96, 0.0, 0.0]],
        },
        {
            "energy": -100.5,
            "type": "opt_cycle",
            "atoms": ["O", "H"],
            "coords": [[0.0, 0.0, 0.0], [0.97, 0.0, 0.0]],
        },
    ]


class _TrajCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.gl = MagicMock()

    def _make(self, steps, **kw):
        kw.setdefault("context", MagicMock())
        return TR.TrajectoryResultDialog(self.gl, steps, **kw)

    def _write_trj(self, name, frames=((-100.0, 0.96), (-99.5, 1.20))):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(_xyz(list(frames)))
        return path


# ---------------------------------------------------------------------------
# NEB auto-load
# ---------------------------------------------------------------------------


class TestAutoLoad(_TrajCase):


    def test_ambiguous_candidates_are_not_guessed_between(self):
        self._write_trj("a_MEP_trj.xyz")
        self._write_trj("b_MEP_trj.xyz")
        dlg = self._make(_summary_steps(), base_dir=self.tmp)
        with patch.object(dlg, "load_external_trj") as load:
            with patch.object(TR.QTimer, "singleShot"):
                dlg.run_auto_load()
        load.assert_not_called()


    def test_a_trajectory_that_already_has_geometry_is_left_alone(self):
        dlg = self._make(_full_steps(), base_dir=self.tmp)
        with patch.object(dlg, "load_external_trj") as load:
            with patch.object(TR.QTimer, "singleShot") as later:
                dlg.run_auto_load()
        load.assert_not_called()
        later.assert_not_called()

    def test_a_non_neb_run_is_never_auto_loaded(self):
        steps = [{"energy": -100.0, "type": "opt_cycle"}]
        dlg = self._make(steps, base_dir=self.tmp)
        with patch.object(dlg, "load_external_trj") as load:
            with patch.object(TR.QTimer, "singleShot") as later:
                dlg.run_auto_load()
        load.assert_not_called()
        later.assert_not_called()

    def test_an_empty_trajectory_is_ignored(self):
        dlg = self._make([])
        with patch.object(dlg, "load_external_trj") as load:
            dlg.run_auto_load()
        load.assert_not_called()


class TestManualLoad(_TrajCase):

    def test_cancelling_loads_nothing(self):
        dlg = self._make(_summary_steps(), base_dir=self.tmp)
        with patch.object(TR.QFileDialog, "getOpenFileName", return_value=("", "")):
            with patch.object(dlg, "load_external_trj") as load:
                dlg.load_mep_trj()
        load.assert_not_called()


# ---------------------------------------------------------------------------
# Graph interaction
# ---------------------------------------------------------------------------


class TestGraphInteraction(_TrajCase):
    def setUp(self):
        super().setUp()
        self.dlg = self._make(_full_steps())
        self.dlg.slider = MagicMock()
        self.dlg.slider.value.return_value = 1

    def test_scrolling_up_steps_backwards(self):
        self.dlg.on_scroll(MagicMock(button="up"))
        self.dlg.slider.setValue.assert_called_with(0)

    def test_scrolling_down_steps_forwards(self):
        self.dlg.slider.value.return_value = 0
        self.dlg.on_scroll(MagicMock(button="down"))
        self.dlg.slider.setValue.assert_called_with(1)

    def test_scrolling_stops_at_the_first_step(self):
        self.dlg.slider.value.return_value = 0
        self.dlg.on_scroll(MagicMock(button="up"))
        self.dlg.slider.setValue.assert_called_with(0)

    def test_scrolling_stops_at_the_last_step(self):
        self.dlg.slider.value.return_value = 1
        self.dlg.on_scroll(MagicMock(button="down"))
        self.dlg.slider.setValue.assert_called_with(1)

    def test_clicking_a_point_selects_that_step(self):
        event = MagicMock()
        event.mouseevent.button = 1
        event.ind = [1]
        self.dlg.on_pick(event)
        self.dlg.slider.setValue.assert_called_with(1)

    def test_a_right_click_on_the_graph_is_ignored(self):
        event = MagicMock()
        event.mouseevent.button = 3
        event.ind = [1]
        self.dlg.on_pick(event)
        self.dlg.slider.setValue.assert_not_called()

    def test_picking_is_disabled_without_geometry(self):
        dlg = self._make(_summary_steps())
        dlg.slider = MagicMock()
        event = MagicMock()
        event.mouseevent.button = 1
        event.ind = [1]
        dlg.on_pick(event)
        dlg.slider.setValue.assert_not_called()


class TestHoverTooltip(_TrajCase):
    def setUp(self):
        super().setUp()
        self.dlg = self._make(_full_steps())
        self.dlg.annot = MagicMock()
        self.dlg.annot.get_visible.return_value = False
        self.scatter = MagicMock()
        self.scatter.get_offsets.return_value = [(0, -100.0), (1, -100.5)]
        self.dlg.scatter = self.scatter

    def _hover(self, over_point=True, index=1, inaxes=True):
        self.scatter.contains.return_value = (over_point, {"ind": [index]})
        event = MagicMock()
        event.inaxes = self.dlg.canvas.axes if inaxes else None
        self.dlg.on_hover(event)

    def test_hovering_a_point_shows_the_tooltip(self):
        self._hover()
        self.dlg.annot.set_visible.assert_called_with(True)

    def test_the_tooltip_names_the_step(self):
        self._hover(index=1)
        self.assertIn("Step 1", self.dlg.annot.set_text.call_args.args[0])

    def test_the_tooltip_reports_the_displayed_energy(self):
        self._hover(index=1)
        self.assertIn(self.dlg.current_unit, self.dlg.annot.set_text.call_args.args[0])

    def test_moving_off_a_point_hides_the_tooltip(self):
        self.dlg.annot.get_visible.return_value = True
        self._hover(over_point=False)
        self.dlg.annot.set_visible.assert_called_with(False)

    def test_an_out_of_range_point_is_ignored(self):
        self._hover(index=99)
        self.dlg.annot.set_text.assert_not_called()

    def test_hovering_outside_the_axes_does_nothing(self):
        self._hover(inaxes=False)
        self.dlg.annot.set_text.assert_not_called()

    def test_hovering_without_a_plot_does_nothing(self):
        self.dlg.scatter = None
        self.dlg.on_hover(MagicMock())
        self.dlg.annot.set_text.assert_not_called()

    def test_a_reaction_coordinate_is_included_when_known(self):
        dlg = self._make([dict(s, dist=0.5 * i) for i, s in enumerate(_full_steps())])
        dlg.annot = MagicMock()
        dlg.annot.get_visible.return_value = False
        scatter = MagicMock()
        scatter.get_offsets.return_value = [(0, -100.0), (1, -100.5)]
        scatter.contains.return_value = (True, {"ind": [1]})
        dlg.scatter = scatter
        event = MagicMock()
        event.inaxes = dlg.canvas.axes
        dlg.on_hover(event)
        self.assertIn("Coord:", dlg.annot.set_text.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
