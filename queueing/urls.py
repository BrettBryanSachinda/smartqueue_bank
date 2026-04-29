from django.urls import path
from . import views

urlpatterns = [
    path("", views.check_in_customer, name="check_in"),
    path("success/<int:ticket_id>/", views.ticket_success, name="ticket_success"),
    path("dashboard/", views.teller_dashboard, name="teller_dashboard"), 
    path("track/<int:ticket_id>/", views.track_ticket, name="track_ticket"),
    path("manager/", views.manager_dashboard, name="manager_dashboard"),
    path("manager/export/", views.export_tickets_csv, name="export_tickets_csv"), # FIX: Added Export Route
    path("signup/", views.teller_signup, name="signup"),
    path("route-dashboard/", views.dashboard_routing, name="dashboard_routing"),
    path("staff-access/", views.force_logout_then_login, name="staff_access_force"),
]