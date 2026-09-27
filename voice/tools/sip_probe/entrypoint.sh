#!/bin/sh
set -eu
test -r /run/secrets/sip220_password
test -r /ca/ca.cert.pem
test -r /audio/operator.wav
mkdir -p /tmp/baresip /output
extension=${SIP_PROBE_EXTENSION:-220}
case "$extension" in ''|*[!0-9]*) echo 'Invalid probe extension' >&2; exit 2;; esac
# Let the automatic opening finish before the spoken fixture, then leave enough
# quiet time for Backend inference and the synthesized reply.
sox /audio/operator.wav /tmp/operator.wav pad "${SIP_PROBE_LEAD_SECONDS:-10}" "${SIP_PROBE_SILENCE_SECONDS:-20}"
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
if [ -n "${SIP_PROBE_PORT:-}" ]; then
    case "$SIP_PROBE_PORT" in *[!0-9]*) exit 2;; esac
    printf 'sip_listen 0.0.0.0:%s\n' "$SIP_PROBE_PORT" >> /tmp/baresip/config
fi
printf '<sip:%s@asterisk:5061;transport=tls>;auth_user=%s;auth_pass=%s;regint=30;answermode=auto;answerdelay=0;audio_codecs=pcmu/8000/1;audio_source=aufile,/tmp/operator.wav;audio_player=aufile,/output/caller.wav;mediaenc=srtp-mand\n' "$extension" "$extension" "$password" > /tmp/baresip/accounts
chmod 600 /tmp/baresip/accounts
exec baresip -v -4 -f /tmp/baresip -t "${SIP_PROBE_SECONDS:-60}"
