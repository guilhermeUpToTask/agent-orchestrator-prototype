"""Unit tests for the unified exception taxonomy (Phase 1)."""
from __future__ import annotations

from src.app.errors import (
    ExternalServiceException,
    ForbiddenException,
    InfrastructureException,
    ResourceNotFoundException,
    UnauthorizedException,
    ValidationException,
)
from src.domain.errors import (
    BaseAppException,
    ConflictException,
    DomainError,
    DomainException,
    InvalidStatusTransitionError,
    ReferentialException,
)


class TestBaseAppException:
    def test_carries_code_message_context(self) -> None:
        exc = BaseAppException("boom", code="X", context={"k": "v"})
        assert exc.message == "boom"
        assert exc.code == "X"
        assert exc.context == {"k": "v"}
        assert str(exc) == "boom"

    def test_default_code_and_empty_context(self) -> None:
        exc = BaseAppException("boom")
        assert exc.code == "INTERNAL_ERROR"
        assert exc.context == {}


class TestDomainErrors:
    def test_domain_error_is_base_app_exception(self) -> None:
        assert issubclass(DomainError, BaseAppException)
        assert DomainException is DomainError

    def test_existing_domain_errors_still_speak_one_language(self) -> None:
        exc = InvalidStatusTransitionError("t1", "created", ["assigned"])
        assert isinstance(exc, DomainError)
        assert isinstance(exc, BaseAppException)
        assert isinstance(exc, ValueError)  # back-compat catch-sites

    def test_conflict_exception_records_versions(self) -> None:
        exc = ConflictException("stale", expected_version=3, actual_version=5)
        assert exc.code == "CONFLICT"
        assert exc.context["expected_version"] == 3
        assert exc.context["actual_version"] == 5

    def test_referential_exception(self) -> None:
        exc = ReferentialException("still referenced")
        assert exc.code == "REFERENTIAL_CONSTRAINT"
        assert isinstance(exc, DomainError)


class TestAppExceptions:
    def test_codes(self) -> None:
        assert ValidationException("x").code == "VALIDATION_ERROR"
        assert ResourceNotFoundException("x").code == "NOT_FOUND"
        assert UnauthorizedException("x").code == "UNAUTHORIZED"
        assert ForbiddenException("x").code == "FORBIDDEN"
        assert ExternalServiceException("x").code == "EXTERNAL_SERVICE_ERROR"
        assert InfrastructureException("x").code == "INFRASTRUCTURE_ERROR"

    def test_all_share_base(self) -> None:
        for cls in (
            ValidationException,
            ResourceNotFoundException,
            UnauthorizedException,
            ForbiddenException,
            ExternalServiceException,
            InfrastructureException,
        ):
            assert issubclass(cls, BaseAppException)

    def test_per_instance_code_override(self) -> None:
        exc = ResourceNotFoundException("nope", code="PROJECT_NOT_FOUND")
        assert exc.code == "PROJECT_NOT_FOUND"
