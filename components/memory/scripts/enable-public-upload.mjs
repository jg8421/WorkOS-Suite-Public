import crypto from 'node:crypto';
import {loadConfig,writeConfig} from '../src/config.mjs';
const {home,config}=loadConfig();
const hostname=process.argv[2]||config.publicUpload?.hostname;
if(!hostname || !/^(?:[a-z0-9-]+\.)+[a-z]{2,}$/i.test(hostname)) throw new Error('Pass your own upload hostname as the first argument.');
config.publicUpload={enabled:true,port:18766,token:config.publicUpload?.token||crypto.randomBytes(32).toString('base64url'),hostname};
writeConfig(home,config);
console.log(JSON.stringify({enabled:true,hostname:config.publicUpload.hostname,tokenPrinted:false}));
