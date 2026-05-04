"""Regression tests: pricing-method description honesty.

The default ``american_call`` / ``american_put`` route used to advertise
itself as ``"QuantLib (American, Binomial Tree)"``, but ``_make_american_greeks``
actually calls ``greeks_american_fdm_ql`` — i.e. price comes from the LR
binomial tree while Greeks come from the FDM engine. The label was a
half-truth and risked misrepresenting which engine produced the Greeks
in any downstream report or UI.

These tests pin that:
- the default american_* description names BOTH engines (Tree + FDM);
- explicit-engine selectors keep their honest, unambiguous labels;
- non-American descriptions are unaffected.
"""

from src.engines import router


# ---------- default (auto) american branch — must mention BOTH engines ----------

def test_american_call_default_description_mentions_both_engines():
    desc = router.route("american_call")[2]
    assert "FDM" in desc, f"expected 'FDM' in description, got: {desc!r}"
    assert ("Tree" in desc) or ("tree" in desc), (
        f"expected 'Tree'/'tree' in description, got: {desc!r}"
    )


def test_american_put_default_description_mentions_both_engines():
    desc = router.route("american_put")[2]
    assert "FDM" in desc, f"expected 'FDM' in description, got: {desc!r}"
    assert ("Tree" in desc) or ("tree" in desc), (
        f"expected 'Tree'/'tree' in description, got: {desc!r}"
    )


# ---------- spot checks: other descriptions remain truthful ----------

def test_european_call_description_unchanged():
    desc = router.route("european_call")[2]
    assert "European" in desc, f"expected 'European' in description, got: {desc!r}"
    # Plain european routing is analytic / Black-Scholes only — must NOT
    # advertise FDM (no FDM is involved on this path).
    assert "FDM" not in desc, (
        f"european_call should not mention 'FDM' (analytic-only path), got: {desc!r}"
    )


def test_explicit_tree_engine_description_unchanged():
    """``engine='tree'`` is explicitly LR-tree price + LR-tree greeks
    (greeks_ql with is_american=True). It must mention 'Tree' and must
    NOT advertise FDM."""
    desc = router.route_with_engine("american_call", "tree")[2]
    assert ("Tree" in desc) or ("tree" in desc), (
        f"expected 'Tree'/'tree' in description, got: {desc!r}"
    )
    assert "FDM" not in desc, (
        f"engine='tree' should not mention 'FDM' (no FDM on this path), "
        f"got: {desc!r}"
    )


def test_explicit_fdm_engine_description_unchanged():
    """``engine='fdm'`` is FDM for both price and greeks; the existing
    label ``"QuantLib (American, FDM)"`` is honest. Just confirm 'FDM'
    is present."""
    desc = router.route_with_engine("american_call", "fdm")[2]
    assert "FDM" in desc, f"expected 'FDM' in description, got: {desc!r}"
