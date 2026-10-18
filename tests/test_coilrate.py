import json
from dataclasses import replace
from pathlib import Path

import pytest

import coilrate
from coilrate import (
    MATERIALS,
    analyze,
    coils_for_rate,
    formula_rate,
    measured_rate,
    parse_spec,
    pick_wire,
    round_to_half,
    shear_stress,
    solid_height,
    ultimate_strength,
    validate,
    wahl_factor,
    wire_for_rate,
)

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "valve.spr"

BASE = """wire = 2
mean_diameter = 16
active_coils = 6
ends = closed
free_length = 35
"""


def _spec(text=BASE):
    spec, issues = parse_spec(text)
    assert issues == []
    return spec


def _errors(issues):
    return [issue for issue in issues if issue.level == "error"]


def _warnings(issues):
    return [issue for issue in issues if issue.level == "warning"]


# ---------------------------------------------------------------- parsing

def test_example_spec_parses_cleanly_with_line_numbers():
    spec, issues = parse_spec(EXAMPLE.read_text(encoding="utf-8"))
    assert issues == []
    assert spec.name == "Intake valve spring"
    assert spec.material == "music-wire"
    assert spec.wire == 2.0
    assert spec.mean_diameter == 16.0
    assert spec.active_coils == 6.0
    assert spec.ends == "closed"
    assert spec.free_length == 35.0
    assert [(load.force, load.length, load.line) for load in spec.loads] == [(40.0, 29.0, 9), (80.0, 23.0, 10)]


def test_unknown_key_is_reported_with_its_line():
    _, issues = parse_spec("wire = 2\nfrobnicate = 3\n")
    assert [(i.line, i.level) for i in issues if "unknown" in i.message] == [(2, "error")]


def test_duplicate_key_points_at_second_occurrence():
    _, issues = parse_spec(BASE + "wire = 3\n")
    dupes = [i for i in issues if "duplicate" in i.message]
    assert len(dupes) == 1
    assert dupes[0].line == 6
    assert "line 1" in dupes[0].message


def test_non_numeric_value_is_an_error_on_its_line():
    _, issues = parse_spec("wire = two\n")
    assert any(i.line == 1 and "must be a number" in i.message for i in issues)


def test_missing_required_key_is_a_file_level_error():
    text = BASE.replace("ends = closed\n", "")
    _, issues = parse_spec(text)
    missing = [i for i in issues if "missing required key" in i.message]
    assert [(i.line, i.message) for i in missing] == [(0, "missing required key 'ends'")]


def test_malformed_load_is_rejected():
    _, issues = parse_spec(BASE + "load = 40\n")
    assert [(i.line, i.level) for i in issues if "load must look like" in i.message] == [(6, "error")]


def test_comments_and_blank_lines_are_ignored():
    spec, issues = parse_spec("# only a comment\n\n" + BASE + "  # trailing note\n")
    assert issues == []
    assert spec.wire == 2.0


# ---------------------------------------------------------------- validation

def test_example_has_no_errors_or_warnings():
    spec, _ = parse_spec(EXAMPLE.read_text(encoding="utf-8"))
    assert validate(spec) == []


def test_free_length_shorter_than_solid_height_is_an_error():
    issues = validate(_spec(BASE.replace("free_length = 35", "free_length = 10")))
    assert any("must exceed solid height" in i.message for i in _errors(issues))


def test_low_spring_index_is_a_warning():
    issues = validate(_spec(BASE.replace("wire = 2", "wire = 5")))
    assert any("below 4" in i.message for i in _warnings(issues))


def test_high_spring_index_is_a_warning():
    issues = validate(_spec(BASE.replace("wire = 2", "wire = 0.5")))
    assert any("above 12" in i.message for i in _warnings(issues))


def test_long_free_length_warns_about_buckling():
    issues = validate(_spec(BASE.replace("free_length = 35", "free_length = 80")))
    assert any("buckling" in i.message for i in _warnings(issues))


def test_load_outside_free_length_is_an_error_on_its_line():
    issues = validate(_spec(BASE + "load = 10 @ 40\n"))
    assert [i.line for i in _errors(issues) if "shorter than free length" in i.message] == [6]


def test_unknown_material_is_an_error():
    issues = validate(_spec(BASE + "material = unobtainium\n"))
    assert any("unknown material" in i.message for i in _errors(issues))


# ---------------------------------------------------------------- formulas

def test_wahl_factor_at_index_eight():
    assert wahl_factor(8) == pytest.approx(1.184018, rel=1e-5)


def test_formula_rate_matches_hand_calculation():
    # k = G d^4 / (8 D^3 Na) = 81700 * 16 / (8 * 4096 * 6) = 6.6488 N/mm
    assert formula_rate(_spec()) == pytest.approx(6.64876, rel=1e-4)


def test_solid_height_counts_closed_ends():
    assert solid_height(_spec()) == pytest.approx(16.0)


def test_open_ends_add_no_inactive_coils():
    spec = _spec(BASE.replace("ends = closed", "ends = open"))
    assert solid_height(spec) == pytest.approx(12.0)


def test_ultimate_strength_for_music_wire_two_mm():
    assert ultimate_strength(MATERIALS["music-wire"], 2.0) == pytest.approx(1999.6, rel=1e-3)


def test_shear_stress_scales_linearly_with_force():
    spec = _spec()
    assert shear_stress(spec, 100.0) == pytest.approx(2 * shear_stress(spec, 50.0))


def test_coils_for_rate_round_trips_to_target_rate():
    spec = _spec()
    trial = replace(spec, active_coils=coils_for_rate(spec, 6.0))
    assert formula_rate(trial) == pytest.approx(6.0)


