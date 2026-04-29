from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.urls import reverse
from django.contrib.auth import logout as django_logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.http import HttpResponse
from django.db import transaction # Critical for Bank-Grade Concurrency
from django.db.models import Q # UPGRADE: Required for advanced line position math

from .forms import TellerSignUpForm 
from .models import Ticket, Teller, Service
from .services import send_sms_notification, get_queue_analytics

import csv
import random
import qrcode
import io
import base64

# --- ACCESS CONTROL HELPERS ---
def is_manager(user):
    return user.is_authenticated and user.is_staff

def is_teller(user):
    return user.is_authenticated and hasattr(user, 'teller') and not user.is_staff

# --- CUSTOMER FACING VIEWS ---
@never_cache
def check_in_customer(request):
    if request.method == "POST":
        name = request.POST.get('customer_name')
        raw_phone = request.POST.get('phone_number')
        country_code = request.POST.get('country_code', '+263')
        service_id = request.POST.get('service')

        if not name or not raw_phone or not service_id:
            messages.error(request, "All fields are required.")
            return redirect('check_in')

        service = get_object_or_404(Service, id=service_id)
        
        # --- ZIMBABWEAN NUMBER AUTO-FORMAT LOGIC ---
        clean_phone = raw_phone.strip().replace(" ", "").replace("-", "")
        
        if country_code == "+263" and clean_phone.startswith("0"):
            clean_phone = clean_phone[1:]
            
        if clean_phone.startswith("+"):
            formatted_phone = clean_phone
        else:
            formatted_phone = f"{country_code}{clean_phone}"
        # -------------------------------------------

        ticket_num = f"{service.code}-{random.randint(100, 999)}"

        ticket = Ticket.objects.create(
            ticket_number=ticket_num,
            customer_name=name,
            raw_phone=raw_phone,
            country_code=country_code,
            phone_number=formatted_phone,
            service=service,
            status='waiting',
            priority=2 # Automatically assign Normal priority (2)
        )

        check_in_msg = f"SmartQueue: Hi {name}! Your ticket is {ticket_num}. We'll notify you when it's your turn. Thank you for choosing our bank!"
        send_sms_notification(ticket, check_in_msg)
        return redirect('ticket_success', ticket_id=ticket.id)

    today_count = Ticket.objects.filter(created_at__date=timezone.now().date()).count()
    return render(request, 'check_in.html', {
        'services': Service.objects.all(),
        'today_count': today_count
    })

