from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from . import views

app_name = "accounts"
urlpatterns = [
    path("", views.home, name="home"),
    path("accounts/register/", views.register, name="register"),
    path("accounts/login/", LoginView.as_view(template_name="accounts/login.html"), name="login"),
    path("accounts/logout/", LogoutView.as_view(), name="logout"),
    path("customer/", views.customer_area, name="customer_area"),
    path("manager/", views.manager_area, name="manager_area"),
    path("administration/", views.admin_area, name="admin_area"),
]
