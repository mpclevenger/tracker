from django.db import models

from core.versioning import VersionedModel


class Role(models.TextChoices):
    TECH_RISK = "tech_risk", "Tech Risk"
    OWNER = "owner", "Owner"
    SENIOR_MANAGEMENT = "senior_management", "Senior Management"
    AUDITOR = "auditor", "Auditor"


class Person(models.Model):
    """Directory of known employees. Populated by the user switcher in dev and by SSO in prod."""

    emp_id = models.CharField(max_length=32, primary_key=True)
    display_name = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_name"]

    def __str__(self):
        return f"{self.display_name} ({self.emp_id})"


class RoleAssignment(VersionedModel):
    """Employee ID -> role. Global roles have initiative_entity = NULL.

    Owner access is also granted implicitly by Initiative.owner_emp_id and Delegation rows;
    an explicit owner RoleAssignment is optional and scoped to one initiative.
    """

    emp_id = models.CharField(max_length=32, db_index=True)
    role = models.CharField(max_length=32, choices=Role.choices)
    initiative_entity = models.UUIDField(null=True, blank=True, db_index=True)

    class Meta(VersionedModel.Meta):
        pass

    def __str__(self):
        scope = f" on {self.initiative_entity}" if self.initiative_entity else ""
        return f"{self.emp_id}: {self.get_role_display()}{scope}"


class ModuleSettings(VersionedModel):
    """Versioned singleton: on/off switches for optional modules. No row means all on."""

    owner_access = models.BooleanField(default=True)
    meetings = models.BooleanField(default=True)
    audit = models.BooleanField(default=True)
    share_links = models.BooleanField(default=True)
    change_note = models.CharField(max_length=300, blank=True)

    class Meta(VersionedModel.Meta):
        pass
