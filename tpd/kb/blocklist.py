from __future__ import annotations

import json
from functools import lru_cache

from . import DATA_DIR

INVESTOR_PARENTS_PATH = DATA_DIR / "investor_parents.json"


@lru_cache(maxsize=1)
def investor_parents() -> tuple[str, ...]:
    """Holding-company and investor names, as written in the bundled list."""
    if not INVESTOR_PARENTS_PATH.exists():
        return ()
    raw = json.loads(INVESTOR_PARENTS_PATH.read_text(encoding="utf-8"))
    return tuple(str(n).strip() for n in raw if str(n).strip())
