from django.urls import path

from . import views

app_name = "hotels"
urlpatterns = [
    path("hotels/", views.hotel_list, name="hotel_list"),
    path("rooms/", views.room_list, name="room_list"),
]
