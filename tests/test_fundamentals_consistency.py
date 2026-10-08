from analysis.financial_consistency import coherent_yoy_pct


def test_coherent_yoy_repairs_lost_negative_sign():
    current = 23_330_000_000.0
    previous = 25_777_000_000.0

    assert coherent_yoy_pct(current, previous, 9.49) == -9.49


def test_coherent_yoy_keeps_consistent_positive_sign():
    current = 28_224_000_000.0
    previous = 25_777_000_000.0

    assert coherent_yoy_pct(current, previous, 9.49) == 9.49


def test_coherent_yoy_does_not_override_different_calculation_basis():
    current = 23_330_000_000.0
    previous = 25_777_000_000.0

    assert coherent_yoy_pct(current, previous, 30.0) == 30.0
