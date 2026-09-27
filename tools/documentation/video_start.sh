#!/bin/sh
set -eu
mkdir -p /root/.pki/nssdb
certutil -N -d sql:/root/.pki/nssdb --empty-password
certutil -A -d sql:/root/.pki/nssdb -n trainer112-ca -t 'C,,' -i /ca/ca.cert.pem
export NODE_PATH=/recorder/node_modules
exec node /project/tools/documentation/video_recorder.cjs
