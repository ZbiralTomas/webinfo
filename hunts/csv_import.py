"""CSV import for puzzles and teams.

Both parsers accept comma- or semicolon-delimited CSVs (auto-detected via
csv.Sniffer, falling back to comma). Files may be encoded as UTF-8 or UTF-8
with BOM (the latter is what Excel/Numbers commonly produce).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from django.contrib.auth.hashers import make_password
from django.db import transaction

from accounts.models import Team
from hunts.models import Hint, Puzzle


PUZZLE_COLUMNS = {
    "display_id",
    "order",
    "name",
    "arrival_code",
    "password",
    "base_points",
    "prerequisites",
    "solve_message",
    "hint1_text",
    "hint1_cost",
    "hint2_text",
    "hint2_cost",
    "hint3_text",
    "hint3_cost",
}

TEAM_COLUMNS = {"name", "password"}


class CsvImportError(Exception):
    """Raised when validation fails. Contains a list of human-readable error lines."""

    def __init__(self, errors):
        self.errors = errors
        super().__init__("\n".join(errors))


@dataclass
class ImportResult:
    created: int = 0
    notes: list[str] = field(default_factory=list)


def _decode(file) -> str:
    raw = file.read()
    if isinstance(raw, str):
        return raw
    # Strip a UTF-8 BOM if present
    return raw.decode("utf-8-sig")


def _read_rows(text: str):
    """Parse CSV into a list of dict rows, autodetecting delimiter (`,` or `;`)."""
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;")
    except csv.Error:
        dialect = csv.excel  # comma fallback
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    rows = []
    for i, row in enumerate(reader, start=2):  # row 1 is the header
        # Strip whitespace from values so trailing spaces don't break things
        cleaned = {
            (k or "").strip(): (v.strip() if isinstance(v, str) else v)
            for k, v in row.items()
        }
        # Skip fully blank rows
        if not any(cleaned.get(c) for c in cleaned):
            continue
        cleaned["_row"] = i
        rows.append(cleaned)
    return reader.fieldnames or [], rows


def _check_columns(fieldnames, required):
    headers = {(h or "").strip() for h in fieldnames}
    missing = required - headers
    extra = headers - required
    errors = []
    if missing:
        errors.append(f"Missing required columns: {sorted(missing)}")
    if extra:
        errors.append(f"Unknown columns (will be ignored): {sorted(extra)}")
    return errors


# ---------------------------------------------------------------------------
# Puzzles
# ---------------------------------------------------------------------------


def import_puzzles(file, *, puzzlehunt) -> ImportResult:
    text = _decode(file)
    fieldnames, rows = _read_rows(text)

    errors = []
    for e in _check_columns(fieldnames, PUZZLE_COLUMNS):
        if e.startswith("Missing"):
            errors.append(e)
    if errors:
        raise CsvImportError(errors)

    # Existing puzzles in this hunt — used for prereq references and
    # uniqueness checks (we'll add CSV rows on top of these).
    existing = {p.display_id: p for p in puzzlehunt.puzzles.all()}
    existing_arrival_codes = {p.arrival_code for p in existing.values()}

    # First pass: validate each row in isolation and collect by display_id.
    parsed = []
    seen_display_ids = set(existing.keys())
    seen_arrival_codes = set(existing_arrival_codes)

    for row in rows:
        rn = row["_row"]

        display_id = row.get("display_id", "")
        if not display_id:
            errors.append(f"Row {rn}: display_id is required")
            continue
        if display_id in seen_display_ids:
            errors.append(
                f"Row {rn}: display_id {display_id!r} already exists in this hunt"
            )
            continue
        seen_display_ids.add(display_id)

        name = row.get("name", "")
        if not name:
            errors.append(f"Row {rn}: name is required")
        arrival_code = row.get("arrival_code", "")
        if not arrival_code:
            errors.append(f"Row {rn}: arrival_code is required")
        elif arrival_code in seen_arrival_codes:
            errors.append(
                f"Row {rn}: arrival_code {arrival_code!r} already in use in this hunt"
            )
            continue
        else:
            seen_arrival_codes.add(arrival_code)

        password = row.get("password", "")
        if not password:
            errors.append(f"Row {rn}: password is required")

        order_raw = row.get("order", "")
        order = None
        if order_raw:
            try:
                order = int(order_raw)
                if order < 1:
                    raise ValueError
            except ValueError:
                errors.append(f"Row {rn}: order must be a positive integer, got {order_raw!r}")
                order = None

        base_points_raw = row.get("base_points", "")
        base_points = 1
        if base_points_raw:
            try:
                base_points = int(base_points_raw)
                if base_points < 0:
                    raise ValueError
            except ValueError:
                errors.append(
                    f"Row {rn}: base_points must be a non-negative integer, got {base_points_raw!r}"
                )

        # Hints: validate sequentiality and cost presence.
        hints = []
        broken = False
        for n in (1, 2, 3):
            text_v = row.get(f"hint{n}_text", "")
            cost_v = row.get(f"hint{n}_cost", "")
            if text_v == "" and cost_v == "":
                # missing → stop; remaining hints must also be missing
                if n == 1:
                    pass  # no hints at all is fine
                # check that no later hints are filled
                for m in range(n + 1, 4):
                    if row.get(f"hint{m}_text", "") or row.get(f"hint{m}_cost", ""):
                        errors.append(
                            f"Row {rn}: hint{m} given but hint{n} is empty (hints must be sequential)"
                        )
                        broken = True
                        break
                break
            if not text_v:
                errors.append(f"Row {rn}: hint{n}_text is empty but hint{n}_cost is filled")
                broken = True
                break
            if not cost_v:
                errors.append(f"Row {rn}: hint{n}_cost is required when hint{n}_text is given")
                broken = True
                break
            try:
                cost_i = int(cost_v)
                if cost_i < 0:
                    raise ValueError
            except ValueError:
                errors.append(
                    f"Row {rn}: hint{n}_cost must be a non-negative integer, got {cost_v!r}"
                )
                broken = True
                break
            hints.append((n, text_v, cost_i))
        if broken:
            continue

        # Prerequisites: parse to a list of display_ids
        prereq_field = row.get("prerequisites", "")
        prereqs = [p.strip() for p in prereq_field.split(";") if p.strip()] if prereq_field else []

        parsed.append(
            {
                "row": rn,
                "display_id": display_id,
                "order": order,
                "name": name,
                "arrival_code": arrival_code,
                "password": password,
                "base_points": base_points,
                "solve_message": row.get("solve_message", ""),
                "prereqs": prereqs,
                "hints": hints,
            }
        )

    # Second pass: cross-row prereq validation (must reference known display_ids,
    # no cycles).
    by_id = {p["display_id"]: p for p in parsed}
    all_known = set(existing.keys()) | set(by_id.keys())

    for p in parsed:
        for ref in p["prereqs"]:
            if ref not in all_known:
                errors.append(
                    f"Row {p['row']}: prerequisite {ref!r} not found in this hunt"
                )

    if not _detect_cycles_ok(parsed, existing, errors):
        pass  # errors already appended by the helper

    if errors:
        raise CsvImportError(errors)

    # Commit. All-or-nothing.
    with transaction.atomic():
        created_puzzles = {}
        for p in parsed:
            puzzle = Puzzle(
                puzzlehunt=puzzlehunt,
                display_id=p["display_id"],
                order=p["order"],
                name=p["name"],
                arrival_code=p["arrival_code"],
                password=p["password"],
                base_points=p["base_points"],
                solve_message=p["solve_message"],
            )
            puzzle.save()
            created_puzzles[p["display_id"]] = puzzle
            for order, text_v, cost_i in p["hints"]:
                Hint.objects.create(puzzle=puzzle, order=order, text=text_v, cost=cost_i)

        # Wire up prerequisites only after all rows exist.
        for p in parsed:
            if not p["prereqs"]:
                continue
            puzzle = created_puzzles[p["display_id"]]
            ref_objs = []
            for ref in p["prereqs"]:
                if ref in created_puzzles:
                    ref_objs.append(created_puzzles[ref])
                else:
                    ref_objs.append(existing[ref])
            puzzle.prerequisites.set(ref_objs)

    return ImportResult(created=len(parsed))


def _detect_cycles_ok(parsed, existing, errors_out) -> bool:
    """Build the eventual prerequisite graph and check it's a DAG."""
    # Adjacency list keyed by display_id; edges X -> Y mean X requires Y.
    edges: dict[str, list[str]] = {}
    for p in parsed:
        edges[p["display_id"]] = list(p["prereqs"])
    for did, puzzle in existing.items():
        edges[did] = [pre.display_id for pre in puzzle.prerequisites.all()]

    WHITE, GREY, BLACK = 0, 1, 2
    color = {n: WHITE for n in edges}

    def dfs(node, stack):
        color[node] = GREY
        stack.append(node)
        for nxt in edges.get(node, []):
            if color.get(nxt) == GREY:
                cycle = stack[stack.index(nxt):] + [nxt]
                errors_out.append(
                    f"Prerequisite cycle detected: {' -> '.join(cycle)}"
                )
                return False
            if color.get(nxt) == WHITE:
                if not dfs(nxt, stack):
                    return False
        stack.pop()
        color[node] = BLACK
        return True

    for n in list(color):
        if color[n] == WHITE:
            if not dfs(n, []):
                return False
    return True


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------


def import_teams(file, *, puzzlehunt) -> ImportResult:
    text = _decode(file)
    fieldnames, rows = _read_rows(text)

    errors = []
    for e in _check_columns(fieldnames, TEAM_COLUMNS):
        if e.startswith("Missing"):
            errors.append(e)
    if errors:
        raise CsvImportError(errors)

    existing_names = set(puzzlehunt.teams.values_list("name", flat=True))
    seen_names = set(existing_names)
    parsed = []
    for row in rows:
        rn = row["_row"]
        name = row.get("name", "")
        password = row.get("password", "")
        if not name:
            errors.append(f"Row {rn}: name is required")
            continue
        if not password:
            errors.append(f"Row {rn}: password is required")
            continue
        if name in seen_names:
            errors.append(f"Row {rn}: team name {name!r} already exists in this hunt")
            continue
        seen_names.add(name)
        parsed.append({"name": name, "password": password})

    if errors:
        raise CsvImportError(errors)

    with transaction.atomic():
        Team.objects.bulk_create(
            [
                Team(
                    puzzlehunt=puzzlehunt,
                    name=p["name"],
                    password=make_password(p["password"]),
                )
                for p in parsed
            ]
        )

    return ImportResult(created=len(parsed))
