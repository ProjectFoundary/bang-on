// Build-only, provider-blocked real-App finishing fixture. Open through CUA.
import {createRequire} from 'node:module';
import {readFileSync,writeFileSync} from 'node:fs';
const out=process.env.KEYFX_FINISHING_FIXTURE_DIR||'/tmp/bang-finishing-fixture';
process.env.KEYFX_FINISHING_FIXTURE='1';process.env.KEYFX_BATCH_FIXTURE=out;
await import('./elementBatchFixture.mjs');
const require=createRequire(new URL('../package.json',import.meta.url)),{zipSync,strToU8}=require('fflate');
writeFileSync(out+'/fixture-layers.zip',zipSync({'README.txt':strToU8('Synthetic UI fixture only. No source footage, model media or real layer exports.\n')}));
const html=readFileSync(out+'/index.html','utf8').replace('<title>Batch LTX workflow fixture</title>','<title>Finishing approval, lighting and export fixture</title>').replace('SYNTHETIC QA · No provider access','SYNTHETIC FINISH QA · No provider access');
writeFileSync(out+'/index.html',html);
console.log('Finishing fixture: '+out);
