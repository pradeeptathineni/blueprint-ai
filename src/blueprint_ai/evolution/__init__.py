"""Public evolution planning and transaction surface."""

from .catalog import TRANSFORMATIONS, catalog_data
from .engine import (
    accept_evolution,
    apply_evolution,
    load_plan,
    plan_evolution,
    project_fingerprint,
    rollback_evolution,
)
from .models import (
    AuthoritativeToolContract,
    EvolutionPlan,
    EvolutionReport,
    ResidualContract,
    TransformationSpec,
    TypedPostcondition,
)

__all__ = [
    "AuthoritativeToolContract",
    "EvolutionPlan",
    "EvolutionReport",
    "ResidualContract",
    "TRANSFORMATIONS",
    "TransformationSpec",
    "TypedPostcondition",
    "accept_evolution",
    "apply_evolution",
    "catalog_data",
    "load_plan",
    "plan_evolution",
    "project_fingerprint",
    "rollback_evolution",
]
