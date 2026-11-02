'use strict';

const assert = require('node:assert/strict');
const mqtt = require('/opt/gatekeeper-backend/node_modules/mqtt');
const tag = 'tablet_' + 'b'.repeat(24);
const client = mqtt.connect('mqtt://127.0.0.1:' + process.argv[2], {
    clientId: 'reference-app-acceptance', reconnectPeriod: 0
});
let state, login, acquired, token, lease, heartbeat;
const adminReplies = new Map();
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let fail;
const failure = new Promise((resolve, reject) => { fail = reject; });
client.on('error', fail);
client.on('message', (topic, data) => {
    try {
        const body = JSON.parse(data.toString());
        if (topic === 'system/state') state = body;
        if (topic === 'system/auth/reply/' + tag) login = body;
        if (topic === 'tablet/admin/reply/' + tag && body.requestId === 'reference-acquire') acquired = body;
        if (topic === 'tablet/admin/reply/' + tag && body.requestId) adminReplies.set(body.requestId, body);
    } catch (error) { fail(error); }
});
async function until(predicate, message) {
    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
        if (predicate()) return;
        await pause(50);
    }
    throw Error(message);
}
const publish = (topic, body) => new Promise((resolve, reject) =>
    client.publish(topic, JSON.stringify(body), {qos: 1}, error => error ? reject(error) : resolve()));
const command = (action, fields = {}) => publish('tablet/command/in', {
    action, user: 'admin', clientTag: tag, sessionToken: token,
    gateId: 'test_gate', controlLeaseId: lease, ...fields
});
async function admin(action, fields = {}) {
    const requestId = 'admin-' + action + '-' + Date.now();
    await publish('tablet/admin/request/' + tag, {
        action, user: 'admin', clientTag: tag, sessionToken: token, requestId, ...fields
    });
    await until(() => adminReplies.has(requestId), 'Missing admin reply: ' + action);
    return adminReplies.get(requestId);
}
async function scenario() {
    await new Promise((resolve, reject) => client.on('connect', () => client.subscribe(
        ['system/state', 'system/auth/reply/' + tag, 'tablet/admin/reply/' + tag],
        {qos: 1}, error => error ? reject(error) : resolve())));
    await until(() => state?.siteName === 'Reference app test' &&
        state.gates?.some(gate => gate.id === 'test_gate' && gate.status === 'online'),
    'Reference startup/FC1 state did not reach tablet.');
    await publish('tablet/auth/request/' + tag, {
        username: 'admin', password: 'reference-test-password', device: 'Isolated reference tablet'
    });
    await until(() => login, 'Reference login reply missing.');
    assert.equal(login.success, true);
    token = login.sessionToken;
    assert.equal(typeof token, 'string');
    let inventory = await admin('get_network_state');
    assert.equal(inventory.ok, true);
    assert(inventory.data.network.discoveredDevices.logoDevices.some(device =>
        device.ip === '10.10.10.12' && device.mac === '02:11:22:33:44:66'));
    assert.equal((await admin('reboot_switch')).ok, false);
    assert.equal((await admin('add_gate', {
        name: 'Not discovered', ip: '10.10.10.99', mac: '02:11:22:33:44:99'
    })).ok, false);
    const network = await admin('apply_network', {config: {
        interface: 'lan1', masterMode: 'client', clientMode: 'static', staticIp: '10.10.10.3'
    }});
    assert.equal(network.ok, true);
    inventory = await admin('get_network_state');
    assert.equal(inventory.data.network.config.staticIp, '10.10.10.3');
    await command('enter_gate', {requestId: 'reference-acquire'});
    await until(() => acquired, 'Reference gate acquisition reply missing.');
    assert.equal(acquired.success, true);
    lease = acquired.gates.find(gate => gate.id === 'test_gate').controlLeaseId;
    assert(lease);
    heartbeat = setInterval(() => command('ui_heartbeat').catch(fail), 400);
    await command('modbus_cmd', {command: 'KEY', state: true});
    await command('modbus_cmd', {command: 'ENERGIZE', state: true});
    await until(() => state.gates[0].systemEnergized === true, 'Reference energize handshake failed.');
    assert.equal(state.gates[0].physicalEstopHealthy, false);
    await command('modbus_cmd', {command: 'ENERGIZE', state: false});
    await command('modbus_cmd', {command: 'OPEN', state: true});
    await until(() => state.gates[0].limitOpenActive === false, 'Reference OPEN feedback not published.');
    await command('modbus_cmd', {command: 'OPEN', state: false});
    clearInterval(heartbeat);
    await command('release_lock');
    await until(() => state.gates[0].lockedBy == null, 'Reference release did not clear ownership.');
    await command('enter_gate', {requestId: 'reference-reacquire'});
    await until(() => state.gates[0].lockedBy === 'admin', 'Reference reacquisition failed.');
    await until(() => state.gates[0].lockedBy == null, 'Reference heartbeat loss did not release ownership.');
    assert.equal((await admin('add_gate', {
        name: 'Discovered test gate', ip: '10.10.10.12', mac: '02:11:22:33:44:66', zone: 'test_zone'
    })).ok, true);
    await until(() => state.gates.some(gate => gate.name === 'Discovered test gate' && gate.status === 'online'),
        'Newly registered discovered controller did not enter the reference poller.');
    await command('logout');
    console.log('Reference tablet flow passed: login, discovery/admin/network results, discovered-gate registration, ' +
        'acquisition, Key/Energize, OPEN, release and heartbeat loss.');
}
Promise.race([scenario(), failure]).then(() => {
    clearInterval(heartbeat);
    client.end(true);
}, error => {
    clearInterval(heartbeat);
    console.error(error);
    client.end(true);
    process.exitCode = 1;
});
