from django.urls import path

from bookings import views as v

urlpatterns = [
    path("", v.home, name="home"),
    path("admin/login/", v.sign_in, name="login"),
    path("admin/logout/", v.sign_out, name="logout"),
    path("admin/forgot-password/", v.forgot_password, name="forgot"),
    path("admin/reset/<str:uid>/<str:token>/", v.reset_password, name="reset"),
    path("admin/", v.dashboard, name="dashboard"),
]

urlpatterns += [
    path("admin/sessions/", v.sessions, name="sessions"),
    path("admin/sessions/new/", v.session_edit, name="session_new"),
    path("admin/sessions/<int:pk>/", v.session_edit, name="session_edit"),
    path("admin/availability/", v.availability, name="availability"),
]
urlpatterns += [
    path("book/", v.book, name="book"),
    path("confirmation/<uuid:key>/", v.confirmation, name="confirmation"),
    path("admin/bookings/<int:pk>/", v.booking_detail, name="booking_detail"),
]
urlpatterns += [
    path("admin/bookings/<int:pk>/upload/", v.upload_photo, name="upload"),
    path("gallery/<str:token>/", v.gallery, name="gallery"),
    path("gallery/<str:token>/photo/<int:pk>/", v.gallery_photo, name="gallery_photo"),
    path("gallery/<str:token>/download/", v.download_all, name="download_all"),
]
