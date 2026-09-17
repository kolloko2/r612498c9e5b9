import ssl
from pathlib import Path
from types import SimpleNamespace

import directory_auth


def _environment(tmp_path: Path, **overrides: str) -> dict[str, str]:
    ca = tmp_path / "directory-ca.pem"
    ca.write_text("test CA placeholder", encoding="utf-8")
    values = {
        "LDAP_URL": "ldaps://directory:636",
        "LDAP_BIND_DN": "cn=lookup,dc=trainer,dc=local",
        "LDAP_BIND_PASSWORD": "injected-service-secret",
        "LDAP_BASE_DN": "ou=people,dc=trainer,dc=local",
        "LDAP_USER_ATTRIBUTE": "uid",
        "LDAP_CA_CERT_FILE": str(ca),
    }
    values.update(overrides)
    return values


class RecordingBackend:
    def __init__(self, result=True, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def authenticate(self, config, username, password):
        self.calls.append((config, username, password))
        if self.error:
            raise self.error
        return self.result


def test_injected_backend_is_used_only_with_complete_secure_config(tmp_path):
    backend = RecordingBackend()
    auth = directory_auth.DirectoryAuthenticator(environ=_environment(tmp_path), backend=backend)

    assert auth.status() == {"configured": True}
    assert auth.authenticate("trainer.test", "directory-password") is True
    config, username, password = backend.calls[0]
    assert (config.host, config.port, config.user_attribute) == ("directory", 636, "uid")
    assert (username, password) == ("trainer.test", "directory-password")


def test_missing_invalid_or_plain_ldap_configuration_fails_closed(tmp_path):
    cases = [
        {},
        _environment(tmp_path, LDAP_URL="ldap://directory:389"),
        _environment(tmp_path, LDAP_URL="ldaps://user:secret@directory:636"),
        _environment(tmp_path, LDAP_USER_ATTRIBUTE="mail"),
        _environment(tmp_path, LDAP_CA_CERT_FILE=str(tmp_path / "missing.pem")),
        _environment(tmp_path, LDAP_CONNECT_TIMEOUT="30"),
    ]
    for environ in cases:
        backend = RecordingBackend()
        auth = directory_auth.DirectoryAuthenticator(environ=environ, backend=backend)
        assert auth.configured is False
        assert auth.authenticate("trainer.test", "directory-password") is False
        assert backend.calls == []


def test_bad_input_and_backend_errors_are_generic_false(tmp_path):
    backend = RecordingBackend(error=RuntimeError("diagnostic containing a secret"))
    auth = directory_auth.DirectoryAuthenticator(environ=_environment(tmp_path), backend=backend)

    assert auth.authenticate("trainer.test", "directory-password") is False
    assert auth.authenticate(" trainer.test", "directory-password") is False
    assert auth.authenticate("trainer.test", "") is False
    assert len(backend.calls) == 1


def test_samaccountname_is_the_only_other_allowed_attribute(tmp_path):
    config = directory_auth.DirectoryConfig.from_environment(
        _environment(tmp_path, LDAP_USER_ATTRIBUTE="sAMAccountName")
    )
    assert config is not None
    assert config.user_attribute == "sAMAccountName"


def test_ldap_backend_escapes_filter_requires_one_entry_and_rebinds(tmp_path):
    connections = []

    class Connection:
        def __init__(self, server, **kwargs):
            self.server = server
            self.kwargs = kwargs
            self.entries = []
            self.search_call = None
            self.unbound = False
            connections.append(self)

        def bind(self):
            return True

        def search(self, **kwargs):
            self.search_call = kwargs
            self.entries = [SimpleNamespace(entry_dn="uid=trainer.test,ou=people,dc=trainer,dc=local")]
            return True

        def unbind(self):
            self.unbound = True

    fake = SimpleNamespace(
        NONE="NONE",
        SIMPLE="SIMPLE",
        SUBTREE="SUBTREE",
        NO_ATTRIBUTES="1.1",
        Tls=lambda **kwargs: SimpleNamespace(**kwargs),
        Server=lambda host, **kwargs: SimpleNamespace(host=host, **kwargs),
        Connection=Connection,
        utils=SimpleNamespace(conv=SimpleNamespace(escape_filter_chars=lambda value: "escaped-value")),
    )
    config = directory_auth.DirectoryConfig.from_environment(_environment(tmp_path))

    assert directory_auth.Ldap3Backend(fake).authenticate(
        config, "trainer.test*)(uid=*)", "directory-password"
    ) is True
    assert len(connections) == 2
    service, user = connections
    assert service.search_call["search_filter"] == "(uid=escaped-value)"
    assert service.search_call["size_limit"] == 2
    assert user.kwargs["user"] == "uid=trainer.test,ou=people,dc=trainer,dc=local"
    assert user.kwargs["password"] == "directory-password"
    assert service.unbound and user.unbound
    assert service.server.use_ssl is True
    assert service.server.tls.validate == ssl.CERT_REQUIRED
    assert service.server.tls.valid_names == ["directory"]


def test_ldap_backend_rejects_ambiguous_lookup_without_user_bind(tmp_path):
    connections = []

    class Connection:
        def __init__(self, _server, **_kwargs):
            self.entries = []
            self.unbound = False
            connections.append(self)

        def bind(self):
            return True

        def search(self, **_kwargs):
            self.entries = [SimpleNamespace(entry_dn="uid=one"), SimpleNamespace(entry_dn="uid=two")]
            return True

        def unbind(self):
            self.unbound = True

    fake = SimpleNamespace(
        NONE="NONE", SIMPLE="SIMPLE", SUBTREE="SUBTREE", NO_ATTRIBUTES="1.1",
        Tls=lambda **kwargs: kwargs,
        Server=lambda *_args, **_kwargs: object(),
        Connection=Connection,
        utils=SimpleNamespace(conv=SimpleNamespace(escape_filter_chars=lambda value: value)),
    )
    config = directory_auth.DirectoryConfig.from_environment(_environment(tmp_path))

    assert directory_auth.Ldap3Backend(fake).authenticate(config, "duplicate", "password") is False
    assert len(connections) == 1
    assert connections[0].unbound is True
