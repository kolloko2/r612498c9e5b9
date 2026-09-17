param(
    [ValidateSet('auto', 'manual')]
    [string]$Mode = 'auto',
    [string]$EnvFile = (Join-Path $PSScriptRoot '..\..\.env.docker'),
    [string]$CaFile = (Join-Path $PSScriptRoot '..\..\deploy\tls\ca\ca.cert.pem'),
    [string]$AudioFile = (Join-Path $PSScriptRoot '..\..\output\voice-smoke\operator.wav'),
    [string]$OutputDir = (Join-Path $PSScriptRoot '..\..\output\sip220-probe')
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$envPath = (Resolve-Path -LiteralPath $EnvFile).Path
$caPath = (Resolve-Path -LiteralPath $CaFile).Path
$audioPath = (Resolve-Path -LiteralPath $AudioFile).Path
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$outputPath = (Resolve-Path -LiteralPath $OutputDir).Path
$line = Get-Content -LiteralPath $envPath | Where-Object { $_ -match '^SIP_ACCOUNTS_JSON=' } | Select-Object -First 1
if (-not $line) { throw 'SIP_ACCOUNTS_JSON is missing' }
$json = $line.Substring($line.IndexOf('=') + 1).Trim()
if (($json.StartsWith("'") -and $json.EndsWith("'")) -or ($json.StartsWith('"') -and $json.EndsWith('"'))) {
    $json = $json.Substring(1, $json.Length - 2)
}
$accounts = $json | ConvertFrom-Json
$password = $accounts.'220'
if (-not $password) { throw 'Training account 220 is not provisioned' }
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('trainer112-sip220-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temporary | Out-Null
$secretPath = Join-Path $temporary 'sip220_password'
[IO.File]::WriteAllText($secretPath, $password, [Text.UTF8Encoding]::new($false))
$container = 'trainer112-sip220-probe-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
try {
    # The local training PBX advertises 127.0.0.1 for host-installed phones.
    # Share its network namespace so the containerized test phone reaches the
    # same loopback without changing the production SIP transport settings.
    docker run -d --name $container --network container:trainer112-asterisk-1 `
        -v "${secretPath}:/run/secrets/sip220_password:ro" `
        -v "${caPath}:/ca/ca.cert.pem:ro" `
        -v "${audioPath}:/audio/operator.wav:ro" `
        -v "${outputPath}:/output" trainer112-sip220-probe:dev | Out-Null
    Start-Sleep -Seconds 4
    $running = docker inspect -f '{{.State.Running}}' $container
    if ($running -ne 'true') {
        docker logs $container 2>&1
        throw 'SIP 220 probe client exited before registration'
    }
    $create = docker exec trainer112-backend-1 python -c "import json,os,ssl,urllib.request,uuid; data=json.dumps({'session_id':str(uuid.uuid4()),'extension':'220','mode':'$Mode'}).encode(); req=urllib.request.Request('https://voice:8001/api/v1/calls',data=data,headers={'Content-Type':'application/json','Authorization':'Bearer '+os.environ['VOICE_API_TOKEN']}); ctx=ssl.create_default_context(cafile=os.environ['INTERNAL_CA_FILE']); print(urllib.request.urlopen(req,context=ctx,timeout=10).read().decode())"
    $call = $create | ConvertFrom-Json
    docker exec trainer112-backend-1 python -c "import json,os,ssl,time,urllib.request; cid='$($call.call_id)'; ctx=ssl.create_default_context(cafile=os.environ['INTERNAL_CA_FILE']); h={'Authorization':'Bearer '+os.environ['VOICE_API_TOKEN']}; deadline=time.monotonic()+15; status='';
while time.monotonic()<deadline:
 r=urllib.request.urlopen(urllib.request.Request('https://voice:8001/api/v1/calls/'+cid,headers=h),context=ctx,timeout=5); status=json.loads(r.read())['status'];
 if status=='active': break
 time.sleep(.25)
assert status=='active', status"
    if ($Mode -eq 'manual') {
        docker exec trainer112-backend-1 python -c "import json,os,ssl,urllib.request,uuid; cid='$($call.call_id)'; ctx=ssl.create_default_context(cafile=os.environ['INTERNAL_CA_FILE']); h={'Authorization':'Bearer '+os.environ['VOICE_API_TOKEN'],'Content-Type':'application/json'}; data=json.dumps({'message_id':str(uuid.uuid4()),'text':'Проверка обратного звукового канала.'}).encode(); req=urllib.request.Request('https://voice:8001/api/v1/chat/'+cid+'/say',data=data,headers=h); urllib.request.urlopen(req,context=ctx,timeout=10).read()"
        Start-Sleep -Seconds 20
    } else {
        $autoJson = docker exec trainer112-backend-1 python -c "import json,os,ssl,time,urllib.request; cid='$($call.call_id)'; ctx=ssl.create_default_context(cafile=os.environ['INTERNAL_CA_FILE']); h={'Authorization':'Bearer '+os.environ['VOICE_API_TOKEN']}; deadline=time.monotonic()+50; result={};
while time.monotonic()<deadline:
 r=urllib.request.urlopen(urllib.request.Request('https://voice:8001/api/v1/chat/'+cid,headers=h),context=ctx,timeout=5); chat=json.loads(r.read()); messages=chat['messages']; recognized=[m for m in messages if m['role']=='me' and m['status']=='recognized' and 'раз два три' in m.get('text','')]; played=[m for m in messages if m['role']=='bot' and m['status']=='played']; result={'recognized':len(recognized),'played':len(played)};
 if recognized and len(played)>=2: break
 time.sleep(.5)
print(json.dumps(result)); assert result.get('recognized') and result.get('played',0)>=2, result"
        $auto = $autoJson | ConvertFrom-Json
    }
    $endedJson = docker exec trainer112-backend-1 python -c "import os,ssl,urllib.request; req=urllib.request.Request('https://voice:8001/api/v1/calls/$($call.call_id)/hangup',data=b'',method='POST',headers={'Authorization':'Bearer '+os.environ['VOICE_API_TOKEN']}); ctx=ssl.create_default_context(cafile=os.environ['INTERNAL_CA_FILE']); print(urllib.request.urlopen(req,context=ctx,timeout=60).read().decode())"
    $ended = $endedJson | ConvertFrom-Json
    if ($ended.status -ne 'ended') { throw "Voice call ended with status $($ended.status)" }
    $tracksJson = docker exec trainer112-voice-1 python -c "import json,wave; p='$($ended.recordings.operator)'.rsplit('/',1)[0]; out={};
for name in ('operator','caller'):
 f=wave.open(p+'/'+name+'.wav','rb'); data=f.readframes(f.getnframes()); width=f.getsampwidth(); samples=memoryview(data).cast('h'); out[name]={'frames':len(samples),'rms':int((sum(int(x)*int(x) for x in samples)/max(1,len(samples)))**.5)}
print(json.dumps(out))"
    $tracks = $tracksJson | ConvertFrom-Json
    if ($tracks.operator.rms -le 0 -or $tracks.caller.rms -le 0) {
        throw "Silent Voice track: operator RMS=$($tracks.operator.rms), caller RMS=$($tracks.caller.rms)"
    }
    $chatJson = docker exec trainer112-backend-1 python -c "import json,os,ssl,urllib.request; ctx=ssl.create_default_context(cafile=os.environ['INTERNAL_CA_FILE']); req=urllib.request.Request('https://voice:8001/api/v1/chat/$($call.call_id)',headers={'Authorization':'Bearer '+os.environ['VOICE_API_TOKEN']}); print(urllib.request.urlopen(req,context=ctx,timeout=5).read().decode())"
    $chat = $chatJson | ConvertFrom-Json
    if (-not ($chat.messages | Where-Object { $_.role -eq 'me' -and $_.status -eq 'recognized' -and $_.text -match 'раз два три' })) {
        throw 'Vosk did not recognize the expected synthetic phrase'
    }
    $replyRole = if ($Mode -eq 'auto') { 'bot' } else { 'operator' }
    if (-not ($chat.messages | Where-Object { $_.role -eq $replyRole -and $_.status -eq 'played' })) {
        throw 'Silero reply did not finish playback'
    }
    $probeLogs = docker logs $container 2>&1 | Out-String
    foreach ($evidence in @('200 OK', "Using media encryption 'srtp-mand'", 'SRTP is Enabled', 'Call established', 'incoming rtp')) {
        if ($probeLogs -notmatch [regex]::Escape($evidence)) { throw "Missing SIP evidence: $evidence" }
    }
    $probeLogs | Select-String -Pattern 'registered|call established|audio|TLS|SRTP|error' | Select-Object -Last 30
    Write-Output "PASS mode=$Mode; call_id=$($call.call_id); operator_rms=$($tracks.operator.rms); caller_rms=$($tracks.caller.rms); STT=recognized; TTS=played"
}
finally {
    if (docker ps -a --format '{{.Names}}' | Select-String -SimpleMatch $container) {
        docker rm -f $container 2>$null | Out-Null
    }
    if ((Resolve-Path -LiteralPath $temporary).Path.StartsWith([IO.Path]::GetTempPath(), [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $temporary -Recurse -Force
    }
}
