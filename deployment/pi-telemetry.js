const fs = global.get('commissioningFs');
const os = global.get('commissioningOs');
try {
    const now = Date.now();
    const number = path => {
        const value = Number(fs.readFileSync(path, 'utf8').trim());
        if (!Number.isFinite(value) || value < 0) throw new Error('Invalid telemetry number: ' + path);
        return value;
    };
    const stats = {
        sampledAt: now,
        model: fs.readFileSync('/proc/device-tree/model', 'utf8').replace(/\0/g, '').trim(),
        hardwareProfile: 'raspberry-pi-test',
        revpiIOAvailable: false,
        uptimeSeconds: os.uptime(),
        hasWlan: fs.existsSync('/sys/class/net/wlan0/wireless'),
        temp: number('/sys/class/thermal/thermal_zone0/temp') / 1000,
        ram: 100 * (1 - os.freemem() / os.totalmem()),
        ramStr: (os.totalmem() / 1073741824).toFixed(1) + 'GB'
    };
    const memory = fs.readFileSync('/proc/meminfo', 'utf8');
    const total = /^MemTotal:\s+(\d+)/m.exec(memory);
    const available = /^MemAvailable:\s+(\d+)/m.exec(memory);
    if (!total || !available || Number(total[1]) <= 0) throw new Error('Invalid system memory sample');
    stats.ram = 100 * (1 - Number(available[1]) / Number(total[1]));
    const cpu = fs.readFileSync('/proc/stat', 'utf8').split('\n')[0].trim().split(/\s+/).slice(1, 9).map(Number);
    if (cpu.length !== 8 || cpu.some(value => !Number.isFinite(value) || value < 0)) {
        throw new Error('Invalid CPU sample');
    }
    const current = { total: cpu.reduce((a, b) => a + b, 0), idle: cpu[3] + cpu[4] };
    const previous = context.get('pi_previous_cpu');
    if (previous && current.total > previous.total && current.idle >= previous.idle) {
        stats.cpu = Math.max(0, Math.min(100, 100 * (1 - (current.idle - previous.idle) / (current.total - previous.total))));
    }
    context.set('pi_previous_cpu', current);
    const disk = fs.statfsSync('/var/lib/gatekeeper');
    if (disk.blocks <= 0 || disk.bsize <= 0) throw new Error('Invalid disk sample');
    stats.diskPct = 100 * (1 - disk.bfree / disk.blocks);
    stats.diskStr = (disk.blocks * disk.bsize / 1073741824).toFixed(1) + 'GB';
    stats.diskUsedGB = ((disk.blocks - disk.bfree) * disk.bsize / 1073741824).toFixed(1);
    stats.diskFreeGB = (disk.bavail * disk.bsize / 1073741824).toFixed(1);
    const mapping = JSON.parse(fs.readFileSync('/etc/gatekeeper/interface-roles.json', 'utf8'));
    if (mapping.version !== 1 || !mapping.roles || mapping.roles.management.mac === mapping.roles.gate.mac) {
        throw new Error('Invalid telemetry interface-role mapping');
    }
    const addresses = os.networkInterfaces();
    const previousNet = context.get('pi_previous_net') || {};
    const nextNet = {};
    for (const [role, suffix, index] of [['management', 'A', '0'], ['gate', 'B', '1']]) {
        const identity = mapping.roles[role];
        const matches = fs.readdirSync('/sys/class/net').filter(name =>
            fs.existsSync('/sys/class/net/' + name + '/device') &&
            !fs.existsSync('/sys/class/net/' + name + '/wireless') &&
            fs.readFileSync('/sys/class/net/' + name + '/address', 'utf8').trim().toLowerCase() === identity.mac);
        if (matches.length !== 1) throw new Error('Telemetry ' + role + ' adapter missing or duplicated');
        const name = matches[0], root = '/sys/class/net/' + name;
        if (fs.realpathSync(root + '/device/driver').split('/').pop() !== identity.driver) {
            throw new Error('Telemetry adapter driver changed');
        }
        stats['interface' + index] = name;
        stats['mac' + index] = identity.mac.toUpperCase();
        stats['ip' + index] = (addresses[name] || []).filter(item => item.family === 'IPv4' && !item.internal)
            .map(item => item.address).join(', ') || 'UNASSIGNED';
        // operstate remains readable even when carrier/speed is unavailable after unplugging.
        stats['link' + suffix] = fs.readFileSync(root + '/operstate', 'utf8').trim() === 'up';
        const bytes = number(root + '/statistics/rx_bytes') + number(root + '/statistics/tx_bytes');
        const old = previousNet[role];
        const delta = old && old.mac === identity.mac && bytes >= old.bytes ? bytes - old.bytes : null;
        const elapsed = old ? (now - old.at) / 1000 : 0;
        if (delta !== null && elapsed > 0) {
            stats['traffic' + suffix] = delta;
            if (stats['link' + suffix]) {
                const speed = number(root + '/speed');
                if (speed > 0) stats['util' + suffix] = Math.min(100, delta * 8 / elapsed / (speed * 1000000) * 100);
            }
        }
        nextNet[role] = { mac: identity.mac, bytes, at: now };
    }
    context.set('pi_previous_net', nextNet);
    stats.hbSpeed = 2;
    global.set('hwStats', stats);
    node.status({ fill: 'green', shape: 'dot', text: stats.model + ' telemetry' });
    return { topic: 'system/state', qos: 1, retain: false, payload: JSON.stringify({
        hwStats: stats, sentAt: now, siteName: global.get('zoo_site_name'),
        gates: global.get('zoo_gates') || [], zones: global.get('zoo_zones') || [],
        sessionTimeout: global.get('zoo_timeout')
    }) };
} catch (error) {
    node.status({ fill: 'red', shape: 'ring', text: 'Pi telemetry unavailable' });
    node.error('Pi telemetry sample failed: ' + error.message);
    return null;
}
