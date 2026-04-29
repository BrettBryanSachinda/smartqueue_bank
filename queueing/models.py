from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User

class Service(models.Model):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=10, unique=True)
    prefix = models.CharField(max_length=2, default="A")
    description = models.TextField(blank=True)
    
    def __str__(self): 
        return self.name

class Teller(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='teller', null=True, blank=True)
    name = models.CharField(max_length=100)
    counter_number = models.IntegerField(unique=True)
    max_concurrent = models.IntegerField(default=1)
    is_online = models.BooleanField(default=False)
    
    def __str__(self): 
        return f"{self.name} (Counter {self.counter_number})"

class Ticket(models.Model):
    STATUS_CHOICES = [
        ('waiting', 'Waiting'), 
        ('serving', 'Serving'), 
        ('done', 'Done'), 
        ('delayed', 'Delayed')
    ]
    
    # FIX: Changed to Integers for flawless mathematical sorting
    # 1 is highest priority, 3 is lowest.
    PRIORITY_CHOICES = [
        (1, 'High-Priority'), 
        (2, 'Normal'), 
        (3, 'Delayed')
    ]

    ticket_number = models.CharField(max_length=15)
    customer_name = models.CharField(max_length=100)
    
    # Zim Formatting Support
    raw_phone = models.CharField(max_length=20, blank=True, null=True)
    country_code = models.CharField(max_length=5, default="+263")
    phone_number = models.CharField(max_length=20) 
    
    service = models.ForeignKey(Service, on_delete=models.CASCADE)
    
    # Current active teller
    teller = models.ForeignKey(Teller, on_delete=models.SET_NULL, null=True, blank=True)
    
    # Permanent Audit Trail - Who actually finished this ticket?
    served_by = models.ForeignKey(Teller, on_delete=models.SET_NULL, null=True, blank=True, related_name='completed_tickets')
    
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='waiting')
    priority = models.IntegerField(choices=PRIORITY_CHOICES, default=2)
    
    # Precision Time Tracking
    created_at = models.DateTimeField(auto_now_add=True)
    called_at = models.DateTimeField(null=True, blank=True) 
    completed_at = models.DateTimeField(null=True, blank=True) 
    updated_at = models.DateTimeField(auto_now=True)
    
    # Audit trail for Manager Actions
    last_modified_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='modified_tickets')
    sms_log = models.TextField(null=True, blank=True)

    class Meta:
        # Sorts by Priority (1 first, then 2, then 3), then by oldest time
        ordering = ['priority', 'created_at']
        indexes = [
            # UPGRADE: Comprehensive index for Lightning-fast "Take Next" queries
            models.Index(fields=['status', 'service', 'priority', 'created_at']),
        ]

    def __str__(self): 
        return f"{self.ticket_number} - {self.customer_name}"