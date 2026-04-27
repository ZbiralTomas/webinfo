from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet
from django.utils.html import format_html_join

from .models import AnswerAttempt, Contact, Hint, Puzzle, Puzzlehunt, PuzzleAttempt


# ---------------------------------------------------------------------------
# Puzzlehunt
# ---------------------------------------------------------------------------


class PuzzlehuntForm(forms.ModelForm):
    class Meta:
        model = Puzzlehunt
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        h1 = cleaned.get("hint1_min_minutes")
        h2 = cleaned.get("hint2_min_minutes")
        h3 = cleaned.get("hint3_min_minutes")
        if h1 is not None and h2 is not None and h2 < h1:
            self.add_error(
                "hint2_min_minutes",
                "Musí být alespoň tak velké jako práh 1. nápovědy.",
            )
        if h2 is not None and h3 is not None and h3 < h2:
            self.add_error(
                "hint3_min_minutes",
                "Musí být alespoň tak velké jako práh 2. nápovědy.",
            )
        return cleaned


class ContactInline(admin.TabularInline):
    model = Contact
    extra = 0
    fields = ("order", "name", "phone", "note")


@admin.register(Puzzlehunt)
class PuzzlehuntAdmin(admin.ModelAdmin):
    form = PuzzlehuntForm
    change_form_template = "admin/hunts/puzzlehunt/change_form.html"
    list_display = (
        "name",
        "scoring_type",
        "is_active",
        "allow_skip",
        "puzzle_count",
        "team_count",
        "created_at",
    )
    list_filter = ("scoring_type", "is_active")
    search_fields = ("name",)
    fieldsets = (
        ("Obecné", {"fields": ("name", "scoring_type", "is_active")}),
        (
            "Časování nápověd (minuty od příchodu)",
            {"fields": ("hint1_min_minutes", "hint2_min_minutes", "hint3_min_minutes")},
        ),
        (
            "Pravidla hry",
            {"fields": ("allow_skip", "show_total_count", "max_active_puzzles")},
        ),
    )
    inlines = [ContactInline]

    @admin.display(description="Šifry")
    def puzzle_count(self, obj):
        return obj.puzzles.count()

    @admin.display(description="Týmy")
    def team_count(self, obj):
        return obj.teams.count()

    def get_urls(self):
        from django.urls import path

        urls = super().get_urls()
        custom = [
            path(
                "<int:hunt_id>/stats/",
                self.admin_site.admin_view(self.stats_view),
                name="hunts_puzzlehunt_stats",
            ),
            path(
                "<int:hunt_id>/puzzle/<int:puzzle_id>/",
                self.admin_site.admin_view(self.puzzle_drilldown_view),
                name="hunts_puzzlehunt_puzzle_drilldown",
            ),
            path(
                "<int:hunt_id>/leaderboard.csv",
                self.admin_site.admin_view(self.leaderboard_csv_view),
                name="hunts_puzzlehunt_leaderboard_csv",
            ),
        ]
        return custom + urls

    def stats_view(self, request, hunt_id):
        from django.http import Http404
        from django.shortcuts import render

        from . import stats

        hunt = Puzzlehunt.objects.filter(pk=hunt_id).first()
        if hunt is None:
            raise Http404("Šifrovačka nenalezena")

        return render(
            request,
            "admin/hunts/puzzlehunt/stats.html",
            {
                **self.admin_site.each_context(request),
                "title": f"Statistiky — {hunt.name}",
                "hunt": hunt,
                "puzzle_rows": stats.puzzle_stats(hunt),
                "leaderboard_rows": stats.leaderboard(hunt),
                "matrix": stats.team_progress_matrix(hunt),
                "is_time_hunt": hunt.scoring_type == Puzzlehunt.SCORING_TIME,
                "opts": self.model._meta,
            },
        )

    def puzzle_drilldown_view(self, request, hunt_id, puzzle_id):
        from django.http import Http404
        from django.shortcuts import render

        from . import stats
        from .models import Puzzle

        hunt = Puzzlehunt.objects.filter(pk=hunt_id).first()
        puzzle = (
            Puzzle.objects.filter(pk=puzzle_id, puzzlehunt=hunt).first()
            if hunt is not None else None
        )
        if puzzle is None:
            raise Http404("Šifra v této šifrovačce nenalezena")

        return render(
            request,
            "admin/hunts/puzzlehunt/puzzle_drilldown.html",
            {
                **self.admin_site.each_context(request),
                "title": f"{puzzle} — statistiky týmů",
                "hunt": hunt,
                "puzzle": puzzle,
                "team_rows": stats.puzzle_team_stats(puzzle),
                "opts": self.model._meta,
            },
        )

    def leaderboard_csv_view(self, request, hunt_id):
        import csv

        from django.http import Http404, HttpResponse
        from django.utils.text import slugify

        from . import stats

        hunt = Puzzlehunt.objects.filter(pk=hunt_id).first()
        if hunt is None:
            raise Http404("Puzzlehunt not found")

        rows = stats.leaderboard(hunt)
        is_time = hunt.scoring_type == Puzzlehunt.SCORING_TIME

        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="zebricek-{slugify(hunt.name) or "sifrovacka"}.csv"'
        )
        writer = csv.writer(response)
        if is_time:
            writer.writerow(
                ["poradi", "tym", "vyreseno", "celkovy_cas_min",
                 "uplynulo_min", "penalizace_min", "pouzite_napovedy", "preskoceno"]
            )
            for r in rows:
                writer.writerow([
                    r.rank, r.team.name, r.score.solved, r.score.value,
                    r.score.elapsed, r.score.penalty, r.hints_used, r.score.skipped,
                ])
        else:
            writer.writerow(
                ["poradi", "tym", "skore", "vyreseno", "preskoceno", "pouzite_napovedy"]
            )
            for r in rows:
                writer.writerow([
                    r.rank, r.team.name, r.score.value,
                    r.score.solved, r.score.skipped, r.hints_used,
                ])
        return response


