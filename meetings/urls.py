from django.urls import path

from meetings import views

app_name = "meetings"

urlpatterns = [
    path("", views.meeting_list, name="list"),
    path("new/", views.meeting_edit, name="new"),
    path("<uuid:entity_id>/edit/", views.meeting_edit, name="edit"),
    path("followup/<uuid:entity_id>/close/", views.followup_close, name="followup_close"),
    path("followup/<uuid:entity_id>/reopen/", views.followup_reopen, name="followup_reopen"),
]
