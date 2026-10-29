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


def attempt_of(team, puzzle):
    """The team's attempt on `puzzle`, created if the team hasn't arrived yet."""
    from .models import PuzzleAttempt

    return PuzzleAttempt.objects.get_or_create(team=team, puzzle=puzzle)[0]


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


class DiacriticsTests(TestCase):
    def _team(self, password="příklad", **puzzle_kwargs):
        hunt = Puzzlehunt.objects.create(
            name="D", scoring_type="time", start_code="Začátek"
        )
        puzzle = Puzzle.objects.create(
            puzzlehunt=hunt, display_id="A", name="A", arrival_code="Příchod",
            password=password, **puzzle_kwargs,
        )
        team = Team.objects.create(puzzlehunt=hunt, name="T")
        return hunt, puzzle, team

    def _expire_cooldown(self, team):
        from datetime import timedelta

        from django.utils import timezone

        team.answer_attempts.update(submitted_at=timezone.now() - timedelta(minutes=5))

    def test_answer_without_diacritics_is_wrong(self):
        hunt, puzzle, team = self._team()
        game.enter_arrival_code(team, "Příchod")
        self.assertFalse(game.submit_answer(team, puzzle.pk, "priklad").correct)
        self._expire_cooldown(team)
        self.assertFalse(game.submit_answer(team, puzzle.pk, "příklád").correct)
        self._expire_cooldown(team)
        self.assertTrue(game.submit_answer(team, puzzle.pk, "příklad").correct)

    def test_answer_is_still_case_insensitive(self):
        hunt, puzzle, team = self._team()
        game.enter_arrival_code(team, "Příchod")
        self.assertTrue(game.submit_answer(team, puzzle.pk, "  PŘÍKLAD ").correct)

    def test_decomposed_accents_match_composed(self):
        import unicodedata

        hunt, puzzle, team = self._team()
        game.enter_arrival_code(team, "Příchod")
        typed = unicodedata.normalize("NFD", "příklad")
        self.assertNotEqual(typed, "příklad")
        self.assertTrue(game.submit_answer(team, puzzle.pk, typed).correct)

    def test_start_code_respects_diacritics(self):
        hunt, puzzle, team = self._team()
        with self.assertRaises(game.GameError):
            game.enter_arrival_code(team, "zacatek")
        self.assertEqual(game.get_active_attempts(team).count(), 0)

    def test_start_code_with_diacritics_works(self):
        hunt = Puzzlehunt.objects.create(name="S", scoring_type="time", start_code="Začátek")
        Puzzle.objects.create(
            puzzlehunt=hunt, display_id="I", name="I", password="x", opens_at_start=True
        )
        team = Team.objects.create(puzzlehunt=hunt, name="T")
        self.assertEqual(len(game.enter_arrival_code(team, "ZAČÁTEK")), 1)

    def test_arrival_code_still_ignores_diacritics(self):
        hunt, puzzle, team = self._team()
        self.assertEqual(len(game.enter_arrival_code(team, "prichod")), 1)


class ArrivalCodeDiacriticsTests(TestCase):
    def _setup(self, diacritics):
        hunt = Puzzlehunt.objects.create(
            name="K", scoring_type="time", arrival_codes_diacritics=diacritics
        )
        puzzle = Puzzle.objects.create(
            puzzlehunt=hunt, display_id="A", name="A", arrival_code="Příchod", password="x"
        )
        team = Team.objects.create(puzzlehunt=hunt, name="T")
        return puzzle, team

    def test_default_ignores_diacritics_and_case(self):
        self.assertFalse(Puzzlehunt._meta.get_field("arrival_codes_diacritics").default)
        puzzle, team = self._setup(diacritics=False)
        self.assertEqual(len(game.enter_arrival_code(team, "PRICHOD")), 1)

    def test_option_rejects_missing_diacritics(self):
        puzzle, team = self._setup(diacritics=True)
        with self.assertRaises(game.GameError):
            game.enter_arrival_code(team, "Prichod")

    def test_option_still_ignores_case(self):
        puzzle, team = self._setup(diacritics=True)
        self.assertEqual([a.puzzle for a in game.enter_arrival_code(team, "  PŘÍCHOD ")], [puzzle])

    def test_option_accepts_decomposed_accents(self):
        import unicodedata

        puzzle, team = self._setup(diacritics=True)
        typed = unicodedata.normalize("NFD", "příchod")
        self.assertEqual([a.puzzle for a in game.enter_arrival_code(team, typed)], [puzzle])

    def test_option_keeps_codes_differing_only_by_diacritics_apart(self):
        hunt = Puzzlehunt.objects.create(
            name="K2", scoring_type="time", arrival_codes_diacritics=True
        )
        a = Puzzle.objects.create(puzzlehunt=hunt, display_id="A", name="A", arrival_code="Kód", password="x")
        b = Puzzle.objects.create(puzzlehunt=hunt, display_id="B", name="B", arrival_code="kod", password="y")
        team = Team.objects.create(puzzlehunt=hunt, name="T")
        self.assertEqual(game.enter_arrival_code(team, "KOD")[0].puzzle, b)
        self.assertEqual(game.enter_arrival_code(team, "kód")[0].puzzle, a)


