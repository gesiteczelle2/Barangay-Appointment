from django.db import migrations


def seed_default_puroks(apps, schema_editor):
    Barangay = apps.get_model('Shi_id', 'Barangay')
    PurokSitio = apps.get_model('Shi_id', 'PurokSitio')
    database = schema_editor.connection.alias

    for barangay in Barangay.objects.using(database).filter(
        city_municipality__psgc_code='072251000',
    ).iterator():
        for number in range(1, 16):
            PurokSitio.objects.using(database).get_or_create(
                barangay_id=barangay.pk,
                name=f'Purok {number}',
            )


class Migration(migrations.Migration):
    dependencies = [
        ('Shi_id', '0003_resident_locations'),
    ]

    operations = [
        migrations.RunPython(seed_default_puroks, migrations.RunPython.noop),
    ]
