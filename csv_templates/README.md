# CSV templates

Two templates for bulk-importing into a puzzlehunt. The admin upload form
asks you to pick a target hunt, then accepts the CSV.

- `puzzles_template.csv` — puzzle rows. See column reference below.
- `teams_template.csv` — team rows.

Both files are UTF-8, comma-delimited, standard CSV quoting.

## puzzles.csv columns

| Column          | Required | Notes                                                                  |
| --------------- | -------- | ---------------------------------------------------------------------- |
| `display_id`    | yes      | Unique within hunt. Free-form string.                                  |
| `order`         | no       | Integer. Blank → auto-assigned to lowest unused.                       |
| `name`          | yes      |                                                                        |
| `arrival_code`  | yes      | Unique within hunt.                                                    |
| `password`      | yes      | Answer. Case-insensitive comparison at solve time.                     |
| `base_points`   | no       | Points awarded when this puzzle is solved (points-based hunts only). Defaults to 10. |
| `prerequisites` | no       | Semicolon-separated display_ids, e.g. `P1;P2`.                         |
| `solve_message` | no       | Free text shown after a correct solve. Quote if it contains commas.    |
| `hint1_text`    | no       | Leave blank if no first hint.                                          |
| `hint1_cost`    | no       | Integer (points or minutes — unit follows the hunt's scoring type).    |
| `hint2_text`    | no       | Only if hint 1 is filled.                                              |
| `hint2_cost`    | no       | Required if hint2_text is given.                                       |
| `hint3_text`    | no       | Only if hint 2 is filled.                                              |
| `hint3_cost`    | no       | Required if hint3_text is given.                                       |

**Rules enforced by import:**
- Hints must be sequential (no hint 2 without hint 1).
- Prerequisites must reference other puzzles in the same hunt.
- No prerequisite cycles.
- `display_id` and `arrival_code` must each be unique within the hunt.
- `hintN_cost` is required when `hintN_text` is non-empty.

## teams.csv columns

| Column     | Required | Notes                                                |
| ---------- | -------- | ---------------------------------------------------- |
| `name`     | yes      | Unique within hunt. Case-sensitive.                  |
| `password` | yes      | **Plaintext** here — hashed on import. Delete the file from your computer afterwards. |
