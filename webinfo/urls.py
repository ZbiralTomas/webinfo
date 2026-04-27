from django.contrib import admin
from django.urls import include, path


admin.site.site_header = "TMM WEBINFO"
admin.site.site_title = "TMM Webinfo – administrace"
admin.site.index_title = "Správa šifrovaček"


urlpatterns = [
    path("admin/", admin.site.urls),
    path("play/", include("hunts.urls")),
    path("", include("accounts.urls")),
]
