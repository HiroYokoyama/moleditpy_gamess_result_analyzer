"""Plugin entry points and metadata for the GAMESS Result Analyzer plugin."""

PLUGIN_NAME = "GAMESS Result Analyzer"
PLUGIN_VERSION = "1.0.1"
PLUGIN_AUTHOR = "HiroYokoyama"
PLUGIN_DESCRIPTION = "Comprehensive analyzer for GAMESS output files (.log, .out). Includes Vibrational, MO, TDDFT, and NMR analysis."
PLUGIN_TAGS = ["Analysis", "DFT", "File"]
PLUGIN_SUPPORTED_MOLEDITPY_VERSION = ">=5.0.0, <6.0.0"
PLUGIN_SUPPORTED_PYTHON_VERSION = ">=3.9, <3.15"
PLUGIN_DEPENDENCIES = ["rdkit", "PySide6-Essentials", "matplotlib", "Pillow"]
PLUGIN_OPTIONAL_DEPENDENCIES = []

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402
import logging  # noqa: E402

_context = None  # Stored from initialize() so run() can use the registry API


def _read_gamess_file(path, parent_widget):
    """Read an GAMESS output file trying several encodings. Returns content string or None."""
    from .loading import read_gamess_text

    try:
        return read_gamess_text(path)
    except (OSError, ValueError) as e:
        QMessageBox.critical(
            parent_widget, "Error Reading File", f"Could not read file:\n{e}"
        )
        return None


def _open_gamess_file(path, context):
    """Parse and display an GAMESS output file using the context registry for window management."""
    QApplication.processEvents()
    mw = context.get_main_window()

    from .loading import load_gamess_parser

    try:
        parser = load_gamess_parser(path, mw)
    except (OSError, ValueError) as e:
        QMessageBox.critical(mw, "Error Reading File", f"Could not read file:\n{e}")
        return
    if parser is None:  # cancelled by the user
        return

    # Close existing window if open
    existing = context.get_window("analyzer")
    if existing is not None:
        try:
            if existing.close() is False:
                return
        except (RuntimeError, AttributeError) as e:
            logging.warning("Could not close the previous analyzer window: %s", e)

    # New result loaded — any atom colors applied to the previous molecule's
    # indices must not bleed onto this (possibly differently-indexed) one.
    from .utils import clear_atom_color_overrides, sync_main_window_file

    clear_atom_color_overrides(mw)
    sync_main_window_file(mw, path, context)

    from .gui import GAMESSResultAnalyzerDialog

    win = GAMESSResultAnalyzerDialog(mw, parser, path, context)
    context.register_window("analyzer", win)

    win.show()
    win.raise_()
    win.activateWindow()

    QApplication.processEvents()
    # fit_camera=True: this is a fresh file load (drop or file-opener), so
    # reframe the camera after drawing. Without it the newly loaded molecule
    # keeps the previous view's zoom/center and can fall outside the sphere.
    win.load_structure_3d(fit_camera=True)
    QApplication.processEvents()


def _open_gamess_analyzer_empty(context):
    """Open the analyzer dialog with no file pre-loaded."""
    QApplication.processEvents()
    mw = context.get_main_window()

    # Close existing window if open
    existing = context.get_window("analyzer")
    if existing is not None:
        try:
            # If already open, just raise it
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return
        except (RuntimeError, AttributeError) as e:
            logging.warning("Could not re-raise the existing analyzer window: %s", e)

    from .parser import GAMESSParser
    from .gui import GAMESSResultAnalyzerDialog
    from .utils import clear_atom_color_overrides

    parser = GAMESSParser()  # empty — no file loaded
    clear_atom_color_overrides(mw)

    win = GAMESSResultAnalyzerDialog(mw, parser, "", context)
    context.register_window("analyzer", win)

    win.show()
    win.raise_()
    win.activateWindow()
    QApplication.processEvents()


def _on_document_reset(context):
    """Close the analyzer window and drop stale 3D overlay state on File->New.

    File->New wipes the plotter (mw.clear_3d_view()) but does not know about
    plugin-owned state: the analyzer window would otherwise keep showing the
    previous file's data, and any atom-color overrides applied by the Atomic
    Charges view would survive and could bleed onto a differently-indexed
    molecule loaded afterwards.
    """
    win = context.get_window("analyzer")
    if win is not None:
        try:
            win.close()
        except (RuntimeError, AttributeError) as e:
            logging.warning(
                "Could not close the analyzer window on document reset: %s", e
            )

    from .utils import clear_atom_color_overrides

    clear_atom_color_overrides(context.get_main_window())


def is_gamess_output(path):
    """Whether the start of *path* identifies it as GAMESS output."""
    # rungms prints a few hundred lines of settings before the program's banner.
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        header = f.read(200000)
    return "GAMESS VERSION =" in header or "GAMESS execution script" in header


def _can_decline(context):
    """Whether the host lets an opener decline with False (MoleditPy 5.2.0).

    An older host takes False as handled, so there the file is passed on by raising.
    """
    version = getattr(context, "moleditpy_version", "")
    try:
        return tuple(int(part) for part in version.split(".")[:2]) >= (5, 2)
    except (AttributeError, ValueError):
        return False


def initialize(context):
    """Initialize the GAMESS Result Analyzer plugin.

    Both .log and .out are shared with other programs' results, so the openers
    look at the file and decline anything else: the host then offers it to the
    next opener, which is how an ORCA .out still reaches the ORCA analyzer.
    """
    global _context
    _context = context

    def open_gamess_file(path):
        if not is_gamess_output(path):
            if _can_decline(context):
                return False
            raise ValueError(f"{path} is not GAMESS output")
        _open_gamess_file(path, context)

    def handle_drop(path):
        if path.lower().endswith((".log", ".out")):
            try:
                if is_gamess_output(path):
                    _open_gamess_file(path, context)
                    return True
            except (OSError, ValueError) as e:
                logging.warning(
                    "Could not read dropped file %s to check for a GAMESS header: %s",
                    path,
                    e,
                )
        return False

    context.register_file_opener(".log", open_gamess_file, priority=110)
    context.register_file_opener(".out", open_gamess_file, priority=110)
    context.register_drop_handler(handle_drop, priority=110)
    context.register_document_reset_handler(lambda: _on_document_reset(context))

    # Extensions menu entry — opens analyzer without requiring a file first
    context.add_menu_action(
        "Extensions/GAMESS Result Analyzer",
        lambda: _open_gamess_analyzer_empty(context),
    )


def run(mw):  # pylint: disable=unused-argument  # host calls run(mw); reopens via module context
    """Legacy run() entry: called from Plugins menu by the host.

    Opens the analyzer directly without requiring a file — the user can
    use 'Select File' or 'Select from Directory' inside the dialog.
    """
    context = _context
    if context is None:
        return

    _open_gamess_analyzer_empty(context)