# ---------------------------------------------------------------------------
# Puzzle
# ---------------------------------------------------------------------------


def _creates_cycle(target_puzzle, candidate):
    """True if making `candidate` a prerequisite of `target_puzzle` would create a cycle.

    Walks the existing prerequisite graph from `candidate` and returns True if it
    can reach `target_puzzle`.
    """
    if target_puzzle.pk is None:
        return False
    if candidate.pk == target_puzzle.pk:
        return True
    visited = {candidate.pk}
    stack = [candidate]
    while stack:
        cur = stack.pop()
        for p in cur.prerequisites.all():
            if p.pk == target_puzzle.pk:
                return True
            if p.pk not in visited:
                visited.add(p.pk)
                stack.append(p)
    return False


class PuzzleForm(forms.ModelForm):
    class Meta:
        model = Puzzle
        fields = "__all__"

    def clean_prerequisites(self):
        prereqs = self.cleaned_data.get("prerequisites")
        if not prereqs:
            return prereqs

        hunt = self.cleaned_data.get("puzzlehunt") or getattr(
            self.instance, "puzzlehunt", None
        )
        if hunt is not None:
            offenders = [p for p in prereqs if p.puzzlehunt_id != hunt.id]
            if offenders:
                names = ", ".join(str(p) for p in offenders)
                raise ValidationError(
                    f"Předpoklady musí být ze stejné šifrovačky: {names}."
                )

        for p in prereqs:
            if _creates_cycle(self.instance, p):
                raise ValidationError(
                    f"Přidání {p} jako předpoklad by vytvořilo cyklus."
                )

        return prereqs


class HintInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        orders = []
        for form in self.forms:
            if not form.cleaned_data:
                continue
            if form.cleaned_data.get("DELETE"):
                continue
            order = form.cleaned_data.get("order")
            if order is not None:
                orders.append(order)
        orders.sort()
        expected = list(range(1, len(orders) + 1))
        if orders != expected:
            raise ValidationError(
                "Nápovědy musí být očíslované od 1 bez mezer "
                "(povolené sady: {}, {1}, {1,2}, nebo {1,2,3})."
            )


class HintInline(admin.TabularInline):
    model = Hint
    extra = 0
    fields = ("order", "text", "cost")
    formset = HintInlineFormSet

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        formfield = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name == "cost" and request is not None:
            match = getattr(request, "resolver_match", None)
            if match:
                obj_id = match.kwargs.get("object_id")
                if obj_id:
                    puzzle = Puzzle.objects.filter(pk=obj_id).first()
                    if puzzle is not None:
                        formfield.label = f"Cena ({puzzle.puzzlehunt.hint_unit})"
        return formfield


