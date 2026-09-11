"""Authenticated execution-worker boundary."""

from gateway.workers.service import InProcessWorkerClient, WorkerService

__all__ = ["InProcessWorkerClient", "WorkerService"]
