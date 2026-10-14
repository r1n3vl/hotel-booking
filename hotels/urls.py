from django.urls import path

from . import management, views

app_name = "hotels"
urlpatterns = [
    path("management/hotels/", management.hotel_list, name="manage_hotels"),
    path("management/hotels/new/", management.hotel_create, name="hotel_create"),
    path("management/hotels/<int:pk>/edit/", management.hotel_update, name="hotel_update"),
    path("management/rooms/", management.room_list, name="manage_rooms"),
    path("management/rooms/new/", management.room_create, name="room_create"),
    path("management/rooms/<int:pk>/edit/", management.room_update, name="room_update"),
    path("hotels/", views.hotel_list, name="hotel_list"),
    path("rooms/", views.room_list, name="room_list"),
]