def ticket_success(request, ticket_id):
    ticket = get_object_or_404(Ticket, id=ticket_id)
    # FIX: Count only people waiting for the SAME service
    people_ahead = Ticket.objects.filter(status='waiting', service=ticket.service, created_at__lt=ticket.created_at).count()
    
    success_url = request.build_absolute_uri(reverse('track_ticket', args=[ticket.id]))
    
    qr_data = success_url 

    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(qr_data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    qr_base64 = base64.b64encode(buffer.getvalue()).decode()
    
    return render(request, 'ticket_success.html', {
        'ticket': ticket,
        'wait_time': people_ahead * 5,
        'qr_code': qr_base64
    })

def track_ticket(request, ticket_id):
    ticket = get_object_or_404(Ticket, id=ticket_id)
    stats = get_queue_analytics()
    
    # FIX: Grab the value, and if it's 0 (no data today), force it to 5
    avg_service = stats.get('avg_service_time', 0)
    if avg_service == 0:
        avg_service = 5  
        
    if ticket.status in ['waiting', 'delayed']:
        # UPGRADE: Exact mathematical queue position factoring in Priority Jumps and Specific Service Lines
        position = Ticket.objects.filter(
            Q(status__in=['waiting', 'delayed']) & 
            Q(service=ticket.service) &
            (Q(priority__lt=ticket.priority) | Q(priority=ticket.priority, created_at__lt=ticket.created_at))
        ).count() + 1
        
        est_time = max(0, int(position * avg_service))
    else:
        position = 0
        est_time = 0
        
    return render(request, 'track_ticket.html', {
        'ticket': ticket,
        'position': position,
        'est_time': int(est_time)
    })

# --- STAFF FACING VIEWS (SECURED) ---
@login_required
def dashboard_routing(request):
    if request.user.is_staff:
        return redirect('manager_dashboard')
    if hasattr(request.user, 'teller'):
        return redirect('teller_dashboard')
    
    django_logout(request)
    messages.error(request, "Access Denied: You do not have a staff profile assigned.")
    return redirect('login')

@user_passes_test(is_teller, login_url='login')
def teller_dashboard(request):
    current_teller = getattr(request.user, 'teller', None)
    
    if request.method == 'POST':
        action = request.POST.get('action')
        ticket_id = request.POST.get('ticket_id')
        
        with transaction.atomic():
            if action == 'take_next':
                current_serving = Ticket.objects.filter(status='serving', teller=current_teller).first()
                if not current_serving:
                    service_id = request.POST.get('service')
                    # FIX: Removed the '-' so it sorts 1 (High) before 2 (Normal)
                    next_ticket = Ticket.objects.select_for_update().filter(
                        status__in=['waiting', 'delayed'],
                        service_id=service_id
                    ).order_by('priority', 'created_at').first()

                    if next_ticket:
                        next_ticket.status = 'serving'
                        next_ticket.teller = current_teller
                        next_ticket.called_at = timezone.now()
                        next_ticket.save()

                        call_msg = f"SmartQueue: It's your turn! Please proceed to {current_teller.name}."
                        send_sms_notification(next_ticket, call_msg)

                    else:
                        messages.warning(request, "No waiting customers for this service right now.")
            
            elif action == 'mark_done' and ticket_id:
                ticket = Ticket.objects.select_for_update().filter(id=ticket_id, teller=current_teller).first()
                if ticket:
                    ticket.status = 'done'
                    ticket.completed_at = timezone.now()
                    ticket.served_by = current_teller 
                    ticket.save()
            
            elif action == 'mark_delayed' and ticket_id:
                ticket = Ticket.objects.select_for_update().filter(id=ticket_id, teller=current_teller).first()
                if ticket:
                    ticket.status = 'delayed'
                    ticket.priority = 3 # FIX: Use integer 3 for Delayed
                    ticket.teller = None 
                    ticket.save()
        
        return redirect('teller_dashboard')

    total_waiting = Ticket.objects.filter(status__in=['waiting', 'delayed']).count()
    completed_today = Ticket.objects.filter(
        status='done', 
        served_by=current_teller,  
        completed_at__date=timezone.now().date()
    ).count()
    delayed_tickets = Ticket.objects.filter(status='delayed')
    
    stats = get_queue_analytics()
    # Sorting corrected here for the dashboard view
    waiting_tickets = Ticket.objects.filter(status__in=['waiting', 'delayed']).order_by('priority', 'created_at')
    current_serving = Ticket.objects.filter(status='serving', teller=current_teller).first()

    # Fetch the 5 most recent tickets that have an SMS log entry
    recent_sms = Ticket.objects.exclude(sms_log__isnull=True).exclude(sms_log__exact='').order_by('-id')[:5]

    context = {
        'services': Service.objects.all(),
        'waiting_tickets': waiting_tickets,
        'current_ticket': current_serving,
        'total_waiting': total_waiting,
        'completed_today': completed_today,
        'delayed_tickets': delayed_tickets,
        'avg_wait_time': stats.get('avg_wait_time', 0),
        'analytics': stats,
        'sms_log': recent_sms,
    }
    return render(request, 'teller_dashboard.html', context)

@user_passes_test(is_manager, login_url='login')
def manager_dashboard(request):
    if request.method == "POST":
        action = request.POST.get('action')
        ticket_id = request.POST.get('ticket_id')
        
        with transaction.atomic():
            ticket = get_object_or_404(Ticket.objects.select_for_update(), id=ticket_id)
            ticket.last_modified_by = request.user 

            if action == "delete":
                ticket.delete()
                messages.success(request, "Ticket deleted permanently.")
                
            elif action == "reassign":
                new_teller = get_object_or_404(Teller, id=request.POST.get('teller_id'))
                ticket.teller = new_teller
                ticket.status = 'serving'
                ticket.save()
                messages.success(request, f"{ticket.ticket_number} → {new_teller.name}")
                
            elif action == "priority":
                ticket.priority = int(request.POST.get('priority'))
                ticket.save()
                messages.success(request, f"{ticket.ticket_number} priority updated.")
                
            elif action == "change_service":
                new_service = get_object_or_404(Service, id=request.POST.get('service_id'))
                ticket.service = new_service
                ticket.status = 'waiting'
                ticket.teller = None
                ticket.save()
                messages.success(request, f"{ticket.ticket_number} moved to {new_service.name}")
                
            elif action == "reset":
                ticket.status = 'waiting'
                ticket.teller = None
                ticket.save()
                messages.warning(request, f"{ticket.ticket_number} returned to queue")

        # Redirect immediately after POST processing to prevent form resubmission
        return redirect('manager_dashboard')

    # --- GET REQUEST LOGIC (Indentation fixed) ---
    teller_performance = []
    for teller in Teller.objects.all():
        completed = Ticket.objects.filter(served_by=teller, completed_at__date=timezone.now().date())
        total_time = sum([max(0, (t.completed_at - t.called_at).total_seconds() / 60) for t in completed if t.called_at and t.completed_at])
        
        teller_performance.append({
            'teller': teller,
            'served': completed.count(),
            'avg_time': round(total_time / completed.count(), 1) if completed.count() > 0 else 0
        })

    analytics = get_queue_analytics()
    context = {
        'tellers': Teller.objects.all(),
        'services': Service.objects.all(),
        'waiting_tickets': Ticket.objects.filter(status='waiting').order_by('priority', 'created_at'),
        'serving_tickets': Ticket.objects.filter(status='serving'),
        'analytics': analytics,
        'service_stats': analytics.get('service_stats', []),
        'teller_performance': teller_performance,  # FIX: Added this to the context so it renders in HTML!
    }
    return render(request, 'manager_dashboard.html', context)

    
# --- UTILITY VIEWS ---
def force_logout_then_login(request):
    django_logout(request)
    messages.info(request, "Logged out successfully.")
    return redirect('login')

@user_passes_test(is_manager, login_url='login')
def export_tickets_csv(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="daily_report.csv"'
    writer = csv.writer(response)
    writer.writerow(['Ticket', 'Customer', 'Status', 'Served By', 'Service Duration (min)'])
    
    for t in Ticket.objects.filter(created_at__date=timezone.now().date()):
        duration = 0
        if t.called_at and t.completed_at:
             # FIX: Added max(0) to prevent negative durations in the export
             duration = max(0, round((t.completed_at - t.called_at).total_seconds() / 60, 1))
        
        teller_name = t.served_by.name if t.served_by else "Unassigned"
        writer.writerow([t.ticket_number, t.customer_name, t.status, teller_name, duration])
    return response

def teller_signup(request):
    if request.method == 'POST':
        form = TellerSignUpForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Account created! Please log in.")
            return redirect('login')
    return render(request, 'signup.html', {'form': TellerSignUpForm()})