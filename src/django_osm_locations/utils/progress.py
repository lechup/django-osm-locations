"""
Progress bars, drawn only when someone is watching.

A bar is a stream of carriage returns. That is right on an interactive
terminal and wrong everywhere else: unreadable in a log file, and in cron it
is output on stderr from a job that had nothing to say, which is how a nightly
import starts sending mail.

So the default is to decide from the terminal. ``LOCATIONS_SHOW_PROGRESS``
overrides that in either direction, and callers can pass ``show`` explicitly --
management commands do, for ``-v 0`` and ``--no-progress``.
"""
from django.conf import settings
from tqdm import tqdm


def show_progress_default():
    """``True``, ``False``, or ``None`` meaning "decide from the terminal"."""
    return getattr(settings, 'LOCATIONS_SHOW_PROGRESS', None)


def progress(iterable=None, total=None, desc=None, unit='obj', show=None, leave=False):
    """
    Wrap an iterable in a progress bar.

    :param show: True to force the bar, False to suppress it, None to decide
        from whether stderr is a terminal.
    """
    if show is None:
        show = show_progress_default()
    return tqdm(
        iterable,
        total=total,
        desc=desc,
        unit=unit,
        unit_scale=True,
        leave=leave,
        dynamic_ncols=True,
        # tqdm's own convention: True off, False on, None auto-detect
        disable=None if show is None else not show,
    )
