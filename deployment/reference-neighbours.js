const stats = global.get('hwStats');
const fresh = stats && Number.isFinite(stats.sampledAt) &&
    Date.now() >= stats.sampledAt && Date.now() - stats.sampledAt <= 6000;
if (!fresh || !stats.interface1 || stats.linkB !== true) {
    node.error('Gate neighbour read requires fresh mapped gate-interface telemetry.');
    node.done();
    return null;
}
global.get('commissioningChildProcess').execFile('/usr/bin/ip',
    ['-j', '-4', 'neighbour', 'show', 'dev', stats.interface1],
    {timeout: 4000, maxBuffer: 262144}, (error, stdout) => {
        try {
            if (error) throw new Error('ip neighbour read failed (' + error.code + ').');
            const rows = JSON.parse(stdout);
            if (!Array.isArray(rows)) throw new Error('Invalid gate neighbour response.');
            msg.payload = rows.filter(row => row.dev === undefined || row.dev === stats.interface1)
                .filter(row => /^(?:[a-f0-9]{2}:){5}[a-f0-9]{2}$/i.test(row.lladdr || '') &&
                    Array.isArray(row.state) &&
                    !row.state.some(value => ['FAILED', 'INCOMPLETE'].includes(value)))
                .map(row => '? (' + row.dst + ') at ' + row.lladdr + ' [ether] on ' + stats.interface1)
                .join('\n');
            node.send(msg);
        } catch (failure) {
            node.error('Gate neighbour discovery failed: ' + failure.message);
        } finally {
            node.done();
        }
    });
return null;