def test_wire_for_rate_round_trips_to_target_rate():
    spec = _spec()
    trial = replace(spec, wire=wire_for_rate(spec, 6.0))
    assert formula_rate(trial) == pytest.approx(6.0)


def test_round_to_half_rounds_to_nearest_half_coil():
    assert round_to_half(6.138) == 6.0
    assert round_to_half(6.26) == 6.5
    assert round_to_half(5.0) == 5.0


def test_measured_rate_uses_the_two_loads_furthest_apart():
    spec, _ = parse_spec(EXAMPLE.read_text(encoding="utf-8"))
    assert measured_rate(spec.loads) == pytest.approx(40.0 / 6.0)


def test_measured_rate_needs_two_distinct_lengths():
    spec = _spec(BASE + "load = 40 @ 29\n")
    with pytest.raises(ValueError):
        measured_rate(spec.loads)


# ---------------------------------------------------------------- analysis

def test_analysis_of_example_matches_hand_calculation():
    spec, _ = parse_spec(EXAMPLE.read_text(encoding="utf-8"))
    result = analyze(spec)
    assert result["spring_index"] == pytest.approx(8.0)
    assert result["solid_height_mm"] == pytest.approx(16.0)
    assert result["deflection_to_solid_mm"] == pytest.approx(19.0)
    assert result["allowable_shear_mpa"] == pytest.approx(899.8, rel=1e-3)
    assert result["safety_factor"] == pytest.approx(1.18, rel=1e-2)
    assert all(load["within_allowable"] for load in result["loads"])


def test_overstressed_load_is_flagged():
    spec = _spec(BASE + "load = 400 @ 25\n")
    result = analyze(spec)
    assert result["loads"][0]["within_allowable"] is False


def test_measured_rate_is_none_without_two_loads():
    assert analyze(_spec())["measured_rate_n_per_mm"] is None


def test_pick_recommends_two_mm_wire_for_six_point_five_n_per_mm():
    spec, _ = parse_spec(EXAMPLE.read_text(encoding="utf-8"))
    rows, best = pick_wire(spec, 6.5, coilrate.RATE_TOLERANCE, 1.0)
    assert len(rows) == len(coilrate.STANDARD_WIRES)
    assert best is not None
    assert best["wire_mm"] == 2.0
    assert best["active_coils"] == 6.0


# ---------------------------------------------------------------- CLI

def test_check_example_exits_zero_and_prints_report(capsys):
    assert coilrate.main(["check", str(EXAMPLE)]) == 0
    out = capsys.readouterr().out
    assert "formula rate" in out
    assert "safety factor" in out


def test_check_min_safety_gate_fails_when_threshold_is_high(capsys):
    assert coilrate.main(["check", str(EXAMPLE), "--min-safety", "5"]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_check_json_is_machine_readable(capsys):
    assert coilrate.main(["check", str(EXAMPLE), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["analysis"]["rate_n_per_mm"] == pytest.approx(6.64876, rel=1e-4)
    assert payload["gate"] is None


def test_lint_reports_line_numbers_and_fails(tmp_path, capsys):
    bad = tmp_path / "bad.spr"
    bad.write_text("wire = 2\nfrobnicate = 3\n", encoding="utf-8")
    assert coilrate.main(["lint", str(bad)]) == 1
    assert f"{bad}:2: error: unknown key 'frobnicate'" in capsys.readouterr().out


def test_lint_passes_clean_file(capsys):
    assert coilrate.main(["lint", str(EXAMPLE)]) == 0
    assert "ok" in capsys.readouterr().out


def test_rate_command_passes_for_consistent_loads(capsys):
    assert coilrate.main(["rate", str(EXAMPLE)]) == 0
    assert "PASS" in capsys.readouterr().out


def test_rate_command_fails_for_inconsistent_loads(tmp_path, capsys):
    spec_file = tmp_path / "off.spr"
    spec_file.write_text(BASE + "load = 40 @ 29\nload = 80 @ 22\n", encoding="utf-8")
    assert coilrate.main(["rate", str(spec_file)]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_load_command_gives_force_inside_the_spring(capsys):
    assert coilrate.main(["load", str(EXAMPLE), "30"]) == 0
    out = capsys.readouterr().out
    assert "deflection   5.000 mm" in out
    assert "status       OK" in out


def test_load_command_rejects_length_inside_solid_height(capsys):
    assert coilrate.main(["load", str(EXAMPLE), "10"]) == 1
    assert "must lie between solid height" in capsys.readouterr().out


def test_coils_command_suggests_six_coils_for_six_point_five(capsys):
    assert coilrate.main(["coils", str(EXAMPLE), "6.5"]) == 0
    out = capsys.readouterr().out
    assert "suggested (nearest half coil, minimum 2): 6" in out


def test_wire_command_picks_next_standard_size(capsys):
    assert coilrate.main(["wire", str(EXAMPLE), "6.5"]) == 0
    assert "next standard size up: 2 mm" in capsys.readouterr().out


def test_pick_command_recommends_a_wire(capsys):
    assert coilrate.main(["pick", str(EXAMPLE), "6.5"]) == 0
    assert "recommended: 2 mm wire, 6 active coils" in capsys.readouterr().out


def test_nonpositive_target_rate_is_a_usage_error(capsys):
    assert coilrate.main(["coils", str(EXAMPLE), "0"]) == 2


def test_materials_command_lists_every_material(capsys):
    assert coilrate.main(["materials"]) == 0
    out = capsys.readouterr().out
    for key in MATERIALS:
        assert key in out


def test_missing_file_is_a_usage_error(tmp_path, capsys):
    assert coilrate.main(["check", str(tmp_path / "nope.spr")]) == 2
    assert "nope.spr" in capsys.readouterr().err
