from django.db import models


class Puzzlehunt(models.Model):
    SCORING_POINTS = "points"
    SCORING_TIME = "time"
    SCORING_CHOICES = [
        (SCORING_POINTS, "Body"),
        (SCORING_TIME, "Čas"),
    ]

    name = models.CharField("název", max_length=200, unique=True)
    scoring_type = models.CharField("typ bodování", max_length=10, choices=SCORING_CHOICES)
    hint1_min_minutes = models.PositiveIntegerField("minuty do 1. nápovědy", default=0)
    hint2_min_minutes = models.PositiveIntegerField("minuty do 2. nápovědy", default=0)
    hint3_min_minutes = models.PositiveIntegerField("minuty do 3. nápovědy", default=0)
    allow_skip = models.BooleanField("povolit přeskočení", default=False)
    show_total_count = models.BooleanField("zobrazit celkový počet šifer", default=True)
    max_active_puzzles = models.PositiveIntegerField(
        "max. počet aktivních šifer",
        null=True,
        blank=True,
        help_text="Nejvyšší počet šifer, které může mít tým otevřené (doražených, ale nevyřešených/nepřeskočených) zároveň. Prázdné = bez omezení.",
    )
    is_active = models.BooleanField("aktivní", default=True)
    created_at = models.DateTimeField("vytvořeno", auto_now_add=True)

    class Meta:
        verbose_name = "šifrovačka"
        verbose_name_plural = "šifrovačky"

    @property
    def hint_unit(self):
        return "bodů" if self.scoring_type == self.SCORING_POINTS else "minut"

    def hint_threshold_minutes(self, order):
        return {
            1: self.hint1_min_minutes,
            2: self.hint2_min_minutes,
            3: self.hint3_min_minutes,
        }[order]

    def __str__(self):
        return self.name


class Puzzle(models.Model):
    puzzlehunt = models.ForeignKey(
        Puzzlehunt, on_delete=models.CASCADE, related_name="puzzles",
        verbose_name="šifrovačka",
    )
    display_id = models.CharField("identifikátor", max_length=20)
    order = models.PositiveIntegerField(
        "pořadí",
        null=True,
        blank=True,
        help_text="Číselné pořadí v rámci šifrovačky. Prázdné = automaticky doplnit nejnižší volné číslo.",
    )
    name = models.CharField("název", max_length=200)
    arrival_code = models.CharField("příchozí kód", max_length=100)
    password = models.CharField("heslo", max_length=100)
    base_points = models.PositiveIntegerField(
        "základní body",
        default=10,
        help_text="Body udělené za vyřešení této šifry (používá se pouze u bodových šifrovaček).",
    )
    prerequisites = models.ManyToManyField(
        "self",
        symmetrical=False,
        blank=True,
        related_name="unlocks",
        verbose_name="předpoklady",
    )
    solve_message = models.TextField("zpráva po vyřešení", blank=True)

    class Meta:
        verbose_name = "šifra"
        verbose_name_plural = "šifry"
        constraints = [
            models.UniqueConstraint(
                fields=["puzzlehunt", "display_id"],
                name="unique_display_id_per_hunt",
            ),
            models.UniqueConstraint(
                fields=["puzzlehunt", "arrival_code"],
                name="unique_arrival_code_per_hunt",
            ),
            models.UniqueConstraint(
                fields=["puzzlehunt", "order"],
                name="unique_order_per_hunt",
            ),
        ]
        ordering = ["puzzlehunt", "order", "display_id"]

    def save(self, *args, **kwargs):
        if self.order is None and self.puzzlehunt_id:
            used = set(
                Puzzle.objects.filter(puzzlehunt_id=self.puzzlehunt_id)
                .exclude(pk=self.pk)
                .values_list("order", flat=True)
            )
            i = 1
            while i in used:
                i += 1
            self.order = i
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.display_id} – {self.name}"


