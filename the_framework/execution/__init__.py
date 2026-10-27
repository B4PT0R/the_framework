"""Explicit local/host process channels, independent of agent inference."""

from .launch import ExecutionTarget, launch_process
from .process import LocalProcess, ProcessHost, RemoteProcess

__all__ = ["LocalProcess", "ProcessHost", "RemoteProcess", "ExecutionTarget", "launch_process"]
