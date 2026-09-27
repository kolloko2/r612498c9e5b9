#!/bin/sh
set -eu
mkdir -p /tmp/video-phone
password=$(cat /capture/sip.secret)
printf '%s\n' 'sip_cafile /ca/ca.cert.pem' 'sip_verify_server yes' 'sip_transports tls' 'module_path /usr/lib/baresip/modules' 'call_local_timeout 180' 'call_max_calls 1' 'call_accept yes' 'audio_player aufile,/capture/phone-received.wav' 'audio_source aufile,/capture/live.wav' 'ausrc_srate 16000' 'auplay_srate 16000' 'ausrc_channels 1' 'auplay_channels 1' 'audio_codecs pcmu/8000/1' 'module g711.so' 'module aufile.so' 'module srtp.so' 'module account.so' 'module menu.so' > /tmp/video-phone/config
printf '<sip:220@asterisk:5061;transport=tls>;auth_user=220;auth_pass=%s;regint=30;answermode=auto;audio_codecs=pcmu/8000/1;mediaenc=srtp-mand\n' "$password" > /tmp/video-phone/accounts
chmod 600 /tmp/video-phone/accounts
exec baresip -4 -f /tmp/video-phone -t 7200
