const fs=require('fs'),http=require('http');
const code=fs.readFileSync(process.argv[2],'utf8');
const req=http.request({hostname:'127.0.0.1',port:8765,method:'POST',headers:{'Content-Type':'application/json'}},res=>{res.pipe(process.stdout)});
req.on('error',e=>{console.error(e.message);process.exitCode=1});req.end(JSON.stringify({code}));
