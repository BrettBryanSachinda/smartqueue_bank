from django.contrib import admin
from .models import Service, Teller, Ticket

@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ('name', 'code')
    search_fields = ('name', 'code')

@admin.register(Teller)
class TellerAdmin(admin.ModelAdmin):
    list_display = ('name', 'counter_number', 'max_concurrent')
    search_fields = ('name',)

@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = (
        'ticket_number', 
        'customer_name', 
        'service', 
        'status', 
        'priority', 
        'created_at', 
        'called_at'
    )
    list_filter = ('status', 'priority', 'service', 'created_at')
    search_fields = ('ticket_number', 'customer_name', 'phone_number')
    readonly_fields = ('created_at', 'called_at', 'sms_log')