from django.urls import path

from tracker.views import audit, exec_summary, initiative, intake, lifecycle, owner, watch, workbench

urlpatterns = [
    # exec summary (landing for Tech Risk, Senior Management, Auditor)
    path("", exec_summary.home, name="home"),
    path("exec/", exec_summary.exec_summary, name="exec_summary"),
    path("share/<str:token>/", exec_summary.shared, name="exec_shared"),
    # initiative page (all roles; owner sees edit affordances)
    path("initiative/<uuid:entity_id>/", initiative.detail, name="initiative"),
    path("initiative/<uuid:entity_id>/override/", initiative.override_modal, name="override_modal"),
    path("initiative/<uuid:entity_id>/override/set/", initiative.override_set, name="override_set"),
    path("initiative/<uuid:entity_id>/override/clear/", initiative.override_clear, name="override_clear"),
    path("initiative/<uuid:entity_id>/visibility/<str:kind>/<uuid:item_entity>/", initiative.set_visibility, name="set_visibility"),
    path("initiative/<uuid:entity_id>/overlay/add/", initiative.overlay_add, name="overlay_add"),
    path("initiative/<uuid:entity_id>/overlay/<uuid:item_entity>/remove/", initiative.overlay_remove, name="overlay_remove"),
    path("initiative/<uuid:entity_id>/overlay/<uuid:item_entity>/move/<str:direction>/", initiative.overlay_move, name="overlay_move"),
    path("initiative/<uuid:entity_id>/stage/<int:stage>/item/<int:item>/<str:action>/", lifecycle.item_action, name="stage_item"),
    path("initiative/<uuid:entity_id>/stage/<int:stage>/gate/", lifecycle.record_gate, name="stage_gate"),
    path("initiative/<uuid:entity_id>/history/", audit.history, name="initiative_history"),
    path("initiative/<uuid:entity_id>/oversight/", initiative.oversight_modal, name="oversight_modal"),
    path("initiative/<uuid:entity_id>/people/", initiative.edit_people, name="edit_people"),
    path("initiative/<uuid:entity_id>/control/<uuid:item_entity>/review/", initiative.review_control, name="review_control"),
    path("initiative/<uuid:entity_id>/checkin/", owner.check_in, name="check_in"),
    path("modal/close/", initiative.close_modal, name="close_modal"),
    # owner editing
    path("my/", owner.my_initiative, name="my_initiative"),
    path("initiative/<uuid:entity_id>/edit/", owner.edit_initiative, name="edit_initiative"),
    path("initiative/<uuid:entity_id>/phases/", owner.edit_phases, name="edit_phases"),
    path("initiative/<uuid:entity_id>/milestones/", owner.edit_milestones, name="edit_milestones"),
    path("initiative/<uuid:entity_id>/risks/", owner.edit_risks, name="edit_risks"),
    path("initiative/<uuid:entity_id>/controls/", owner.edit_controls, name="edit_controls"),
    # intake
    path("intake/", intake.intake_form, name="intake"),
    path("intake/<uuid:entity_id>/accept/", intake.accept, name="intake_accept"),
    path("intake/<uuid:entity_id>/return/", intake.return_to_owner, name="intake_return"),
    # watch list
    path("watch/", watch.watch_list, name="watch_list"),
    path("watch/add/", watch.watch_add, name="watch_add"),
    path("watch/<uuid:entity_id>/", watch.watch_item, name="watch_item"),
    path("watch/<uuid:entity_id>/edit/", watch.watch_edit, name="watch_edit"),
    path("watch/<uuid:entity_id>/confirm/", watch.watch_confirm, name="watch_confirm"),
    path("watch/<uuid:entity_id>/drop/", watch.watch_drop, name="watch_drop"),
    path("watch/<uuid:entity_id>/delete/", watch.watch_delete, name="watch_delete"),
    # workbench (Tech Risk)
    path("workbench/", workbench.triage, name="workbench"),
    path("workbench/snapshots/", workbench.snapshots_tab, name="workbench_snapshots"),
    path("workbench/snapshots/take/", workbench.take_snapshot, name="take_snapshot"),
    path("workbench/admin/", workbench.admin_tab, name="workbench_admin"),
    path("workbench/admin/controls/upload/", workbench.upload_controls, name="upload_controls"),
    path("workbench/admin/controls/template.xlsx", workbench.control_template, name="control_template"),
    path("workbench/admin/controls/<uuid:entity_id>/trac/", workbench.control_trac_link, name="control_trac_link"),
    path("workbench/remind/<uuid:entity_id>/", workbench.reminder_modal, name="reminder_modal"),
    path("workbench/admin/thresholds/", workbench.set_threshold, name="set_threshold"),
    path("workbench/admin/gates/", workbench.set_gate_settings, name="set_gate_settings"),
    path("workbench/admin/modules/", workbench.set_modules, name="set_modules"),
    path("workbench/admin/roles/", workbench.roles, name="roles"),
    path("workbench/admin/roles/<uuid:entity_id>/retire/", workbench.retire_role, name="retire_role"),
    path("initiative/<uuid:entity_id>/close/", workbench.close_initiative, name="close_initiative"),
    path("initiative/<uuid:entity_id>/delete/", workbench.delete_initiative, name="delete_initiative"),
    path("initiative/<uuid:entity_id>/archive/", workbench.archive_initiative, name="archive_initiative"),
    # lifecycle & guidance
    path("lifecycle/", lifecycle.guidance_page, name="lifecycle"),
    path("lifecycle/<int:stage>/edit/", lifecycle.edit_guidance, name="edit_guidance"),
    # audit
    path("audit/", audit.audit_home, name="audit"),
    path("audit/snapshot/<int:pk>/", audit.snapshot_record, name="snapshot_record"),
]
