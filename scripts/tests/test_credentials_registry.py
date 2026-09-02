"""Credential registry contract (scripts/credentials_registry.py).

The registry is the single declared surface of UI-manageable credentials.
Like the preferences registry, it must be honest: every service either has a
real validator or an explicit note saying why it cannot be checked live.
"""

import re

import credential_validators
from credentials_registry import (
    GROUP_ORDER,
    SERVICES,
    fields_by_name,
    service_by_id,
)

NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class TestRegistryShape:
    def test_service_ids_unique(self):
        ids = [s.id for s in SERVICES]
        assert len(ids) == len(set(ids))

    def test_field_names_globally_unique(self):
        names = [f.name for s in SERVICES for f in s.fields]
        assert len(names) == len(set(names))

    def test_every_group_is_declared(self):
        for service in SERVICES:
            assert service.group in GROUP_ORDER

    def test_field_names_are_valid_secret_names(self):
        for service in SERVICES:
            for field in service.fields:
                assert NAME_RE.match(field.name), field.name

    def test_every_service_has_fields_and_labels(self):
        for service in SERVICES:
            assert service.label
            assert len(service.fields) >= 1
            for field in service.fields:
                assert field.label


class TestRegistryIsHonest:
    def test_every_validator_id_resolves(self):
        for service in SERVICES:
            if service.validator is not None:
                assert service.validator in credential_validators.VALIDATORS, (
                    f"{service.id} declares validator {service.validator!r} "
                    "which does not exist"
                )

    def test_slow_implies_validator(self):
        for service in SERVICES:
            if service.slow:
                assert service.validator is not None

    def test_unvalidatable_services_carry_a_note(self):
        for service in SERVICES:
            if service.validator is None:
                assert service.note, (
                    f"{service.id} has no validator and no note explaining why"
                )

    def test_ib_flex_is_never_live_validated(self):
        """The Flex token already took a 24h-168h throttle embargo once."""
        assert service_by_id("ib_flex").validator is None

    def test_ib_gateway_is_never_live_validated(self):
        """A login probe fires a real IBKR Mobile 2FA push."""
        assert service_by_id("ib_gateway").validator is None


class TestExpectedSurface:
    def test_core_credentials_present(self):
        names = fields_by_name()
        for expected in (
            "UW_TOKEN",
            "ANTHROPIC_API_KEY",
            "EXA_API_KEY",
            "CEREBRAS_API_KEY",
            "XAI_API_KEY",
            "MENTHORQ_USER",
            "MENTHORQ_PASS",
            "THEMARKETEAR_EMAIL",
            "THEMARKETEAR_PASSWORD",
            "TURSO_DB_URL",
            "TURSO_AUTH_TOKEN",
            "CLERK_SECRET_KEY",
            "PUSHOVER_USER",
            "PUSHOVER_TOKEN",
            "IB_FLEX_TOKEN",
            "TWS_USERID",
            "TWS_PASSWORD",
        ):
            assert expected in names, expected

    def test_lookup_helpers(self):
        service = service_by_id("anthropic")
        assert service.id == "anthropic"
        assert service_by_id("nope") is None
        assert fields_by_name()["UW_TOKEN"].secret is True

    def test_identifier_fields_marked_not_secret(self):
        names = fields_by_name()
        assert names["MENTHORQ_USER"].secret is False
        assert names["TURSO_DB_URL"].secret is False
        assert names["MENTHORQ_PASS"].secret is True
        assert names["TURSO_AUTH_TOKEN"].secret is True
