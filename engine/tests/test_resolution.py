"""Text parsing, trigram scoring and dish resolution.

The parser tests are written from strings a South Indian user would actually type,
including code-mixed ones. They are the specification.
"""

from __future__ import annotations

import pytest

from pathyam_engine.resolution import (
    DishResolver, InMemoryCandidateSource, LexiconEntry, normalize, parse_log,
    similarity, trigrams, word_similarity,
)
from pathyam_engine.resolution.trigram import phonetic_key, phonetic_similarity

# --------------------------------------------------------------- trigram ----


def test_trigram_padding_matches_pg_trgm():
    """pg_trgm pads with two leading spaces and one trailing before taking 3-grams."""
    assert trigrams("dosa") == frozenset({"  d", " do", "dos", "osa", "sa "})


def test_similarity_is_symmetric_and_bounded():
    assert similarity("dosa", "dosa") == 1.0
    assert similarity("dosa", "idli") < 0.2
    assert similarity("dosa", "dosai") == similarity("dosai", "dosa")
    assert 0.0 <= similarity("dosa", "dosai") <= 1.0


@pytest.mark.parametrize("variant", ["dosai", "dose", "dosey"])
def test_close_romanisations_match_on_trigrams(variant):
    assert similarity("dosa", variant) >= 0.2, variant


@pytest.mark.parametrize("variant", ["thosai", "dhosa", "dosai", "dose", "dosey"])
def test_distant_romanisations_match_phonetically(variant):
    """Trigram alone gives similarity('dosa','thosai') = 0.09 - unusable.

    South Indian transliteration is inconsistent enough that phonetic folding is not
    a nicety; without it, half the ways people spell a dish never retrieve it.
    """
    assert phonetic_similarity("dosa", variant) >= 0.5, variant


def test_phonetic_key_collapses_transliteration_variants():
    assert phonetic_key("thosai") == phonetic_key("dosa") == phonetic_key("dhosai")
    assert phonetic_key("idli") == phonetic_key("iddly") == phonetic_key("idly")


def test_phonetic_key_ignores_native_script():
    """Native script is already unambiguous; folding it would only lose information."""
    assert phonetic_key("தோசை") == "தோசை"


def test_word_similarity_finds_a_word_inside_a_phrase():
    """A one-word query should still match a multi-word dish name."""
    assert word_similarity("dosa", "dosa masala") > similarity("dosa", "dosa masala")
    assert word_similarity("dosa", "dosa masala") == 1.0


def test_normalize_preserves_indic_combining_marks():
    """Regression: Python's \\w drops Mn/Mc marks, Postgres's [:alnum:] keeps them.

    The naive re.sub(r'[^\\w]+', ' ', text) turns 'தோசை' into 'த ச', silently
    breaking every native-script lookup while the SQL side kept working.
    """
    assert normalize("தோசை") == "தோசை"
    assert normalize("ದೋಸೆ") == "ದೋಸೆ"
    assert normalize("ദോശ") == "ദോശ"
    assert normalize("దోస") == "దోస"


def test_normalize_matches_sql_semantics():
    assert normalize("  Dosa, Masala!  ") == "dosa masala"
    assert normalize("") == ""


# ---------------------------------------------------------- text parsing ----


def test_plain_english_with_digit():
    (item,) = parse_log("2 masala dosa")
    assert item.quantity == 2
    assert item.dish_phrase == "masala dosa"
    assert item.effective_quantity == 2


def test_tamil_number_word_romanised():
    (item,) = parse_log("rendu idli")
    assert item.quantity == 2 and item.dish_phrase == "idli"


def test_tamil_native_script():
    (item,) = parse_log("ஒரு தோசை")
    assert item.quantity == 1
    assert item.dish_phrase == "தோசை"
    assert "ta" in item.scripts


