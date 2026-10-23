from __future__ import annotations


class TruthGuardError(Exception):
    """Base class for expected, user-presentable errors."""


class StructuredOutputError(TruthGuardError):
    """An LLM failed to return schema-valid output after the repair retry (fail closed)."""


class ProviderUnavailableError(TruthGuardError):
    """Every provider in the fallback chain failed or is open-circuited."""


class OfflineModeError(TruthGuardError):
    """A live LLM call was requested while LLM_MODE=offline."""


class ReplayMissError(TruthGuardError):
    """LLM_MODE=replay and no cached response exists for the prompt."""


class IngestError(TruthGuardError):
    pass


class UploadRejectedError(TruthGuardError):
    pass


class NotFoundError(TruthGuardError):
    pass
