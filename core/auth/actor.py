"""The Actor is the resolved identity + permissions for the current request."""

from dataclasses import dataclass, field, replace
from uuid import UUID

from core.models import Person, Role, RoleAssignment


@dataclass(frozen=True)
class Actor:
    emp_id: str = ""
    display_name: str = ""
    roles: frozenset = field(default_factory=frozenset)
    owned_initiatives: frozenset = field(default_factory=frozenset)
    led_initiatives: frozenset = field(default_factory=frozenset)  # subset of owned: led only, not owned or delegated

    @property
    def is_authenticated(self):
        return bool(self.emp_id)

    @property
    def is_tech_risk(self):
        return Role.TECH_RISK in self.roles

    @property
    def is_auditor(self):
        return Role.AUDITOR in self.roles

    @property
    def is_senior_management(self):
        return Role.SENIOR_MANAGEMENT in self.roles

    @property
    def is_any_owner(self):
        return bool(self.owned_initiatives)

    def is_owner_of(self, initiative_entity: UUID) -> bool:
        return initiative_entity in self.owned_initiatives

    # --- visibility ------------------------------------------------------------

    @property
    def sees_internal(self):
        """Internal notes and the derived-vs-override pair."""
        return self.is_tech_risk

    @property
    def sees_override_detail(self):
        """Override badge, derived RAG and the override reason."""
        return self.is_tech_risk or self.is_auditor

    @property
    def sees_exec_pages(self):
        return self.is_tech_risk or self.is_auditor or self.is_senior_management

    def visible_layers(self, initiative_entity: UUID | None = None) -> frozenset:
        """Visibility layers this actor may read for a given initiative."""
        if self.is_tech_risk:
            return frozenset({"internal", "owner", "exec"})
        layers = set()
        if self.is_auditor or self.is_senior_management:
            layers.add("exec")
        if initiative_entity is not None and self.is_owner_of(initiative_entity):
            layers.update({"owner", "exec"})
        return frozenset(layers)

    def can_edit_initiative(self, initiative_entity: UUID) -> bool:
        """Edit the plan and manage workstreams: Tech Risk, the owner and delegates, not workstream leads."""
        return self.is_tech_risk or (self.is_owner_of(initiative_entity) and initiative_entity not in self.led_initiatives)

    def can_check_in(self, initiative_entity: UUID) -> bool:
        return self.is_tech_risk or self.is_owner_of(initiative_entity)


ANONYMOUS = Actor()


def resolve_actor(emp_id: str | None) -> Actor:
    if not emp_id:
        return ANONYMOUS
    # Imported here to avoid an import cycle with tracker.models at startup.
    from tracker.models import Delegation, Initiative, Workstream

    person = Person.objects.filter(pk=emp_id).first()
    roles = frozenset(
        RoleAssignment.objects.current()
        .filter(emp_id=emp_id, initiative_entity__isnull=True)
        .values_list("role", flat=True)
    )
    owned = set(
        Initiative.objects.current()
        .listed()
        .filter(owner_emp_id=emp_id)
        .values_list("entity_id", flat=True)
    )
    owned.update(
        Delegation.objects.current()
        .filter(delegate_emp_id=emp_id)
        .values_list("initiative_entity", flat=True)
    )
    owned.update(
        RoleAssignment.objects.current()
        .filter(emp_id=emp_id, role=Role.OWNER, initiative_entity__isnull=False)
        .values_list("initiative_entity", flat=True)
    )
    led = set(
        Workstream.objects.current().filter(lead_emp_id=emp_id).values_list("initiative_entity", flat=True)
    ) - owned
    owned.update(led)
    return Actor(
        emp_id=emp_id,
        display_name=person.display_name if person else emp_id,
        roles=roles,
        owned_initiatives=frozenset(owned),
        led_initiatives=frozenset(led),
    )


def apply_modules(actor: Actor, modules: dict) -> Actor:
    """Drop auditor and owner rights for switched-off modules; Tech Risk is unchanged."""
    roles, owned, led = actor.roles, actor.owned_initiatives, actor.led_initiatives
    if not modules.get("audit", True):
        roles = roles - {Role.AUDITOR}
    if not modules.get("owner_access", True) and not actor.is_tech_risk:
        owned, led = frozenset(), frozenset()
    if roles == actor.roles and owned == actor.owned_initiatives:
        return actor
    return replace(actor, roles=roles, owned_initiatives=owned, led_initiatives=led)
