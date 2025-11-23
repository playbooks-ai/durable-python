"""Custom exceptions for the durable-python package."""


class CheckpointException(Exception):
    """Raised when execution is paused at a checkpoint."""

    def __init__(self, checkpoint: int, namespace: dict):
        self.checkpoint = checkpoint
        self.namespace = namespace
        super().__init__(f"Execution paused at checkpoint {checkpoint}")


class PauseForEventException(Exception):
    """Raised when execution is waiting for an external event."""

    def __init__(self, event: str):
        self.event = event
        super().__init__(f"Execution waiting for event {event}")


class AlreadyDurableException(Exception):
    """Raised when attempting to transform a function that is already durable."""

    def __init__(self):
        super().__init__("Function is already durable")


class NoSourceCodeException(Exception):
    """Raised when a function has no source code and cannot be made durable."""

    def __init__(self):
        super().__init__("Function has no source code, cannot make durable")
