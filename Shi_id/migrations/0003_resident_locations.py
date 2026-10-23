import django.conf
import django.db.models.deletion
from django.db import migrations, models


TOLEDO_BARANGAYS = (
    ('072251001', 'Awihao'),
    ('072251002', 'Bagakay'),
    ('072251003', 'Bato'),
    ('072251004', 'Biga'),
    ('072251005', 'Bulongan'),
    ('072251006', 'Bunga'),
    ('072251007', 'Cabitoonan'),
    ('072251008', 'Calongcalong'),
    ('072251009', 'Cambang-ug'),
    ('072251010', 'Camp 8'),
    ('072251011', 'Canlumampao'),
    ('072251012', 'Cantabaco'),
    ('072251013', 'Capitan Claudio'),
    ('072251014', 'Carmen'),
    ('072251015', 'Daanglungsod'),
    ('072251016', 'Don Andres Soriano'),
    ('072251017', 'Dumlog'),
    ('072251024', 'Gen. Climaco'),
    ('072251018', 'Ibo'),
    ('072251019', 'Ilihan'),
    ('072251023', 'Juan Climaco, Sr.'),
    ('072251020', 'Landahan'),
    ('072251021', 'Loay'),
    ('072251022', 'Luray II'),
    ('072251025', 'Matab-ang'),
    ('072251026', 'Media Once'),
    ('072251027', 'Pangamihan'),
    ('072251028', 'Poblacion'),
    ('072251029', 'Poog'),
    ('072251030', 'Putingbato'),
    ('072251031', 'Sagay'),
    ('072251032', 'Sam-ang'),
    ('072251033', 'Sangi'),
    ('072251034', 'Santo Niño'),
    ('072251035', 'Subayon'),
    ('072251037', 'Talavera'),
    ('072251039', 'Tubod'),
    ('072251038', 'Tungkay'),
)


def seed_toledo_barangays(apps, schema_editor):
    CityMunicipality = apps.get_model('Shi_id', 'CityMunicipality')
    Barangay = apps.get_model('Shi_id', 'Barangay')
    city, _ = CityMunicipality.objects.using(schema_editor.connection.alias).get_or_create(
        psgc_code='072251000',
        defaults={'name': 'City of Toledo', 'province': 'Cebu', 'is_active': True},
    )
    for psgc_code, name in TOLEDO_BARANGAYS:
        Barangay.objects.using(schema_editor.connection.alias).get_or_create(
            psgc_code=psgc_code,
            defaults={'city_municipality_id': city.pk, 'name': name, 'is_active': True},
        )


class Migration(migrations.Migration):
    dependencies = [
        ('Shi_id', '0002_appointment_scheduling'),
        migrations.swappable_dependency(django.conf.settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='appointment',
            name='resident_address',
            field=models.CharField(blank=True, default='', max_length=500),
            preserve_default=False,
        ),
        migrations.CreateModel(
            name='CityMunicipality',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('province', models.CharField(max_length=100)),
                ('psgc_code', models.CharField(max_length=12, unique=True)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name_plural': 'cities and municipalities',
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='Barangay',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('psgc_code', models.CharField(max_length=12, unique=True)),
                ('is_active', models.BooleanField(default=True)),
                ('city_municipality', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='barangays',
                    to='Shi_id.citymunicipality',
                )),
            ],
            options={
                'verbose_name_plural': 'barangays',
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='PurokSitio',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('is_active', models.BooleanField(default=True)),
                ('barangay', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='puroks',
                    to='Shi_id.barangay',
                )),
            ],
            options={
                'verbose_name': 'purok or sitio',
                'verbose_name_plural': 'puroks and sitios',
                'ordering': ['barangay__name', 'name'],
            },
        ),
        migrations.CreateModel(
            name='ResidentProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('middle_name', models.CharField(blank=True, max_length=100)),
                ('contact_number', models.CharField(max_length=20)),
                ('street_address', models.CharField(max_length=200)),
                ('purok', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='residents',
                    to='Shi_id.puroksitio',
                )),
                ('user', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='resident_profile',
                    to=django.conf.settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'ordering': ['user__last_name', 'user__first_name'],
            },
        ),
        migrations.AddConstraint(
            model_name='puroksitio',
            constraint=models.UniqueConstraint(
                fields=('barangay', 'name'),
                name='uniq_purok_name_per_barangay',
            ),
        ),
        migrations.RunPython(seed_toledo_barangays, migrations.RunPython.noop),
    ]
