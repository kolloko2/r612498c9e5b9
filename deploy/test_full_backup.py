import pytest
from cryptography.exceptions import InvalidTag
from full_backup import HOST_CONFIGURATION_PATHS,encrypt,decrypt,ensure_key,read_key


def test_authenticated_roundtrip_and_tamper(tmp_path):
    ensure_key(tmp_path);key=read_key(tmp_path);source=tmp_path/'source';source.write_bytes(b'synthetic'*1000)
    cipher=tmp_path/'encrypted';encrypt(source,cipher,key)
    recovered=tmp_path/'recovered';decrypt(cipher,recovered,key)
    assert recovered.read_bytes()==source.read_bytes()
    with pytest.raises(FileExistsError):decrypt(cipher,recovered,key)
    damaged=bytearray(cipher.read_bytes());damaged[35]^=1;cipher.write_bytes(damaged)
    target=tmp_path/'must-not-exist'
    with pytest.raises(InvalidTag):decrypt(cipher,target,key)
    assert not target.exists()


def test_legacy_host_bundle_never_traverses_private_ca():
    normalized={str(path).replace('\\','/').lower() for path in HOST_CONFIGURATION_PATHS}
    assert 'deploy/tls/ca/ca.cert.pem' in normalized
    assert not any('deploy/tls/private' in path or path.endswith('ca.key.pem') for path in normalized)
