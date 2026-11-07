"""
Tests for orbital energy diagram overlap shifting logic.
"""

import sys
from unittest.mock import MagicMock

# Stub PySide6 to prevent DLL loading in test environment
if "PySide6" not in sys.modules:
    pyside6 = MagicMock()
    sys.modules["PySide6"] = pyside6
    sys.modules["PySide6.QtWidgets"] = pyside6.QtWidgets
    sys.modules["PySide6.QtCore"] = pyside6.QtCore
    sys.modules["PySide6.QtGui"] = pyside6.QtGui

from gamess_result_analyzer.energy_diag import calculate_arrow_shifts


def test_calculate_arrow_shifts_no_overlap():
    def mock_val_to_y(energy):
        # Higher energy -> smaller Y coordinate (higher up on screen)
        return 1000 - energy * 100

    items = [(0, 3.0, 1.0), (1, 2.0, 1.0), (2, 1.0, 1.0)]

    # Distance 20 (RKS)
    shifts = calculate_arrow_shifts(items, mock_val_to_y, distance=20)
    assert shifts == {0: 0, 1: 0, 2: 0}


def test_calculate_arrow_shifts_two_overlap_rks():
    def mock_val_to_y(energy):
        return 1000 - energy * 100

    items = [(0, 3.0, 1.0), (1, 2.9, 1.0), (2, 1.0, 1.0)]

    # Distance 20 (RKS) -> shifts should be [10, -10]
    shifts = calculate_arrow_shifts(items, mock_val_to_y, distance=20)
    assert shifts == {0: 10, 1: -10, 2: 0}


def test_calculate_arrow_shifts_two_overlap_uks():
    def mock_val_to_y(energy):
        return 1000 - energy * 100

    items = [(0, 3.0, 1.0), (1, 2.9, 1.0), (2, 1.0, 1.0)]

    # Distance 10 (UKS) -> shifts should be [5, -5]
    shifts = calculate_arrow_shifts(items, mock_val_to_y, distance=10)
    assert shifts == {0: 5, 1: -5, 2: 0}


def test_calculate_arrow_shifts_multiple_overlap_rks():
    def mock_val_to_y(energy):
        return 1000 - energy * 100

    items = [(0, 3.0, 1.0), (1, 2.95, 1.0), (2, 2.90, 1.0)]

    # Distance 20 (RKS) -> shifts should be [20, 0, -20]
    shifts = calculate_arrow_shifts(items, mock_val_to_y, distance=20)
    assert shifts == {0: 20, 1: 0, 2: -20}


def test_calculate_arrow_shifts_multiple_overlap_uks():
    def mock_val_to_y(energy):
        return 1000 - energy * 100

    items = [(0, 3.0, 1.0), (1, 2.95, 1.0), (2, 2.90, 1.0)]

    # Distance 10 (UKS) -> shifts should be [10, 0, -10]
    shifts = calculate_arrow_shifts(items, mock_val_to_y, distance=10)
    assert shifts == {0: 10, 1: 0, 2: -10}


def test_calculate_arrow_shifts_four_overlap_rks():
    def mock_val_to_y(energy):
        return 1000 - energy * 100

    items = [(0, 3.0, 1.0), (1, 2.95, 1.0), (2, 2.90, 1.0), (3, 2.85, 1.0)]

    # Distance 20 (RKS) -> shifts should be [30, 10, -10, -30]
    shifts = calculate_arrow_shifts(items, mock_val_to_y, distance=20)
    assert shifts == {0: 30, 1: 10, 2: -10, 3: -30}
