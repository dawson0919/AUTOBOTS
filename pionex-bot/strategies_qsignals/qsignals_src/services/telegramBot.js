/**
 * Telegram Bot Service for Q-SIGNALS
 * Handles: Bot commands, user linking, signal notifications
 */
const https = require('https');

const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN || '';
const SITE_URL = process.env.SITE_URL || 'https://q-signals-production.up.railway.app';

// ── Telegram API Helper ────────────────────────
function telegramAPI(method, body = {}) {
    return new Promise((resolve, reject) => {
        if (!BOT_TOKEN) return reject(new Error('TELEGRAM_BOT_TOKEN not set'));

        const data = JSON.stringify(body);
        const options = {
            hostname: 'api.telegram.org',
            path: `/bot${BOT_TOKEN}/${method}`,
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Content-Length': Buffer.byteLength(data)
            }
        };

        const req = https.request(options, res => {
            let responseData = '';
            res.on('data', chunk => responseData += chunk);
            res.on('end', () => {
                try {
                    const json = JSON.parse(responseData);
                    if (json.ok) resolve(json.result);
                    else reject(new Error(json.description || 'Telegram API error'));
                } catch (e) {
                    reject(e);
                }
            });
        });

        req.on('error', reject);
        req.write(data);
        req.end();
    });
}

// ── Send Message ────────────────────────────────
async function sendMessage(chatId, text, options = {}) {
    try {
        return await telegramAPI('sendMessage', {
            chat_id: chatId,
            text,
            parse_mode: 'HTML',
            disable_web_page_preview: true,
            ...options
        });
    } catch (err) {
        console.error(`[Telegram] Failed to send message to ${chatId}:`, err.message);
        return null;
    }
}

// ── Send Signal Notification ─────────────────────
async function sendSignalNotification(chatId, signal) {
    const { strategyName, symbol, type, price, timeframe, rule } = signal;
    const symbolClean = symbol.replace('USDT', '/USDT');
    const emoji = type === 'LONG' ? '🟢' : '🔴';
    const action = type === 'LONG' ? 'BUY' : 'SELL';
    const precision = price < 1 ? 4 : (price < 100 ? 2 : 0);

    const ruleMap = {
        'S1': '突破均線 Golden Cross',
        'S2': '回踩均線 Bounce',
        'S3': '趨勢追買 Bull Trend',
        'S4': '負乖離回補 Deviation Reversion',
        'S5': '跌破均線 Death Cross'
    };

    // Format time to Asia/Taipei explicitly
    const signalDate = new Date(signal.entryTime || Date.now());
    let timeStr = signalDate.toLocaleString('en-US', {
        timeZone: 'Asia/Taipei',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false
    });

    // Fix common Node.js formatting quirk where midnight is sometimes 24:00
    // e.g. "Feb 7, 24:00" -> "Feb 8, 00:00"
    if (timeStr.includes(' 24:')) {
        const nextDay = new Date(signalDate.getTime() + 60 * 60 * 1000);
        timeStr = nextDay.toLocaleString('en-US', {
            timeZone: 'Asia/Taipei',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            hour12: false
        }).replace(/\b24:/, '00:');
    }

    const message = [
        `${emoji} <b>NEW SIGNAL — ${action}</b>`,
        ``,
        `📊 <b>${strategyName}</b>`,
        `💰 ${symbolClean} • ${timeframe}`,
        `💲 Price: <b>$${Number(price).toLocaleString('en-US', { minimumFractionDigits: precision, maximumFractionDigits: precision })}</b>`,
        rule ? `📐 ${ruleMap[rule] || rule}` : '',
        `⏰ <b>${timeStr} (UTC+8)</b>`,
        ``,
        `🔗 <a href="${SITE_URL}/strategy-detail.html?strategy=${signal.strategyId}&symbol=${symbol}&timeframe=${timeframe}">View Details</a>`,
        ``,
        `<pre>v2.2-stable</pre>`
    ].filter(Boolean).join('\n');

    return sendMessage(chatId, message);
}

