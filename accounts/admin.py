from django import forms
from django.contrib import admin
from django.db.models import Q
from django.urls import reverse
from django.utils.html import format_html

from hunts.models import (
    ActivePuzzleAttempt,
    AnswerAttempt,
    FinishedPuzzleAttempt,
    Puzzlehunt,
)

from .models import Team


class TeamAdminForm(forms.ModelForm):
    new_password = forms.CharField(
        widget=forms.PasswordInput(render_value=False),
        required=False,
        help_text="Leave blank to keep the current password.",
    )

    class Meta:
        model = Team
        fields = ["puzzlehunt", "name", "new_password"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk is None:
            self.fields["new_password"].required = True
            self.fields["new_password"].help_text = "Initial password for the team."

    def save(self, commit=True):
        team = super().save(commit=False)
        raw = self.cleaned_data.get("new_password")
        if raw:
            team.set_password(raw)
        if commit:
            team.save()
        return team


class ActivePuzzleAttemptInline(admin.TabularInline):
    model = ActivePuzzleAttempt
    fk_name = "team"
    extra = 0
    can_delete = False
    fields = ("puzzle", "arrived_at", "hints_taken")
    readonly_fields = fields
    verbose_name_plural = "Active puzzles"

    def has_add_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .filter(solved_at__isnull=True, skipped=False)
        )


class FinishedPuzzleAttemptInline(admin.TabularInline):
    model = FinishedPuzzleAttempt
    fk_name = "team"
    extra = 0
    can_delete = False
    fields = ("puzzle", "arrived_at", "solved_at", "hints_taken", "skipped")
    readonly_fields = fields
    verbose_name_plural = "Finished puzzles"

    def has_add_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .filter(Q(solved_at__isnull=False) | Q(skipped=True))
        )


class AnswerAttemptInline(admin.TabularInline):
    model = AnswerAttempt
    extra = 0
    can_delete = False
    fields = ("submitted_at", "puzzle", "submitted_answer", "correct")
    readonly_fields = fields
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    form = TeamAdminForm
    change_list_template = "admin/accounts/team/change_list.html"
    list_display = ("name", "puzzlehunt", "active_puzzle_count", "created_at")
    list_filter = ("puzzlehunt",)
    search_fields = ("name",)
    fieldsets = (
        ("Identification", {"fields": ("puzzlehunt", "name")}),
        ("Authentication", {"fields": ("new_password",)}),
    )
    inlines = [
        ActivePuzzleAttemptInline,
        FinishedPuzzleAttemptInline,
        AnswerAttemptInline,
    ]

    @admin.display(description="Active puzzles")
    def active_puzzle_count(self, obj):
        count = obj.puzzle_attempts.filter(solved_at__isnull=True, skipped=False).count()
        url = (
            reverse("admin:hunts_puzzleattempt_changelist")
            + f"?team__id__exact={obj.id}"
        )
        return format_html('<a href="{}">{}</a>', url, count)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "puzzlehunt":
            match = getattr(request, "resolver_match", None)
            obj_id = match.kwargs.get("object_id") if match else None
            if not obj_id:
                kwargs["queryset"] = Puzzlehunt.objects.filter(is_active=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_urls(self):
        from django.urls import path

        urls = super().get_urls()
        custom = [
            path(
                "import-csv/",
                self.admin_site.admin_view(self.import_csv_view),
                name="accounts_team_import_csv",
            ),
        ]
        return custom + urls

    def import_csv_view(self, request):
        from django.contrib import messages
        from django.shortcuts import redirect, render
        from django.urls import reverse

        from hunts.csv_import import CsvImportError, import_teams

        errors = []
        if request.method == "POST":
            hunt_id = request.POST.get("puzzlehunt") or None
            uploaded = request.FILES.get("csv_file")
            hunt = Puzzlehunt.objects.filter(pk=hunt_id).first() if hunt_id else None
            if hunt is None:
                errors.append("Pick a puzzlehunt to import into.")
            if uploaded is None:
                errors.append("Attach a CSV file.")
            if not errors:
                try:
                    result = import_teams(uploaded, puzzlehunt=hunt)
                except CsvImportError as e:
                    errors = e.errors
                else:
                    messages.success(
                        request,
                        f"Imported {result.created} team(s) into {hunt.name}.",
                    )
                    return redirect(reverse("admin:accounts_team_changelist"))

        return render(
            request,
            "admin/accounts/team/import_csv.html",
            {
                **self.admin_site.each_context(request),
                "title": "Import teams from CSV",
                "hunts": Puzzlehunt.objects.all().order_by("name"),
                "errors": errors,
                "opts": self.model._meta,
            },
        )