class TimedSkipTests(TestCase):
    """Hunt-wide skip that opens `skip_min_minutes` after arriving (the "totálka")."""

    def _setup(self, **hunt_kwargs):
        hunt = Puzzlehunt.objects.create(
            name="Totálka", scoring_type="time", allow_skip=True,
            skip_min_minutes=90, **hunt_kwargs,
        )
        puzzle = Puzzle.objects.create(
            puzzlehunt=hunt, display_id="P1", name="P1", arrival_code="K1", password="x"
        )
        team = Team.objects.create(puzzlehunt=hunt, name="T")
        game.enter_arrival_code(team, "K1")
        return hunt, puzzle, team

    def _age(self, team, minutes):
        from datetime import timedelta

        from django.utils import timezone

        team.puzzle_attempts.update(arrived_at=timezone.now() - timedelta(minutes=minutes))

    def test_skip_locked_before_threshold(self):
        hunt, puzzle, team = self._setup()
        self._age(team, 89)
        attempt = team.puzzle_attempts.get()
        self.assertFalse(game.can_skip(team, attempt))
        with self.assertRaises(game.GameError) as ctx:
            game.skip_puzzle(team, puzzle.pk)
        self.assertIn("~", str(ctx.exception))
        self.assertFalse(team.puzzle_attempts.get().skipped)

    def test_skip_open_after_threshold(self):
        hunt, puzzle, team = self._setup()
        self._age(team, 91)
        self.assertTrue(game.can_skip(team, team.puzzle_attempts.get()))
        game.skip_puzzle(team, puzzle.pk)
        self.assertTrue(team.puzzle_attempts.get().skipped)

    def test_threshold_counts_from_arrival_at_each_puzzle(self):
        hunt, puzzle, team = self._setup()
        p2 = Puzzle.objects.create(
            puzzlehunt=hunt, display_id="P2", name="P2", arrival_code="K2", password="y"
        )
        self._age(team, 200)
        game.enter_arrival_code(team, "K2")  # just arrived, own clock
        self.assertFalse(game.can_skip(team, team.puzzle_attempts.get(puzzle=p2)))
        self.assertTrue(game.can_skip(team, team.puzzle_attempts.get(puzzle=puzzle)))

    def test_default_zero_means_skip_anytime(self):
        hunt, puzzle, team = self._setup()
        hunt.skip_min_minutes = 0
        hunt.save()
        team.refresh_from_db()
        self.assertTrue(game.can_skip(team, team.puzzle_attempts.get()))

    def test_skip_disabled_when_allow_skip_off(self):
        hunt, puzzle, team = self._setup()
        hunt.allow_skip = False
        hunt.save()
        team.refresh_from_db()
        self._age(team, 500)
        self.assertFalse(game.can_skip(team, team.puzzle_attempts.get()))
        with self.assertRaises(game.GameError):
            game.skip_puzzle(team, puzzle.pk)


