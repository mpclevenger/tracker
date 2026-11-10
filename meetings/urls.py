from django.urls import path

from meetings import views

app_name = "meetings"

urlpatterns = [
    path("", views.meeting_list, name="list"),
    path("new/", views.meeting_edit, name="new"),
    path("<uuid:entity_id>/edit/", views.meeting_edit, name="edit"),
    path("series/", views.series_list, name="series_list"),
    path("series/new/", views.series_edit, name="series_new"),
    path("series/<uuid:entity_id>/", views.series_detail, name="series_detail"),
    path("series/<uuid:entity_id>/edit/", views.series_edit, name="series_edit"),
    path("series/<uuid:entity_id>/remove/", views.series_remove, name="series_remove"),
    path("followup/<uuid:entity_id>/close/", views.followup_close, name="followup_close"),
    path("followup/<uuid:entity_id>/reopen/", views.followup_reopen, name="followup_reopen"),
]
