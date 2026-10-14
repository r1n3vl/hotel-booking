from django.urls import path

from . import views

app_name = "bookings"
urlpatterns = [
    path("rooms/<int:room_id>/book/", views.booking_create, name="create"),
    path("bookings/<int:pk>/", views.booking_detail, name="detail"),
]
