"""
Domain-Specific Dictionaries for SecondBrain

Contains synonym mappings, abbreviation expansions, and context hints
for normalizing concepts in the knowledge graph.

Three-tier normalization system:
1. UNAMBIGUOUS_SYNONYMS - Direct O(1) lookup (BTC -> Bitcoin)
2. AMBIGUOUS_TERMS - Context-dependent (PE -> Price To Earnings OR Private Equity)
3. CONTEXT_HINTS - Keywords to disambiguate ambiguous terms
"""

import re
from typing import Dict, List, Set, Optional


# Words to ignore during concept extraction
STOPWORDS: Set[str] = {
    "AND", "THE", "FOR", "BUT", "NOT", "WITH", "THAT", "THIS", "FROM",
    "HAVE", "ARE", "WAS", "ALL", "ONE", "HAS", "CAN", "OUT", "INTO",
    "NOW", "NEW", "BIG", "GET", "USE", "HOW", "WHO", "WHY", "YES"
}

# Tier 1: Unambiguous abbreviations -> Full names (deterministic O(1) lookup)
UNAMBIGUOUS_SYNONYMS: Dict[str, str] = {
    # Crypto
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "DOGE": "Dogecoin",

    # Stocks
    "AAPL": "Apple Inc",
    "GOOGL": "Alphabet Inc",
    "MSFT": "Microsoft",
    "TCS": "Tata Consultancy Services",
    "INFY": "Infosys",

    # Indices
    "NIFTY": "Nifty 50",
    "SENSEX": "BSE Sensex",
    "SPX": "S&P 500",

    # Currencies
    "USD": "US Dollar",
    "INR": "Indian Rupee",

    # Finance Models
    "GARCH": "Generalized Autoregressive Conditional Heteroskedasticity",
    "ARIMA": "Autoregressive Integrated Moving Average",
    "CAPM": "Capital Asset Pricing Model",
    "EMH": "Efficient Market Hypothesis",

    # Finance Metrics
    "EBITDA": "Earnings Before Interest Taxes Depreciation Amortization",
    "HFT": "High Frequency Trading",
    "CAGR": "Compound Annual Growth Rate",
    "ROE": "Return On Equity",
    "ROA": "Return On Assets",
    "CPI": "Consumer Price Index",
    "GDP": "Gross Domestic Product",

    # Institutions
    "FED": "Federal Reserve",
    "RBI": "Reserve Bank Of India",

    # AI/ML
    "NLP": "Natural Language Processing",
    "LLM": "Large Language Model",
    "LSTM": "Long Short-Term Memory",
    "CNN": "Convolutional Neural Network",
    "RNN": "Recurrent Neural Network",
    "GPT": "Generative Pre-trained Transformer",
    "RAG": "Retrieval Augmented Generation",
}

# Tier 2: Ambiguous abbreviations (need context to resolve)
AMBIGUOUS_TERMS: Dict[str, List[str]] = {
    "PE": ["Price To Earnings", "Private Equity"],
    "VAR": ["Vector Autoregression", "Value At Risk"],
    "ML": ["Machine Learning", "Maximum Likelihood"],
    "IV": ["Implied Volatility", "Independent Variable"],
    "ATM": ["At The Money", "Automated Teller Machine"],
    "IR": ["Information Ratio", "Interest Rate"],
    "ES": ["Expected Shortfall", "E-mini S&P"],
    "MV": ["Mean-Variance", "Market Value"],
    "BL": ["Black-Litterman", "Baseline"],
}

