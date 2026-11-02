const output = String(msg.payload || '');
const match = output.match(/__EXIT__(\d+)/);
let ok, message;
if (msg.action === 'reboot') {
    try {
        const result = JSON.parse(output);
        ok = msg.rc?.code === 0 && result.ok === true;
        message = String(result.message || 'Reboot result missing.');
    } catch (error) {
        ok = false;
        message = 'Invalid Pi reboot response: ' + error.message;
    }
} else {
    ok = !!match && Number(match[1]) === 0;
    message = output.replace(/\n?__EXIT__\d+\s*$/, '').trim() ||
        (ok ? 'Command completed.' : 'Command failed without a confirmed exit code.');
}
msg.topic = msg.replyTopic;
msg.payload = JSON.stringify({requestId: msg.requestId, action: msg.action, ok, message});
return msg;
