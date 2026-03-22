import process from 'node:process';

import { runCliEntry } from './grab-pdf-lib.mjs';

const exitCode = await runCliEntry(process.argv.slice(2));
process.exitCode = exitCode;
