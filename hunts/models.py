from django.db import models


class Puzzlehunt(models.Model):
    SCORING_POINTS = "points"
    SCORING_TIME = "time"
    SCORING_CHOICES = [
        (SCORING_POINTS, "Points"),
        (SCORING_TIME, "Time"),
    ]

    name = models.CharField(max_length=200, unique=True)
    scoring_type = models.CharField(max_length=10, choices=SCORING_CHOICES)
    hint1_min_minutes = models.PositiveIntegerField(default=0)
    hint2_min_minutes = models.PositiveIntegerField(default=0)
    hint3_min_minutes = models.PositiveIntegerField(default=0)
    allow_skip = models.BooleanField(default=False)
    show_total_count = models.BooleanField(default=True)
    max_active_puzzles = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Maximum number of puzzles a team can have active (arrived but not solved/skipped) at the same time. Leave blank for no limit.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def hint_unit(self):
        return "points" if self.scoring_type == self.SCORING_POINTS else "minutes"

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
        Puzzlehunt, on_delete=models.CASCADE, related_name="puzzles"
    )
    display_id = models.CharField(max_length=20)
    order = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Numeric ordering within the hunt. Leave blank to auto-assign the lowest unused number.",
    )
    name = models.CharField(max_length=200)
    arrival_code = models.CharField(max_length=100)
    password = models.CharField(max_length=100)
    base_points = models.PositiveIntegerField(
        default=1,
        help_text="Points awarded for solving this puzzle (only used for points-based hunts).",
    )
    prerequisites = models.ManyToManyField(
        "self",
        symmetrical=False,
        blank=True,
        related_name="unlocks",
    )
    solve_message = models.TextField(blank=True)

    class Meta:
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
        Puzzlehunt, on_delete=models.CASCADE, related_name="contacts"
    )
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=40)
    note = models.CharField(max_length=200, blank=True)
    order = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Display order within the hunt. Leave blank to auto-assign the lowest unused number.",
    )

    class Meta:
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
    puzzle = models.ForeignKey(Puzzle, on_delete=models.CASCADE, related_name="hints")
    order = models.PositiveSmallIntegerField(
        choices=[(1, "1st hint"), (2, "2nd hint"), (3, "3rd hint")],
    )
    text = models.TextField()
    cost = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["puzzle", "order"],
                name="unique_hint_order_per_puzzle",
            ),
        ]
        ordering = ["puzzle", "order"]

    def __str__(self):
        return f"Hint {self.order} of {self.puzzle.display_id}"


class PuzzleAttempt(models.Model):
    team = models.ForeignKey(
        "accounts.Team", on_delete=models.CASCADE, related_name="puzzle_attempts"
    )
    puzzle = models.ForeignKey(
        Puzzle, on_delete=models.PROTECT, related_name="attempts"
    )
    arrived_at = models.DateTimeField(auto_now_add=True)
    solved_at = models.DateTimeField(null=True, blank=True)
    hints_taken = models.PositiveSmallIntegerField(default=0)
    skipped = models.BooleanField(default=False)

    class Meta:
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
        verbose_name = "active puzzle"
        verbose_name_plural = "active puzzles"


class FinishedPuzzleAttempt(PuzzleAttempt):
    class Meta:
        proxy = True
        verbose_name = "finished puzzle"
        verbose_name_plural = "finished puzzles"


class AnswerAttempt(models.Model):
    team = models.ForeignKey(
        "accounts.Team", on_delete=models.CASCADE, related_name="answer_attempts"
    )
    puzzle = models.ForeignKey(
        Puzzle, on_delete=models.PROTECT, related_name="answer_attempts"
    )
    submitted_answer = models.CharField(max_length=200)
    correct = models.BooleanField()
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
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
