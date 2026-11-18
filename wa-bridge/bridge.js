/**
 * J.A.R.V.I.S. WhatsApp bridge (whatsapp-web.js), v2.
 *
 * WA_MODE=self (default)
 *   The bridge is linked to YOUR OWN WhatsApp. Jarvis can:
 *     - read your chats (GET /chats, GET /messages)  - read-only, nothing is marked as read
 *     - announce new private messages from other people
 *     - message you in your "Message yourself" chat (marked with a robot emoji)
 *     - take commands from you there: start a message to yourself with "Jarvis, ..."
 *
 * WA_MODE=assistant
 *   The bridge is linked to a SEPARATE WhatsApp account that only Jarvis uses. You text it from
 *   OWNER_WHATSAPP and it answers. (It can then only read that separate account's chats.)
 *
 * Every HTTP route requires the x-bridge-token header (BRIDGE_TOKEN from ../.env).
 * The HTTP server only listens on 127.0.0.1.
 */

const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '..', '.env') });

const express = require('express');
const qrcode = require('qrcode-terminal');
const { Client, LocalAuth } = require('whatsapp-web.js');

const BRIDGE_PORT = parseInt(process.env.BRIDGE_PORT || '3917', 10);
const BRIDGE_TOKEN = process.env.BRIDGE_TOKEN || '';
const JARVIS_URL = process.env.JARVIS_URL || 'http://127.0.0.1:5000';
const WA_MODE = (process.env.WA_MODE || 'self').toLowerCase() === 'assistant' ? 'assistant' : 'self';
const OWNER_NUMBER = (process.env.OWNER_WHATSAPP || '').replace(/\D/g, '');
const ANNOUNCE_GROUPS = (process.env.WA_ANNOUNCE_GROUPS || 'false').toLowerCase() === 'true';
const BOT_PREFIX = '\u{1F916} '; // robot emoji marks messages written by Jarvis

if (!BRIDGE_TOKEN) {
  console.error('BRIDGE_TOKEN is missing in .env - refusing to start.');
  process.exit(1);
}
if (WA_MODE === 'assistant' && !OWNER_NUMBER) {
  console.error('WA_MODE=assistant needs OWNER_WHATSAPP in .env (digits with country code) - refusing to start.');
  process.exit(1);
}

let ready = false;
let lastQr = null;
let selfWid = null;  // your own WhatsApp id (self mode) or the Jarvis account id (assistant mode)
let ownerWid = null; // where Jarvis sends messages to you

const client = new Client({
  authStrategy: new LocalAuth({ dataPath: path.join(__dirname, '.wwebjs_auth') }),
  puppeteer: {
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
  }
});

// ------------------------------------------------------------------------
// Helpers
// ------------------------------------------------------------------------
const contactNames = new Map();

async function nameOf(msg) {
  const key = msg.author || msg.from;
  if (contactNames.has(key)) return contactNames.get(key);
  let name = 'someone';
  try {
    const contact = await msg.getContact();
    name = contact.name || contact.pushname || contact.number || name;
  } catch (err) {
    /* keep fallback */
  }
  contactNames.set(key, name);
  return name;
}

function previewOf(msg, max) {
  const limit = max || 200;
  let text = String((msg && msg.body) || '').replace(/\s+/g, ' ').trim();
  if (!text && msg && msg.hasMedia) text = '[' + (msg.type || 'media') + ']';
  if (!text && msg) text = '[' + (msg.type || 'message') + ']';
  return text.length > limit ? text.slice(0, limit) + '...' : text;
}

function clamp(value, low, high, fallback) {
  const n = parseInt(value, 10);
  if (Number.isNaN(n)) return fallback;
  return Math.max(low, Math.min(high, n));
}

async function postJson(route, payload) {
  const resp = await fetch(JARVIS_URL + route, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'x-bridge-token': BRIDGE_TOKEN },
    body: JSON.stringify(payload)
  });
  return resp.json();
}

async function askJarvis(text) {
  try {
    const data = await postJson('/api/whatsapp-in', { message: text });
    if (data && data.reply) return data.reply;
    return 'Something went wrong on my side. ' + ((data && data.error) || '');
  } catch (err) {
    return 'I could not reach my main system just now.';
  }
}

