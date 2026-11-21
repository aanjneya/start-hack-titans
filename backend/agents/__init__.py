"""All agents. Importing this package registers every agent class in base.REGISTRY."""
from . import chaser, ingest, reconciler, risk, planner, watcher  # noqa: F401
from .base import REGISTRY, Agent, register  # noqa: F401

from .planner_delivery import PlanningReplyIngest

# Extend the existing Ingest subscription in place so each file and reply is processed once.
REGISTRY[REGISTRY.index(ingest.IngestNormalizer)] = PlanningReplyIngest
