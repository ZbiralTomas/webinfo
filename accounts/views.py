from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from hunts.models import Puzzlehunt

from .auth import clear_team_cookie, get_team_id, set_team_cookie
from .models import Team


@ratelimit(key="ip", rate="20/m", method="POST", block=False)
@ensure_csrf_cookie
def login_view(request):
    error = None
    if request.method == "POST":
        if getattr(request, "limited", False):
            error = "Příliš mnoho pokusů o přihlášení. Zkus to znovu za chvíli."
        else:
            name = request.POST.get("name", "").strip()
            password = request.POST.get("password", "")
            hunt_id = request.POST.get("puzzlehunt") or None
            team = Team.objects.filter(puzzlehunt_id=hunt_id, name=name).first()
            if team and team.check_password(password):
                response = redirect("hunts:play")
                set_team_cookie(response, team.id)
                return response
            error = "Špatný název týmu, heslo nebo šifrovačka."
    else:
        if get_team_id(request):
            return redirect("hunts:play")

    hunts = Puzzlehunt.objects.filter(is_active=True).order_by("name")
    return render(
        request,
        "accounts/login.html",
        {"hunts": hunts, "error": error},
    )


@require_POST
def logout_view(request):
    response = redirect("accounts:login")
    clear_team_cookie(response)
    return response
