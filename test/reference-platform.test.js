'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const {EventEmitter} = require('node:events');
const root = path.join(__dirname, '..', 'deployment');

async function neighbours({age = 0, failure = null} = {}) {
    const sent = [], errors = [], calls = [];
    await new Promise(resolve => {
        const values = {
            hwStats: {sampledAt: Date.now() - age, interface1: 'eth1', linkB: true},
            commissioningChildProcess: {execFile(file, args, options, callback) {
                calls.push({file, args});
                callback(failure, JSON.stringify([
                    {dst: '10.10.10.11', lladdr: '10:d6:57:e7:67:8c', state: ['REACHABLE']},
                    {dst: '10.10.10.12', lladdr: '10:d6:57:e7:67:99', state: ['FAILED']},
                    {dst: '192.168.101.1', dev: 'eth0', lladdr: '10:d6:57:e7:67:01', state: ['REACHABLE']}
                ]));
            }}
        };
        vm.runInNewContext('(function(){' + fs.readFileSync(path.join(root, 'reference-neighbours.js'), 'utf8') +
            '\n})()', {msg: {gateInfo: {name: 'Preserved'}}, global: {get: key => values[key]},
            node: {done: resolve, send: msg => sent.push(msg), error: error => errors.push(error)}});
    });
    return {sent, errors, calls};
}

test('mapped Pi neighbours produce the reference ARP shape without a net-tools dependency', async () => {
    const result = await neighbours();
    assert.equal(result.sent[0].payload, '? (10.10.10.11) at 10:d6:57:e7:67:8c [ether] on eth1');
    assert.equal(result.sent[0].gateInfo.name, 'Preserved');
    assert.equal(result.calls[0].file, '/usr/bin/ip');
    assert.deepEqual(Array.from(result.calls[0].args), ['-j', '-4', 'neighbour', 'show', 'dev', 'eth1']);
});

test('stale telemetry or failed neighbour reads never publish fabricated discovery', async () => {
    for (const options of [{age: 7000}, {failure: {code: 'EIO'}}]) {
        const result = await neighbours(options);
        assert.equal(result.sent.length, 0);
        assert.equal(result.errors.length, 1);
    }
});

async function platform({action = 'apply_network', owned = false, response = '{"ok":true}\n',
    event = null} = {}) {
    const sent = [], errors = [], writes = [], values = new Map(), cache = new Map();
    const stream = new EventEmitter();
    stream.setEncoding = stream.setTimeout = stream.destroy = () => {};
    stream.write = value => {
        writes.push(JSON.parse(value));
        if (event) stream.emit(event, {code: 'ECONNRESET'});
        else stream.emit('data', response);
    };
    values.set('zoo_gates', [{lockedBy: owned ? 'admin' : null}]);
    values.set('commissioningNet', {createConnection(target) {
        assert.equal(target, '/run/gatekeeper-network/control.sock');
        setImmediate(() => stream.emit('connect'));
        return stream;
    }});
    await new Promise(resolve => {
        vm.runInNewContext('(function(){' +
            fs.readFileSync(path.join(root, 'reference-platform-request.js'), 'utf8') + '\n})()', {
            msg: {action, networkConfig: {interface: 'lan1', staticIp: '10.10.10.3'},
                requestId: 'original', replyTopic: 'private/reply'},
            context: {get: key => cache.get(key), set: (key, value) => cache.set(key, value)},
            global: {get: key => values.get(key)},
            node: {send: msg => sent.push(msg), error: error => errors.push(error), done: resolve}
        });
    });
    return {sent, errors, writes, cache};
}

test('network adapter sends structured fixed actions and preserves reference result correlation', async () => {
    for (const action of ['apply_network', 'apply_management', 'confirm_management', 'reboot']) {
        const result = await platform({action});
        assert.equal(result.writes[0].action, action === 'apply_network' ? 'apply_gate' : action);
        assert.equal(result.sent[0].requestId, 'original');
        assert.equal(result.sent[0].replyTopic, 'private/reply');
        assert.equal(result.sent[0].rc.code, 0);
        assert.equal(result.cache.get('reference_platform_busy'), false);
    }
});

test('owned gates, bad replies and connection failures explicitly fail without false success', async () => {
    for (const options of [{owned: true}, {action: 'shell'}, {response: '{}\n'},
        {response: '{bad\n'}, {event: 'error'}, {event: 'timeout'}, {event: 'close'}]) {
        const result = await platform(options);
        assert.equal(result.sent[0].rc.code, 1);
        assert.equal(JSON.parse(result.sent[0].payload).ok, false);
        assert(result.errors.length > 0);
        if (options.owned || options.action === 'shell') assert.equal(result.writes.length, 0);
    }
});

test('OS formatter respects structured reboot failures and requires a confirmed ping exit code', () => {
    for (const [action, payload, code, expected] of [
        ['reboot', '{"ok":false,"message":"An action is already running."}', 1, false],
        ['reboot', '{"ok":true,"message":"System reboot requested."}', 0, true],
        ['reboot', '{"ok":true}', 1, false],
        ['reboot', 'not json', 0, false],
        ['ping', '4 packets transmitted, 4 received\n__EXIT__0', 0, true],
        ['ping', '0 received\n__EXIT__1', 0, false],
        ['ping', '', 0, false]
    ]) {
        const result = vm.runInNewContext('(function(){' +
            fs.readFileSync(path.join(root, 'reference-os-result.js'), 'utf8') + '\n})()', {
            msg: {action, payload, rc: {code}, requestId: 'original', replyTopic: 'private/reply'}
        });
        assert.equal(JSON.parse(result.payload).ok, expected);
        assert.equal(result.topic, 'private/reply');
    }
});