# Tier 3: Context hints for disambiguation
CONTEXT_HINTS: Dict[str, List[str]] = {
    "Price To Earnings": ["ratio", "multiple", "valuation", "earnings"],
    "Private Equity": ["fund", "acquisition", "buyout", "capital"],
    "Vector Autoregression": ["model", "econometric", "lag", "time series"],
    "Value At Risk": ["risk", "loss", "confidence", "percentile"],
    "Machine Learning": ["algorithm", "training", "prediction", "model", "neural"],
    "Maximum Likelihood": ["estimation", "statistical", "parameter"],
    "At The Money": ["option", "strike", "call", "put"],
    "Automated Teller Machine": ["bank", "cash", "withdraw"],
    "Implied Volatility": ["option", "vega", "skew"],
    "Independent Variable": ["regression", "predictor", "feature"],
}


def preprocess_unambiguous(text: str) -> str:
    """
    Expand unambiguous abbreviations in text.

    Example:
        "BTC is rising" -> "Bitcoin (BTC) is rising"
    """
    expanded = text
    keys_sorted = sorted(UNAMBIGUOUS_SYNONYMS.keys(), key=len, reverse=True)
    pattern_str = r'\b(' + '|'.join(map(re.escape, keys_sorted)) + r')\b'

    for abbr in set(re.findall(pattern_str, text)):
        full = UNAMBIGUOUS_SYNONYMS[abbr]
        expanded = re.sub(
            r'\b' + re.escape(abbr) + r'\b',
            f"{full} ({abbr})",
            expanded,
            count=1
        )
    return expanded


def build_disambiguation_hints(text: str) -> str:
    """
    Build disambiguation hints for ambiguous terms found in text.

    Returns a string with hints for the AI to use during extraction.
    """
    detected = []

    for abbr, meanings in AMBIGUOUS_TERMS.items():
        if re.search(r'\b' + re.escape(abbr) + r'\b', text):
            hint = f"- '{abbr}' could be: {', '.join(meanings)}"
            for m in meanings:
                if m in CONTEXT_HINTS:
                    hint += f"\n  -> Use '{m}' if you see: {', '.join(CONTEXT_HINTS[m])}"
            detected.append(hint)

    return "\n".join(detected) if detected else ""


def get_canonical_map() -> Dict[str, str]:
    """
    Build a complete map of terms to their canonical names.

    Returns a dict where both abbreviations and full names map to canonical forms.
    """
    canonical_map = {}

    # Add unambiguous mappings
    for abbr, full_name in UNAMBIGUOUS_SYNONYMS.items():
        canonical_map[abbr.upper()] = full_name
        if full_name.upper() not in canonical_map:
            canonical_map[full_name.upper()] = full_name

    # Add ambiguous term meanings (first meaning as default if not already present)
    for abbr, meanings in AMBIGUOUS_TERMS.items():
        for meaning in meanings:
            if meaning.upper() not in canonical_map:
                canonical_map[meaning.upper()] = meaning

    return canonical_map


def normalize_concept(name: str, context: Optional[str] = None) -> str:
    """
    Normalize a concept name to its canonical form.

    Args:
        name: The concept name to normalize
        context: Optional surrounding text for disambiguation

    Returns:
        The canonical name for the concept
    """
    upper_name = name.upper().strip()

    # Check unambiguous synonyms first
    if upper_name in UNAMBIGUOUS_SYNONYMS:
        return UNAMBIGUOUS_SYNONYMS[upper_name]

    # Check ambiguous terms
    if upper_name in AMBIGUOUS_TERMS:
        meanings = AMBIGUOUS_TERMS[upper_name]

        # If context provided, try to disambiguate
        if context:
            context_lower = context.lower()
            for meaning in meanings:
                if meaning in CONTEXT_HINTS:
                    hints = CONTEXT_HINTS[meaning]
                    if any(hint in context_lower for hint in hints):
                        return meaning

        # Default to first meaning
        return meanings[0]

    # Return as-is if not found
    return name.strip()


def is_stopword(word: str) -> bool:
    """Check if a word is a stopword."""
    return word.upper() in STOPWORDS


def filter_stopwords(words: List[str]) -> List[str]:
    """Remove stopwords from a list of words."""
    return [w for w in words if not is_stopword(w)]
