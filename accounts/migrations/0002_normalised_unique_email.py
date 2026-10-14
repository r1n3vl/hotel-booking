from django.db import migrations, models
from django.db.models.functions import Lower, Trim

import accounts.models


def normalise_emails(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    users = list(User.objects.using(schema_editor.connection.alias).all())
    seen = set()
    for user in users:
        email = user.email.strip().lower()
        if not email or email in seen:
            raise RuntimeError(
                "Email migration stopped: resolve blank or duplicate normalised "
                "emails explicitly before retrying. No users were removed."
            )
        seen.add(email)
    for user in users:
        email = user.email.strip().lower()
        if email != user.email:
            User.objects.using(schema_editor.connection.alias).filter(pk=user.pk).update(
                email=email
            )


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]
    operations = [
        migrations.RunPython(normalise_emails, migrations.RunPython.noop),
        migrations.AlterModelManagers(
            name="user", managers=[("objects", accounts.models.UserManager())]
        ),
        migrations.AlterField(
            model_name="user",
            name="email",
            field=models.EmailField(max_length=254, unique=True, verbose_name="email address"),
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.CheckConstraint(
                condition=~models.Q(email=""), name="user_email_not_empty"
            ),
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.CheckConstraint(
                condition=models.Q(email=Lower(Trim("email"))), name="user_email_normalised"
            ),
        ),
        migrations.AlterModelOptions(
            name="user",
            options={
                "verbose_name": "user",
                "verbose_name_plural": "users",
                "permissions": [
                    ("access_customer_area", "Can access the customer area"),
                    ("access_manager_area", "Can access the manager area"),
                    ("access_admin_area", "Can access the administrator area"),
                ],
            },
        ),
    ]
