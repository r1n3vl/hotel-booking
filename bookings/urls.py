from django.urls import path

from . import list_views, views

app_name = "bookings"
urlpatterns = [
    path("bookings/", list_views.my_bookings, name="my_bookings"),
    path("management/bookings/", list_views.all_bookings, name="all_bookings"),
    path("bookings/<int:pk>/cancel/", list_views.cancel, name="cancel"),
    path("bookings/<int:pk>/confirm/", list_views.confirm, name="confirm"),
    path("rooms/<int:room_id>/book/", views.booking_create, name="create"),
    path("bookings/<int:pk>/", views.booking_detail, name="detail"),
]