@pytest.mark.parametrize("text,qty", [
    ("oru dosai", 1), ("moonu idli", 3), ("naalu vada", 4),
    ("ondu dose", 1), ("eradu idli", 2), ("mooru vade", 3),
    ("randu dosha", 2), ("anchu idli", 5),
    ("oka dosa", 1), ("moodu idli", 3),
    ("ಎರಡು ದೋಸೆ", 2), ("രണ്ട് ദോശ", 2), ("రెండు దోస", 2),
])
def test_number_words_across_languages(text, qty):
    (item,) = parse_log(text)
    assert item.quantity == qty, f"{text!r} -> {item.quantity}"


def test_unit_extraction():
    (item,) = parse_log("2 katori sambar")
    assert item.quantity == 2
    assert item.unit_key == "katori_south"
    assert item.dish_phrase == "sambar"


def test_fraction_quantity():
    (item,) = parse_log("1/2 dosa")
    assert item.quantity == 0.5


def test_half_word():
    (item,) = parse_log("half plate biryani")
    assert item.quantity == 0.5 and item.unit_key == "plate"


def test_multiple_items_split_on_plus():
    items = parse_log("3 idli + sambar")
    assert len(items) == 2
    assert items[0].quantity == 3 and items[0].dish_phrase == "idli"
    assert items[1].dish_phrase == "sambar"


def test_multiple_items_split_on_and_and_comma():
    items = parse_log("2 katori sambar and 1 dosa, 3 idli")
    assert [i.dish_phrase for i in items] == ["sambar", "dosa", "idli"]
    assert [i.quantity for i in items] == [2, 1, 3]


def test_modifier_scales_portion_when_unattached():
    (item,) = parse_log("dosa konjam")
    assert "konjam" in item.modifiers
    assert item.quantity_scale == pytest.approx(0.6)
    assert item.effective_quantity == pytest.approx(0.6)


def test_modifier_before_ingredient_becomes_a_parameter_hint():
    """'konjam ennai' is the user volunteering a fat_g value, not a portion comment."""
    (item,) = parse_log("dosa konjam ennai")
    assert item.parameter_hints == {"fat_g": pytest.approx(0.6)}
    assert item.quantity_scale == 1.0
    assert item.dish_phrase == "dosa"


def test_kannada_oil_modifier():
    (item,) = parse_log("masale dose swalpa enne")
    assert item.parameter_hints == {"fat_g": pytest.approx(0.6)}
    assert item.dish_phrase == "masale dose"


def test_extra_oil_hint_scales_up():
    (item,) = parse_log("dosa extra ghee")
    assert item.parameter_hints["fat_g"] == pytest.approx(1.5)


def test_bare_ingredient_word_stays_in_the_dish_phrase():
    """'ghee roast' is a dish; it must not be parsed as a hint about fat."""
    (item,) = parse_log("1 ghee roast")
    assert item.dish_phrase == "ghee roast"
    assert item.parameter_hints == {}


def test_trailing_modifier_chunk_attaches_to_the_previous_dish():
    """Regression: "2 masale dose, swalpa enne" split on the comma and stranded the hint.

    The user volunteered the oil amount; parsing it into a dish-less item threw that
    away silently and the app then asked a question it had already been answered.
    """
    items = parse_log("2 masale dose, swalpa enne")
    assert len(items) == 1
    assert items[0].dish_phrase == "masale dose"
    assert items[0].quantity == 2
    assert items[0].parameter_hints == {"fat_g": pytest.approx(0.6)}


def test_trailing_modifier_without_a_preceding_dish_is_kept_standalone():
    items = parse_log("konjam ennai")
    assert len(items) == 1 and items[0].dish_phrase == ""


def test_code_mixed_line():
    """The trailing hint attaches to the dish it follows, not to a phantom third item."""
    items = parse_log("2 masale dose + ondu idli, swalpa enne")
    assert len(items) == 2
    assert items[0].quantity == 2 and items[0].dish_phrase == "masale dose"
    assert items[0].parameter_hints == {}
    assert items[1].quantity == 1 and items[1].dish_phrase == "idli"
    assert items[1].parameter_hints == {"fat_g": pytest.approx(0.6)}


