"""Money formatting."""

import re


def fmt_inr(x: float) -> str:
    """Indian digit grouping: 120000 -> 1,20,000."""
    n = int(round(x))
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        head = re.sub(r"(\d)(?=(\d{2})+$)", r"\1,", head)
        s = f"{head},{tail}"
    return ("-" if n < 0 else "") + s