class IntroSkipScheduleTests(TestCase):
    """3 intro puzzles; after 45 min 2 solved suffice, after 75 min 1, after 90 min 0."""

    def _started(self):
        hunt, intro, l1, l2, team = make_opening_hunt()
        intro_3 = intro[:3]  # make_opening_hunt creates exactly 3 intro puzzles
        hunt.intro_skip_times = "45, 75, 90"
        hunt.save()
        team.refresh_from_db()
        game.enter_arrival_code(team, "START")
        return hunt, intro_3, l1, team

    def _age(self, team, minutes):
        from datetime import timedelta

        from django.utils import timezone

        team.puzzle_attempts.update(arrived_at=timezone.now() - timedelta(minutes=minutes))

    def _blocked(self, team):
        with self.assertRaises(game.GameError):
            game.enter_arrival_code(team, "L1CODE")

    def test_credits_follow_schedule(self):
        hunt, intro, l1, team = self._started()
        expected = {44: 0, 46: 1, 74: 1, 76: 2, 89: 2, 91: 3, 500: 3}
        for minutes, available in expected.items():
            self._age(team, minutes)
            self.assertEqual(game.intro_skip_status(team).available, available, minutes)

    def test_after_45_min_two_solved_are_enough(self):
        hunt, intro, l1, team = self._started()
        game.submit_answer(team, intro[0].pk, intro[0].password)
        game.submit_answer(team, intro[1].pk, intro[1].password)
        self._blocked(team)
        self._age(team, 46)
        game.skip_puzzle(team, intro[2].pk)
        self.assertEqual(len(game.enter_arrival_code(team, "L1CODE")), 1)

    def test_after_75_min_one_solved_is_enough(self):
        hunt, intro, l1, team = self._started()
        game.submit_answer(team, intro[0].pk, intro[0].password)
        self._age(team, 46)
        game.skip_puzzle(team, intro[1].pk)
        with self.assertRaises(game.GameError):
            game.skip_puzzle(team, intro[2].pk)  # only one credit so far
        self._blocked(team)
        self._age(team, 76)
        game.skip_puzzle(team, intro[2].pk)
        game.enter_arrival_code(team, "L1CODE")

    def test_after_90_min_nothing_needs_solving(self):
        hunt, intro, l1, team = self._started()
        self._age(team, 91)
        for p in intro:
            game.skip_puzzle(team, p.pk)
        game.enter_arrival_code(team, "L1CODE")

    def test_countdown_to_next_and_none_after_last(self):
        hunt, intro, l1, team = self._started()
        self._age(team, 10)
        self.assertEqual(game.intro_skip_status(team).next_in_minutes, 35)
        self._age(team, 80)
        self.assertEqual(game.intro_skip_status(team).next_in_minutes, 10)
        self._age(team, 95)
        self.assertIsNone(game.intro_skip_status(team).next_in_minutes)

    def test_no_more_skips_once_all_used(self):
        hunt, intro, l1, team = self._started()
        self._age(team, 95)
        for p in intro:
            game.skip_puzzle(team, p.pk)
        self.assertEqual(game.intro_skip_status(team).available, 0)

    def test_times_validation(self):
        hunt = Puzzlehunt(name="V", scoring_type="time")
        for good in ("", "45", "45, 75,90", "30;60"):
            hunt.intro_skip_times = good
            hunt.full_clean()
        for bad in ("45,45", "90,45", "0,10", "abc", "45,,x", "-5"):
            hunt.intro_skip_times = bad
            with self.assertRaises(ValidationError, msg=bad):
                hunt.full_clean()


class IntroSkipTimerTests(TestCase):
    def _age_start(self, team, minutes):
        """Pretend the team started `minutes` ago."""
        from datetime import timedelta

        from django.utils import timezone

        team.puzzle_attempts.update(arrived_at=timezone.now() - timedelta(minutes=minutes))

    def _started(self, times="30, 60, 90, 120", **kw):
        hunt, intro, l1, l2, team = make_opening_hunt(**kw)
        hunt.intro_skip_times = times
        hunt.save()
        team.refresh_from_db()
        game.enter_arrival_code(team, "START")
        return hunt, intro, l1, l2, team

    def test_no_skip_before_first_interval(self):
        hunt, intro, l1, l2, team = self._started()
        self._age_start(team, 29)
        self.assertEqual(game.intro_skip_status(team).available, 0)
        self.assertFalse(game.can_skip(team, attempt_of(team, intro[0])))
        with self.assertRaises(game.GameError):
            game.skip_puzzle(team, intro[0].pk)

    def test_one_skip_per_interval_chosen_by_team(self):
        hunt, intro, l1, l2, team = self._started()
        self._age_start(team, 31)
        status = game.intro_skip_status(team)
        self.assertEqual(status.available, 1)
        self.assertTrue(game.can_skip(team, attempt_of(team, intro[1])))
        game.skip_puzzle(team, intro[1].pk)  # team picks the second one
        self.assertTrue(team.puzzle_attempts.get(puzzle=intro[1]).skipped)
        self.assertFalse(team.puzzle_attempts.get(puzzle=intro[0]).skipped)
        self.assertEqual(game.intro_skip_status(team).available, 0)
        with self.assertRaises(game.GameError):
            game.skip_puzzle(team, intro[0].pk)

    def test_unused_skips_accumulate(self):
        hunt, intro, l1, l2, team = self._started()
        self._age_start(team, 65)
        self.assertEqual(game.intro_skip_status(team).available, 2)
        game.skip_puzzle(team, intro[0].pk)
        game.skip_puzzle(team, intro[1].pk)
        with self.assertRaises(game.GameError):
            game.skip_puzzle(team, intro[2].pk)

    def test_next_in_countdown(self):
        hunt, intro, l1, l2, team = self._started()
        self._age_start(team, 10)
        self.assertEqual(game.intro_skip_status(team).next_in_minutes, 20)

    def test_skipped_intro_counts_toward_unlocking_next_puzzle(self):
        hunt, intro, l1, l2, team = self._started()
        self._age_start(team, 31)
        game.skip_puzzle(team, intro[0].pk)
        for p in intro[1:]:
            game.submit_answer(team, p.pk, p.password)
        self.assertEqual(game.get_unlock_notices(team), [l1])

    def test_timer_overrides_allow_skip_for_intro_only(self):
        hunt, intro, l1, l2, team = self._started(allow_skip=True)
        # Intro puzzle: timer decides even though allow_skip is on.
        self.assertFalse(game.can_skip(team, attempt_of(team, intro[0])))
        # Other puzzles: allow_skip still applies.
        self.assertTrue(game.can_skip(team, attempt_of(team, l1)))

    def test_timer_off_falls_back_to_allow_skip(self):
        hunt, intro, l1, l2, team = make_opening_hunt(allow_skip=True)
        game.enter_arrival_code(team, "START")
        self.assertIsNone(game.intro_skip_status(team))
        self.assertTrue(game.can_skip(team, attempt_of(team, intro[0])))
        hunt.allow_skip = False
        hunt.save()
        team.refresh_from_db()
        self.assertFalse(game.can_skip(team, attempt_of(team, intro[0])))

    def test_no_status_before_start(self):
        hunt, intro, l1, l2, team = make_opening_hunt()
        hunt.intro_skip_times = "30, 60, 90, 120"
        hunt.save()
        team.refresh_from_db()
        self.assertIsNone(game.intro_skip_status(team))


