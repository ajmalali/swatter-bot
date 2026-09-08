"""Every N seconds, for each bound repo: fetch Issues updated since the cursor, update the index,
re-embed changed Issues, and fire notifications for closes and reopens. See ADR 0001.

TODO(scaffold): implement run_once. loop() is the thread entry point used by `swatter run`.
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)


def run_once(ctx) -> None:  # noqa: ANN001  (AppContext lives in slack.app; avoid the cycle)
    raise NotImplementedError


def loop(ctx, interval_seconds: int) -> None:  # noqa: ANN001
    while True:
        try:
            run_once(ctx)
        except NotImplementedError:
            log.warning("poller not implemented yet")
        except Exception:
            log.exception("poll cycle failed")
        time.sleep(interval_seconds)
