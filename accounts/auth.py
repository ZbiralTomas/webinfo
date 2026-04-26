from functools import wraps

from django.core.signing import BadSignature
from django.shortcuts import redirect

from .models import Team


TEAM_COOKIE_NAME = "webinfo_team"
TEAM_COOKIE_SALT = "webinfo.team"
TEAM_COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def get_team_id(request):
    try:
        raw = request.get_signed_cookie(
            TEAM_COOKIE_NAME,
            salt=TEAM_COOKIE_SALT,
            max_age=TEAM_COOKIE_MAX_AGE,
            default=None,
        )
    except BadSignature:
        return None
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def set_team_cookie(response, team_id):
    response.set_signed_cookie(
        TEAM_COOKIE_NAME,
        str(team_id),
        salt=TEAM_COOKIE_SALT,
        max_age=TEAM_COOKIE_MAX_AGE,
        httponly=True,
        samesite="Lax",
    )


def clear_team_cookie(response):
    response.delete_cookie(TEAM_COOKIE_NAME)


def team_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        team_id = get_team_id(request)
        if not team_id:
            return redirect("accounts:login")
        team = (
            Team.objects.select_related("puzzlehunt")
            .filter(id=team_id)
            .first()
        )
        if team is None:
            response = redirect("accounts:login")
            clear_team_cookie(response)
            return response
        request.team = team
        return view(request, *args, **kwargs)

    return wrapper