// ── Send Close Signal Notification ───────────────────
async function sendCloseSignalNotification(chatId, signal) {
    const { strategyName, symbol, type, entryPrice, exitPrice, pnlPercent, timeframe, entryTime, exitTime } = signal;
    const symbolClean = symbol.replace('USDT', '/USDT');
    const pnlEmoji = pnlPercent >= 0 ? '✅' : '❌';
    const pnlSign = pnlPercent >= 0 ? '+' : '';
    const precision = exitPrice < 1 ? 4 : (exitPrice < 100 ? 2 : 0);

    // Calculate hold duration
    const holdMs = new Date(exitTime).getTime() - new Date(entryTime).getTime();
    const holdHours = Math.floor(holdMs / (1000 * 60 * 60));
    const holdDays = Math.floor(holdHours / 24);
    const holdRemainder = holdHours % 24;
    const holdStr = holdDays > 0 ? `${holdDays}d ${holdRemainder}h` : `${holdHours}h`;

    // Format time to Asia/Taipei
    const closeDate = new Date(exitTime || Date.now());
    let timeStr = closeDate.toLocaleString('en-US', {
        timeZone: 'Asia/Taipei',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false
    });

    if (timeStr.includes(' 24:')) {
        const nextDay = new Date(closeDate.getTime() + 60 * 60 * 1000);
        timeStr = nextDay.toLocaleString('en-US', {
            timeZone: 'Asia/Taipei',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            hour12: false
        }).replace(/\b24:/, '00:');
    }

    const message = [
        `🔓 <b>CLOSE SIGNAL — EXIT ${type}</b>`,
        ``,
        `📊 <b>${strategyName}</b>`,
        `💰 ${symbolClean} • ${timeframe}`,
        `📈 Entry: <b>$${Number(entryPrice).toLocaleString('en-US', { minimumFractionDigits: precision, maximumFractionDigits: precision })}</b>  →  Exit: <b>$${Number(exitPrice).toLocaleString('en-US', { minimumFractionDigits: precision, maximumFractionDigits: precision })}</b>`,
        `${pnlEmoji} P&L: <b>${pnlSign}${Number(pnlPercent).toFixed(2)}%</b>`,
        `⏱ Hold: ${holdStr}`,
        `⏰ <b>${timeStr} (UTC+8)</b>`,
        ``,
        `🔗 <a href="${SITE_URL}/strategy-detail.html?strategy=${signal.strategyId}&symbol=${symbol}&timeframe=${timeframe}">View Details</a>`,
        ``,
        `<pre>v2.2-stable</pre>`
    ].filter(Boolean).join('\n');

    return sendMessage(chatId, message);
}

// ── Send Holding Signal Notification ───────────────────
async function sendHoldingSignalNotification(chatId, signal) {
    const { strategyName, symbol, type, entryPrice, currentPrice, pnlPercent, timeframe, entryTime } = signal;
    const symbolClean = symbol.replace('USDT', '/USDT');
    const pnlEmoji = pnlPercent >= 0 ? '📈' : '📉';
    const pnlSign = pnlPercent >= 0 ? '+' : '';
    const precision = currentPrice < 1 ? 4 : (currentPrice < 100 ? 2 : 0);

    // Calculate hold duration
    const holdMs = Date.now() - new Date(entryTime).getTime();
    const holdHours = Math.floor(holdMs / (1000 * 60 * 60));
    const holdDays = Math.floor(holdHours / 24);
    const holdRemainder = holdHours % 24;
    const holdStr = holdDays > 0 ? `${holdDays}d ${holdRemainder}h` : `${holdHours}h`;

    const message = [
        `⏳ <b>POSITION HOLDING — ${type}</b>`,
        ``,
        `📊 <b>${strategyName}</b>`,
        `💰 ${symbolClean} • ${timeframe}`,
        `📍 Entry: <b>$${Number(entryPrice).toLocaleString('en-US', { minimumFractionDigits: precision, maximumFractionDigits: precision })}</b>`,
        `🕒 Current: <b>$${Number(currentPrice).toLocaleString('en-US', { minimumFractionDigits: precision, maximumFractionDigits: precision })}</b>`,
        `${pnlEmoji} Unr. P&L: <b>${pnlSign}${Number(pnlPercent).toFixed(2)}%</b>`,
        `⏱ Hold: ${holdStr}`,
        ``,
        `🔗 <a href="${SITE_URL}/strategy-detail.html?strategy=${signal.strategyId}&symbol=${symbol}&timeframe=${timeframe}">View Details</a>`,
        ``,
        `<pre>v2.2-stable</pre>`
    ].filter(Boolean).join('\n');

    return sendMessage(chatId, message);
}

