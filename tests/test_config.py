"""
GenerationConfig validation tests.
"""

from shared.models import GenerationConfig


def test_valid_default_config_passes():
    assert GenerationConfig().validate() == []


def test_volume_below_minimum_rejected():
    for vol in [0, 1, -1, 499]:
        errors = GenerationConfig(volume=vol).validate()
        assert any("volume" in e for e in errors), f"Expected error for volume={vol}"


def test_minimum_volume_accepted():
    assert GenerationConfig(volume=500).validate() == []


def test_match_probability_out_of_range():
    errors = GenerationConfig(match_probability=1.5).validate()
    assert any("match_probability" in e for e in errors)


def test_settlement_ratio_out_of_range():
    errors = GenerationConfig(settlement_ratio=-0.1).validate()
    assert any("settlement_ratio" in e for e in errors)


def test_currency_distribution_not_summing_to_one():
    cfg = GenerationConfig(currency_config={"distribution": {"EUR/USD": 0.9, "GBP/USD": 0.5}})
    errors = cfg.validate()
    assert any("currency" in e for e in errors)


def test_trade_type_distribution_not_summing_to_one():
    cfg = GenerationConfig(trade_type_distribution={"Spot": 0.9, "Forward": 0.5})
    errors = cfg.validate()
    assert any("trade_type" in e for e in errors)


def test_invalid_edge_case_name_rejected():
    cfg = GenerationConfig(edge_cases=["not_a_real_edge_case"])
    errors = cfg.validate()
    assert any("edge" in e.lower() for e in errors)


def test_unknown_direction_rejected():
    cfg = GenerationConfig(direction_distribution={"Buy": 0.5, "Hold": 0.5})
    errors = cfg.validate()
    assert any("direction" in e.lower() or "Invalid" in e for e in errors)


def test_from_dict_roundtrip():
    original = GenerationConfig(volume=500, seed=7)
    restored = GenerationConfig.from_dict(original.to_rules())
    assert restored.volume == original.volume
    assert restored.seed == original.seed


def test_scenario_name_preserved_in_from_dict():
    cfg = GenerationConfig.from_dict({"volume": 500, "scenario_name": "stress_test"})
    assert cfg.scenario_name == "stress_test"


def test_reference_date_field_passes_through():
    cfg = GenerationConfig.from_dict({"reference_date": "2023-06-01T09:00:00"})
    assert cfg.reference_date == "2023-06-01T09:00:00"
    assert "reference_date" in cfg.to_rules()
