"""What the pipeline asks a model to do, and what each job needs from one.

Call sites name a purpose rather than a model. Classification wants a cheap
model that returns strict JSON for a hundred records at a time; a written
summary wants a stronger one; ranking wants something in between. Naming models
at the call site would mean that choice could only be revised by editing every
module that makes one.

Each purpose also declares how many requests one run of it costs. That is what
lets the daily allowance be checked before a run starts rather than discovered
halfway through — the pipeline has already spent a day's quota mid-cycle and
carried on as though nothing had happened.
"""
from __future__ import annotations

from dataclasses import dataclass

PURPOSE_CLASSIFY = "classify"
PURPOSE_PULSE = "pulse"
PURPOSE_PUBLISH = "publish"
PURPOSE_PUSH = "push"


@dataclass(frozen=True)
class Purpose:
    name: str
    label: str
    #: What this job needs, in plain terms, for the admin screen that chooses
    #: the model. Somebody picking between models should not have to read the
    #: pipeline to know what they are choosing for.
    needs: str
    #: Requests one run costs, assuming the batching each caller does. Used to
    #: warn before a run rather than to enforce.
    calls_per_run: int


PURPOSES: dict[str, Purpose] = {
    PURPOSE_CLASSIFY: Purpose(
        name=PURPOSE_CLASSIFY,
        label="Signal classification",
        needs="Strict JSON for up to a hundred records in one call. Cheap and fast "
              "matters more than eloquence; the output is fields, not prose.",
        calls_per_run=1,
    ),
    PURPOSE_PULSE: Purpose(
        name=PURPOSE_PULSE,
        label="Market Pulse",
        needs="A short written read on the week that may interpret, not merely "
              "restate. The one place where writing quality is the point.",
        calls_per_run=1,
    ),
    PURPOSE_PUBLISH: Purpose(
        name=PURPOSE_PUBLISH,
        label="Mode Publish reports",
        needs="Client-facing prose from figures that are already settled. Accuracy "
              "to the supplied numbers matters more than range.",
        calls_per_run=1,
    ),
    PURPOSE_PUSH: Purpose(
        name=PURPOSE_PUSH,
        label="Mode Push rationale",
        needs="One batched call covering the whole ranked list: a sentence a "
              "consultant can send, and a flag where the numbers and the text "
              "disagree. Never scores — the score stays deterministic.",
        calls_per_run=1,
    ),
}
