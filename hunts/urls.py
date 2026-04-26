from django.urls import path

from . import views


app_name = "hunts"

urlpatterns = [
    path("", views.play_view, name="play"),
    path("active/", views.active_view, name="active"),
    path("history/", views.history_view, name="history"),
    path("contact/", views.contact_view, name="contact"),
    path("arrive/", views.arrive_view, name="arrive"),
    path("answer/", views.answer_view, name="answer"),
    path("hint/", views.hint_view, name="hint"),
    path("skip/", views.skip_view, name="skip"),
]
