"""Y-bucketed spatial index over pdfplumber word dicts."""

from __future__ import annotations

# ----- spatial index for words -----------------------------------------------
_BUCKET_SIZE = 20.0  # pt — covers typical text-line height


class WordIndex:
    """Y-coordinate bucketed spatial index over pdfplumber word dicts.

    Built once per page; replaces O(N) linear scans with O(K) bucket lookups
    where K is the number of words in the queried vertical band.
    """

    __slots__ = ("_buckets", "_bucket_size", "all_words")

    def __init__(self, words: list, bucket_size: float = _BUCKET_SIZE):
        self._bucket_size = bucket_size
        self.all_words = words
        self._buckets: dict[int, list] = {}
        for w in words:
            cy = (float(w["top"]) + float(w["bottom"])) / 2.0
            key = int(cy / bucket_size)
            bucket = self._buckets.get(key)
            if bucket is None:
                self._buckets[key] = [w]
            else:
                bucket.append(w)

    def _candidate_keys(self, top: float, bottom: float) -> range:
        lo = int(top / self._bucket_size) - 1
        hi = int(bottom / self._bucket_size) + 1
        return range(lo, hi + 1)

    def query_rect(self, x0: float, top: float, x1: float, bottom: float,
                   pad: float = 1.0) -> list:
        """Return words whose centre falls inside the padded rectangle."""
        out: list = []
        for k in self._candidate_keys(top - pad, bottom + pad):
            bucket = self._buckets.get(k)
            if bucket is None:
                continue
            for w in bucket:
                cx = (w["x0"] + w["x1"]) / 2.0
                cy = (w["top"] + w["bottom"]) / 2.0
                if x0 - pad <= cx <= x1 + pad and top - pad <= cy <= bottom + pad:
                    out.append(w)
        return out

    def query_outside_rects(self, bboxes: list) -> list:
        """Return words whose centre is NOT inside any of the bboxes."""
        out: list = []
        for w in self.all_words:
            cx = (w["x0"] + w["x1"]) / 2.0
            cy = (w["top"] + w["bottom"]) / 2.0
            inside = False
            for (bx0, btop, bx1, bbottom) in bboxes:
                if bx0 - 1 <= cx <= bx1 + 1 and btop - 1 <= cy <= bbottom + 1:
                    inside = True
                    break
            if not inside:
                out.append(w)
        return out


__all__ = ["WordIndex"]
