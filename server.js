// Compatibility entry point; Python 3.10+ runs the current reusable board.
// Set ADMIN_TOKEN before starting. The original proxy is in legacy/server.js.
const { spawn } = require('node:child_process');
const child = spawn(process.env.PYTHON || 'python3', [require('node:path').join(__dirname, 'server.py')], { stdio: 'inherit', env: process.env });
for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => child.kill(signal));
child.on('error', error => { console.error(error.message); process.exitCode = 1; });
child.on('exit', code => { process.exitCode = code ?? 1; });
