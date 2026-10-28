from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("hunts", "0011_strict_arrival_codes"),
    ]

    operations = [
        migrations.RenameField(
            model_name="puzzlehunt",
            old_name="strict_arrival_codes",
            new_name="arrival_codes_diacritics",
        ),
        migrations.AlterField(
            model_name="puzzlehunt",
            name="arrival_codes_diacritics",
            field=models.BooleanField(
                default=False,
                help_text="Zapnuto: u příchozích kódů se rozlišuje česká diakritika („kód“ ≠ „kod“). Vypnuto (výchozí): diakritika se ignoruje. Velikost písmen se nerozlišuje nikdy.",
                verbose_name="příchozí kódy rozlišují diakritiku",
            ),
        ),
    ]
