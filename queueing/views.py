from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.urls import reverse
from django.contrib.auth import logout as django_logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.utils import timezone
from django.http import HttpResponse
from django.db import transaction # Critical for Bank-Grade Concurrency

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
        # 1. Clean the input of spaces or dashes
        clean_phone = raw_phone.strip().replace(" ", "").replace("-", "")
        
        # 2. If it's a Zimbabwe code (+263) and starts with '0', strip it (e.g., 0772 -> 772)
        if country_code == "+263" and clean_phone.startswith("0"):
            clean_phone = clean_phone[1:]
            
        # 3. If they accidentally typed the '+' in the input field, don't duplicate it
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
            status='waiting'
        )

        check_in_msg = f"SmartQueue: Hi {name}! Your ticket is {ticket_num}. Track live: [LINK_HERE]"
        send_sms_notification(ticket, check_in_msg)
        return redirect('ticket_success', ticket_id=ticket.id)

    today_count = Ticket.objects.filter(created_at__date=timezone.now().date()).count()
    return render(request, 'check_in.html', {
        'services': Service.objects.all(),
        'today_count': today_count
    })

def ticket_success(request, ticket_id):
    ticket = get_object_or_404(Ticket, id=ticket_id)
    people_ahead = Ticket.objects.filter(status='waiting', created_at__lt=ticket.created_at).count()
    
    # --- FIX: Generate the absolute URL for the ticket success page ---
    # We use 'ticket_success' here instead of 'track_ticket'
    success_url = request.build_absolute_uri(reverse('ticket_success', args=[ticket.id]))
    
    # Pass the URL to the QR code
    qr_data = success_url 
    # ------------------------------------------------------------------

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
    avg_service = stats.get('avg_service_time', 5) 
    
    if ticket.status in ['waiting', 'delayed']:
        position = Ticket.objects.filter(status__in=['waiting', 'delayed'], created_at__lt=ticket.created_at).count() + 1
        est_time = position * avg_service
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
            # 1. Action: Teller wants to take the next customer in line
            if action == 'take_next':
                # First ensure they aren't already serving someone
                current_serving = Ticket.objects.filter(status='serving', teller=current_teller).first()
                if not current_serving:
                    service_id = request.POST.get('service')
                    # Find the oldest ticket for the selected service
                    next_ticket = Ticket.objects.select_for_update().filter(
                        status__in=['waiting', 'delayed'],
                        service_id=service_id
                    ).order_by('-priority', 'created_at').first()

                    if next_ticket:
                        next_ticket.status = 'serving'
                        next_ticket.teller = current_teller
                        next_ticket.called_at = timezone.now()
                        next_ticket.save()
                    else:
                        messages.warning(request, "No waiting customers for this service right now.")
            
            # 2. Action: Teller completes the current ticket
            elif action == 'mark_done' and ticket_id:
                ticket = Ticket.objects.select_for_update().filter(id=ticket_id, teller=current_teller).first()
                if ticket:
                    ticket.status = 'done'
                    ticket.completed_at = timezone.now()
                    ticket.served_by = current_teller # Audit logging
                    ticket.save()
            
            # 3. Action: Customer didn't show up, mark as delayed
            elif action == 'mark_delayed' and ticket_id:
                ticket = Ticket.objects.select_for_update().filter(id=ticket_id, teller=current_teller).first()
                if ticket:
                    ticket.status = 'delayed'
                    ticket.priority = 'delayed' 
                    ticket.teller = None # Frees up the teller to take the next person
                    ticket.save()
        
        # After any POST action, reload the dashboard clean
        return redirect('teller_dashboard')

    # Gather data for the GET request (loading the dashboard)
    total_waiting = Ticket.objects.filter(status__in=['waiting', 'delayed']).count()
    completed_today = Ticket.objects.filter(
        status='done', 
        served_by=current_teller,  
        completed_at__date=timezone.now().date()
    ).count()
    delayed_tickets = Ticket.objects.filter(status='delayed')
    
    stats = get_queue_analytics()
    waiting_tickets = Ticket.objects.filter(status__in=['waiting', 'delayed']).order_by('-priority', 'created_at')
    current_serving = Ticket.objects.filter(status='serving', teller=current_teller).first()

    context = {
        'services': Service.objects.all(),
        'waiting_tickets': waiting_tickets,
        'current_ticket': current_serving,
        'total_waiting': total_waiting,
        'completed_today': completed_today,
        'delayed_tickets': delayed_tickets,
        'avg_wait_time': stats.get('avg_wait_time', 0),
        'analytics': stats,
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
                return redirect('manager_dashboard')
                
            elif action == "reassign":
                new_teller = get_object_or_404(Teller, id=request.POST.get('teller_id'))
                ticket.teller = new_teller
                ticket.status = 'serving'
                ticket.save()
                messages.success(request, f"{ticket.ticket_number} → {new_teller.name}")
                
            elif action == "priority":
                ticket.priority = request.POST.get('priority')
                ticket.save()
                messages.success(request, f"{ticket.ticket_number} set to {ticket.priority}")
                
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

        return redirect('manager_dashboard')

    analytics = get_queue_analytics()
    context = {
        'tellers': Teller.objects.all(),
        'services': Service.objects.all(),
        'waiting_tickets': Ticket.objects.filter(status='waiting'),
        'serving_tickets': Ticket.objects.filter(status='serving'),
        'analytics': analytics,
        'service_stats': analytics.get('service_stats', []),
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
             duration = round((t.completed_at - t.called_at).total_seconds() / 60, 1)
        
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