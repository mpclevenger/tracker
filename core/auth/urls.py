from django.urls import path

from core.auth import views

urlpatterns = [
    path("switch/", views.switch_user, name="switch_user"),
]
