import django.conf
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('Shi_id', '0001_initial'),
        migrations.swappable_dependency(django.conf.settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='appointment',
            name='appointment_id',
            field=models.CharField(blank=True, max_length=20, null=True, unique=True),
        ),
        migrations.AddField(
            model_name='appointment',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='appointment',
            name='resident',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='appointments',
                to=django.conf.settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterModelOptions(
            name='appointment',
            options={'ordering': ['-date', '-time']},
        ),
        migrations.CreateModel(
            name='ScheduleClosure',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField()),
                ('time', models.TimeField(blank=True, null=True)),
                ('reason', models.CharField(max_length=300)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('closed_by', models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='schedule_closures',
                    to=django.conf.settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'ordering': ['date', 'time'],
            },
        ),
        migrations.AddConstraint(
            model_name='appointment',
            constraint=models.UniqueConstraint(
                condition=models.Q(('status__in', ['Pending', 'Approved'])),
                fields=('date', 'time'),
                name='uniq_active_appointment_slot',
            ),
        ),
        migrations.AddConstraint(
            model_name='scheduleclosure',
            constraint=models.UniqueConstraint(
                condition=models.Q(('time__isnull', True)),
                fields=('date',),
                name='uniq_closed_schedule_day',
            ),
        ),
        migrations.AddConstraint(
            model_name='scheduleclosure',
            constraint=models.UniqueConstraint(
                condition=models.Q(('time__isnull', False)),
                fields=('date', 'time'),
                name='uniq_closed_schedule_slot',
            ),
        ),
    ]
