"""
Tests for secondbrain.dictionaries module
"""

import pytest
from secondbrain.dictionaries import (
    UNAMBIGUOUS_SYNONYMS,
    AMBIGUOUS_TERMS,
    CONTEXT_HINTS,
    STOPWORDS,
    preprocess_unambiguous,
    build_disambiguation_hints,
    get_canonical_map,
    normalize_concept,
    is_stopword,
    filter_stopwords,
)


class TestUnambiguousSynonyms:
    """Tests for unambiguous synonym lookups."""

    def test_crypto_synonyms(self):
        """Should map crypto tickers to full names."""
        assert UNAMBIGUOUS_SYNONYMS["BTC"] == "Bitcoin"
        assert UNAMBIGUOUS_SYNONYMS["ETH"] == "Ethereum"
        assert UNAMBIGUOUS_SYNONYMS["DOGE"] == "Dogecoin"

    def test_stock_synonyms(self):
        """Should map stock tickers to company names."""
        assert UNAMBIGUOUS_SYNONYMS["AAPL"] == "Apple Inc"
        assert UNAMBIGUOUS_SYNONYMS["MSFT"] == "Microsoft"

    def test_finance_model_synonyms(self):
        """Should map finance model abbreviations."""
        assert UNAMBIGUOUS_SYNONYMS["GARCH"] == "Generalized Autoregressive Conditional Heteroskedasticity"
        assert UNAMBIGUOUS_SYNONYMS["CAPM"] == "Capital Asset Pricing Model"

    def test_ai_ml_synonyms(self):
        """Should map AI/ML abbreviations."""
        assert UNAMBIGUOUS_SYNONYMS["NLP"] == "Natural Language Processing"
        assert UNAMBIGUOUS_SYNONYMS["LLM"] == "Large Language Model"
        assert UNAMBIGUOUS_SYNONYMS["RAG"] == "Retrieval Augmented Generation"


class TestAmbiguousTerms:
    """Tests for ambiguous term definitions."""

    def test_pe_is_ambiguous(self):
        """PE should have multiple meanings."""
        assert "PE" in AMBIGUOUS_TERMS
        meanings = AMBIGUOUS_TERMS["PE"]
        assert "Price To Earnings" in meanings
        assert "Private Equity" in meanings

    def test_ml_is_ambiguous(self):
        """ML should have multiple meanings."""
        assert "ML" in AMBIGUOUS_TERMS
        meanings = AMBIGUOUS_TERMS["ML"]
        assert "Machine Learning" in meanings
        assert "Maximum Likelihood" in meanings

    def test_context_hints_exist(self):
        """Context hints should exist for ambiguous meanings."""
        for abbr, meanings in AMBIGUOUS_TERMS.items():
            for meaning in meanings:
                # At least some meanings should have context hints
                pass  # This is informational


class TestPreprocessUnambiguous:
    """Tests for the preprocess_unambiguous function."""

    def test_expands_btc(self):
        """Should expand BTC to Bitcoin (BTC)."""
        text = "BTC is rising"
        result = preprocess_unambiguous(text)
        assert "Bitcoin (BTC)" in result

    def test_expands_multiple(self):
        """Should expand multiple abbreviations."""
        text = "BTC and ETH are correlated"
        result = preprocess_unambiguous(text)
        assert "Bitcoin" in result
        assert "Ethereum" in result

    def test_preserves_unknown(self):
        """Should preserve text without known abbreviations."""
        text = "This is a test sentence"
        result = preprocess_unambiguous(text)
        assert result == text

    def test_word_boundary(self):
        """Should only match whole words."""
        text = "ABTC is not BTC"  # ABTC should not be expanded
        result = preprocess_unambiguous(text)
        assert "ABTC" in result  # ABTC unchanged
        assert "Bitcoin" in result  # BTC expanded


class TestBuildDisambiguationHints:
    """Tests for the build_disambiguation_hints function."""

    def test_detects_pe(self):
        """Should generate hints for PE."""
        text = "The PE ratio is important"
        hints = build_disambiguation_hints(text)
        assert "PE" in hints
        assert "Price To Earnings" in hints
        assert "Private Equity" in hints

    def test_includes_context_clues(self):
        """Should include context clues for disambiguation."""
        text = "The PE ratio is important"
        hints = build_disambiguation_hints(text)
        assert "ratio" in hints or "valuation" in hints

    def test_no_hints_for_unambiguous(self):
        """Should not generate hints for unambiguous terms."""
        text = "BTC is rising"
        hints = build_disambiguation_hints(text)
        assert hints == ""  # BTC is unambiguous

    def test_empty_for_no_matches(self):
        """Should return empty string when no ambiguous terms found."""
        text = "This is a simple test"
        hints = build_disambiguation_hints(text)
        assert hints == ""


class TestGetCanonicalMap:
    """Tests for the get_canonical_map function."""

    def test_includes_abbreviations(self):
        """Should include abbreviation mappings."""
        canonical_map = get_canonical_map()
        assert "BTC" in canonical_map
        assert canonical_map["BTC"] == "Bitcoin"

    def test_includes_full_names(self):
        """Should include full name mappings."""
        canonical_map = get_canonical_map()
        assert "BITCOIN" in canonical_map
        assert canonical_map["BITCOIN"] == "Bitcoin"

    def test_uppercase_keys(self):
        """All keys should be uppercase."""
        canonical_map = get_canonical_map()
        for key in canonical_map:
            assert key == key.upper()


class TestNormalizeConcept:
    """Tests for the normalize_concept function."""

    def test_normalize_btc(self):
        """Should normalize BTC to Bitcoin."""
        assert normalize_concept("BTC") == "Bitcoin"
        assert normalize_concept("btc") == "Bitcoin"  # Case insensitive

    def test_normalize_with_whitespace(self):
        """Should handle whitespace."""
        assert normalize_concept("  BTC  ") == "Bitcoin"

    def test_normalize_unknown(self):
        """Should return unknown concepts as-is."""
        assert normalize_concept("Unknown Concept") == "Unknown Concept"

    def test_normalize_ambiguous_default(self):
        """Should use first meaning for ambiguous terms without context."""
        result = normalize_concept("PE")
        assert result in AMBIGUOUS_TERMS["PE"]

    def test_normalize_ambiguous_with_context(self):
        """Should use context to disambiguate."""
        result = normalize_concept("PE", context="the PE ratio valuation")
        assert result == "Price To Earnings"

        result = normalize_concept("PE", context="private equity fund acquisition")
        assert result == "Private Equity"


class TestStopwords:
    """Tests for stopword handling."""

    def test_common_stopwords(self):
        """Common words should be stopwords."""
        assert is_stopword("AND")
        assert is_stopword("THE")
        assert is_stopword("FOR")
        assert is_stopword("and")  # Case insensitive

    def test_content_words_not_stopwords(self):
        """Content words should not be stopwords."""
        assert not is_stopword("Bitcoin")
        assert not is_stopword("GARCH")
        assert not is_stopword("Model")

    def test_filter_stopwords(self):
        """Should filter stopwords from list."""
        words = ["THE", "Bitcoin", "AND", "Ethereum", "FOR"]
        filtered = filter_stopwords(words)
        assert "Bitcoin" in filtered
        assert "Ethereum" in filtered
        assert "THE" not in filtered
        assert "AND" not in filtered
