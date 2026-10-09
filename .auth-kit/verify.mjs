import { runProductCli } from './runtime/auth-product-contract.mjs';
await runProductCli(process.argv.slice(2), new URL('../', import.meta.url));
