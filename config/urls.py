from django.urls import include, path

urlpatterns = [
    path("", include("tracker.urls")),
    path("meetings/", include("meetings.urls")),
    path("auth/", include("core.auth.urls")),
]
