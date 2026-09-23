from django.contrib import admin
from django.contrib.auth.views import LogoutView
from django.urls import include, path

from monitor.views import PageLoginView

admin.site.site_header = "Docker Track"
admin.site.site_title = "Docker Track admin"
admin.site.index_title = "Projects and users"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", PageLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("", include("monitor.urls")),
]