@admin.register(Puzzle)
class PuzzleAdmin(admin.ModelAdmin):
    form = PuzzleForm
    change_list_template = "admin/hunts/puzzle/change_list.html"
    list_display = ("order", "display_id", "name", "puzzlehunt", "arrival_code")
    list_filter = ("puzzlehunt",)
    search_fields = ("display_id", "name", "arrival_code")
    filter_horizontal = ("prerequisites",)
    inlines = [HintInline]
    fieldsets = (
        ("Identifikace", {"fields": ("puzzlehunt", "display_id", "order", "name")}),
        ("Řešení", {"fields": ("arrival_code", "password", "base_points", "solve_message")}),
        ("Předpoklady", {"fields": ("prerequisites",)}),
    )

    class Media:
        js = ("hunts/admin/puzzle_prereq_filter.js",)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "puzzlehunt":
            match = getattr(request, "resolver_match", None)
            obj_id = match.kwargs.get("object_id") if match else None
            if not obj_id:
                kwargs["queryset"] = Puzzlehunt.objects.filter(is_active=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name == "prerequisites" and request is not None:
            match = getattr(request, "resolver_match", None)
            obj_id = match.kwargs.get("object_id") if match else None
            if obj_id:
                obj = Puzzle.objects.filter(pk=obj_id).first()
                if obj is not None:
                    kwargs["queryset"] = Puzzle.objects.filter(
                        puzzlehunt=obj.puzzlehunt
                    ).exclude(pk=obj.pk)
            else:
                # On create, no hunt is selected yet. Start with an empty queryset.
                # The JS picks a hunt → fetches matching puzzles → repopulates the picker.
                kwargs["queryset"] = Puzzle.objects.none()
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def get_urls(self):
        from django.urls import path

        urls = super().get_urls()
        custom = [
            path(
                "_puzzles_in_hunt/<int:hunt_id>/",
                self.admin_site.admin_view(self.puzzles_in_hunt_view),
                name="hunts_puzzle_puzzles_in_hunt",
            ),
            path(
                "import-csv/",
                self.admin_site.admin_view(self.import_csv_view),
                name="hunts_puzzle_import_csv",
            ),
        ]
        return custom + urls

    def puzzles_in_hunt_view(self, request, hunt_id):
        from django.http import JsonResponse

        exclude_id = request.GET.get("exclude")
        qs = Puzzle.objects.filter(puzzlehunt_id=hunt_id).order_by(
            "order", "display_id"
        )
        if exclude_id:
            qs = qs.exclude(pk=exclude_id)
        return JsonResponse(
            {"puzzles": [{"id": p.id, "label": str(p)} for p in qs]}
        )

    def import_csv_view(self, request):
        from django.contrib import messages
        from django.shortcuts import redirect, render
        from django.urls import reverse

        from .csv_import import CsvImportError, import_puzzles

        errors = []
        if request.method == "POST":
            hunt_id = request.POST.get("puzzlehunt") or None
            uploaded = request.FILES.get("csv_file")
            hunt = Puzzlehunt.objects.filter(pk=hunt_id).first() if hunt_id else None
            if hunt is None:
                errors.append("Vyber šifrovačku, do které se má importovat.")
            if uploaded is None:
                errors.append("Přilož soubor CSV.")
            if not errors:
                try:
                    result = import_puzzles(uploaded, puzzlehunt=hunt)
                except CsvImportError as e:
                    errors = e.errors
                else:
                    messages.success(
                        request,
                        f"Naimportováno {result.created} šifer do {hunt.name}.",
                    )
                    return redirect(reverse("admin:hunts_puzzle_changelist"))

        return render(
            request,
            "admin/hunts/puzzle/import_csv.html",
            {
                **self.admin_site.each_context(request),
                "title": "Import šifer ze souboru CSV",
                "hunts": Puzzlehunt.objects.all().order_by("name"),
                "errors": errors,
                "opts": self.model._meta,
            },
        )


# ---------------------------------------------------------------------------
# PuzzleAttempt + AnswerAttempt
# ---------------------------------------------------------------------------


@admin.register(PuzzleAttempt)
class PuzzleAttemptAdmin(admin.ModelAdmin):
    list_display = ("team", "puzzle", "arrived_at", "solved_at", "hints_taken", "skipped")
    list_filter = ("skipped", "puzzle__puzzlehunt", "team")
    readonly_fields = ("arrived_at", "answer_history")
    fields = (
        "team",
        "puzzle",
        "arrived_at",
        "solved_at",
        "hints_taken",
        "skipped",
        "answer_history",
    )

    @admin.display(description="Pokusy o odpověď na tuto šifru")
    def answer_history(self, obj):
        if obj.pk is None:
            return "—"
        attempts = AnswerAttempt.objects.filter(
            team=obj.team, puzzle=obj.puzzle
        ).order_by("-submitted_at")
        if not attempts:
            return "(zatím žádné odpovědi)"
        return format_html_join(
            "",
            '<div>{} — <b>{}</b>: {}</div>',
            (
                (
                    a.submitted_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "SPRÁVNĚ" if a.correct else "špatně",
                    a.submitted_answer,
                )
                for a in attempts
            ),
        )


@admin.register(AnswerAttempt)
class AnswerAttemptAdmin(admin.ModelAdmin):
    list_display = ("team", "puzzle", "submitted_answer", "correct", "submitted_at")
    list_filter = ("correct", "puzzle__puzzlehunt", "team")
