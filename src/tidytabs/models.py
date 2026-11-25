from dataclasses import dataclass


@dataclass(frozen=True)
class Tab:
    """One open browser tab. `index` is the position inside its window."""

    window_id: str
    tab_id: str
    index: int
    title: str
    url: str