// ------------------------------------------------------------------------
// WhatsApp events
// ------------------------------------------------------------------------
client.on('qr', (qr) => {
  lastQr = qr;
  ready = false;
  console.log('\nScan this QR code with the WhatsApp account to link (' + WA_MODE + ' mode):');
  console.log('WhatsApp > Settings > Linked devices > Link a device\n');
  qrcode.generate(qr, { small: true });
});

client.on('authenticated', () => {
  console.log('WhatsApp authenticated.');
});

client.on('auth_failure', (message) => {
  console.error('WhatsApp authentication failed:', message);
  ready = false;
});

client.on('ready', async () => {
  ready = true;
  lastQr = null;
  try {
    selfWid = client.info.wid._serialized;
  } catch (err) {
    selfWid = null;
  }
  try {
    if (WA_MODE === 'self') {
      ownerWid = selfWid;
    } else {
      const id = await client.getNumberId(OWNER_NUMBER);
      ownerWid = id ? id._serialized : null;
    }
  } catch (err) {
    ownerWid = null;
  }
  if (ownerWid) {
    console.log('WhatsApp bridge ready (' + WA_MODE + ' mode). Messages to you go to ' + ownerWid);
  } else {
    console.warn('WhatsApp is ready, but the owner chat could not be resolved. Check OWNER_WHATSAPP.');
  }
});

client.on('disconnected', (reason) => {
  console.warn('WhatsApp disconnected:', reason);
  ready = false;
  ownerWid = null;
  setTimeout(() => {
    client.initialize().catch((err) => console.error('Re-initialize failed:', err.message));
  }, 5000);
});

// Self mode: commands you type to yourself, e.g. "Jarvis, what's on my calendar?"
client.on('message_create', async (msg) => {
  if (WA_MODE !== 'self') return;
  try {
    if (!msg.fromMe) return;
    const inSelfChat = msg.to === selfWid || msg.from === msg.to;
    if (!inSelfChat) return;
    const body = String(msg.body || '').trim();
    if (!body || body.startsWith(BOT_PREFIX.trim())) return; // ignore Jarvis's own messages
    const match = body.match(/^jarvis\b[\s,:;.!-]*([\s\S]*)$/i);
    if (!match || !match[1].trim()) return;
    const reply = await askJarvis(match[1].trim());
    await client.sendMessage(selfWid, BOT_PREFIX + reply);
  } catch (err) {
    console.error('Self-chat command failed:', err.message);
  }
});

// Incoming messages from other people (self mode) or from you (assistant mode)
client.on('message', async (msg) => {
  try {
    if (msg.fromMe || msg.isStatus || msg.from === 'status@broadcast') return;

    if (WA_MODE === 'assistant') {
      const contact = await msg.getContact();
      const senderNumber = String(contact && contact.number ? contact.number : '').replace(/\D/g, '');
      if (senderNumber !== OWNER_NUMBER) return; // ignore everyone except the owner
      if (msg.type !== 'chat' || !msg.body) {
        await msg.reply('I can only read text messages for now.');
        return;
      }
      await msg.reply(await askJarvis(msg.body));
      return;
    }

    // self mode: tell Jarvis a message arrived (sender only, never the content)
    const chat = await msg.getChat();
    if (chat.isGroup && !ANNOUNCE_GROUPS) return;
    if (chat.isMuted) return;
    const sender = chat.isGroup ? await nameOf(msg) : chat.name || (await nameOf(msg));
    await postJson('/api/whatsapp-event', { sender: sender, chat: chat.name || '', isGroup: !!chat.isGroup });
  } catch (err) {
    console.error('Inbound message handling failed:', err.message);
  }
});

// ------------------------------------------------------------------------
// HTTP API for the Python server
// ------------------------------------------------------------------------
const app = express();
app.use(express.json({ limit: '256kb' }));

app.use((req, res, next) => {
  if (req.get('x-bridge-token') !== BRIDGE_TOKEN) {
    return res.status(403).json({ service: 'jarvis-wa-bridge', error: 'Forbidden: x-bridge-token required' });
  }
  next();
});

