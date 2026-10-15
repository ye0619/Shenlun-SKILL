/**
 * Harness-side prompt-cache steward.
 *
 * Provider prompt caches match an exact token prefix, so every request that
 * diverges from the previous one re-bills the whole diverged prefix. This
 * plugin keeps that cost visible and bounds the one cause the Harness itself
 * controls: oversized tool results entering the session log.
 *
 * It is deliberately a weak-mechanism plugin: one read-only tool, one
 * observation listener, and (opt-in, default on) one result transform.
 */
import z from '@deepseek-ai/schemastery';

const name = 'cache-steward';
const inject = ['tools'];

export const Config = z.object({
    /** Transform oversized tool results before they enter the session log. */
    trimOversizedToolResults: z.boolean().default(true),
    /** Byte budget for one tool result; larger results keep head and tail. */
    toolResultSoftCapBytes: z.number().min(2048).default(24576),
    /** How many heaviest session events a report lists. */
    reportTopEvents: z.number().min(1).max(50).default(8),
    /** Emit an info-level line for every detected cache seam. */
    logSeams: z.boolean().default(true),
});

const encoder = new TextEncoder();

function byteLength(value) {
    if (typeof value === 'string') return encoder.encode(value).length;
    if (value === undefined || value === null) return 0;
    try {
        return encoder.encode(JSON.stringify(value)).length;
    } catch {
        return 0;
    }
}

function fnv1a(text, seed) {
    let h = seed >>> 0;
    for (let i = 0; i < text.length; i += 1) {
        h ^= text.charCodeAt(i);
        h = Math.imul(h, 16777619) >>> 0;
    }
    return h;
}

function stringify(value) {
    if (typeof value === 'string') return value;
    if (value === undefined) return 'undefined';
    try {
        return JSON.stringify(value);
    } catch {
        return String(value);
    }
}

/** Compare two segment lists by (label, hash) and return the first divergence. */
function firstDivergence(previous, current) {
    if (previous.length === 0) return { index: 0, reason: 'first-observed-request' };
    const limit = Math.min(previous.length, current.length);
    for (let i = 0; i < limit; i += 1) {
        const a = previous[i];
        const b = current[i];
        if (a.label !== b.label) return { index: i, reason: `segment-kind-changed:${a.label}->${b.label}`, previous: a, current: b };
        if (a.hash !== b.hash) return { index: i, reason: `content-changed:${b.label}`, previous: a, current: b };
    }
    if (previous.length > current.length) {
        return { index: limit, reason: `segment-removed:${previous[limit].label}`, previous: previous[limit], current: null };
    }
    return null;
}

/**
 * Stable order in which a chat request's parts are laid out on the wire.
 * `system`/`tools` precede `messages`, so they must precede them here too:
 * otherwise appending a message shifts every later segment and a harmless
 * continuation is misreported as a seam.
 */
const FIELD_ORDER = [
    'system',
    'systemPrompt',
    'developerMessage',
    'tools',
    'messages',
    'toolChoice',
    'temperature',
    'topP',
    'maxTokens',
    'stream',
];

function orderedKeys(object) {
    const present = Object.keys(object);
    const known = FIELD_ORDER.filter((key) => present.includes(key));
    const rest = present.filter((key) => !FIELD_ORDER.includes(key)).sort();
    return [...known, ...rest];
}

/**
 * Build a stable per-request segment list. Every entry is hashed, never kept,
 * so the visitor holds no message content and cannot leak prompt text.
 *
 * Array element counts are deliberately NOT emitted as their own segment: a
 * request that merely appends to a list is a cache continuation, not a seam.
 * Only content changes inside an existing position (or a positional kind
 * change) count as a divergence.
 */
function collectSegments(options) {
    const segments = [];
    const push = (label, value) => {
        const text = stringify(value);
        const bytes = byteLength(text);
        segments.push({ label, bytes, hash: `${fnv1a(text, 0x811c9dc5).toString(16)}:${bytes}` });
    };

    if (options === undefined || options === null) return segments;
    if (Array.isArray(options)) {
        for (let i = 0; i < options.length; i += 1) push(`options[${i}]`, options[i]);
        return segments;
    }
    if (typeof options !== 'object') {
        push('options', options);
        return segments;
    }

    for (const key of orderedKeys(options)) {
        const value = options[key];
        if (Array.isArray(value)) {
            for (let i = 0; i < value.length; i += 1) push(`${key}[${i}]`, value[i]);
        } else if (value !== undefined) {
            push(key, value);
        }
    }
    return segments;
}

function totalBytes(segments) {
    let sum = 0;
    for (const segment of segments) sum += segment.bytes;
    return sum;
}

const trimNotice = (droppedBytes) =>
    `\n\n…[cache-steward: ${droppedBytes} bytes elided from this tool result to keep the prompt-cache prefix lean]…\n\n`;

/** Keep head and tail of an oversized result string under the cap. */
function clipText(text, cap) {
    if (text.length <= cap) return null;
    const head = Math.max(1024, Math.floor(cap * 0.6));
    const tail = Math.max(1024, cap - head);
    const dropped = text.length - head - tail;
    if (dropped <= 0) return null;
    return text.slice(0, head) + trimNotice(dropped) + text.slice(text.length - tail);
}

function clipValue(value, cap) {
    if (typeof value === 'string') return clipText(value, cap);
    if (Array.isArray(value)) {
        let changed = false;
        const next = value.map((item) => {
            const clipped = clipValue(item, cap);
            if (clipped !== null && clipped !== item) changed = true;
            return clipped === null ? item : clipped;
        });
        return changed ? next : null;
    }
    if (value !== null && typeof value === 'object') {
        let changed = false;
        const next = {};
        for (const [key, item] of Object.entries(value)) {
            const clipped = clipValue(item, cap);
            if (clipped !== null && clipped !== item) changed = true;
            next[key] = clipped === null ? item : clipped;
        }
        return changed ? next : null;
    }
    return null;
}