class LoginMixin:
    def _login(self, team):
        from django.core.signing import get_cookie_signer

        from accounts.auth import TEAM_COOKIE_NAME, TEAM_COOKIE_SALT

        signer = get_cookie_signer(salt=TEAM_COOKIE_NAME + TEAM_COOKIE_SALT)
        self.client.cookies[TEAM_COOKIE_NAME] = signer.sign(str(team.pk))


class PlayerViewTests(LoginMixin, TestCase):
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


class IntroSkipViewTests(LoginMixin, TestCase):
    def test_skip_button_appears_after_interval_and_works(self):
        from datetime import timedelta

        from django.utils import timezone

        hunt, intro, l1, l2, team = make_opening_hunt()
        hunt.intro_skip_times = "30, 60, 90, 120"
        hunt.save()
        self._login(team)
        self.client.post("/play/arrive/", {"arrival_code": "start"})

        resp = self.client.get("/play/active/")
        self.assertContains(resp, "Dostupná přeskočení úvodních šifer: <strong>0</strong>")
        self.assertNotContains(resp, "Přeskočit</button>")

        team.puzzle_attempts.update(arrived_at=timezone.now() - timedelta(minutes=31))
        resp = self.client.get("/play/active/")
        self.assertContains(resp, "<strong>1</strong>")
        self.assertContains(resp, "Přeskočit</button>", count=3)  # one per intro card

        resp = self.client.post("/play/skip/", {"puzzle_id": intro[0].pk}, follow=True)
        self.assertContains(resp, "přeskočena")
        self.assertNotContains(resp, "Přeskočit</button>")


class TimedSkipViewTests(LoginMixin, TestCase):
    def test_countdown_then_button(self):
        from datetime import timedelta

        from django.utils import timezone

        hunt = Puzzlehunt.objects.create(
            name="V", scoring_type="time", allow_skip=True, skip_min_minutes=90
        )
        puzzle = Puzzle.objects.create(
            puzzlehunt=hunt, display_id="P1", name="Prvni", arrival_code="K1", password="x"
        )
        team = Team.objects.create(puzzlehunt=hunt, name="T")
        self._login(team)
        self.client.post("/play/arrive/", {"arrival_code": "k1"})

        resp = self.client.get("/play/active/")
        self.assertContains(resp, "Přeskočit lze za ~90 min.")
        self.assertNotContains(resp, "Přeskočit</button>")
        resp = self.client.post("/play/skip/", {"puzzle_id": puzzle.pk}, follow=True)
        self.assertContains(resp, "Přeskočení bude dostupné")

        team.puzzle_attempts.update(arrived_at=timezone.now() - timedelta(minutes=91))
        resp = self.client.get("/play/active/")
        self.assertContains(resp, "Přeskočit</button>")
        self.assertNotContains(resp, "Přeskočit lze za")
        resp = self.client.post("/play/skip/", {"puzzle_id": puzzle.pk}, follow=True)
        self.assertContains(resp, "přeskočena")
