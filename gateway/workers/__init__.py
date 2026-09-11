"""Authenticated execution-worker boundary."""

from gateway.workers.remote import RemoteWorkerClient
from gateway.workers.service import InProcessWorkerClient, WorkerService

__all__ = ["InProcessWorkerClient", "RemoteWorkerClient", "WorkerService"]
