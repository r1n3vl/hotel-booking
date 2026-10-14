from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import UserManager as DjangoUserManager
from django.db import models
from django.db.models.functions import Lower, Trim


class UserManager(DjangoUserManager):
    @classmethod
    def normalize_email(cls, email):
        return (email or "").strip().lower()


class User(AbstractUser):
    email = models.EmailField("email address", unique=True)

    objects = UserManager()

    class Meta(AbstractUser.Meta):
        constraints = [
            models.CheckConstraint(condition=~models.Q(email=""), name="user_email_not_empty"),
            models.CheckConstraint(
                condition=models.Q(email=Lower(Trim("email"))),
                name="user_email_normalised",
            ),
        ]
        permissions = [
            ("access_customer_area", "Can access the customer area"),
            ("access_manager_area", "Can access the manager area"),
            ("access_admin_area", "Can access the administrator area"),
        ]

    def clean_fields(self, exclude=None):
        self.email = UserManager.normalize_email(self.email)
        return super().clean_fields(exclude=exclude)

    def clean(self):
        super().clean()
        self.email = UserManager.normalize_email(self.email)

    def save(self, *args, **kwargs):
        self.email = UserManager.normalize_email(self.email)
        return super().save(*args, **kwargs)