def test_empty_and_whitespace_input():
    assert parse_log("") == []
    assert parse_log("   ") == []


def test_no_quantity_defaults_to_one():
    (item,) = parse_log("dosa")
    assert item.quantity is None
    assert item.effective_quantity == 1.0


# ------------------------------------------------------------- resolution ----


@pytest.fixture
def lexicon():
    return InMemoryCandidateSource([
        LexiconEntry(11, "PY-F-000100", "Dosa, plain", "en", "Dosa, plain", True,
                     template_id=1, food_group="prepared_dish"),
        LexiconEntry(11, "PY-F-000100", "Dosa, plain", "ta", "தோசை", True, template_id=1),
        LexiconEntry(11, "PY-F-000100", "Dosa, plain", "ta", "dosai", False, template_id=1),
        LexiconEntry(11, "PY-F-000100", "Dosa, plain", "kn", "ದೋಸೆ", True, template_id=1),
        LexiconEntry(11, "PY-F-000100", "Dosa, plain", "kn", "dose", False, template_id=1),
        LexiconEntry(12, "PY-F-000101", "Dosa, masala", "en", "Masala dosa", True,
                     template_id=3, food_group="prepared_dish"),
        LexiconEntry(12, "PY-F-000101", "Dosa, masala", "kn", "masale dose", True,
                     template_id=3),
        LexiconEntry(12, "PY-F-000101", "Dosa, masala", "ta", "மசாலா தோசை", True,
                     template_id=3),
        LexiconEntry(20, "PY-F-000200", "Idli", "en", "Idli", True, template_id=4),
        LexiconEntry(20, "PY-F-000200", "Idli", "ta", "இட்லி", True, template_id=4),
        LexiconEntry(30, "PY-F-000003", "Black gram dhal", "ta", "ulundhu", True,
                     template_id=None, food_group="pulse"),
    ])


@pytest.fixture
def resolver(lexicon):
    return DishResolver(lexicon)


def test_exact_match_scores_top(resolver):
    (item,) = resolver.resolve_text("1 idli")
    assert item.best.name_en == "Idli"
    assert item.best.method == "exact"
    assert not item.needs_confirmation


def test_misspelling_still_resolves(resolver):
    (item,) = resolver.resolve_text("2 thosai")
    assert item.best.food_id == 11


def test_native_script_resolves(resolver):
    (item,) = resolver.resolve_text("ஒரு தோசை")
    assert item.best.food_id == 11
    assert item.best.lang == "ta"
    assert item.best.method == "exact"


def test_kannada_romanised_resolves_to_masala_dosa(resolver):
    (item,) = resolver.resolve_text("2 masale dose")
    assert item.best.food_id == 12
    assert item.parsed.quantity == 2


def test_one_candidate_per_food_not_per_language(resolver):
    """A dish with names in four languages is one candidate, not four."""
    (item,) = resolver.resolve_text("dosa")
    food_ids = [c.food_id for c in item.candidates]
    assert len(food_ids) == len(set(food_ids))


def test_preferred_language_breaks_a_tie(resolver):
    plain = resolver.resolve_text("dose")[0]
    kannada = resolver.resolve_text("dose", preferred_lang="kn")[0]
    kn_boost = next(c for c in kannada.candidates if c.food_id == 11)
    assert "preferred_lang" in kn_boost.boosts
    assert kn_boost.score >= next(c for c in plain.candidates if c.food_id == 11).score


def test_script_match_boosts_that_language(resolver):
    (item,) = resolver.resolve_text("தோசை")
    assert item.best.lang == "ta"
    assert "script_match" in item.best.boosts or item.best.method == "exact"


def test_computable_dish_is_preferred_over_bare_ingredient(resolver):
    (item,) = resolver.resolve_text("dosa")
    assert item.best.template_id is not None


