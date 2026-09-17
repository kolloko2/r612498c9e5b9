"""Optional fail-closed LDAP/AD credential verification.

Directory authentication proves only that a directory accepted a password.  It
does not create an application user or assign an application role.
"""

from __future__ import annotations

import os
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol
from urllib.parse import urlsplit


_USER_ATTRIBUTES = {"uid": "uid", "samaccountname": "sAMAccountName"}
_DEFAULT_CONNECT_TIMEOUT = 5
_DEFAULT_OPERATION_TIMEOUT = 5
_MIN_TIMEOUT = 1
_MAX_TIMEOUT = 15


@dataclass(frozen=True)
class DirectoryConfig:
    host: str
    port: int
    bind_dn: str
    bind_password: str
    base_dn: str
    user_attribute: str
    ca_cert_file: str
    connect_timeout: int
    operation_timeout: int

    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> "DirectoryConfig | None":
        """Return a complete, secure configuration or ``None``.

        Invalid and partial configuration is deliberately indistinguishable from
        absent configuration to callers.  Authentication consequently fails
        closed without exposing configuration details through the login path.
        """

        url = environ.get("LDAP_URL", "").strip()
        bind_dn = environ.get("LDAP_BIND_DN", "").strip()
        bind_password = environ.get("LDAP_BIND_PASSWORD", "")
        base_dn = environ.get("LDAP_BASE_DN", "").strip()
        attribute = _USER_ATTRIBUTES.get(environ.get("LDAP_USER_ATTRIBUTE", "").strip().lower())
        ca_cert_file = environ.get("LDAP_CA_CERT_FILE", "").strip()
        if not all((url, bind_dn, bind_password, base_dn, attribute, ca_cert_file)):
            return None

        try:
            parsed = urlsplit(url)
            port = parsed.port or 636
            connect_timeout = _timeout(environ.get("LDAP_CONNECT_TIMEOUT"), _DEFAULT_CONNECT_TIMEOUT)
            operation_timeout = _timeout(environ.get("LDAP_OPERATION_TIMEOUT"), _DEFAULT_OPERATION_TIMEOUT)
        except (TypeError, ValueError):
            return None

        # No downgrade, embedded credentials, URL options, or ambiguous paths.
        if (
            parsed.scheme.lower() != "ldaps"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or not 1 <= port <= 65535
            or not Path(ca_cert_file).is_file()
        ):
            return None

        return cls(
            host=parsed.hostname,
            port=port,
            bind_dn=bind_dn,
            bind_password=bind_password,
            base_dn=base_dn,
            user_attribute=attribute,
            ca_cert_file=ca_cert_file,
            connect_timeout=connect_timeout,
            operation_timeout=operation_timeout,
        )


def _timeout(raw: str | None, default: int) -> int:
    value = default if raw is None or not raw.strip() else int(raw)
    if not _MIN_TIMEOUT <= value <= _MAX_TIMEOUT:
        raise ValueError("directory timeout outside safe bounds")
    return value


class DirectoryBackend(Protocol):
    def authenticate(self, config: DirectoryConfig, username: str, password: str) -> bool: ...


class Ldap3Backend:
    """ldap3 implementation, imported only when directory auth is attempted."""

    def __init__(self, ldap3_module=None):
        self._ldap3_module = ldap3_module

    def _ldap3(self):
        if self._ldap3_module is None:
            import ldap3  # Optional dependency; intentionally lazy.

            self._ldap3_module = ldap3
        return self._ldap3_module

    def authenticate(self, config: DirectoryConfig, username: str, password: str) -> bool:
        ldap3 = self._ldap3()
        tls = ldap3.Tls(
            validate=ssl.CERT_REQUIRED,
            ca_certs_file=config.ca_cert_file,
            valid_names=[config.host],
        )
        server = ldap3.Server(
            config.host,
            port=config.port,
            use_ssl=True,
            tls=tls,
            connect_timeout=config.connect_timeout,
            get_info=ldap3.NONE,
        )
        service = ldap3.Connection(
            server,
            user=config.bind_dn,
            password=config.bind_password,
            authentication=ldap3.SIMPLE,
            receive_timeout=config.operation_timeout,
            auto_referrals=False,
            raise_exceptions=True,
        )
        try:
            if not service.bind():
                return False
            escaped_username = ldap3.utils.conv.escape_filter_chars(username)
            found = service.search(
                search_base=config.base_dn,
                search_filter=f"({config.user_attribute}={escaped_username})",
                search_scope=ldap3.SUBTREE,
                attributes=[ldap3.NO_ATTRIBUTES],
                size_limit=2,
                time_limit=config.operation_timeout,
            )
            if not found or len(service.entries) != 1:
                return False
            user_dn = str(service.entries[0].entry_dn)
        finally:
            service.unbind()

        user = ldap3.Connection(
            server,
            user=user_dn,
            password=password,
            authentication=ldap3.SIMPLE,
            receive_timeout=config.operation_timeout,
            auto_referrals=False,
            raise_exceptions=True,
        )
        try:
            return bool(user.bind())
        finally:
            user.unbind()


class DirectoryAuthenticator:
    """Configuration boundary with an injectable transport for focused tests."""

    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        backend: DirectoryBackend | None = None,
    ):
        self._environ = os.environ if environ is None else environ
        self._backend = backend or Ldap3Backend()

    @property
    def configured(self) -> bool:
        return DirectoryConfig.from_environment(self._environ) is not None

    def status(self) -> dict[str, bool]:
        return {"configured": self.configured}

    def authenticate(self, username: str, password: str) -> bool:
        config = DirectoryConfig.from_environment(self._environ)
        if config is None or not _valid_credential_input(username, password):
            return False
        try:
            return bool(self._backend.authenticate(config, username, password))
        except Exception:
            # Login callers receive one generic credential failure.  In particular,
            # never leak LDAP bind diagnostics, DNs, usernames, or passwords here.
            return False


def _valid_credential_input(username: str, password: str) -> bool:
    return bool(
        isinstance(username, str)
        and isinstance(password, str)
        and username
        and username == username.strip()
        and len(username) <= 256
        and "\x00" not in username
        and password
        and len(password) <= 1024
        and "\x00" not in password
    )


def status() -> dict[str, bool]:
    """Return only whether complete secure directory configuration is present."""

    return DirectoryAuthenticator().status()


def authenticate(username: str, password: str) -> bool:
    """Authenticate against the configured directory, returning only a boolean."""

    return DirectoryAuthenticator().authenticate(username, password)
