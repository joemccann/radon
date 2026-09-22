"""Whole-document duplicate detection before any model call."""
import random

from research import novelty


def prose(seed, words=2500):
    # A research note is thousands of words; simhash distance is only meaningful at that size.
    vocab = [f"w{seed}x{i}" for i in range(400)] + "positioning flows equity bond vol percentile week fund cta added".split()
    rng = random.Random(seed)
    return " ".join(rng.choice(vocab) for _ in range(words))


TEXT = prose(1)


def test_fingerprints_are_stable_and_near_duplicates_are_close():
    a = novelty.fingerprint(TEXT)
    assert a == novelty.fingerprint(TEXT)
    edited = TEXT.replace(" percentile ", " quartile ", 2).replace(" week ", " month ", 1)
    assert novelty.hamming(a, novelty.fingerprint(edited)) <= 3
    assert novelty.hamming(a, novelty.fingerprint(prose(2))) > 10


def test_duplicate_lookup_reports_the_matching_published_key():
    index = {"published-key": novelty.fingerprint(TEXT), "other": novelty.fingerprint(prose(3))}
    assert novelty.duplicate_of(novelty.fingerprint(TEXT.replace(" flows ", " flow ", 3)), index) == "published-key"
    assert novelty.duplicate_of(novelty.fingerprint(prose(4)), index) is None


def test_short_or_empty_text_never_matches():
    assert novelty.duplicate_of(novelty.fingerprint(""), {"k": novelty.fingerprint("")}) is None
