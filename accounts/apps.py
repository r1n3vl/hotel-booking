from django.apps import AppConfig
from django.db.models.signals import post_migrate


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"

    def ready(self):
        from .roles import provision_roles_after_migrate

        post_migrate.connect(
            provision_roles_after_migrate,
            dispatch_uid="accounts.provision_roles",
        )
