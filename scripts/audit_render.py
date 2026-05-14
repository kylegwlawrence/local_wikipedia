"""Audit the render pipeline against real article content.

Random-samples N articles from a wiki database, runs each through
``render.convert_wikitext_to_html``, and counts remaining occurrences of
patterns that indicate unhandled wikitext (leaked templates, magic words,
specialised tags, raw external-link brackets, pipe tricks).

Run before/after a render change to quantify what improved or regressed:

    python -m scripts.audit_render --wiki enwiki --sample 1000 > before.txt
    # … apply fix …
    python -m scripts.audit_render --wiki enwiki --sample 1000 > after.txt
    diff before.txt after.txt
"""

import argparse
import random
import re
import sqlite3
import sys
from collections import Counter

from paths import db_path_for
from render import convert_wikitext_to_html

PATTERNS: dict[str, re.Pattern[str]] = {
    "unhandled_template": re.compile(r"\{\{"),
    "magic_word": re.compile(r"__[A-Z]+(?:_[A-Z]+)*__"),
    "ref_tag": re.compile(r"<ref\b", re.IGNORECASE),
    "references_tag": re.compile(r"<references\b", re.IGNORECASE),
    "poem_tag": re.compile(r"<poem\b", re.IGNORECASE),
    "chem_tag": re.compile(r"<(chem|ce)\b", re.IGNORECASE),
    "score_tag": re.compile(r"<score\b", re.IGNORECASE),
    "timeline_tag": re.compile(r"<timeline\b", re.IGNORECASE),
    "hiero_tag": re.compile(r"<hiero\b", re.IGNORECASE),
    "noinclude_tag": re.compile(r"<(noinclude|includeonly|onlyinclude)\b", re.IGNORECASE),
    "external_link_bracket": re.compile(r"\[https?://"),
    "pipe_trick": re.compile(r"\[\[[^\]|]+\|\]\]"),
}


def _sample_articles(conn: sqlite3.Connection, sample: int) -> list[tuple[int, str, str]]:
    """Random-sample articles by drawing page_ids in [1, max], looking them up.

    Much faster than ``ORDER BY RANDOM()`` (which scans the whole table) on
    19M-row enwiki — we trade slight bias against deleted page_ids for orders
    of magnitude better wall-clock time. We oversample 3x then trim, since
    not every random id maps to a real article.
    """
    cur = conn.execute("SELECT MAX(page_id) FROM articles")
    max_id = cur.fetchone()[0]
    if not max_id:
        return []

    target = sample * 3
    ids = random.sample(range(1, max_id + 1), min(target, max_id))
    placeholders = ",".join("?" * len(ids))
    cur = conn.execute(
        f"SELECT page_id, title, text_content FROM articles "
        f"WHERE page_id IN ({placeholders}) "
        f"AND text_content IS NOT NULL AND length(text_content) > 200 "
        f"LIMIT ?",
        (*ids, sample),
    )
    return cur.fetchall()


def audit(wiki: str, sample: int) -> int:
    path = db_path_for(wiki)
    if not path.exists():
        print(f"ERROR: no database at {path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        articles = _sample_articles(conn, sample)
    finally:
        conn.close()

    if not articles:
        print(f"ERROR: no articles found in {path}", file=sys.stderr)
        return 1

    print(f"Audited {len(articles)} articles from {path}")

    # Count occurrences and articles-affected (one article may contain many).
    occurrences: Counter[str] = Counter()
    articles_affected: Counter[str] = Counter()
    render_errors = 0

    for _page_id, _title, wikitext in articles:
        try:
            rendered = convert_wikitext_to_html(wikitext or "")
        except Exception:
            render_errors += 1
            continue
        for name, pat in PATTERNS.items():
            matches = pat.findall(rendered)
            if matches:
                occurrences[name] += len(matches)
                articles_affected[name] += 1

    print()
    print(f"{'pattern':<28} {'occurrences':>12} {'articles':>10} {'%articles':>10}")
    print("-" * 64)
    total = len(articles)
    for name in PATTERNS:
        occ = occurrences.get(name, 0)
        art = articles_affected.get(name, 0)
        pct = art / total * 100 if total else 0
        print(f"{name:<28} {occ:>12} {art:>10} {pct:>9.2f}%")

    print()
    print(f"render errors: {render_errors} ({render_errors / total * 100:.2f}%)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wiki", default="enwiki", help="wiki name (default: enwiki)")
    parser.add_argument("--sample", type=int, default=1000, help="article count (default: 1000)")
    args = parser.parse_args(argv)
    return audit(args.wiki, args.sample)


if __name__ == "__main__":
    sys.exit(main())
