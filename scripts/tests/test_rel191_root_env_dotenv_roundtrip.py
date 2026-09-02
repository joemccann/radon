"""REL-191 (R-523): the wizard's root-.env encodings round-trip through the
REAL python-dotenv.

The encodings below are byte-exact pins of what web/lib/setup/envFiles.ts
emits for the "python" dialect (pinned on the TS side by
web/tests/setup-env-files-durability.test.ts). The two files together close
the loop: TS pins source -> encoding, this file proves encoding -> value.
"""
from pathlib import Path

from dotenv import dotenv_values

# (encoded line, key, expected value) — exactly as quotePython emits.
CASES = [
    ("A='plain'", "A", "plain"),
    ("B='RX$ab'", "B", "RX$ab"),  # single quotes: $ literal in python-dotenv
    ('K="RX$ab\'cd"', "K", "RX$ab'cd"),  # dq: bare $ literal, ' allowed
    ('L="a\\\\b\\"c\'d"', "L", 'a\\b"c\'d'),  # dq: \\ and \" unescape
]


class TestRootDialectRoundTrip:
    def test_every_pinned_encoding_reads_back_byte_identical(self, tmp_path):
        for line, key, expected in CASES:
            env = tmp_path / f"{key}.env"
            env.write_text(line + "\n", encoding="utf-8")
            parsed = dotenv_values(env)
            assert parsed.get(key) == expected, (line, parsed.get(key))

    def test_the_old_posix_idiom_really_was_unparseable(self, tmp_path):
        # The pre-fix quote() emitted this for RX$ab'cd; python-dotenv drops
        # the whole statement (returns no key at all) — kept as the executable
        # record of why the encoding changed.
        env = tmp_path / "old.env"
        env.write_text("K='RX$ab'\\''cd'\n", encoding="utf-8")
        assert dotenv_values(env).get("K") != "RX$ab'cd"
