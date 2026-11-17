"""The composition-based ambiguity filter: which names lose their string/topic
sources (Trends/GDELT/Reddit) because the query can't separate brand from noise.

Pageviews are Q-ID-keyed and never gated by this, so an ambiguous-named brand
with real pageviews still ranks — only its string signals are dropped.
"""

from __future__ import annotations

from vgr_brand_index.signals import _is_ambiguous_name


def test_short_tokens_are_ambiguous():
    # Two chars or fewer collide with acronyms/homographs (GU came back 75x Gucci).
    assert _is_ambiguous_name("GU")
    assert _is_ambiguous_name("AG")
    assert _is_ambiguous_name("Y3")


def test_single_common_words_are_ambiguous():
    for w in ("Only", "Guess", "Theory", "About", "Island", "Blue"):
        assert _is_ambiguous_name(w), w


def test_legal_entity_suffix_is_ambiguous():
    # The holding/corporate form mis-resolves to the parent topic or is a dup of
    # the common name; drop its string sources (pv still carries it if real).
    assert _is_ambiguous_name("Adidas AG")
    assert _is_ambiguous_name("Blue Inc")
    assert _is_ambiguous_name("Island Company")
    assert _is_ambiguous_name("Tommy Hilfiger B.V.")


def test_all_common_word_phrase_is_ambiguous():
    assert _is_ambiguous_name("About You")


def test_distinctive_multiword_brands_pass_through():
    # No common/suffix tokens — these must NOT be filtered.
    for b in ("Louis Vuitton", "Calvin Klein", "Air Jordan", "Yves Saint Laurent",
              "Dolce & Gabbana", "Massimo Dutti", "Max Mara"):
        assert not _is_ambiguous_name(b), b


def test_distinctive_single_tokens_pass_through():
    for b in ("Nike", "Zara", "Puma", "Fila", "Shein", "Uniqlo", "Gucci"):
        assert not _is_ambiguous_name(b), b
