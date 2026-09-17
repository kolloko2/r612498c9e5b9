#!/bin/sh
set -eu
test -r /run/secrets/sip220_password
test -r /ca/ca.cert.pem
test -r /audio/operator.wav
mkdir -p /tmp/baresip /output
# Let the automatic opening finish before the spoken fixture, then leave enough
# quiet time for Backend inference and the synthesized reply.
sox /audio/operator.wav /tmp/operator.wav pad 10 20
password=$(cat /run/secrets/sip220_password)
if printf '%s' "$password" | grep -q '[;"]'; then
    echo 'Unsupported password characters' >&2
    exit 2
fi
cat > /tmp/baresip/config <<'EOF'
sip_cafile /ca/ca.cert.pem
sip_verify_server yes
sip_transports tls
module_path /usr/lib/baresip/modules
call_local_timeout 60
call_max_calls 1
call_accept yes
audio_player aufile,/output/caller.wav
audio_source aufile,/tmp/operator.wav
ausrc_srate 16000
auplay_srate 16000
ausrc_channels 1
auplay_channels 1
audio_codecs pcmu/8000/1
module g711.so
module aufile.so
module srtp.so
module account.so
module menu.so
EOF
printf '<sip:220@asterisk:5061;transport=tls>;auth_user=220;auth_pass=%s;regint=30;answermode=auto;answerdelay=0;audio_codecs=pcmu/8000/1;audio_source=aufile,/tmp/operator.wav;audio_player=aufile,/output/caller.wav;mediaenc=srtp-mand\n' "$password" > /tmp/baresip/accounts
chmod 600 /tmp/baresip/accounts
exec baresip -v -4 -f /tmp/baresip -t 60