def test_weak_match_requests_confirmation():
    """Below the confidence floor, ask rather than assume."""
    source = InMemoryCandidateSource([
        LexiconEntry(1, "PY-F-000001", "Pongal", "en", "pongal", True, template_id=1),
    ])
    (item,) = DishResolver(source).resolve_text("pongalam kootu")
    assert item.confidence < 0.72
    assert item.needs_confirmation


def test_close_run_pair_requests_confirmation():
    """Two variants of the same dish family is ambiguity, not weakness - still ask.

    A bare "dosa" against a lexicon holding only "set dosa" and "ghee dosa" is exactly
    the case where guessing is wrong: both are equally good matches and they differ by
    ~100 kcal. The margin, not the confidence, is what catches this.
    """
    source = InMemoryCandidateSource([
        LexiconEntry(1, "PY-F-000001", "Set dosa", "en", "set dosa", True, template_id=1),
        LexiconEntry(2, "PY-F-000002", "Ghee dosa", "en", "ghee dosa", True, template_id=2),
    ])
    (item,) = DishResolver(source).resolve_text("2 dosa")
    assert len(item.candidates) == 2
    assert item.margin < 0.08
    assert item.needs_confirmation


def test_confident_unambiguous_match_does_not_ask(resolver):
    (item,) = resolver.resolve_text("2 idli")
    assert not item.needs_confirmation


def test_exact_match_never_asks_even_when_a_rival_scores_alike(resolver):
    """Regression: "தோசை" matched plain dosa exactly, but masala dosa contains that
    word and its boosts tied the score, giving margin 0.00 and a pointless prompt."""
    (item,) = resolver.resolve_text("தோசை")
    assert item.best.method == "exact"
    assert item.best.food_id == 11
    assert not item.needs_confirmation


def test_boosts_cannot_lift_a_fuzzy_match_to_a_perfect_score(resolver):
    (item,) = resolver.resolve_text("தோசை")
    for candidate in item.candidates:
        if candidate.method != "exact":
            assert candidate.score <= 0.99


def test_unmatchable_input_returns_no_candidates(resolver):
    (item,) = resolver.resolve_text("2 quantum widgets")
    assert item.candidates == []
    assert item.needs_confirmation


def test_multi_item_line_resolves_each(resolver):
    items = resolver.resolve_text("2 idli + 1 masala dosa")
    assert len(items) == 2
    assert items[0].best.name_en == "Idli"
    assert items[1].best.food_id == 12


def test_resolution_carries_parameter_hints_through(resolver):
    (item,) = resolver.resolve_text("masale dose swalpa enne")
    assert item.best.food_id == 12
    assert item.parsed.parameter_hints == {"fat_g": pytest.approx(0.6)}


def test_result_serialises(resolver):
    (item,) = resolver.resolve_text("2 idli")
    d = item.as_dict()
    assert set(d) == {"parsed", "candidates", "confidence", "margin", "needs_confirmation"}
    assert d["candidates"][0]["name_en"] == "Idli"


# ------------------------------------------------------- prior scaling ----


def test_prior_scaling_moves_a_lognormal_median_exactly():
    """'konjam ennai' scales the prior; it must not pin it to a made-up number."""
    import numpy as np
    from pathyam_engine import Prior, sample_prior

    base = Prior("lognormal", {"mu": 2.08, "sigma": 0.55})
    less = base.scaled(0.6)
    rng = np.random.default_rng(1)
    assert np.median(sample_prior(less, 20000, rng)) == pytest.approx(
        np.exp(2.08) * 0.6, rel=0.03
    )
    assert less.params["sigma"] == 0.55, "shape must be preserved"


def test_prior_scaling_leaves_categoricals_alone():
    from pathyam_engine import Prior

    p = Prior("categorical", {"categories": ["a", "b"], "weights": [0.5, 0.5]})
    assert p.scaled(2.0) is p


def test_prior_scaling_rejects_non_positive_factor():
    from pathyam_engine import Prior, PriorError

    with pytest.raises(PriorError, match="must be positive"):
        Prior("normal", {"mu": 1, "sigma": 1}).scaled(0)