export function apply(ctx, config = {}) {
    const logger = ctx.logger?.('cache-steward') ?? ctx.logger ?? undefined;
    const log = (level, message) => {
        try {
            if (logger && typeof logger[level] === 'function') logger[level](message);
        } catch {
            /* logging must never break a request */
        }
    };

    /** @type {Map<string, object>} */
    const sessions = new Map();
    /** @type {Map<string, {bytes:number,count:number}>} */
    const toolBytes = new Map();

    ctx.effect(() => ctx.on('llm/stream', (options, next) => {
        try {
            const segments = collectSegments(options);
            const sessionId = ctx.currentSessionId ?? ctx.session?.id ?? 'default';
            const state = sessions.get(sessionId) ?? { segments: [], requests: 0, seams: 0, missBytes: 0, lastTotal: 0 };
            const divergence = firstDivergence(state.segments, segments);
            state.requests += 1;
            if (divergence !== null) {
                state.seams += 1;
                const divergenceOffset = divergence.index < segments.length
                    ? segments.slice(0, divergence.index).reduce((sum, item) => sum + item.bytes, 0)
                    : totalBytes(segments);
                state.missBytes += divergenceOffset;
                const isFirst = divergence.reason === 'first-observed-request';
                const level = config.logSeams === false ? 'debug' : 'info';
                if (isFirst) {
                    log(level, `session ${sessionId}: first request observed, prefix ${totalBytes(segments)} bytes (cold start, nothing to reuse)`);
                } else {
                    log(level, `session ${sessionId}: request #${state.requests} cache seam at segment ${divergence.index} (${divergence.reason}); ~${divergenceOffset} bytes of prefix re-billed`);
                }
            }
            state.segments = segments;
            state.lastTotal = totalBytes(segments);
            sessions.set(sessionId, state);
        } catch (error) {
            log('warn', `cache seam probe failed: ${error?.message ?? error}`);
        }
        return next();
    }), 'cache-steward: llm/stream seam probe');

    ctx.effect(() => ctx.on('tools/post-execute', (exec, result, next) => next().then((settled) => {
        try {
            const outcome = settled ?? result;
            const bytes = byteLength(outcome);
            const tool = String(exec?.name ?? exec?.toolName ?? 'unknown');
            const bucket = toolBytes.get(tool) ?? { bytes: 0, count: 0 };
            bucket.bytes += bytes;
            bucket.count += 1;
            toolBytes.set(tool, bucket);

            if (config.trimOversizedToolResults === false) return settled;
            const cap = config.toolResultSoftCapBytes ?? 24576;
            if (bytes <= cap) return settled;

            const content = outcome?.content ?? outcome;
            const clipped = clipValue(content, cap);
            if (clipped === null) return settled;
            log('info', `${tool}: tool result ${bytes} bytes exceeds cap ${cap}; elided oversized payload before log entry`);
            return { ...(outcome && typeof outcome === 'object' ? outcome : {}), content: clipped };
        } catch (error) {
            log('warn', `tool result trim failed: ${error?.message ?? error}`);
            return settled;
        }
    })), 'cache-steward: oversized tool result trim');

    ctx.effect(() => ctx.tools.register({
        name: 'cache_steward',
        description:
            'Report prompt-cache health for the current Harness session: request count, detected cache seams with the segment index that diverged, and the heaviest tool outputs by byte size. Read-only diagnostic for token consumption.',
        parameters: {
            action: {
                type: 'string',
                required: true,
                enum: ['report', 'reset'],
                description: 'report returns current counters; reset clears the in-memory counters.',
            },
        },
        async execute(args) {
            const sessionId = ctx.currentSessionId ?? ctx.session?.id ?? 'default';
            if (args?.action === 'reset') {
                sessions.delete(sessionId);
                toolBytes.clear();
                return 'cache-steward counters reset for this session.';
            }
            const state = sessions.get(sessionId);
            const top = [...toolBytes.entries()].sort((a, b) => b[1].bytes - a[1].bytes).slice(0, config.reportTopEvents ?? 8);
            const lines = [];
            lines.push(`session: ${sessionId}`);
            if (state === undefined) {
                lines.push('requests observed this session: 0');
            } else {
                lines.push(`requests observed: ${state.requests}`);
                lines.push(`cache seams detected: ${state.seams} (first-divergence detections; a seam means the provider must re-bill the prefix from that point)`);
                lines.push(`estimated re-billed prefix bytes: ${state.missBytes}`);
                lines.push(`current request prefix bytes: ${state.lastTotal}`);
                if (state.segments.length > 0) {
                    const heaviest = [...state.segments].sort((a, b) => b.bytes - a.bytes).slice(0, config.reportTopEvents ?? 8);
                    lines.push('heaviest request segments:');
                    for (const segment of heaviest) lines.push(`  ${segment.bytes} B  ${segment.label}`);
                }
            }
            if (top.length > 0) {
                lines.push('heaviest tool outputs this session:');
                for (const [tool, bucket] of top) lines.push(`  ${bucket.bytes} B  ${tool} (${bucket.count} results)`);
            }
            lines.push('note: prompt-cache hit rate itself is measured at the provider/proxy layer; this report explains which Harness-side change caused a seam.');
            return lines.join('\n');
        },
    }), 'cache-steward: cache_steward tool');

    ctx.effect(() => ctx.on('session/disposed', () => {
        sessions.clear();
        toolBytes.clear();
    }), 'cache-steward: session cleanup');
}

export { name, inject };