class Contact(models.Model):
    puzzlehunt = models.ForeignKey(
        Puzzlehunt, on_delete=models.CASCADE, related_name="contacts",
        verbose_name="šifrovačka",
    )
    name = models.CharField("jméno", max_length=100)
    phone = models.CharField("telefon", max_length=40)
    note = models.CharField("poznámka", max_length=200, blank=True)
    order = models.PositiveIntegerField(
        "pořadí",
        null=True,
        blank=True,
        help_text="Pořadí zobrazení v rámci šifrovačky. Prázdné = automaticky doplnit nejnižší volné číslo.",
    )

    class Meta:
        verbose_name = "kontakt"
        verbose_name_plural = "kontakty"
        ordering = ["puzzlehunt", "order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["puzzlehunt", "order"],
                name="unique_contact_order_per_hunt",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.order is None and self.puzzlehunt_id:
            used = set(
                Contact.objects.filter(puzzlehunt_id=self.puzzlehunt_id)
                .exclude(pk=self.pk)
                .values_list("order", flat=True)
            )
            i = 1
            while i in used:
                i += 1
            self.order = i
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.phone})"


class Hint(models.Model):
    puzzle = models.ForeignKey(
        Puzzle, on_delete=models.CASCADE, related_name="hints",
        verbose_name="šifra",
    )
    order = models.PositiveSmallIntegerField(
        "pořadí",
        choices=[(1, "1. nápověda"), (2, "2. nápověda"), (3, "3. nápověda")],
    )
    text = models.TextField("text")
    cost = models.PositiveIntegerField("cena", default=0)

    class Meta:
        verbose_name = "nápověda"
        verbose_name_plural = "nápovědy"
        constraints = [
            models.UniqueConstraint(
                fields=["puzzle", "order"],
                name="unique_hint_order_per_puzzle",
            ),
        ]
        ordering = ["puzzle", "order"]

    def __str__(self):
        return f"Nápověda {self.order} k {self.puzzle.display_id}"


class PuzzleAttempt(models.Model):
    team = models.ForeignKey(
        "accounts.Team", on_delete=models.CASCADE, related_name="puzzle_attempts",
        verbose_name="tým",
    )
    puzzle = models.ForeignKey(
        Puzzle, on_delete=models.PROTECT, related_name="attempts",
        verbose_name="šifra",
    )
    arrived_at = models.DateTimeField("příchod", auto_now_add=True)
    solved_at = models.DateTimeField("vyřešeno", null=True, blank=True)
    hints_taken = models.PositiveSmallIntegerField("použité nápovědy", default=0)
    skipped = models.BooleanField("přeskočeno", default=False)

    class Meta:
        verbose_name = "pokus o šifru"
        verbose_name_plural = "pokusy o šifry"
        constraints = [
            models.UniqueConstraint(
                fields=["team", "puzzle"],
                name="unique_attempt_per_team_puzzle",
            ),
        ]

    @property
    def is_active(self):
        return self.solved_at is None and not self.skipped

    @property
    def is_finished(self):
        return not self.is_active

    def __str__(self):
        return f"{self.team} on {self.puzzle}"


class ActivePuzzleAttempt(PuzzleAttempt):
    class Meta:
        proxy = True
        verbose_name = "aktivní šifra"
        verbose_name_plural = "aktivní šifry"


class FinishedPuzzleAttempt(PuzzleAttempt):
    class Meta:
        proxy = True
        verbose_name = "dokončená šifra"
        verbose_name_plural = "dokončené šifry"


class AnswerAttempt(models.Model):
    team = models.ForeignKey(
        "accounts.Team", on_delete=models.CASCADE, related_name="answer_attempts",
        verbose_name="tým",
    )
    puzzle = models.ForeignKey(
        Puzzle, on_delete=models.PROTECT, related_name="answer_attempts",
        verbose_name="šifra",
    )
    submitted_answer = models.CharField("odeslaná odpověď", max_length=200)
    correct = models.BooleanField("správně")
    submitted_at = models.DateTimeField("odesláno", auto_now_add=True)

    class Meta:
        verbose_name = "odpověď"
        verbose_name_plural = "odpovědi"
        ordering = ["-submitted_at"]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.correct:
            attempt = PuzzleAttempt.objects.filter(
                team=self.team, puzzle=self.puzzle
            ).first()
            if attempt and attempt.solved_at is None and not attempt.skipped:
                attempt.solved_at = self.submitted_at
                attempt.save(update_fields=["solved_at"])

    def __str__(self):
        return f"{self.team} → {self.puzzle}: {self.submitted_answer!r}"