// ── Generate Linking Code ───────────────────────
function generateLinkCode() {
    const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
    let code = '';
    for (let i = 0; i < 6; i++) {
        code += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    return code;
}

// ── Pending Link Codes (in-memory, expires in 10 min) ──
const pendingLinks = new Map(); // code -> { userId, expiresAt }

function createLinkCode(userId) {
    // Remove existing codes for this user
    for (const [code, data] of pendingLinks.entries()) {
        if (data.userId === userId) pendingLinks.delete(code);
    }

    const code = generateLinkCode();
    pendingLinks.set(code, {
        userId,
        expiresAt: Date.now() + 10 * 60 * 1000 // 10 minutes
    });

    // Auto-cleanup
    setTimeout(() => pendingLinks.delete(code), 10 * 60 * 1000);

    return code;
}

function consumeLinkCode(code) {
    const upper = code.toUpperCase().trim();
    const data = pendingLinks.get(upper);
    if (!data) return null;
    if (Date.now() > data.expiresAt) {
        pendingLinks.delete(upper);
        return null;
    }
    pendingLinks.delete(upper);
    return data.userId;
}

// ── Process Bot Update ──────────────────────────
async function processUpdate(update, db) {
    const msg = update.message;
    if (!msg || !msg.text) return;

    const chatId = msg.chat.id;
    const text = msg.text.trim();
    const firstName = msg.from.first_name || 'User';

    // /start command
    if (text === '/start') {
        await sendMessage(chatId, [
            `👋 <b>Welcome to Q-SIGNALS Bot, ${firstName}!</b>`,
            ``,
            `To receive trading signals, link your account:`,
            ``,
            `1️⃣ Go to your <a href="${SITE_URL}/profile.html">Profile Page</a>`,
            `2️⃣ Click "Link Telegram"`,
            `3️⃣ Copy the 6-digit code`,
            `4️⃣ Send it here: <code>/link CODE</code>`,
            ``,
            `📌 Commands:`,
            `/link CODE — Link your account`,
            `/status — Check connection status`,
            `/help — Show this help message`
        ].join('\n'));
        return;
    }

    // /help command
    if (text === '/help') {
        await sendMessage(chatId, [
            `📌 <b>Q-SIGNALS Bot Commands</b>`,
            ``,
            `/link CODE — Link your Q-SIGNALS account`,
            `/unlink — Disconnect Telegram`,
            `/status — Check connection status`,
            `/help — Show this message`,
            ``,
            `🌐 <a href="${SITE_URL}">Visit Q-SIGNALS</a>`
        ].join('\n'));
        return;
    }

    // /link CODE command
    if (text.startsWith('/link')) {
        const code = text.replace('/link', '').trim();
        if (!code) {
            await sendMessage(chatId, '❌ Please provide the code: <code>/link YOUR_CODE</code>');
            return;
        }

        const userId = consumeLinkCode(code);
        if (!userId) {
            await sendMessage(chatId, '❌ Invalid or expired code. Please generate a new code from your Profile page.');
            return;
        }

        // Save chat ID to profile
        try {
            const { getSupabaseAdmin } = db;
            const supabase = getSupabaseAdmin();

            await supabase
                .from('profiles')
                .update({
                    telegram_chat_id: String(chatId),
                    telegram_username: msg.from.username || null,
                    telegram_linked_at: new Date().toISOString()
                })
                .eq('id', userId);

            await sendMessage(chatId, [
                `✅ <b>Account linked successfully!</b>`,
                ``,
                `You will now receive trading signal notifications here.`,
                ``,
                `🔔 Make sure you have subscribed to strategies on the platform.`,
                `🌐 <a href="${SITE_URL}/profile.html">Manage Subscriptions</a>`
            ].join('\n'));

            console.log(`[Telegram] User ${userId} linked to chat ${chatId}`);
        } catch (err) {
            console.error('[Telegram] Link error:', err);
            await sendMessage(chatId, '❌ Failed to link account. Please try again.');
        }
        return;
    }

    // /unlink command
    if (text === '/unlink') {
        try {
            const { getSupabaseAdmin } = db;
            const supabase = getSupabaseAdmin();

            // Find profile by chat_id
            const { data: profile } = await supabase
                .from('profiles')
                .select('id')
                .eq('telegram_chat_id', String(chatId))
                .single();

            if (profile) {
                await supabase
                    .from('profiles')
                    .update({
                        telegram_chat_id: null,
                        telegram_username: null,
                        telegram_linked_at: null
                    })
                    .eq('id', profile.id);

                await sendMessage(chatId, '✅ Telegram disconnected. You will no longer receive signal notifications.');
            } else {
                await sendMessage(chatId, '⚠️ No linked account found.');
            }
        } catch (err) {
            console.error('[Telegram] Unlink error:', err);
            await sendMessage(chatId, '❌ Failed to unlink. Please try again.');
        }
        return;
    }

    // /status command
    if (text === '/status') {
        try {
            const { getSupabaseAdmin } = db;
            const supabase = getSupabaseAdmin();

            const { data: profile } = await supabase
                .from('profiles')
                .select('id, email, role')
                .eq('telegram_chat_id', String(chatId))
                .single();

            if (profile) {
                await sendMessage(chatId, [
                    `✅ <b>Connected</b>`,
                    ``,
                    `📧 ${profile.email}`,
                    `🏷️ ${(profile.role || 'standard').toUpperCase()} Member`,
                    `💬 Chat ID: <code>${chatId}</code>`,
                    ``,
                    `🌐 <a href="${SITE_URL}/profile.html">Manage Account</a>`
                ].join('\n'));
            } else {
                await sendMessage(chatId, '❌ Not linked. Use /link to connect your account.');
            }
        } catch (err) {
            await sendMessage(chatId, '❌ Error checking status.');
        }
        return;
    }

    // Unknown command - try to link if it looks like a code
    if (/^[A-Z0-9]{6}$/i.test(text)) {
        const userId = consumeLinkCode(text);
        if (userId) {
            // Auto-detect as link code
            try {
                const { getSupabaseAdmin } = db;
                const supabase = getSupabaseAdmin();

                await supabase
                    .from('profiles')
                    .update({
                        telegram_chat_id: String(chatId),
                        telegram_username: msg.from.username || null,
                        telegram_linked_at: new Date().toISOString()
                    })
                    .eq('id', userId);

                await sendMessage(chatId, `✅ <b>Account linked!</b> You'll now receive signal notifications.`);
                return;
            } catch (err) {
                // Fall through to default message
            }
        }
    }

    // Default response
    await sendMessage(chatId, `🤖 I don't understand that command. Type /help to see available commands.`);
}

// ── Webhook Setup ───────────────────────────────
async function setWebhook(url) {
    try {
        const result = await telegramAPI('setWebhook', {
            url: `${url}/api/telegram/webhook`,
            allowed_updates: ['message']
        });
        console.log('[Telegram] Webhook set:', result);
        return result;
    } catch (err) {
        console.error('[Telegram] Failed to set webhook:', err.message);
        return null;
    }
}

// ── Polling Mode (development) ──────────────────
let pollingInterval = null;
let lastUpdateId = 0;

async function startPolling(db) {
    if (!BOT_TOKEN) {
        console.log('[Telegram] No BOT_TOKEN, skipping bot startup');
        return;
    }

    console.log('[Telegram] Starting polling mode...');

    // Get bot info
    try {
        const me = await telegramAPI('getMe');
        console.log(`[Telegram] Bot: @${me.username} (${me.first_name})`);
    } catch (err) {
        console.error('[Telegram] Failed to get bot info:', err.message);
        return;
    }

    pollingInterval = setInterval(async () => {
        try {
            const updates = await telegramAPI('getUpdates', {
                offset: lastUpdateId + 1,
                timeout: 5,
                limit: 10
            });

            for (const update of updates) {
                lastUpdateId = update.update_id;
                await processUpdate(update, db);
            }
        } catch (err) {
            // Silent retry
        }
    }, 3000);
}

function stopPolling() {
    if (pollingInterval) {
        clearInterval(pollingInterval);
        pollingInterval = null;
    }
}

module.exports = {
    sendMessage,
    sendSignalNotification,
    sendCloseSignalNotification,
    sendHoldingSignalNotification,
    createLinkCode,
    consumeLinkCode,
    processUpdate,
    setWebhook,
    startPolling,
    stopPolling
};
