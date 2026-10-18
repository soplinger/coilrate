# coilrate

**Design helical compression springs from one plain-text file, offline.**

coilrate reads a short spec describing a spring's wire, coil diameter, active coils, ends and working loads. It returns the spring rate, spring index, Wahl factor, solid height, shear stress at solid, safety factor and buckling risk. It also solves for the coil count or wire size that hits a target rate, and ranks standard wire sizes against your limits. It is written in Python with the standard library only: no servers, no accounts, no network calls.

## Features

- **Full design report** (`check`): spring index, Wahl factor, total and active coils, solid height, rate, shear at solid, allowable shear and safety factor, all in one readable report.
- **JSON output** (`check --json`) for scripts and spreadsheets.
- **CI safety gate** (`check --min-safety 1.2`): exits 1 when the solid-shear safety factor falls below your minimum.
- **Line-numbered linting** (`lint`): unknown keys, duplicates, non-numeric values, malformed loads and missing required keys, each reported as `file:line`.
- **Strict lint mode** (`lint --strict`): turns warnings into failures for CI.
- **Geometry warnings**: spring index outside 4–12, fewer than two active coils, and free length long enough to risk buckling.
- **Solid-height check**: refuses specs whose free length does not clear the solid height.
- **Two-load rate test** (`rate`): compares the formula rate with the rate measured from two working loads, with a ±5% tolerance and PASS/FAIL.
- **Working-load shear** (`check`): flags any working load whose shear stress exceeds the allowable.
- **Force at a length** (`load FILE LENGTH`): the force and shear at any installed length between solid height and free length.
- **Solve active coils** (`coils FILE RATE`): the exact coil count for a target rate, rounded to the nearest half coil.
- **Solve wire diameter** (`wire FILE RATE`): the exact wire diameter for a target rate, plus the next standard size up.
- **Pick a wire** (`pick FILE RATE`): tries every standard wire size, prints a comparison table and recommends the safest candidate that meets the rate tolerance, spring-index range and safety minimum.
- **Built-in materials** (`materials`): music wire, hard-drawn carbon steel and stainless 302, each with shear modulus and tensile-strength coefficients.

## Quick start

Save a spec as `valve.spr` (or use the bundled `examples/valve.spr`):

```text
# Intake valve spring for a small four-stroke engine
name = Intake valve spring
material = music-wire
wire = 2.0           # mm
mean_diameter = 16   # mm, mean coil diameter
active_coils = 6
ends = closed
free_length = 35     # mm, installed free length
load = 40 @ 29       # N at mm (valve open part way)
load = 80 @ 23       # N at mm (valve fully open)
```

Lint it, check the rate against the two-load test, and get the full report:

```bash
python3 coilrate.py lint examples/valve.spr
# examples/valve.spr: ok

python3 coilrate.py rate examples/valve.spr
# formula rate   6.6488 N/mm
# measured rate  6.6667 N/mm
# difference     +0.27% (tolerance ±5%)
# result         PASS

python3 coilrate.py check examples/valve.spr --min-safety 1.1
```

Find the best standard wire for a 6.5 N/mm target:

```bash
python3 coilrate.py pick examples/valve.spr 6.5
```

## Spec file format

One `key = value` pair per line. `#` starts a comment.

| Key | Meaning | Required |
| --- | --- | --- |
| `name` | label for reports | no |
| `material` | `music-wire`, `hard-drawn` or `stainless-302` (default `music-wire`) | no |
| `wire` | wire diameter, mm | yes |
| `mean_diameter` | mean coil diameter, mm | yes |
| `active_coils` | number of active coils | yes |
| `ends` | `open` or `closed` | yes |
| `free_length` | unloaded length, mm | yes |
| `load` | `FORCE @ LENGTH` in N at mm. Repeat for each working point | no |

## Commands

| Command | Purpose |
| --- | --- |
| `check FILE [--json] [--min-safety X]` | design report, optional JSON and safety gate |
| `lint FILE [--strict]` | line-numbered errors and warnings |
| `rate FILE` | formula rate against the two-load test |
| `load FILE LENGTH` | force and shear at an installed length |
| `coils FILE RATE` | active coils for a target rate |
| `wire FILE RATE` | wire diameter for a target rate |
| `pick FILE RATE [--tolerance F] [--min-safety X]` | rank standard wire sizes |
| `materials` | list built-in materials |

Exit codes: `0` success, `1` lint errors, a failed gate, a failed rate test or an overstressed load, `2` usage error or unreadable file.

## Formulas and assumptions

- Spring rate: `k = G·d⁴ / (8·D³·Na)`
- Spring index: `C = D / d`; Wahl factor: `K = (4C − 1)/(4C − 4) + 0.615/C`
- Shear stress: `τ = K·8·F·D / (π·d³)`
- Solid height: `(Na + 2)·d` for closed ends, `Na·d` for open ends
- Ultimate tensile strength: `Sut = A / dᵐ`, using the `A` and `m` coefficients in the built-in table
- Allowable shear: `0.45 × Sut`. The safety factor is the allowable shear divided by the shear at solid.

Material constants are typical handbook values, not a certified data set. Confirm them against your supplier's certificate before you build anything that matters. coilrate does not model fatigue, set removal, end-grinding tolerances or hot-coiled wire. Use it to plan and compare designs, not to sign off a safety-critical part.

## Run

Requires Python 3.9 or later. No installation step is needed:

```bash
python3 coilrate.py --help
```

## Test

Tests use pytest, the only third-party dependency:

```bash
python3 -m pytest -q
```

The suite covers parsing and line-numbered errors, validation rules, the spring formulas, the coil and wire solvers, the wire picker and the CLI exit codes. Tests write only to pytest's temporary directories.

## Project layout

```text
coilrate.py              the whole tool: parser, checks, formulas, CLI
examples/valve.spr       sample spec used in the Quick start
tests/conftest.py        puts the project root on the import path
tests/test_coilrate.py   test suite
```
