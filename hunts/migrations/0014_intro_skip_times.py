from django.db import migrations, models

import hunts.models


def interval_to_times(apps, schema_editor):
    """A uniform "every N minutes" becomes the explicit list N, 2N, 3N, ...
    with one entry per intro puzzle of the hunt."""
    Puzzlehunt = apps.get_model("hunts", "Puzzlehunt")
    for hunt in Puzzlehunt.objects.exclude(intro_skip_interval_minutes__isnull=True):
        n = max(1, hunt.puzzles.filter(opens_at_start=True).count())
        step = hunt.intro_skip_interval_minutes
        hunt.intro_skip_times = ", ".join(str(step * k) for k in range(1, n + 1))
        hunt.save(update_fields=["intro_skip_times"])


def times_to_interval(apps, schema_editor):
    Puzzlehunt = apps.get_model("hunts", "Puzzlehunt")
    for hunt in Puzzlehunt.objects.exclude(intro_skip_times=""):
        first = hunt.intro_skip_times.replace(";", ",").split(",")[0].strip()
        if first.isdigit() and int(first) > 0:
            hunt.intro_skip_interval_minutes = int(first)
            hunt.save(update_fields=["intro_skip_interval_minutes"])


class Migration(migrations.Migration):

    dependencies = [
        ("hunts", "0013_skip_min_minutes"),
    ]

    operations = [
        migrations.AddField(
            model_name="puzzlehunt",
            name="intro_skip_times",
            field=models.CharField(
                blank=True,
                max_length=100,
                validators=[hunts.models.validate_skip_times],
                verbose_name="časy přeskočení úvodních šifer (min)",
                help_text="Volitelné. Minuty od startu, ve kterých tým získá jedno přeskočení úvodní šifry (otevřené se startem), oddělené čárkou, např. „45, 75, 90“. Tým pak přeskočí libovolnou úvodní šifru, kterou si vybere. Nevyužitá přeskočení se hromadí. U úvodních šifer rozhodují jen tyto časy, ne volba „povolit přeskočení“. Prázdné = vypnuto.",
            ),
        ),
        migrations.RunPython(interval_to_times, times_to_interval),
        migrations.RemoveField(
            model_name="puzzlehunt",
            name="intro_skip_interval_minutes",
        ),
    ]
