"""Az alcsomag-mappák felvétele a sys.path-ra: a modulok egymást lapos névvel importálják (import rules, from policy import ...),
és a pickle-ozott modulnevek (arena.make_js) is így maradnak. A multiprocessing spawn a szülő sys.path-át örökli."""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _sub in ("core", "ai", "coach", "server", "training", "research", "legacy", "bookprep"):
    _p = str(_HERE / _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)
