from django.db import migrations


def assign_existing_customers(apps, schema_editor):
    alias = schema_editor.connection.alias
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("accounts", "User")
    customer, _ = Group.objects.using(alias).get_or_create(name="CUSTOMER")
    for user in User.objects.using(alias).filter(
        is_staff=False, is_superuser=False, groups__isnull=True
    ):
        user.groups.add(customer)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0002_normalised_unique_email")]
    operations = [migrations.RunPython(assign_existing_customers, migrations.RunPython.noop)]
