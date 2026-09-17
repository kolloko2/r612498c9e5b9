# Optional directory authentication

## Current synthetic deployment

The test directory is running inside Compose with verified LDAPS and no published
LDAP port. `python deploy/directory/bootstrap-demo.py` created local student
`ldap-demo`, explicitly linked to directory username `trainer.test`, and completed
a real authenticated login through the Backend API. Its temporary application
session was logged out. Existing users/roles were not changed.
The generated test password is `DIRECTORY_TEST_PASSWORD` in the ignored
`deploy/directory/private/test.env`; no default password is embedded in source.
Use `python deploy/manage.py up --directory` to retain this overlay on later starts.
The web login offers directory mode and the admin portal can link other non-admin
profiles. The test LDAP dataset is ephemeral/reseeded from ENV; it is not a real AD
domain. This block secures the LDAP link only, not the currently HTTP browser link.

The Backend can verify a username and password against LDAP/Active Directory as
an optional second credential source. Directory authentication proves credentials
only. It does **not** create a trainer account, synchronize names, or map directory
groups to `admin`, `teacher`, or `student` roles. An administrator must first create
an active local trainer account and explicitly link its directory username. Directory
authentication must never grant the local `admin` role.

This adapter does not change the existing local account/session model. Successful
login still issues the application's hashed, expiring session and all authorization
continues to use the role stored locally.

## Backend configuration

Install the optional runtime dependency `ldap3>=2.9,<3`, then set all of these
server-side environment variables:

| Variable | Meaning |
|---|---|
| `LDAP_URL` | Required `ldaps://host[:port]`; plain LDAP and URL credentials/options are rejected |
| `LDAP_BIND_DN` | Least-privilege service account used only to find a user DN |
| `LDAP_BIND_PASSWORD` | Service bind password; ENV only |
| `LDAP_BASE_DN` | Search root, for example `ou=people,dc=trainer,dc=local` |
| `LDAP_USER_ATTRIBUTE` | Exactly `uid` or `sAMAccountName` |
| `LDAP_CA_CERT_FILE` | Existing PEM CA file used with required certificate and hostname validation |
| `LDAP_CONNECT_TIMEOUT` | Optional integer 1–15 seconds; default 5 |
| `LDAP_OPERATION_TIMEOUT` | Optional integer 1–15 seconds; default 5 |

Configuration is considered active only when every required value is present and
valid. There is no TLS-disable setting or insecure fallback. The service search uses
an escaped equality filter, requests at most two results, accepts exactly one DN,
and then verifies the supplied password with a separate bind as that DN. Empty or
oversized credentials, missing `ldap3`, timeouts, certificate errors, bind failures,
zero/multiple matches, and all other provider errors return `false`. Provider details
and credentials are not logged or exposed to the login caller.

Application integration surface:

```python
from directory_auth import DirectoryAuthenticator

directory = DirectoryAuthenticator()
directory.status()                         # {"configured": bool}
directory.authenticate(username, password) # bool
```

Tests inject a backend into `DirectoryAuthenticator`; this is dependency injection,
not an environment-controlled authentication bypass. Production uses the lazily
imported `ldap3` backend.

## Isolated test directory

`deploy/directory/compose.yaml` builds a disposable OpenLDAP server for integration
testing. It listens only on LDAPS inside its Compose network and publishes no host
port. Its database is a tmpfs and is recreated on each container start. It contains
only synthetic entries under `dc=trainer,dc=local`:

- least-privilege lookup DN `cn=lookup,ou=system,dc=trainer,dc=local`;
- test user `uid=trainer.test,ou=people,dc=trainer,dc=local`.

All three passwords are required at launch and are not stored in the image or
repository. Generate an ignored `deploy/directory/private/test.env`; the script
refuses to overwrite an existing file and does not print its values:

```powershell
./deploy/directory/generate-test-env.ps1
```

For another deployment workflow, supply values through a protected, ignored env
file or secret-aware process environment.

Before starting, generate the project test CA and a server certificate whose SAN
contains the exact Compose hostname `directory`. The expected read-only files are:

```text
deploy/tls/ca/ca.cert.pem
deploy/tls/certs/directory.cert.pem
deploy/tls/private/directory.key.pem
```

Then, only on an isolated development machine, merge the fixture into the main
Compose project (relative paths intentionally resolve from the repository root):

```powershell
docker compose -f docker-compose.yml -f deploy/directory/compose.yaml `
  --env-file .env.docker --env-file deploy/directory/private/test.env up --build -d
```

Do not connect it to an employer directory and do not load real 112 personnel or
incident data. The overlay attaches Backend to the fixture, mounts only the public
CA into Backend, and waits for the directory health check. Port 636 is exposed to
containers but is not published on the host.

Configure the Backend container with:

```text
LDAP_URL=ldaps://directory:636
LDAP_BIND_DN=cn=lookup,ou=system,dc=trainer,dc=local
LDAP_BASE_DN=dc=trainer,dc=local
LDAP_USER_ATTRIBUTE=uid
LDAP_CA_CERT_FILE=/run/tls/ca.cert.pem
```

Supply `LDAP_BIND_PASSWORD` from the same protected runtime source as
`DIRECTORY_BIND_PASSWORD`. Manually create a non-admin local account named
`trainer.test` before testing login. The directory entry alone cannot sign in to
the application because automatic provisioning is intentionally absent.

Anonymous directory reads are denied. The anonymous `auth` ACL on `userPassword`
is the minimum needed for an LDAP simple bind to validate a password; it does not
permit reading password values. The lookup account can search attributes but cannot
administer entries. This fixture is test infrastructure, not a supported production
directory deployment.
