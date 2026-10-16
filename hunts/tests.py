import io

from django.core.exceptions import ValidationError
from django.test import TestCase

from accounts.models import Team

from . import game, scoring
from .admin import PuzzleForm
from .csv_import import CsvImportError, import_puzzles
from .models import Puzzle, Puzzlehunt


def make_opening_hunt(**hunt_kwargs):
    """Hunt with 3 intro puzzles opened by a start code, then a linear chain.

    I1, I2, I3 (opens_at_start) -> L1 (needs all intro) -> L2 (needs L1)
    """
    hunt = Puzzlehunt.objects.create(
        name="Test", scoring_type=Puzzlehunt.SCORING_TIME, start_code="START", **hunt_kwargs
    )
    intro = [
        Puzzle.objects.create(
            puzzlehunt=hunt, display_id=f"I{i}", name=f"Intro {i}",
            password=f"pw{i}", opens_at_start=True,
        )
        for i in (1, 2, 3)
    ]
    l1 = Puzzle.objects.create(
        puzzlehunt=hunt, display_id="L1", name="Linear 1", arrival_code="L1CODE",
        password="l1", unlock_message="Jděte k fontáně, kód je L1CODE.",
    )
    l1.prerequisites.set(intro)
    l2 = Puzzle.objects.create(
        puzzlehunt=hunt, display_id="L2", name="Linear 2", arrival_code="L2CODE",
        password="l2", unlock_message="Další šifra je u lavičky.",
    )
    l2.prerequisites.set([l1])
    team = Team.objects.create(puzzlehunt=hunt, name="T")
    team.set_password("x")
    team.save()
    return hunt, intro, l1, l2, team


class StartCodeFlowTests(TestCase):
    def test_start_code_opens_all_intro_puzzles_at_once(self):
        hunt, intro, l1, l2, team = make_opening_hunt()
        attempts = game.enter_arrival_code(team, "  start ")
        self.assertEqual({a.puzzle for a in attempts}, set(intro))
        self.assertEqual(game.get_active_attempts(team).count(), 3)

    def test_start_ignores_max_active_puzzles(self):
        hunt, intro, l1, l2, team = make_opening_hunt(max_active_puzzles=1)
        game.enter_arrival_code(team, "START")
        self.assertEqual(game.get_active_attempts(team).count(), 3)

    def test_start_code_cannot_be_reused(self):
        hunt, intro, l1, l2, team = make_opening_hunt()
        game.enter_arrival_code(team, "START")
        with self.assertRaises(game.GameError):
            game.enter_arrival_code(team, "START")

    def test_intro_puzzles_cannot_be_opened_without_start_code(self):
        hunt, intro, l1, l2, team = make_opening_hunt()
        with self.assertRaises(game.GameError):
            game.enter_arrival_code(team, "")
        with self.assertRaises(game.GameError):
            game.enter_arrival_code(team, "L1CODE")  # locked by prerequisites

    def test_next_puzzle_requires_all_intro_passwords(self):
        hunt, intro, l1, l2, team = make_opening_hunt()
        game.enter_arrival_code(team, "START")
        for puzzle in intro[:2]:
            self.assertTrue(game.submit_answer(team, puzzle.pk, puzzle.password).correct)
        with self.assertRaises(game.GameError):
            game.enter_arrival_code(team, "L1CODE")
        self.assertEqual(game.get_unlock_notices(team), [])

        game.submit_answer(team, intro[2].pk, intro[2].password)
        self.assertEqual(game.get_unlock_notices(team), [l1])
        attempts = game.enter_arrival_code(team, "L1CODE")
        self.assertEqual([a.puzzle for a in attempts], [l1])
        # Notice disappears once the team has arrived.
        self.assertEqual(game.get_unlock_notices(team), [])

    def test_chain_after_intro_is_linear(self):
        hunt, intro, l1, l2, team = make_opening_hunt()
        game.enter_arrival_code(team, "START")
        for p in intro:
            game.submit_answer(team, p.pk, p.password)
        game.enter_arrival_code(team, "L1CODE")
        with self.assertRaises(game.GameError):
            game.enter_arrival_code(team, "L2CODE")
        self.assertEqual(game.get_unlock_notices(team), [])
        game.submit_answer(team, l1.pk, "l1")
        self.assertEqual(game.get_unlock_notices(team), [l2])
        game.enter_arrival_code(team, "L2CODE")
        game.submit_answer(team, l2.pk, "l2")
        score = scoring.compute_score(team)
        self.assertEqual((score.solved, score.total), (5, 5))

    def test_hunt_without_start_code_is_unchanged(self):
        hunt = Puzzlehunt.objects.create(name="Plain", scoring_type="points")
        p = Puzzle.objects.create(
            puzzlehunt=hunt, display_id="A", name="A", arrival_code="AC", password="x"
        )
        team = Team.objects.create(puzzlehunt=hunt, name="T")
        self.assertEqual([a.puzzle for a in game.enter_arrival_code(team, "ac")], [p])


