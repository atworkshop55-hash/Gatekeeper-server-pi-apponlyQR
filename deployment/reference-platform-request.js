const action = msg.action || msg.payload?.action;
const reject = message => {
    node.error('Pi platform action failed: ' + message);
    node.send({...msg, payload: JSON.stringify({ok: false, message}), rc: {code: 1}});
    node.done();
};
if (context.get('reference_platform_stopping')) {
    reject('Backend is stopping; no request sent.');
    return null;
}
if (context.get('reference_platform_busy')) {
    reject('A platform action is already running.');
    return null;
}
let request;
if (['apply_network', 'apply_management', 'confirm_management'].includes(action)) {
    if ((global.get('zoo_gates') || []).some(gate => gate.lockedBy != null)) {
        reject('Release all gate control sessions before changing networking.');
        return null;
    }
    request = {action: action === 'apply_network' ? 'apply_gate' : action,
        config: msg.networkConfig, pendingId: msg.pendingId};
} else if (action === 'reboot') {
    request = {action: 'reboot'};
} else {
    reject('Unsupported privileged Pi platform action.');
    return null;
}
context.set('reference_platform_busy', true);
let stream, complete = false, sent = false, buffer = '';
let resolvePending;
const pending = new Promise(resolve => { resolvePending = resolve; });
context.set('reference_platform_pending', pending);
const finish = (result, code) => {
    if (complete) return;
    complete = true;
    if (stream) stream.destroy();
    context.set('reference_platform_busy', false);
    context.set('reference_platform_cancel', null);
    if (code) node.error('Pi platform action failed: ' + result.message);
    node.send({...msg, payload: JSON.stringify(result), rc: {code}});
    node.done();
    resolvePending();
};
context.set('reference_platform_cancel', () => {
    finish({ok: false, message: sent
        ? 'Shutdown after platform request; outcome unknown. Inspect locally.'
        : 'Shutdown before platform request; nothing sent.'}, 1);
    return pending;
});
try {
    stream = global.get('commissioningNet').createConnection('/run/gatekeeper-network/control.sock');
    stream.setEncoding('utf8');
    stream.setTimeout(120000);
    stream.on('connect', () => {
        if (complete) return;
        if (action !== 'reboot' && (global.get('zoo_gates') || []).some(gate => gate.lockedBy != null)) {
            finish({ok: false, message: 'A gate session started before network apply; no request sent.'}, 1);
            return;
        }
        try {
            sent = true;
            stream.write(JSON.stringify(request) + '\n');
        } catch (error) {
            finish({ok: false, message: 'Platform send failed; outcome unknown (' + error.message + ').'}, 1);
        }
    });
    stream.on('timeout', () => finish({ok: false, message: 'Platform timeout; inspect the outcome locally.'}, 1));
    stream.on('error', error => finish({ok: false, message: 'Platform socket error (' + error.code + ').' +
        (sent ? ' Outcome unknown; inspect locally.' : ' No request sent.')}, 1));
    stream.on('close', () => finish({ok: false, message: 'Platform service closed without a result.'}, 1));
    stream.on('data', data => {
        if (complete) return;
        buffer += data;
        if (buffer.length > 16384) {
            finish({ok: false, message: 'Oversized platform response; inspect locally.'}, 1);
            return;
        }
        if (!buffer.includes('\n')) return;
        try {
            const result = JSON.parse(buffer.slice(0, buffer.indexOf('\n')));
            if (!result || typeof result.ok !== 'boolean') throw new Error('Missing outcome.');
            finish(result, result.ok ? 0 : 1);
        } catch (error) {
            finish({ok: false, message: 'Invalid platform response: ' + error.message}, 1);
        }
    });
} catch (error) {
    finish({ok: false, message: 'Platform socket setup failed: ' + error.message}, 1);
}
return null;
