from datetime import datetime

from django.contrib.auth.models import User
from django.db import models
from django.db.models import Q

from core.models.provider import Provider


class MaintenanceRecord(models.Model):
    """
    Maintenace can be activated through the database
    """
    start_date = models.DateTimeField()
    end_date = models.DateTimeField(blank=True, null=True)
    title = models.CharField(max_length=256)
    message = models.TextField()
    provider = models.ForeignKey(Provider, blank=True, null=True)
    disable_login = models.BooleanField(default=True)

    @classmethod
    def active(cls, provider=None):
        now = datetime.now()
        records = MaintenanceRecord.objects.filter(
            start_date__lt=now,
            Q(end_date__gt=now) | Q(end_date__isnull=True))
        if provider:
            records = records.filter(Q(provider__exact=provider)\
                                     | Q(provider__isnull=True))
        return records

    @classmethod
    def disable_login_access(cls, request):
        disable_login = False
        if request and 'username' in request.session:
            username = request.session['username']
        user = User.objects.get(username=username)
        if user.is_staff or user.is_superuser:
            return False
        records = MaintenanceRecord.active()
        for record in records:
            if record.disable_login:
                disable_login = True
        return disable_login

    def json(self):
        return {
            'start': self.start_date,
            'end': self.end_date,
            'title': self.title,
            'message': self.message,
        }

    def __unicode__(self):
        return '%s (Maintenance Times: %s - %s Login disabled: %s)' % (
            self.title,
            self.start_date,
            self.end_date,
            self.disable_login
        )

    class Meta:
        db_table = "maintenance_record"
        app_label = "core"
