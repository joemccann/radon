"""Whole-document near-duplicate detection (64-bit simhash over word shingles)."""
from __future__ import annotations
import hashlib
import re

MAX_DISTANCE = 3
MIN_TOKENS = 40


def _shingles(text):
    words = re.findall(r'[a-z0-9]+', text.lower())
    return [' '.join(words[i:i + 3]) for i in range(len(words) - 2)]


def fingerprint(text):
    shingles = _shingles(text or '')
    if len(shingles) < MIN_TOKENS:
        return 0
    weights = [0] * 64
    for shingle in shingles:
        digest = int.from_bytes(hashlib.blake2b(shingle.encode(), digest_size=8).digest(), 'big')
        for bit in range(64):
            weights[bit] += 1 if digest >> bit & 1 else -1
    return sum(1 << bit for bit in range(64) if weights[bit] > 0)


def hamming(a, b):
    return bin(a ^ b).count('1')


def duplicate_of(fp, index, max_distance=MAX_DISTANCE):
    """Key of the closest indexed fingerprint within max_distance, else None. Empty documents never match."""
    if not fp:
        return None
    best = None
    for key, other in index.items():
        if other and (distance := hamming(fp, other)) <= max_distance and (best is None or distance < best[0]):
            best = (distance, key)
    return best[1] if best else None
