# Generated by Django 2.1.1 on 2019-01-07 16:13

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('nefarious', '0018_jackettindexersettings'),
    ]

    operations = [
        migrations.AddField(
            model_name='jackettindexersettings',
            name='nefarious_settings',
            field=models.ForeignKey(default=1, on_delete=django.db.models.deletion.CASCADE, to='nefarious.NefariousSettings'),
            preserve_default=False,
        ),
    ]
