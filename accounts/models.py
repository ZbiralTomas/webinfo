from django.contrib.auth.hashers import check_password, make_password
from django.db import models


class Team(models.Model):
    puzzlehunt = models.ForeignKey(
        "hunts.Puzzlehunt", on_delete=models.CASCADE, related_name="teams",
        verbose_name="šifrovačka",
    )
    name = models.CharField("název týmu", max_length=100)
    password = models.CharField("heslo", max_length=128)
    created_at = models.DateTimeField("vytvořeno", auto_now_add=True)

    class Meta:
        verbose_name = "tým"
        verbose_name_plural = "týmy"
        constraints = [
            models.UniqueConstraint(
                fields=["puzzlehunt", "name"],
                name="unique_team_name_per_hunt",
            ),
        ]
        ordering = ["puzzlehunt", "name"]

    def set_password(self, raw_password):
        self.password = make_password(raw_password)

    def check_password(self, raw_password):
        return check_password(raw_password, self.password)

    def __str__(self):
        return f"{self.name} ({self.puzzlehunt.name})"