class PlayerViewTests(TestCase):
    def _login(self, team):
        from django.core.signing import get_cookie_signer

        from accounts.auth import TEAM_COOKIE_NAME, TEAM_COOKIE_SALT

        signer = get_cookie_signer(salt=TEAM_COOKIE_NAME + TEAM_COOKIE_SALT)
        self.client.cookies[TEAM_COOKIE_NAME] = signer.sign(str(team.pk))

    def test_full_flow_through_views(self):
        hunt, intro, l1, l2, team = make_opening_hunt()
        self._login(team)

        resp = self.client.get("/play/active/")
        self.assertContains(resp, "Zadej startovní heslo")

        resp = self.client.post("/play/arrive/", {"arrival_code": "start"}, follow=True)
        self.assertContains(resp, "Hra začala! Otevřeno šifer: 3.")
        for p in intro:
            self.assertContains(resp, p.name)

        for p in intro:
            resp = self.client.post(
                "/play/answer/", {"puzzle_id": p.pk, "answer": p.password}, follow=True
            )
        self.assertContains(resp, "Odemčeno: L1")
        self.assertContains(resp, "kód je L1CODE")

        resp = self.client.post("/play/arrive/", {"arrival_code": "L1CODE"}, follow=True)
        self.assertContains(resp, "Linear 1")
        self.assertNotContains(resp, "Odemčeno: L1")


class ValidationTests(TestCase):
    def test_arrival_code_required_unless_opens_at_start(self):
        hunt = Puzzlehunt.objects.create(
            name="V", scoring_type="time", start_code="S"
        )
        p = Puzzle(puzzlehunt=hunt, display_id="A", name="A", password="x")
        with self.assertRaises(ValidationError) as ctx:
            p.full_clean()
        self.assertIn("arrival_code", ctx.exception.message_dict)
        p.opens_at_start = True
        p.full_clean()

    def test_opens_at_start_needs_hunt_start_code(self):
        hunt = Puzzlehunt.objects.create(name="V", scoring_type="time")
        p = Puzzle(puzzlehunt=hunt, display_id="A", name="A", password="x", opens_at_start=True)
        with self.assertRaises(ValidationError) as ctx:
            p.full_clean()
        self.assertIn("opens_at_start", ctx.exception.message_dict)

    def test_arrival_code_cannot_equal_start_code(self):
        hunt = Puzzlehunt.objects.create(name="V", scoring_type="time", start_code="Start")
        p = Puzzle(puzzlehunt=hunt, display_id="A", name="A", password="x", arrival_code="start")
        with self.assertRaises(ValidationError):
            p.full_clean()

    def test_several_blank_arrival_codes_allowed(self):
        hunt, intro, *_ = make_opening_hunt()  # three blank codes saved fine
        self.assertEqual(len(intro), 3)

    def test_admin_form_rejects_prerequisites_on_start_puzzle(self):
        hunt, intro, l1, l2, team = make_opening_hunt()
        form = PuzzleForm(
            data={
                "puzzlehunt": hunt.pk, "display_id": "X", "name": "X", "password": "x",
                "base_points": 10, "opens_at_start": "on",
                "prerequisites": [intro[0].pk],
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("prerequisites", form.errors)


class CsvImportTests(TestCase):
    HEADER = (
        "display_id,order,name,arrival_code,arrival_message,opens_at_start,"
        "password,base_points,prerequisites,solve_message,unlock_message,"
        "hint1_text,hint1_cost,hint2_text,hint2_cost,hint3_text,hint3_cost\n"
    )

    def _file(self, rows):
        return io.BytesIO((self.HEADER + rows).encode("utf-8"))

    def test_import_start_and_linear_puzzles(self):
        hunt = Puzzlehunt.objects.create(name="C", scoring_type="time", start_code="S")
        rows = (
            "I1,1,Intro 1,,,ano,a,,,,,,,,,,\n"
            "I2,2,Intro 2,,,ano,b,,,,,,,,,,\n"
            "L1,3,Linear,L1C,,,c,,I1;I2,,Jděte k fontáně,,,,,,\n"
        )
        result = import_puzzles(self._file(rows), puzzlehunt=hunt)
        self.assertEqual(result.created, 3)
        self.assertTrue(hunt.puzzles.get(display_id="I1").opens_at_start)
        l1 = hunt.puzzles.get(display_id="L1")
        self.assertFalse(l1.opens_at_start)
        self.assertEqual(l1.unlock_message, "Jděte k fontáně")
        self.assertEqual(l1.prerequisites.count(), 2)

    def test_import_rejects_start_puzzle_without_hunt_start_code(self):
        hunt = Puzzlehunt.objects.create(name="C", scoring_type="time")
        with self.assertRaises(CsvImportError):
            import_puzzles(self._file("I1,1,Intro,,,yes,a,,,,,,,,,,\n"), puzzlehunt=hunt)

    def test_import_rejects_missing_arrival_code_on_normal_puzzle(self):
        hunt = Puzzlehunt.objects.create(name="C", scoring_type="time", start_code="S")
        with self.assertRaises(CsvImportError):
            import_puzzles(self._file("P1,1,Plain,,,,a,,,,,,,,,,\n"), puzzlehunt=hunt)

    def test_import_rejects_prerequisites_on_start_puzzle(self):
        hunt = Puzzlehunt.objects.create(name="C", scoring_type="time", start_code="S")
        rows = "I1,1,Intro,,,yes,a,,,,,,,,,,\nI2,2,Intro2,,,yes,b,,I1,,,,,,,,\n"
        with self.assertRaises(CsvImportError):
            import_puzzles(self._file(rows), puzzlehunt=hunt)

    def test_sample_template_imports(self):
        from pathlib import Path

        hunt = Puzzlehunt.objects.create(name="C", scoring_type="time", start_code="S")
        path = Path(__file__).resolve().parent.parent / "csv_templates" / "uvod_a_linearni_template.csv"
        with path.open("rb") as f:
            result = import_puzzles(f, puzzlehunt=hunt)
        self.assertEqual(result.created, 6)
        self.assertEqual(hunt.puzzles.filter(opens_at_start=True).count(), 4)
        self.assertEqual(hunt.puzzles.get(display_id="L1").prerequisites.count(), 4)
