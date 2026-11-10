from django import forms

from meetings.models import MeetingSeries

_CTRL = {"class": "form-control form-control-sm"}
_DATE = forms.DateInput(attrs={"type": "date", "class": "form-control form-control-sm"}, format="%Y-%m-%d")


class MeetingForm(forms.Form):
    held_on = forms.DateField(label="Date", widget=_DATE)
    stage = forms.TypedChoiceField(label="Stage", coerce=int, widget=forms.Select(attrs={"class": "form-select form-select-sm"}))
    series = forms.ChoiceField(
        label="Series", required=False, widget=forms.Select(attrs={"class": "form-select form-select-sm"})
    )
    what = forms.CharField(label="What", max_length=200, widget=forms.TextInput(attrs={**_CTRL, "placeholder": "Topic or purpose"}))
    who = forms.CharField(label="Who", widget=forms.Textarea(attrs={**_CTRL, "rows": 2, "placeholder": "Attendees"}))
    result = forms.CharField(
        label="Result / next steps", widget=forms.Textarea(attrs={**_CTRL, "rows": 4, "placeholder": "Outcome and decisions"})
    )
    initiatives = forms.MultipleChoiceField(
        label="Linked initiatives", required=False, widget=forms.CheckboxSelectMultiple
    )

    def __init__(self, *args, initiative_choices=(), stage_choices=(), series_choices=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["initiatives"].choices = initiative_choices
        self.fields["stage"].choices = stage_choices
        self.fields["series"].choices = [("", "None (one-off)"), *series_choices]


class SeriesForm(forms.Form):
    name = forms.CharField(label="Name", max_length=200, widget=forms.TextInput(attrs=_CTRL))
    cadence = forms.ChoiceField(
        label="Cadence", choices=MeetingSeries.Cadence.choices,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    usual_attendees = forms.CharField(
        label="Usual attendees", required=False, widget=forms.Textarea(attrs={**_CTRL, "rows": 2})
    )
    initiatives = forms.MultipleChoiceField(
        label="Initiatives", required=False, widget=forms.CheckboxSelectMultiple
    )
    default_stage = forms.TypedChoiceField(
        label="Default stage", required=False, coerce=int, empty_value=None,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    description = forms.CharField(
        label="Description", max_length=300, required=False, widget=forms.TextInput(attrs=_CTRL)
    )

    def __init__(self, *args, initiative_choices=(), stage_choices=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["initiatives"].choices = initiative_choices
        self.fields["default_stage"].choices = [("", "None"), *stage_choices]


class FollowUpForm(forms.Form):
    entity_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    text = forms.CharField(widget=forms.TextInput(attrs=_CTRL))
    owner = forms.CharField(max_length=200, required=False, widget=forms.TextInput(attrs=_CTRL))
    due_date = forms.DateField(required=False, widget=_DATE)
    initiative = forms.ChoiceField(required=False, widget=forms.Select(attrs={"class": "form-select form-select-sm"}))

    def __init__(self, *args, initiative_choices=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["initiative"].choices = [("", "—"), *initiative_choices]


class CloseForm(forms.Form):
    closed_on = forms.DateField(label="Closed on", widget=_DATE)
    closure_note = forms.CharField(label="Closure note", widget=forms.Textarea(attrs={**_CTRL, "rows": 3}))
