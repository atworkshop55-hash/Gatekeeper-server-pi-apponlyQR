'use strict';

module.exports = {
    uiHost: '127.0.0.1',
    uiPort: 1880,
    httpAdminRoot: false,
    httpNodeRoot: false,
    disableEditor: true,
    flowFile: 'flows.json',
    credentialSecret: require('fs').readFileSync('/etc/gatekeeper/credential-secret', 'utf8').trim(),
    functionExternalModules: true,
    functionGlobalContext: {
        commissioningCrypto: require('crypto'),
        commissioningFs: require('fs'),
        commissioningNet: require('net'),
        commissioningOs: require('os'),
        commissioningChildProcess: require('child_process'),
        commissioningQR: require('/opt/gatekeeper-backend/node_modules/qrcode'),
        commissioningSQLite: require('/opt/gatekeeper-backend/node_modules/sqlite3')
    },
    externalModules: {
        autoInstall: false,
        modules: { allowInstall: false },
        palette: { allowInstall: false }
    },
    editorTheme: { projects: { enabled: false } },
    logging: { console: { level: 'info', metrics: false, audit: false } },
    contextStorage: { default: { module: 'memory' } }
};

const fs = require('fs');
const editorConfig = '/etc/gatekeeper/maintenance-editor.json';
function protectedFile(path) {
    const info = fs.lstatSync(path);
    if (!info.isFile() || info.uid !== 0 || (info.mode & 0o777) !== 0o640) {
        throw new Error('Unsafe maintenance editor file: ' + path);
    }
    return fs.readFileSync(path);
}
let editorPresent;
try {
    fs.lstatSync(editorConfig);
    editorPresent = true;
} catch (error) {
    if (error.code !== 'ENOENT') {
        throw error;
    }
    editorPresent = false;
}
if (editorPresent) {
    const editor = JSON.parse(protectedFile(editorConfig).toString('utf8'));
    if (editor.version !== 1 || typeof editor.username !== 'string' || typeof editor.passwordHash !== 'string' ||
        !/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(editor.username) ||
        !/^\$2[aby]\$12\$[./A-Za-z0-9]{53}$/.test(editor.passwordHash)) {
        throw new Error('Invalid maintenance editor configuration');
    }
    module.exports.httpAdminRoot = '/';
    module.exports.disableEditor = false;
    module.exports.https = {
        key: protectedFile('/etc/gatekeeper/editor-tls/server.key'),
        cert: protectedFile('/etc/gatekeeper/editor-tls/server.crt'),
        minVersion: 'TLSv1.2'
    };
    module.exports.adminAuth = {
        type: 'credentials',
        users: [{ username: editor.username, password: editor.passwordHash, permissions: '*' }]
    };
}
