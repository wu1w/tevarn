/**
 * 防回归：聊天输入框必须可点、可聚焦
 * 契约见 MessageInput.tsx 注释 + globals.css .chat-composer*
 */
const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..');
const fails = [];
const ok = (name, cond, detail = '') => {
  if (!cond) fails.push(`${name}${detail ? ' — ' + detail : ''}`);
  else console.log('OK', name);
};

const mi = fs.readFileSync(path.join(root, 'components/chat/MessageInput.tsx'), 'utf8');
const css = fs.readFileSync(path.join(root, 'app/globals.css'), 'utf8');
const page = fs.readFileSync(path.join(root, 'app/page.tsx'), 'utf8');

ok('MessageInput has chat-composer root', mi.includes('chat-composer') && mi.includes('data-testid="chat-composer"'));
ok('textarea has block w-full', /className="[^"]*block w-full/.test(mi) || mi.includes('block w-full max-w-full'));
ok('pointer handler focusComposer', mi.includes('handleComposerPointerDown') && mi.includes('focusComposer'));
const textareaEl = mi.match(/\u003ctextarea[\s\S]*?\/>/);
ok('no disabled on textarea; use readOnly for lock', textareaEl !== null && !textareaEl[0].includes('disabled') && mi.includes('readOnly={inputLocked}'));
ok('no-drag CSS for composer', css.includes('.chat-composer') && css.includes('no-drag'));
ok('textarea width 100% CSS', css.includes('chat-composer-textarea') || css.includes('.chat-composer textarea'));
ok('page uses chat-main-column + messages-pane', page.includes('chat-main-column') && page.includes('chat-messages-pane'));
ok('MessageInput outside messages-pane (sibling)', /chat-messages-pane[\s\S]*<\/div>\s*<MessageInput/.test(page.replace(/\r/g, '')));

if (fails.length) {
  console.error('FAIL', fails);
  process.exit(1);
}
console.log('RESULT: PASS');
