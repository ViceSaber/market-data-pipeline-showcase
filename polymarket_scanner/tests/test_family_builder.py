"""
test_family_builder — Verify completeness computation and date_scope caching.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.family_builder import _compute_completeness, _classify_family_type


# ─── _compute_completeness Tests ──────────────────────────────

def test_completeness_zero_members():
    """Empty family → 0.0"""
    assert _compute_completeness([], "mutually_exclusive") == 0.0
    print("  ✅ zero members → 0.0")


def test_completeness_single_member():
    """Single member → 0.0"""
    assert _compute_completeness([{"id": "1"}], "inclusion_chain") == 0.0
    print("  ✅ single member → 0.0")


def test_completeness_with_template_max():
    """Use template_max_counts as expected count."""
    members = [{"id": str(i)} for i in range(3)]
    # Template has max 4 members across events, we have 3 → 0.75
    template_max = {"btc_price_usd": 4}
    result = _compute_completeness(members, "inclusion_chain", template_max, "btc_price_usd")
    assert result == 0.75, f"expected 0.75, got {result}"
    print("  ✅ template_max 4, count 3 → 0.75")


def test_completeness_fed_rate_cuts():
    """Fed rate cuts: 5 members out of expected 6 → ~0.83"""
    members = [{"id": str(i)} for i in range(5)]
    template_max = {"fed_rate_cuts_2026": 6}
    result = _compute_completeness(
        members, "inclusion_chain", template_max, "fed_rate_cuts_2026")
    assert 0.83 <= result <= 0.84, f"expected ~0.833, got {result}"
    print("  ✅ fed rate cuts 5/6 → 0.833")


def test_completeness_mutual_exclusive_no_template():
    """Mutual exclusive without template → min expected 2"""
    members = [{"id": "1"}, {"id": "2"}]
    result = _compute_completeness(members, "mutually_exclusive")
    assert result == 1.0, f"expected 1.0 (2/2), got {result}"
    print("  ✅ mutual exclusive 2/2 (no template) → 1.0")


def test_completeness_mutual_exclusive_three():
    """Mutual exclusive 3 members, no template → 3/2 = 1.0 (capped)"""
    members = [{"id": str(i)} for i in range(3)]
    result = _compute_completeness(members, "mutually_exclusive")
    assert result == 1.0, f"expected 1.0 (capped), got {result}"
    print("  ✅ mutual exclusive 3 (no template) → 1.0 (capped)")


def test_completeness_mutual_exclusive_with_template():
    """Mutual exclusive with template: 3 out of 4 expected → 0.75"""
    members = [{"id": str(i)} for i in range(3)]
    template_max = {"nba_winner": 4}
    result = _compute_completeness(
        members, "mutually_exclusive", template_max, "nba_winner")
    assert result == 0.75, f"expected 0.75, got {result}"
    print("  ✅ mutual exclusive 3/4 (template) → 0.75")


def test_completeness_chain_no_template():
    """Chain without template → min expected 2"""
    members = [{"id": str(i)} for i in range(2)]
    result = _compute_completeness(members, "threshold_chain")
    assert result == 1.0, f"expected 1.0, got {result}"
    print("  ✅ threshold_chain 2/2 (no template) → 1.0")


def test_completeness_always_one_point_oh_fixed():
    """Verify the old bug: count/max(2,count) always returning 1.0 is fixed."""
    # Old bug: any count >= 2 returned 1.0
    # New: with template_max, 2 out of 5 should be 0.4
    members = [{"id": "1"}, {"id": "2"}]
    template_max = {"some_template": 5}
    result = _compute_completeness(
        members, "inclusion_chain", template_max, "some_template")
    assert result == 0.4, f"expected 0.4, got {result}"
    print("  ✅ 2/5 members → 0.4 (old bug was always 1.0)")


# ─── _classify_family_type Tests ──────────────────────────────

def test_classify_no_price_threshold():
    """Verify 'price_threshold' basis is never produced (dead code removed)."""
    # over_under should classify as inclusion_chain
    result = _classify_family_type("over_under", [100.0, 200.0], ["yes", "yes"])
    assert result == "inclusion_chain", f"expected inclusion_chain, got {result}"

    # threshold_chain for non-over_under bases with multiple lines
    result = _classify_family_type("win_outright", [1.0, 2.0], ["yes", "yes"])
    assert result == "threshold_chain", f"expected threshold_chain, got {result}"

    print("  ✅ over_under → inclusion_chain, other → threshold_chain")


# ─── Run All ─────────────────────────────────────────────────

if __name__ == "__main__":
    print("Running family_builder tests...\n")
    test_completeness_zero_members()
    test_completeness_single_member()
    test_completeness_with_template_max()
    test_completeness_fed_rate_cuts()
    test_completeness_mutual_exclusive_no_template()
    test_completeness_mutual_exclusive_three()
    test_completeness_mutual_exclusive_with_template()
    test_completeness_chain_no_template()
    test_completeness_always_one_point_oh_fixed()
    test_classify_no_price_threshold()
    print("\n🎉 All family_builder tests passed!")
