import africastalking
from django.utils import timezone
from django.db.models import Count

# --- SMS CONFIGURATION ---
AT_USERNAME = "sandbox"
AT_API_KEY = "YOUR_KEY_HERE"

try:
    africastalking.initialize(AT_USERNAME, AT_API_KEY)
    sms = africastalking.SMS
except Exception as e:
    print(f"SMS Gateway failed to initialize: {e}")
    sms = None

# ✅ SMART PHONE NORMALIZER (ZIMBABWE / INT)
def normalize_phone(raw_phone, country_code="+263"):
    phone = str(raw_phone).strip().replace(" ", "")
    if phone.startswith("+"):
        return phone
    if phone.startswith("0"):
        return country_code + phone[1:]
    if phone.startswith(country_code.replace("+", "")):
        return "+" + phone
    return country_code + phone

def send_sms_notification(ticket, message):
    """Send SMS + log it"""
    ticket.sms_log = message
    ticket.called_at = timezone.now()
    ticket.save()
    phone = normalize_phone(ticket.raw_phone, ticket.country_code)

    print(f"--- SMS TO {phone} ---")
    if sms:
        try:
            response = sms.send(message, [phone])
            print(response)
            return True
        except Exception as e:
            print(f"SMS failed: {e}")

    print(f"SIMULATED SMS: {message}")
    return True

# ✅ FIXED + UPGRADED ANALYTICS
def get_queue_analytics():
    from .models import Ticket
    today = timezone.now().date()
    completed = Ticket.objects.filter(status='done', completed_at__date=today)

    # --- AVG SERVICE TIME ---
    durations = []
    for t in completed:
        if t.called_at and t.completed_at:
            diff = (t.completed_at - t.called_at).total_seconds() / 60
            durations.append(diff)
    avg_service_time = sum(durations) / len(durations) if durations else 0

    # --- AVG WAIT TIME ---
    waiting = Ticket.objects.filter(status='waiting')
    wait_times = [(timezone.now() - t.created_at).total_seconds() / 60 for t in waiting]
    avg_wait = sum(wait_times) / len(wait_times) if wait_times else 0

    # ✅ SERVICE DOMINANCE (For Manager Charts)
    service_stats = (
        Ticket.objects.filter(created_at__date=today)
        .values('service__name')
        .annotate(count=Count('id'))
        .order_by('-count')
    )

    return {
        'completed_today': completed.count(),
        'avg_service_time': round(avg_service_time, 1),
        'avg_wait_time': round(avg_wait, 1),
        'total_in_system': Ticket.objects.filter(status__in=['waiting', 'serving', 'delayed']).count(),
        'service_stats': list(service_stats),
    }