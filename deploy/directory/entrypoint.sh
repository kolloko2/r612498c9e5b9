#!/bin/sh
set -eu

if [ -z "${DIRECTORY_ADMIN_PASSWORD:-}" ]; then
    echo "Required directory secret is missing: DIRECTORY_ADMIN_PASSWORD" >&2
    exit 1
fi
if [ -z "${DIRECTORY_BIND_PASSWORD:-}" ]; then
    echo "Required directory secret is missing: DIRECTORY_BIND_PASSWORD" >&2
    exit 1
fi
if [ -z "${DIRECTORY_TEST_PASSWORD:-}" ]; then
    echo "Required directory secret is missing: DIRECTORY_TEST_PASSWORD" >&2
    exit 1
fi

for certificate_file in ca.cert.pem directory.cert.pem directory.key.pem; do
    if [ ! -f "/tls-input/$certificate_file" ]; then
        echo "Required directory TLS file is missing: $certificate_file" >&2
        exit 1
    fi
done

install -d -m 0750 -o openldap -g openldap /run/slapd /var/lib/ldap
install -m 0644 -o root -g openldap /tls-input/ca.cert.pem /run/slapd/ca.cert.pem
install -m 0644 -o root -g openldap /tls-input/directory.cert.pem /run/slapd/directory.cert.pem
install -m 0640 -o root -g openldap /tls-input/directory.key.pem /run/slapd/directory.key.pem

umask 077
admin_secret_file=$(mktemp /run/slapd/admin-secret.XXXXXX)
bind_secret_file=$(mktemp /run/slapd/bind-secret.XXXXXX)
test_secret_file=$(mktemp /run/slapd/test-secret.XXXXXX)
trap 'rm -f "$admin_secret_file" "$bind_secret_file" "$test_secret_file"' EXIT INT TERM
printf '%s' "$DIRECTORY_ADMIN_PASSWORD" > "$admin_secret_file"
printf '%s' "$DIRECTORY_BIND_PASSWORD" > "$bind_secret_file"
printf '%s' "$DIRECTORY_TEST_PASSWORD" > "$test_secret_file"
admin_hash=$(slappasswd -T "$admin_secret_file")
bind_hash=$(slappasswd -T "$bind_secret_file")
test_hash=$(slappasswd -T "$test_secret_file")
rm -f "$admin_secret_file" "$bind_secret_file" "$test_secret_file"
unset DIRECTORY_ADMIN_PASSWORD DIRECTORY_BIND_PASSWORD DIRECTORY_TEST_PASSWORD

cat > /run/slapd/slapd.conf <<EOF
include         /etc/ldap/schema/core.schema
include         /etc/ldap/schema/cosine.schema
include         /etc/ldap/schema/inetorgperson.schema
pidfile         /run/slapd/slapd.pid
argsfile        /run/slapd/slapd.args
modulepath      /usr/lib/ldap
moduleload      back_mdb
TLSCACertificateFile /run/slapd/ca.cert.pem
TLSCertificateFile   /run/slapd/directory.cert.pem
TLSCertificateKeyFile /run/slapd/directory.key.pem
TLSProtocolMin  3.3

database        mdb
maxsize         1073741824
suffix          "dc=trainer,dc=local"
rootdn          "cn=admin,dc=trainer,dc=local"
rootpw          $admin_hash
directory       /var/lib/ldap
index           objectClass eq
index           uid eq
access to attrs=userPassword
    by self write
    by anonymous auth
    by dn.exact="cn=lookup,ou=system,dc=trainer,dc=local" auth
    by * none
access to *
    by dn.exact="cn=lookup,ou=system,dc=trainer,dc=local" read
    by self read
    by * none
EOF

cat > /run/slapd/bootstrap.ldif <<EOF
dn: dc=trainer,dc=local
objectClass: top
objectClass: domain
dc: trainer

dn: ou=people,dc=trainer,dc=local
objectClass: top
objectClass: organizationalUnit
ou: people

dn: ou=system,dc=trainer,dc=local
objectClass: top
objectClass: organizationalUnit
ou: system

dn: cn=lookup,ou=system,dc=trainer,dc=local
objectClass: top
objectClass: organizationalRole
objectClass: simpleSecurityObject
cn: lookup
userPassword: $bind_hash

dn: uid=trainer.test,ou=people,dc=trainer,dc=local
objectClass: top
objectClass: inetOrgPerson
cn: Test Trainer
sn: Trainer
uid: trainer.test
userPassword: $test_hash
EOF

slapadd -f /run/slapd/slapd.conf -l /run/slapd/bootstrap.ldif
chown -R openldap:openldap /var/lib/ldap /run/slapd
chmod 0640 /run/slapd/directory.key.pem

exec slapd -f /run/slapd/slapd.conf -h 'ldaps:///' -u openldap -g openldap -d 0
