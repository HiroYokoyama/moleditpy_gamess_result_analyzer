"""Shared access to the one settings.json beside this package.

Every dialog keeps its own top-level key in that single file, so a save must
merge into what is already on disk instead of replacing it — otherwise closing
one dialog discards every other dialog's settings.

The path is always passed in rather than derived from this module's __file__:
the dialog tests redirect their own module's __file__ at a temp dir to keep the
suite from writing into the package source tree, and that seam only works if
the path still comes from the caller.
"""

import json
import logging
import os

from .utils import save_json_atomic


def load_all(path):
    """Return the whole settings mapping, or {} if it is missing or unusable."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        logging.warning("Could not read settings from %s: %s", path, e)
        return {}
    if not isinstance(data, dict):
        logging.warning("Settings file %s is not a JSON object; ignoring it", path)
        return {}
    return data


def load_section(path, key, default=None):
    """Return the mapping stored under *key*, or *default* ({} if unset)."""
    section = load_all(path).get(key)
    if not isinstance(section, dict):
        return {} if default is None else default
    return section


def save_section(path, key, values):
    """Merge *values* under *key*, preserving the other dialogs' sections.

    Returns True on success. Failures are logged rather than raised: settings
    are never important enough to take down the closeEvent that saves them.
    """
    data = load_all(path)
    data[key] = values
    try:
        save_json_atomic(path, data)
        return True
    except (OSError, TypeError, ValueError) as e:
        logging.warning("Could not save %s settings to %s: %s", key, path, e)
        return False
