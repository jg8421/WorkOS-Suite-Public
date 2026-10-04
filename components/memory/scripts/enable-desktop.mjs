import { loadConfig, writeConfig } from '../src/config.mjs';
const {home,config}=loadConfig();
config.desktopCapture={...config.desktopCapture,enabled:true};
config.cloudInbox={...config.cloudInbox,enabled:true};
// WorkOS currently owns 127.0.0.1:18765; the Memory listener also serves .2.
config.localApiHost='127.0.0.2';
writeConfig(home,config);
console.log('Desktop context capture and cloud inbox enabled. Credentials unchanged.');
