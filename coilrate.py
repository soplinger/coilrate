#!/usr/bin/env python3
"""coilrate: helical compression spring calculator driven by plain-text spec files.

Standard library only. Metric units throughout: mm, N, MPa, N/mm.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

__version__ = "1.0.0"

SHEAR_LIMIT_RATIO = 0.45  # design shear limit as a fraction of the ultimate shear strength
SPRING_INDEX_MIN = 4.0
SPRING_INDEX_MAX = 12.0
BUCKLING_RATIO = 4.0  # free length / mean diameter above which buckling is possible
RATE_TOLERANCE = 0.05  # allowed relative difference between formula and measured rate
STANDARD_WIRES = (0.5, 0.8, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0)  # mm
END_TYPES = {"open": 0, "closed": 2}  # extra inactive coils added by each end type
REQUIRED_KEYS = ("wire", "mean_diameter", "active_coils", "ends", "free_length")
NUMBER_KEYS = ("wire", "mean_diameter", "active_coils", "free_length")
KNOWN_KEYS = {"name", "material", "ends", "load", *NUMBER_KEYS}


@dataclass(frozen=True)
class Material:
    name: str
    a: float  # Sut = a / d**m, with d in mm and Sut in MPa
    m: float
    g: float  # shear modulus in MPa


MATERIALS = {
    "music-wire": Material("Music wire (ASTM A228)", 2211.0, 0.145, 81700.0),
    "hard-drawn": Material("Hard-drawn carbon steel (ASTM A227)", 1783.0, 0.190, 79300.0),
    "stainless-302": Material("Stainless steel 302", 1867.0, 0.146, 69000.0),
}


@dataclass(frozen=True)
class Load:
    force: float  # N
    length: float  # installed length in mm
    line: int


@dataclass(frozen=True)
class Issue:
    line: int  # 0 means the problem is with the file as a whole
    level: str  # "error" or "warning"
    message: str


@dataclass
class Spec:
    name: str = "spring"
    material: str = "music-wire"
    wire: float = 0.0
    mean_diameter: float = 0.0
    active_coils: float = 0.0
    ends: str = ""
    free_length: float = 0.0
    loads: list[Load] = field(default_factory=list)
    lines: dict[str, int] = field(default_factory=dict)  # key -> line where it was set


# ---------------------------------------------------------------- parsing

def _number(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(text)
    return value


def parse_spec(text: str) -> tuple[Spec, list[Issue]]:
    """Read a plain-text spec. Returns the spec plus any syntax-level issues."""
    spec = Spec()
    issues: list[Issue] = []
    seen: dict[str, int] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "=" not in line:
            issues.append(Issue(lineno, "error", "expected 'key = value'"))
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        key = key.lower()
        if key not in KNOWN_KEYS:
            issues.append(Issue(lineno, "error", f"unknown key '{key}'"))
            continue
        if key == "load":
            force_text, sep, length_text = value.partition("@")
            try:
                if not sep:
                    raise ValueError(value)
                spec.loads.append(Load(_number(force_text), _number(length_text), lineno))
            except ValueError:
                issues.append(Issue(lineno, "error", "load must look like 'FORCE @ LENGTH', e.g. 120 @ 45"))
            continue
        if key in seen:
            issues.append(Issue(lineno, "error", f"duplicate key '{key}' (first set on line {seen[key]})"))
            continue
        seen[key] = lineno
        if key == "name":
            spec.name = value
        elif key in ("material", "ends"):
            setattr(spec, key, value.lower())
        else:
            try:
                setattr(spec, key, _number(value))
            except ValueError:
                issues.append(Issue(lineno, "error", f"'{key}' must be a number, got '{value}'"))
                continue
        spec.lines[key] = lineno
    for key in REQUIRED_KEYS:
        if key not in seen:
            issues.append(Issue(0, "error", f"missing required key '{key}'"))
    return spec, issues


# ---------------------------------------------------------------- checks

def validate(spec: Spec) -> list[Issue]:
    """Semantic checks: positivity, spring index, coil count, solid height, loads."""
    issues: list[Issue] = []

    def add(level: str, key: str, message: str, line: int | None = None) -> None:
        where = line if line is not None else spec.lines.get(key, 0)
        issues.append(Issue(where, level, message))

    def given(key: str) -> bool:
        return key in spec.lines

    if given("material") and spec.material not in MATERIALS:
        add("error", "material", f"unknown material '{spec.material}'; run 'coilrate materials'")
    if given("ends") and spec.ends not in END_TYPES:
        add("error", "ends", "ends must be 'open' or 'closed'")
    if given("wire") and spec.wire <= 0:
        add("error", "wire", "wire diameter must be positive")
    if given("mean_diameter") and spec.mean_diameter <= 0:
        add("error", "mean_diameter", "mean diameter must be positive")
    if given("active_coils") and spec.active_coils <= 0:
        add("error", "active_coils", "active coils must be positive")
    if given("free_length") and spec.free_length <= 0:
        add("error", "free_length", "free length must be positive")

    if spec.wire > 0 and spec.mean_diameter > 0:
        index = spec.mean_diameter / spec.wire
        if index <= 1:
            add("error", "mean_diameter", "mean diameter must be larger than the wire diameter")
        elif index < SPRING_INDEX_MIN:
            add("warning", "mean_diameter", f"spring index {index:.2f} is below {SPRING_INDEX_MIN:g}: hard to coil")
        elif index > SPRING_INDEX_MAX:
            add("warning", "mean_diameter", f"spring index {index:.2f} is above {SPRING_INDEX_MAX:g}: unstable, tangles easily")

    if spec.active_coils > 0 and spec.active_coils < 2:
        add("warning", "active_coils", "fewer than 2 active coils gives an unreliable rate")

    solid = None
    if spec.wire > 0 and spec.active_coils > 0 and spec.ends in END_TYPES:
        solid = solid_height(spec)
        if spec.free_length > 0 and spec.free_length <= solid:
            add("error", "free_length", f"free length {spec.free_length:g} mm must exceed solid height {solid:.3f} mm")

    if spec.mean_diameter > 0 and spec.free_length > BUCKLING_RATIO * spec.mean_diameter:
        ratio = spec.free_length / spec.mean_diameter
        add("warning", "free_length", f"free length is {ratio:.2f}x the mean diameter: buckling is possible unless guided")

    if len({load.length for load in spec.loads}) < 2:
        add("warning", "", "fewer than two working loads at different lengths: rate cannot be measured", line=0)
    for load in spec.loads:
        if load.force <= 0:
            add("error", "", "load force must be positive", line=load.line)
        if load.length <= 0:
            add("error", "", "load length must be positive", line=load.line)
        elif spec.free_length > 0 and load.length >= spec.free_length:
            add("error", "", f"load length {load.length:g} mm must be shorter than free length {spec.free_length:g} mm", line=load.line)
        elif solid is not None and load.length <= solid:
            add("error", "", f"load length {load.length:g} mm is at or below solid height {solid:.3f} mm", line=load.line)
    return sorted(issues, key=lambda issue: issue.line)


# ---------------------------------------------------------------- formulas

def wahl_factor(index: float) -> float:
    return (4 * index - 1) / (4 * index - 4) + 0.615 / index


def spring_rate(shear_modulus: float, wire: float, mean_diameter: float, active_coils: float) -> float:
    return shear_modulus * wire**4 / (8 * mean_diameter**3 * active_coils)


def total_coils(spec: Spec) -> float:
    return spec.active_coils + END_TYPES[spec.ends]


def solid_height(spec: Spec) -> float:
    return total_coils(spec) * spec.wire


def ultimate_strength(material: Material, wire: float) -> float:
    return material.a / wire**material.m


def allowable_shear(material: Material, wire: float) -> float:
    return SHEAR_LIMIT_RATIO * ultimate_strength(material, wire)


def shear_stress(spec: Spec, force: float) -> float:
    """Corrected shear stress (Wahl factor) in MPa at a given force."""
    index = spec.mean_diameter / spec.wire
    return wahl_factor(index) * 8 * force * spec.mean_diameter / (math.pi * spec.wire**3)


def formula_rate(spec: Spec) -> float:
    return spring_rate(MATERIALS[spec.material].g, spec.wire, spec.mean_diameter, spec.active_coils)


def force_at_length(spec: Spec, length: float) -> float:
    return formula_rate(spec) * (spec.free_length - length)


def measured_rate(loads: list[Load]) -> float:
    """Rate from the two working loads furthest apart in length."""
    if len({load.length for load in loads}) < 2:
        raise ValueError("needs two working loads at different lengths")
    short = min(loads, key=lambda load: load.length)
    long = max(loads, key=lambda load: load.length)
    return (short.force - long.force) / (long.length - short.length)


def coils_for_rate(spec: Spec, target: float) -> float:
    material = MATERIALS[spec.material]
    return material.g * spec.wire**4 / (8 * spec.mean_diameter**3 * target)


def wire_for_rate(spec: Spec, target: float) -> float:
    material = MATERIALS[spec.material]
    return (8 * spec.mean_diameter**3 * spec.active_coils * target / material.g) ** 0.25


def round_to_half(value: float) -> float:
    return round(value * 2) / 2


# ---------------------------------------------------------------- analysis

def analyze(spec: Spec) -> dict:
    """Full design numbers for a spec that has passed validate()."""
    material = MATERIALS[spec.material]
    index = spec.mean_diameter / spec.wire
    rate = formula_rate(spec)
    solid = solid_height(spec)
    to_solid = spec.free_length - solid
    force_solid = rate * to_solid
    shear_solid = shear_stress(spec, force_solid)
    allowable = allowable_shear(material, spec.wire)
    loads = []
    for load in spec.loads:
        shear = shear_stress(spec, load.force)
        deflection = spec.free_length - load.length
        loads.append({
            "force_n": load.force,
            "length_mm": load.length,
            "deflection_mm": deflection,
            "shear_mpa": shear,
            "within_allowable": shear <= allowable,
            "implied_rate_n_per_mm": load.force / deflection,
        })
    try:
        measured = measured_rate(spec.loads)
    except ValueError:
        measured = None
    return {
        "name": spec.name,
        "material": spec.material,
        "spring_index": index,
        "wahl_factor": wahl_factor(index),
        "rate_n_per_mm": rate,
        "measured_rate_n_per_mm": measured,
        "total_coils": total_coils(spec),
        "solid_height_mm": solid,
        "deflection_to_solid_mm": to_solid,
        "force_at_solid_n": force_solid,
        "shear_at_solid_mpa": shear_solid,
        "ultimate_tensile_mpa": ultimate_strength(material, spec.wire),
        "allowable_shear_mpa": allowable,
        "safety_factor": allowable / shear_solid,
        "buckling_ratio": spec.free_length / spec.mean_diameter,
        "loads": loads,
    }


def pick_wire(spec: Spec, target: float, tolerance: float, min_safety: float) -> tuple[list[dict], dict | None]:
    """Try every standard wire size at the target rate and recommend the safest that passes."""
    rows = []
    for wire in STANDARD_WIRES:
        trial = replace(spec, wire=wire)
        coils = max(2.0, round_to_half(coils_for_rate(trial, target)))
        candidate = replace(trial, active_coils=coils)
        errors = [issue for issue in validate(candidate) if issue.level == "error"]
        row = {"wire_mm": wire, "active_coils": coils}
        if errors:
            row.update(status="invalid", problem=errors[0].message)
            rows.append(row)
            continue
        result = analyze(candidate)
        rate_error = (result["rate_n_per_mm"] - target) / target
        index_ok = SPRING_INDEX_MIN <= result["spring_index"] <= SPRING_INDEX_MAX
        passed = index_ok and abs(rate_error) <= tolerance and result["safety_factor"] >= min_safety
        row.update(
            status="pass" if passed else "fail",
            rate_n_per_mm=result["rate_n_per_mm"],
            rate_error_pct=rate_error * 100,
            spring_index=result["spring_index"],
            safety_factor=result["safety_factor"],
        )
        rows.append(row)
    passing = [row for row in rows if row["status"] == "pass"]
    best = max(passing, key=lambda row: (row["safety_factor"], -row["wire_mm"])) if passing else None
    return rows, best


# ---------------------------------------------------------------- output

def print_issues(path: Path | str, issues: list[Issue]) -> None:
    for issue in issues:
        where = f"{path}:{issue.line}" if issue.line else f"{path}"
        print(f"{where}: {issue.level}: {issue.message}")


def _table(rows: list[tuple[str, str]], indent: str = "  ") -> list[str]:
    width = max(len(label) for label, _ in rows)
    return [f"{indent}{label.ljust(width)}  {value}" for label, value in rows]


def render_report(spec: Spec, result: dict) -> str:
    material = MATERIALS[spec.material]
    out = [f"{spec.name} ({material.name})", "", "Geometry"]
    out += _table([
        ("wire diameter", f"{spec.wire:.3f} mm"),
        ("mean diameter", f"{spec.mean_diameter:.3f} mm"),
        ("spring index C", f"{result['spring_index']:.3f}"),
        ("Wahl factor K", f"{result['wahl_factor']:.4f}"),
        ("active coils", f"{spec.active_coils:g}"),
        ("total coils", f"{result['total_coils']:g}"),
        ("free length", f"{spec.free_length:.3f} mm"),
        ("solid height", f"{result['solid_height_mm']:.3f} mm"),
        ("free length / D", f"{result['buckling_ratio']:.2f}"),
    ])
    out += ["", "Rate"]
    rate_rows = [("formula rate", f"{result['rate_n_per_mm']:.4f} N/mm")]
    if result["measured_rate_n_per_mm"] is not None:
        measured = result["measured_rate_n_per_mm"]
        diff = (measured - result["rate_n_per_mm"]) / result["rate_n_per_mm"]
        verdict = "PASS" if abs(diff) <= RATE_TOLERANCE else "FAIL"
        rate_rows.append(("measured (2 loads)", f"{measured:.4f} N/mm ({diff * 100:+.2f}%, {verdict})"))
    out += _table(rate_rows)
    out += ["", "Stress at solid"]
    out += _table([
        ("deflection to solid", f"{result['deflection_to_solid_mm']:.3f} mm"),
        ("force at solid", f"{result['force_at_solid_n']:.2f} N"),
        ("shear at solid", f"{result['shear_at_solid_mpa']:.1f} MPa"),
        ("allowable shear", f"{result['allowable_shear_mpa']:.1f} MPa"),
        ("safety factor", f"{result['safety_factor']:.2f}"),
    ])
    if result["loads"]:
        out += ["", "Working loads"]
        for load in result["loads"]:
            status = "OK" if load["within_allowable"] else "OVER LIMIT"
            out.append(
                f"  {load['force_n']:g} N at {load['length_mm']:g} mm: deflection {load['deflection_mm']:.3f} mm, "
                f"shear {load['shear_mpa']:.1f} MPa, {status}"
            )
    return "\n".join(out)


# ---------------------------------------------------------------- commands

def read_spec(path: Path | str) -> tuple[Spec, list[Issue]]:
    text = Path(path).read_text(encoding="utf-8")
    spec, issues = parse_spec(text)
    issues.extend(validate(spec))
    return spec, sorted(issues, key=lambda issue: issue.line)


def _analysable(path: Path | str) -> tuple[Spec | None, list[Issue]]:
    spec, issues = read_spec(path)
    if any(issue.level == "error" for issue in issues):
        print_issues(path, issues)
        print("not analysed: fix the errors above first")
        return None, issues
    return spec, issues


def _positive_rate(value: float) -> bool:
    if value <= 0:
        print("coilrate: target rate must be positive", file=sys.stderr)
        return False
    return True


def _show_candidate(candidate: Spec, target: float) -> int:
    issues = validate(candidate)
    if any(issue.level == "error" for issue in issues):
        for issue in issues:
            print(f"{issue.level}: {issue.message}")
        return 1
    result = analyze(candidate)
    error_pct = (result["rate_n_per_mm"] - target) / target * 100
    print(f"achieved rate     {result['rate_n_per_mm']:.4f} N/mm ({error_pct:+.2f}%)")
    print(f"solid height      {result['solid_height_mm']:.3f} mm")
    print(f"safety factor     {result['safety_factor']:.2f}")
    for issue in issues:
        print(f"{issue.level}: {issue.message}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    spec, issues = _analysable(args.file)
    if spec is None:
        return 1
    result = analyze(spec)
    for load, load_result in zip(spec.loads, result["loads"]):
        if not load_result["within_allowable"]:
            issues.append(Issue(load.line, "warning", f"{load.force:g} N at {load.length:g} mm exceeds the allowable shear"))
    issues = sorted(issues, key=lambda issue: issue.line)
    passed = None if args.min_safety is None else result["safety_factor"] >= args.min_safety
    if args.json:
        payload = {
            "file": str(args.file),
            "analysis": result,
            "issues": [asdict(issue) for issue in issues],
            "gate": None if passed is None else {"min_safety": args.min_safety, "passed": passed},
        }
        print(json.dumps(payload, indent=2))
    else:
        print(render_report(spec, result))
        if issues:
            print()
            print_issues(args.file, issues)
        if passed is not None:
            verdict = "PASS" if passed else "FAIL"
            print(f"\nsafety gate: {result['safety_factor']:.2f} >= {args.min_safety:.2f}: {verdict}")
    return 0 if passed is None or passed else 1


def cmd_lint(args: argparse.Namespace) -> int:
    _, issues = read_spec(args.file)
    print_issues(args.file, issues)
    errors = sum(issue.level == "error" for issue in issues)
    warnings = len(issues) - errors
    if issues:
        print(f"{errors} error(s), {warnings} warning(s)")
    else:
        print(f"{args.file}: ok")
    return 1 if errors or (args.strict and warnings) else 0


def cmd_rate(args: argparse.Namespace) -> int:
    spec, _ = _analysable(args.file)
    if spec is None:
        return 1
    try:
        measured = measured_rate(spec.loads)
    except ValueError as exc:
        print(f"rate: {exc}")
        return 1
    formula = formula_rate(spec)
    diff = (measured - formula) / formula
    passed = abs(diff) <= RATE_TOLERANCE
    print(f"formula rate   {formula:.4f} N/mm")
    print(f"measured rate  {measured:.4f} N/mm")
    print(f"difference     {diff * 100:+.2f}% (tolerance ±{RATE_TOLERANCE * 100:g}%)")
    print(f"result         {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


def cmd_load(args: argparse.Namespace) -> int:
    spec, _ = _analysable(args.file)
    if spec is None:
        return 1
    solid = solid_height(spec)
    if not solid < args.length < spec.free_length:
        print(f"load: length {args.length:g} mm must lie between solid height {solid:.3f} mm "
              f"and free length {spec.free_length:g} mm")
        return 1
    deflection = spec.free_length - args.length
    force = force_at_length(spec, args.length)
    shear = shear_stress(spec, force)
    allowable = allowable_shear(MATERIALS[spec.material], spec.wire)
    within = shear <= allowable
    print(f"length       {args.length:.3f} mm")
    print(f"deflection   {deflection:.3f} mm")
    print(f"force        {force:.3f} N")
    print(f"shear        {shear:.1f} MPa (allowable {allowable:.1f} MPa)")
    print(f"status       {'OK' if within else 'OVER LIMIT'}")
    return 0 if within else 1


def cmd_coils(args: argparse.Namespace) -> int:
    if not _positive_rate(args.rate):
        return 2
    spec, _ = _analysable(args.file)
    if spec is None:
        return 1
    exact = coils_for_rate(spec, args.rate)
    coils = max(2.0, round_to_half(exact))
    print(f"exact active coils for {args.rate:g} N/mm: {exact:.3f}")
    print(f"suggested (nearest half coil, minimum 2): {coils:g}")
    return _show_candidate(replace(spec, active_coils=coils), args.rate)


def cmd_wire(args: argparse.Namespace) -> int:
    if not _positive_rate(args.rate):
        return 2
    spec, _ = _analysable(args.file)
    if spec is None:
        return 1
    exact = wire_for_rate(spec, args.rate)
    print(f"exact wire diameter for {args.rate:g} N/mm: {exact:.4f} mm")
    standard = next((wire for wire in STANDARD_WIRES if wire >= exact), None)
    if standard is None:
        print("no standard size is large enough: use a custom wire")
        return 1
    print(f"next standard size up: {standard:g} mm")
    return _show_candidate(replace(spec, wire=standard), args.rate)


def cmd_pick(args: argparse.Namespace) -> int:
    if not _positive_rate(args.rate):
        return 2
    spec, _ = _analysable(args.file)
    if spec is None:
        return 1
    rows, best = pick_wire(spec, args.rate, args.tolerance, args.min_safety)
    print(f"{'wire':>6}  {'coils':>5}  {'rate':>9}  {'error':>8}  {'C':>6}  {'safety':>6}  status")
    for row in rows:
        if row["status"] == "invalid":
            print(f"{row['wire_mm']:>6g}  {row['active_coils']:>5g}  {'-':>9}  {'-':>8}  {'-':>6}  {'-':>6}  "
                  f"invalid: {row['problem']}")
            continue
        print(f"{row['wire_mm']:>6g}  {row['active_coils']:>5g}  {row['rate_n_per_mm']:>9.4f}  "
              f"{row['rate_error_pct']:>+7.2f}%  {row['spring_index']:>6.2f}  {row['safety_factor']:>6.2f}  "
              f"{row['status']}")
    if best is None:
        print("no standard wire meets every limit: change the free length, mean diameter or material")
        return 1
    print(f"recommended: {best['wire_mm']:g} mm wire, {best['active_coils']:g} active coils")
    return 0


def cmd_materials(_args: argparse.Namespace) -> int:
    for key, material in MATERIALS.items():
        print(f"{key:<15} {material.name:<38} G {material.g / 1000:.1f} GPa  A {material.a:g}  m {material.m:g}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="coilrate",
        description="Helical compression spring calculator driven by plain-text spec files.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="full design report for a spec file")
    p.add_argument("file")
    p.add_argument("--json", action="store_true", help="print the analysis as JSON")
    p.add_argument("--min-safety", type=float, help="exit 1 if the solid-shear safety factor is below this")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("lint", help="line-numbered errors and warnings")
    p.add_argument("file")
    p.add_argument("--strict", action="store_true", help="treat warnings as failures")
    p.set_defaults(func=cmd_lint)

    p = sub.add_parser("rate", help="compare the formula rate with the two-load test in the file")
    p.add_argument("file")
    p.set_defaults(func=cmd_rate)

    p = sub.add_parser("load", help="force and shear at an installed length")
    p.add_argument("file")
    p.add_argument("length", type=float, help="installed length in mm")
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("coils", help="solve active coils for a target rate")
    p.add_argument("file")
    p.add_argument("rate", type=float, help="target rate in N/mm")
    p.set_defaults(func=cmd_coils)

    p = sub.add_parser("wire", help="solve wire diameter for a target rate")
    p.add_argument("file")
    p.add_argument("rate", type=float, help="target rate in N/mm")
    p.set_defaults(func=cmd_wire)

    p = sub.add_parser("pick", help="rank standard wire sizes for a target rate")
    p.add_argument("file")
    p.add_argument("rate", type=float, help="target rate in N/mm")
    p.add_argument("--tolerance", type=float, default=RATE_TOLERANCE,
                   help="allowed rate error as a fraction (default 0.05)")
    p.add_argument("--min-safety", type=float, default=1.0, help="minimum solid-shear safety factor (default 1.0)")
    p.set_defaults(func=cmd_pick)

    p = sub.add_parser("materials", help="list the built-in wire materials")
    p.set_defaults(func=cmd_materials)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except OSError as exc:
        print(f"coilrate: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