app.get('/status', (req, res) => {
  res.json({ ready: ready && !!ownerWid, hasQr: !!lastQr, mode: WA_MODE });
});

app.post('/send', async (req, res) => {
  const message = String((req.body && req.body.message) || '').trim();
  if (!message) return res.status(400).json({ error: 'Empty message' });
  if (!ready) return res.status(503).json({ error: 'WhatsApp is not connected yet' });
  try {
    if (!ownerWid) {
      if (WA_MODE === 'self') {
        ownerWid = selfWid;
      } else {
        const id = await client.getNumberId(OWNER_NUMBER);
        if (!id) return res.status(404).json({ error: 'Owner number is not registered on WhatsApp' });
        ownerWid = id._serialized;
      }
    }
    const text = WA_MODE === 'self' ? BOT_PREFIX + message : message;
    await client.sendMessage(ownerWid, text);
    res.json({ ok: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Recent chats, newest first. Read-only: nothing is marked as read.
app.get('/chats', async (req, res) => {
  if (!ready) return res.status(503).json({ error: 'WhatsApp is not connected yet' });
  const limit = clamp(req.query.limit, 1, 20, 8);
  const unreadOnly = req.query.unread === '1';
  try {
    let chats = await client.getChats();
    if (unreadOnly) chats = chats.filter((c) => (c.unreadCount || 0) > 0);
    chats.sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));
    const out = chats.slice(0, limit).map((c) => ({
      name: c.name || (c.id && c.id.user) || 'unknown',
      isGroup: !!c.isGroup,
      unread: c.unreadCount || 0,
      timestamp: c.timestamp || 0,
      last: c.lastMessage ? { fromMe: !!c.lastMessage.fromMe, body: previewOf(c.lastMessage, 200) } : null
    }));
    res.json({ chats: out });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Latest messages of one chat, found by name (partial match) or phone number.
app.get('/messages', async (req, res) => {
  if (!ready) return res.status(503).json({ error: 'WhatsApp is not connected yet' });
  const query = String(req.query.chat || '').trim();
  if (!query) return res.status(400).json({ error: 'Missing chat name' });
  const limit = clamp(req.query.limit, 1, 30, 10);
  try {
    const chats = await client.getChats();
    chats.sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));
    const q = query.toLowerCase();
    const digits = query.replace(/\D/g, '');
    const match =
      chats.find((c) => (c.name || '').toLowerCase() === q) ||
      chats.find((c) => (c.name || '').toLowerCase().includes(q)) ||
      (digits.length >= 6 ? chats.find((c) => c.id && c.id.user && c.id.user.includes(digits)) : null);
    if (!match) return res.status(404).json({ error: 'No chat matching "' + query + '"' });

    const messages = await match.fetchMessages({ limit: limit });
    const out = [];
    for (const m of messages) {
      let from = 'me';
      if (!m.fromMe) from = match.isGroup ? await nameOf(m) : match.name || 'them';
      out.push({ from: from, fromMe: !!m.fromMe, body: previewOf(m, 500), timestamp: m.timestamp || 0, type: m.type });
    }
    res.json({ chat: match.name || query, isGroup: !!match.isGroup, messages: out });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

const server = app.listen(BRIDGE_PORT, '127.0.0.1', () => {
  console.log('Bridge API listening on http://127.0.0.1:' + BRIDGE_PORT + ' (mode: ' + WA_MODE + ')');
  // Only start WhatsApp once the API port is really ours.
  client.initialize().catch((err) => {
    console.error('WhatsApp client failed to start:', err.message);
  });
});

server.on('error', (err) => {
  if (err.code === 'EADDRINUSE') {
    console.error('\nPort ' + BRIDGE_PORT + ' is already used by another program, so the bridge cannot start.');
    console.error('Pick a free port: set BRIDGE_PORT and BRIDGE_URL in .env (for example 3917) and restart both servers.');
    console.error('To find the program using it on Windows:  netstat -ano | findstr :' + BRIDGE_PORT);
  } else {
    console.error('Bridge API failed to start:', err.message);
  }
  process.exit(1);
});
