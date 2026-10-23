from datetime import date, datetime, time, timedelta

from django.utils import timezone

from .models import Appointment, ScheduleClosure


def build_slot_starts():
    opening = datetime.combine(date.min, time(10, 0))
    closing = datetime.combine(date.min, time(15, 0))
    duration = timedelta(minutes=30)
    starts = []
    while opening + duration <= closing:
        starts.append(opening.time())
        opening += duration
    return tuple(starts)


SLOT_STARTS = build_slot_starts()
SLOT_CHOICES = tuple((slot.strftime('%H:%M'), slot.strftime('%I:%M %p')) for slot in SLOT_STARTS)
ACTIVE_STATUSES = ('Pending', 'Approved')


def slot_end(start):
    return (datetime.combine(date.min, start) + timedelta(minutes=30)).time()


def is_business_day(day):
    return day.weekday() < 5


def slot_availability(day, start, *, closures=None, booked_slots=None, current=None):
    current = current or timezone.localtime()
    closures = closures if closures is not None else ScheduleClosure.objects.filter(date=day)
    booked_slots = booked_slots if booked_slots is not None else set(
        Appointment.objects.filter(
            date=day,
            status__in=ACTIVE_STATUSES,
        ).values_list('time', flat=True)
    )

    if day < current.date() or not is_business_day(day):
        return 'gray'
    if any(closure.time is None for closure in closures):
        return 'red'
    if day == current.date() and start <= current.time().replace(tzinfo=None):
        return 'gray'
    if any(closure.time == start for closure in closures) or start in booked_slots:
        return 'red'
    return 'green'


def day_availability(day, *, closures=None, booked_slots=None, current=None):
    current = current or timezone.localtime()
    if day < current.date() or not is_business_day(day):
        return 'gray'
    states = [
        slot_availability(day, start, closures=closures, booked_slots=booked_slots, current=current)
        for start in SLOT_STARTS
    ]
    if 'green' in states:
        return 'green'
    if all(state == 'gray' for state in states):
        return 'gray'
    return 'red'
