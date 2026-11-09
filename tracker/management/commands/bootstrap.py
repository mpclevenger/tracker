"""First-run setup: lifecycle guidance v1.0 and, outside open mode, the first Tech Risk user."""

from django.conf import settings
from django.core.management.base import BaseCommand

from core.models import Person, Role, RoleAssignment
from tracker.services import lifecycle


class Command(BaseCommand):
    help = "Seed lifecycle guidance; optionally grant the Tech Risk role to the first administrator"

    def add_arguments(self, parser):
        parser.add_argument(
            "--tech-risk", metavar="EMP_ID",
            help="Employee ID to grant the Tech Risk role (needed for AUTH_MODE=dev or sso, not for open)",
        )
        parser.add_argument("--name", default="", help="Display name for that employee")

    def handle(self, *args, **options):
        emp_id = options.get("tech_risk")
        if emp_id:
            Person.objects.get_or_create(pk=emp_id, defaults={"display_name": options["name"] or emp_id})
            exists = RoleAssignment.objects.current().filter(
                emp_id=emp_id, role=Role.TECH_RISK, initiative_entity__isnull=True
            ).exists()
            if not exists:
                RoleAssignment.create(emp_id, emp_id=emp_id, role=Role.TECH_RISK)
                self.stdout.write(f"Granted Tech Risk to {emp_id}.")
            else:
                self.stdout.write(f"{emp_id} already has Tech Risk.")
        author = emp_id or "SYSTEM"
        if lifecycle.current_guidance():
            self.stdout.write("Lifecycle guidance already present.")
        else:
            lifecycle.seed_guidance(author)
            self.stdout.write("Lifecycle guidance v1.0 seeded.")
        has_tech_risk = RoleAssignment.objects.current().filter(role=Role.TECH_RISK).exists()
        if settings.AUTH_MODE == "open":
            self.stdout.write("AUTH_MODE=open: every visitor is Tech Risk; no role assignment needed.")
        elif not has_tech_risk:
            self.stdout.write(self.style.WARNING(
                "No Tech Risk user exists. Before switching to AUTH_MODE=dev or sso, run: "
                "manage.py bootstrap --tech-risk EMP_ID --name 'Name'"
            ))
