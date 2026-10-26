from app import rules
from app.rules import auto_price, compute_status, fee_for, title_check

PR = rules.DEFAULT_PRICING


def P(**kw):
    base = dict(availability="In Stock", inventory=1, source_price=10.0, price="$10.00", delivery="", run_at="2026-01-05 10:00:00", listed=1)
    base.update(kw)
    return base


def test_fee_and_price_match_the_sheet():
    # 16 oz -> tier 16 -> 6.33 + 5 buffer; (10 + 11.33) * 1.53 = 32.63 -> 32 + .99
    assert fee_for(16, PR) == 11.33
    assert auto_price(16, 10, PR) == 32.99
    assert fee_for(15.6, PR) == 11.33          # ROUND() first: 15.6 -> 16
    assert fee_for(4, PR) == 9.36
    assert fee_for(4.4, PR) == 9.36            # rounds down to 4
    assert fee_for(4.5, PR) == 9.68            # rounds up to 5 -> tier 8
    assert fee_for(1000, PR) is None           # heavier than the table
    assert auto_price(None, 10, PR) is None


def test_there_is_one_fee_table():
    assert list(PR["tables"]) == ["current"] and PR["active_table"] == "current"
    # editing the tiers is how future rate changes are made: a typed-in table works the same way
    pr = {**PR, "tables": {"current": [{"max": 8, "fee": 7.0}, {"max": 32, "fee": 12.5}]}}
    assert fee_for(6, pr) == 12.0 and fee_for(9, pr) == 17.5 and fee_for(33, pr) is None


def test_status_order():
    assert compute_status(P(availability="Currently unavailable."), 6) == "Out of Stock"
    assert compute_status(P(availability="Only 3 left in stock"), 6) == "Low Inventory"
    assert compute_status(P(price="$12.00"), 6) == "High Price"          # source 10 < amazon 12
    assert compute_status(P(price="$8.00"), 6) == "Low Price"
    assert compute_status(P(availability="Availability not found"), 6) == "Manual Check"
    assert compute_status(P(inventory=0), 6) == "Check Inventory"
    assert compute_status(P(inventory=0, availability="Currently unavailable."), 6) == "Normal"
    assert compute_status(P(price="$10.00"), 6) == "Normal"
    assert compute_status(P(availability=""), 6) == "Not checked"


def test_timeframe_beats_everything():
    p = P(availability="Currently unavailable.", delivery="Delivery January 20", run_at="2026-01-05 10:00:00")
    assert compute_status(p, 6) == "TimeFrame"
    assert compute_status(p, 30) == "Out of Stock"


def test_unchecking_inventory_resolves_a_timeframe_issue():
    slow = dict(delivery="Delivery January 20", run_at="2026-01-05 10:00:00")
    assert compute_status(P(**slow), 6) == "TimeFrame"
    assert compute_status(P(inventory=0, **slow), 6) != "TimeFrame"          # unchecked: not a TimeFrame issue any more
    assert compute_status(P(inventory=0, availability="", **slow), 6) == "Not checked"


def test_lead_days_year_rollover():
    d = rules.lead_days("Delivery January 3", "2025-12-30 00:00:00")
    assert d == 4.0


def test_title_check():
    s, _ = title_check("Acrylic Paint Set 12 Pack", "Acrylic Paint Set, 12 Pack Art Supplies")
    assert s >= 80
    s, note = title_check("Acrylic Paint Set 12 Pack", "Acrylic Paint Set 24 Pack")
    assert s <= 40 and "Pack size differs" in note
    assert title_check("Something", "")[0] is None
    s, _ = title_check("Garden Hose Nozzle", "Wireless Bluetooth Headphones")
    assert s < 20


def test_scrap_price_and_code_stripping():
    assert rules.scrap_price("Now $1,234.50 only") == 1234.5
    assert rules.scrap_price("Price not found") is None
    assert rules.strip_code("Arrives Tue (function(){ var x=1 })") == "Arrives Tue"


def test_oos_variation_compares_meaning_not_words():
    from app.variations import differs
    assert differs("Large, 12pcs", "Color: 12pcs/Size: Medium")                       # the size differs
    assert not differs("Large, 12pcs", "Size: Large/Number of Items: 12")             # same facts, worded differently
    assert not differs("12 pack", "Number of Items: 12")
    assert differs("Red", "Color: Blue") and not differs("Gray", "Color: Grey")
    assert not differs("Multi", "Color: Multi-colored")
    assert differs("12pcs", "Color: White/Number of Items: 24")
    assert not differs("13x10x3.5", "Color: White/Size: 13*10*3.5inch/Number of Items: 1")
    assert not differs("", "Color: Red") and not differs("Bunny3", "")                 # one side blank: never flagged
    assert not differs("Red 12pcs", "Color: Red")                                        # one side says less: not a disagreement
    assert not differs("Red", "Color: Red/Number of It…")                                # cut-off value


def test_new_source_statuses():
    ok = dict(title="Wooden Frame 8 inch", title_ok="", title_conflict=0, title_score=90, variation="Large", amazon_variation="Size: Large")
    assert compute_status(P(**ok), 6) == "Normal"
    assert compute_status(P(**{**ok, "amazon_variation": "Size: Small"}), 6) == "OOS Variation"
    assert compute_status(P(**{**ok, "amazon_variation": "Size: Small", "price": "$12.00"}), 6) == "OOS Variation"   # beats High Price
    assert compute_status(P(**{**ok, "title_conflict": 1}), 6) == "OOS Different Item"
    assert compute_status(P(**{**ok, "title_conflict": 1, "amazon_variation": "Size: Small"}), 6) == "OOS Different Item"
    assert compute_status(P(**{**ok, "title_conflict": 1, "title_ok": ok["title"]}), 6) == "Normal"          # team accepted it
    assert compute_status(P(**{**ok, "title_conflict": 1, "inventory": 0}), 6) == "Check Inventory"          # inventory off: nothing to turn off
    assert compute_status(P(**{**ok, "title_score": 30}), 6) == "Manual Check"                               # title mismatch
    assert compute_status(P(**{**ok, "title_score": 30, "price": "$12.00"}), 6) == "High Price"              # a real price issue comes first
    assert compute_status(P(**{**ok, "title_score": 30}), 6, title_threshold=20) == "Normal"
