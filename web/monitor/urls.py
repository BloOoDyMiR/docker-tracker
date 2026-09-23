from django.urls import path

from . import views

app_name = "monitor"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("c/<str:name>/", views.container_detail, name="container_detail"),
    path("c/<str:name>/logs/", views.container_logs, name="container_logs"),
    path("c/<str:name>/explain/run/", views.explain_submit, name="explain_submit"),
]
