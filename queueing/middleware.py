from django.utils import timezone
from .models import Ticket

class DailyCleanupMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Silently sweep away any 'delayed' tickets created before today
        Ticket.objects.filter(
            status='delayed', 
            created_at__date__lt=timezone.now().date()
        ).delete()

        # Continue loading the requested page
        response = self.get_response(request)
        return response