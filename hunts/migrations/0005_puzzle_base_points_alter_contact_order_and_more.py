from django.db import migrations, models


def backfill_contact_orders(apps, schema_editor):
    Contact = apps.get_model("hunts", "Contact")
    # Group by puzzlehunt, renumber starting from 1
    from collections import defaultdict
    grouped = defaultdict(list)
    for c in Contact.objects.all().order_by("puzzlehunt_id", "id"):
        grouped[c.puzzlehunt_id].append(c)
    for hunt_id, contacts in grouped.items():
        for i, c in enumerate(contacts, start=1):
            c.order = i
            c.save(update_fields=["order"])


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('hunts', '0004_contact'),
    ]

    operations = [
        migrations.AddField(
            model_name='puzzle',
            name='base_points',
            field=models.PositiveIntegerField(default=1, help_text='Points awarded for solving this puzzle (only used for points-based hunts).'),
        ),
        migrations.AlterField(
            model_name='contact',
            name='order',
            field=models.PositiveIntegerField(blank=True, help_text='Display order within the hunt. Leave blank to auto-assign the lowest unused number.', null=True),
        ),
        migrations.RunPython(backfill_contact_orders, reverse_noop),
        migrations.AddConstraint(
            model_name='contact',
            constraint=models.UniqueConstraint(fields=('puzzlehunt', 'order'), name='unique_contact_order_per_hunt'),
        ),
    ]
