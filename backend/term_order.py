"""Shared TRACE term-ordering key.

Lived in server.py until precompute needed the same ordering to pick a course's
current title (the most recent term wins). Two copies of a parser this fiddly
would drift, so it moved here: server.py and precompute.py both import it.
"""

import re


def term_sort_key(title: str) -> int:
    """Returns a numeric sort key where higher = more recent term.
    Order within a year: Fall(7) > Fall A(6) > Full Summer(5) > Summer 2(4) > Summer 1(3) > Spring(2) > Spring A(1)
    """
    if not title:
        return 0
    lower = title.lower()
    # Try word-bounded year first, then 4-digit prefix of 6-digit code (e.g. "202510")
    m = re.search(r'\b(20\d{2})\b', lower) or re.search(r'(20\d{2})\d{2}', lower)
    if not m:
        return 0
    year = int(m.group(1))
    if re.search(r'\bfall\b', lower):
        sub = 6 if re.search(r'\bfall\s+a\b', lower) else 7
    elif re.search(r'\bfull\s+summer\b', lower):
        sub = 5
    elif re.search(r'\bsummer\b', lower):
        if re.search(r'\bsummer\s+2\b', lower):
            sub = 4
        elif re.search(r'\bsummer\s+1\b', lower):
            sub = 3
        else:
            sub = 4
    elif re.search(r'\bspring\b', lower):
        sub = 1 if re.search(r'\bspring\s+a\b', lower) else 2
    else:
        sub = 0
    return year * 10 + sub
