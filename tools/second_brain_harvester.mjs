#!/usr/bin/env node
// second_brain_harvester.mjs — Tiered "info-on-me" harvester for the Second Brain.
//
// Design: extends the claude-adhd multi-tool parsing approach (same sources,
// same noise filters, same node:sqlite read-only access) but classifies user
// turns into 4 semantic extractors (diet, health, work, decisions) with an
// importance score, then publishes distilled digests — never raw transcripts.
//
// Tiers:
//   Tier 0 (local only): raw transcripts stay on disk + .harvest_facts.jsonl
//     fact cache (content-addressed, idempotent regeneration).
//   Tier 1 (vault): 00_Inbox/daily-logs/YYYY-MM-DD.md — small daily digests.
//   Tier 2 (vault): 00_Inbox/entities/*.md — rolling topical dossiers.
//   Tier 3 (vault, review only): tier3-candidates.md — standing-rule
//     candidates for the user to promote manually.
//
// Sources (all read-only, best-effort per source):
//   - Claude Code:  ~/.claude/projects/**/*.jsonl
//   - OpenCode:     ~/.local/share/opencode/opencode.db (message+part tables)
//   - Codex CLI:    ~/.codex/sessions/**/rollout-*.jsonl
//   - Copilot Chat: %APPDATA%/Code/User/workspaceStorage/*/chatSessions/*
//   - Cursor Chat:  %APPDATA%/Cursor/User/workspaceStorage/*/chatSessions/*
//
// Usage:
//   node second_brain_harvester.mjs [--dry-run] [--stats-only]
//     [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--limit N]
//     [--vault PATH] [--facts PATH] [--state PATH] [--force]
//
// Env overrides: SB_VAULT, SB_FACTS, SB_STATE.

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import readline from 'node:readline';
import { execFileSync } from 'node:child_process';

const MAX_FILE_BYTES = 1024 * 1024; // skip huge transcripts (same as adhd indexer)
const MAX_SUMMARY_LEN = 280;
const MAX_TEXT_LEN = 600;
const MAX_BULLETS_PER_SECTION = 30;
// Bound memory: classification and summaries only ever need the head of a
// turn. Pastes longer than this are truncated before classification.
const MAX_TURN_CHARS = 4000;
function capTurnText(text) {
  const t = String(text || '').trim();
  return t.length > MAX_TURN_CHARS ? t.slice(0, MAX_TURN_CHARS) : t;
}

// ---------------------------------------------------------------------------
// Paths & config
// ---------------------------------------------------------------------------

function defaultVault() {
  return process.env.SB_VAULT || 'G:\\My Drive\\SecondBrain';
}
function defaultFacts() {
  return process.env.SB_FACTS || 'C:\\SecondBrainStaging\\SecondBrain\\_tools\\.harvest_facts.jsonl';
}
function defaultState() {
  return process.env.SB_STATE || 'C:\\SecondBrainStaging\\SecondBrain\\_tools\\.harvest_state.json';
}
function homeDir() {
  return os.homedir();
}
function claudeProjectsDir() {
  return process.env.ADHD_PROJECTS_DIR || path.join(homeDir(), '.claude', 'projects');
}
function codexSessionsDir() {
  return process.env.ADHD_CODEX_DIR || path.join(homeDir(), '.codex', 'sessions');
}
function opencodeDbPath() {
  return (
    process.env.ADHD_OPENCODE_DB ||
    path.join(homeDir(), '.local', 'share', 'opencode', 'opencode.db')
  );
}
function copilotChatDir() {
  return (
    process.env.SB_COPILOT_DIR ||
    path.join(
      process.env.APPDATA || path.join(homeDir(), 'AppData', 'Roaming'),
      'Code',
      'User',
      'workspaceStorage'
    )
  );
}
function cursorChatDir() {
  return (
    process.env.SB_CURSOR_DIR ||
    path.join(
      process.env.APPDATA || path.join(homeDir(), 'AppData', 'Roaming'),
      'Cursor',
      'User',
      'workspaceStorage'
    )
  );
}

// ---------------------------------------------------------------------------
// Noise filters (same philosophy as claude-adhd)
// ---------------------------------------------------------------------------

const SKIP_PREFIXES = ['<', '[{', '{'];
export function looksLikeCommand(text) {
  return SKIP_PREFIXES.some((p) => text.startsWith(p));
}

const SYSTEM_NOISE_RE = /^(<system-reminder>|<local-command-|<command-name>|Caveat:|\[claude-adhd)/;

function looksLikePureCodeDump(text) {
  const lines = text.split('\n');
  if (lines.length < 6) return false;
  let codeish = 0;
  for (const ln of lines) {
    const t = ln.trim();
    if (
      /^(import |from |def |class |function |const |let |var |return |if |for |while |try:|except|Traceback|File "|\s{2,}\S+@\S+|PS [A-Z]:\\|>>> )/.test(t) ||
      (/[{};]\s*$/.test(t) && /[=();]/.test(t))
    ) {
      codeish++;
    }
  }
  return codeish / lines.length > 0.4;
}

// ---------------------------------------------------------------------------
// Semantic extractors (the extension beyond claude-adhd's TASK_PATTERNS)
// ---------------------------------------------------------------------------

export const DIET_PATTERNS = [
  /\b(had|having|ate|eating)\b.{0,50}\b(breakfast|lunch|dinner|meal|snack|protein|food)\b/i,
  /\bwhat did i (eat|have|ate)\b/i,
  /\b(breakfast|lunch|dinner)\b/i,
  /\b(fasting|intermittent fasting|calorie|calories|macros?\b|diet\b)/i,
  /\b(protein shake|whey|oats|paneer|chicken|rice\b|eggs?\b|dal\b|roti\b|biryani|pizza|burger|sandwich|salad|coffee|chai|alcohol|beer)\b/i,
  /\b(ordered|cooked|made|had).{0,25}\b(food|dinner|lunch|meal|takeout|restaurant)\b/i,
  /\b(hungry|full meal|overeat|skipped (a )?meal)\b/i,
];

// Every alternative below is anchored to fitness/body/medical context on
// purpose. Bare words were measured against real logs and rejected:
//   running  -> "still running", "keep running" (processes)
//   training/trainer -> "model training", HuggingFace Trainer
//   exercise -> "thought exercise"
//   sleep    -> laptop "sleep mode", "wake from standby"
//   recovery -> laptop/SSD "recovery plan"
//   steps    -> "next steps", "step limit"
//   weight   -> "weight the options" (ML "weights" never matched anyway)
//   pressure -> "under pressure" (only "blood pressure" is health)
//   cold     -> "cold email", "cold start" (only illness phrasing is health)
//   pain     -> "pain point" (only body-anchored pain is health)
//   tablet   -> iPad/tablet PC (dropped; medicine/dose still match)
//   exhausted-> "quota exhausted" (only personal phrasing is health)
export const HEALTH_PATTERNS = [
  /\b(workout|gym|cardio|yoga|bench( press)?|squat|deadlift|push-?ups?|pull-?ups?)\b/i,
  /\b(went (for a )?run|went running|morning run|evening run|ran \d+)\b/i,
  /\b(walked? \d+|\d[\d,]* ?steps|step[- ]?count|daily steps)\b/i,
  /\b((strength|weight|gym|workout) training|training (split|program|plan|session)|(gym|personal|fitness) trainer|my trainer|trainer (fee|session|split|plan|appointment))\b/i,
  /\b(do(ing)? (some )?exercise|exercise (routine|bike|daily))\b/i,
  /\b(my sleep|sleep (well|badly|late|early|deprived)|slept\b|went to (sleep|bed)|wok?e up|insomnia|(take|took) a nap|sleep (quality|duration|hours?|schedule|routine|track\w*|apnea|deprivation))\b/i,
  /\b(tired|fatigue|energy|i'm exhausted|i am exhausted|feeling exhausted|exhausted (and|from))\b/i,
  /\b((back|knee|neck|shoulder|joint|muscle|chest|stomach|head|body) pain|pain (in|behind|around)|in pain|injur(y|ed|ies)|soreness|sore muscles?|sprain|strain|doctor|hospital|clinic|prescription|medicine|dose|dosage|\d+\s?mg|\bmg\b|TRT|testosterone|hCG|sustanon|supplement|creatine|vitamin|protein powder|blood (test|pressure|work|sugar)|anxiety|mood|(lost|gained) weight|weight (loss|gain)|my weight|weighs? \d+|\d+\s?kg|\bkg\b|fever|cough|common cold|caught (a )?cold|down with (a )?cold|cold symptoms|handwriting|medical|health checkup)\b/i,
];

const KNOWN_REPOS = [
  'naukri', 'instahyre', 'editor-pack', 'second-brain', 'secondbrain',
  'job-tracker', 'jobtracker', 'linkedin', 'facecard', 'github',
  'opencode', 'copilot', 'cursor', 'codex', 'reel', 'video-use',
  'ffmpeg', 'bot', 'applier', 'dashboard', 'playlist', 'captions',
  'takeout', 'drive', 'notebooklm', 'yt-saved', 'ig-saved',
];

export const WORK_PATTERNS = [
  new RegExp(`\\b(${KNOWN_REPOS.join('|')})\\b`, 'i'),
  /\b(built|fixed|deployed|refactor(ed|ing)?|merged|committed|commit|pull request|\bPR\b|bug|error|traceback|exception|test(s|ing|ed)?|pipeline|automation|script|bot\b|cron|task (scheduler|scheduled)|applied|application|interview|resume|profile|scrap(e|ing|er)|playwright|selenium|CDP)\b/i,
  /\b(working on|worked on|shipped|implemented|migrated|debugg(ed|ing)?|installed|configured|setup|wired up)\b/i,
];

export const DECISION_PATTERNS = [
  /\b(decided to|my rule is|from now on|i chose|i choose|never do|always do|i prefer|standing rule|locked decision|my priority|life goal)\b/i,
  /\b(earn more|side hustle|trainer|25%|waking time)\b/i,
];

const STANDING_RULE_RE = /\b(always|never|from now on|(my )?rule( is)?|priority|standing|locked|goal\b.*first)\b/i;
const SPECIFIC_ENTITY_RE = /(\d+\s?(g|kg|mg|ml|hrs?|hours?|minutes?|mins?|km|steps)|Rs\.?\s?\d|₹\s?\d|\b\d{2,}:\d{2}\b)/i;

function cleanText(text) {
  return text.replace(/\s+/g, ' ').trim();
}

function makeSummary(text, maxLen = MAX_SUMMARY_LEN) {
  const clean = cleanText(text);
  if (clean.length <= maxLen) return clean;
  const cut = clean.slice(0, maxLen);
  const lastSpace = cut.lastIndexOf(' ');
  return (lastSpace > maxLen * 0.6 ? cut.slice(0, lastSpace) : cut) + ' …';
}

function countHits(patterns, text) {
  let n = 0;
  for (const re of patterns) {
    if (re.test(text)) n++;
  }
  return n;
}

// Long pasted documents (skill files, post drafts, specs) contain incidental
// single-word hits ("sleep" in a skill doc, "coffee" in a LinkedIn draft).
// For texts over LONG_TEXT_CHARS, diet/health need corroboration (2+ distinct
// pattern hits); short texts keep single-hit sensitivity for real meal logs.
const LONG_TEXT_CHARS = 800;
// `claude doctor` is a CLI health-check command, not a physician.
const HEALTH_EXCLUSIONS = [/\bclaude (code )?doctor\b/i];

function classifyTurn(text) {
  const cats = [];
  const dietHits = countHits(DIET_PATTERNS, text);
  const healthHits = countHits(HEALTH_PATTERNS, text);
  const longText = text.length > LONG_TEXT_CHARS;
  if (dietHits > 0 && (!longText || dietHits >= 2)) cats.push('diet');
  if (healthHits > 0 && (!longText || healthHits >= 2)) {
    if (!HEALTH_EXCLUSIONS.some((re) => re.test(text))) cats.push('health');
  }
  if (WORK_PATTERNS.some((re) => re.test(text))) cats.push('work');
  if (DECISION_PATTERNS.some((re) => re.test(text))) cats.push('decision');
  return cats;
}

function importanceOf(text, cats) {
  if (cats.length === 0) return 0;
  if (cats.includes('decision') && STANDING_RULE_RE.test(text)) return 3;
  if (SPECIFIC_ENTITY_RE.test(text) && cats.length > 0) return 2;
  if (cats.includes('diet') || cats.includes('health')) return 2;
  if (cats.includes('decision')) return 2;
  if (cats.includes('work')) return 1;
  return 1;
}

function factId(dateStr, sessionKey, summary) {
  const h = crypto.createHash('sha1');
  h.update(`${dateStr}|${sessionKey}|${summary}`);
  return h.digest('hex').slice(0, 16);
}

function toDateStr(ts) {
  const d = new Date(ts);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function toTimeStr(ts) {
  const d = new Date(ts);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

// ---------------------------------------------------------------------------
// Source readers — each yields { text, ts, source, sessionKey, sessionTitle }
// (user turns only: "info on me" lives in what the user said)
// ---------------------------------------------------------------------------

function walkFiles(root, exts, maxBytes, streamJsonl) {
  const out = [];
  const walk = (dir) => {
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries) {
      const full = path.join(dir, e.name);
      if (e.isDirectory()) {
        if (e.name === 'node_modules' || e.name === '.git') continue;
        walk(full);
      } else if (e.isFile()) {
        if (!exts.some((x) => full.endsWith(x))) continue;
        try {
          const st = fs.statSync(full);
          if (st.size > (maxBytes || MAX_FILE_BYTES)) {
            // .jsonl transcripts are line-delimited: stream them instead of
            // skipping, so huge sessions still contribute their user turns.
            // (Single-blob .json files have no streaming path — skip those.)
            if (streamJsonl && full.endsWith('.jsonl')) {
              out.push({ full, mtime: st.mtimeMs, size: st.size, stream: true });
              continue;
            }
            out.push({ full, mtime: st.mtimeMs, size: st.size, skipped: true });
            continue;
          }
          out.push({ full, mtime: st.mtimeMs, size: st.size });
        } catch {
          /* ignore unreadable */
        }
      }
    }
  };
  walk(root);
  return out;
}

// Stream a line-delimited JSON file without loading it whole into memory.
// Caps lines processed to bound time on pathological files.
const MAX_STREAM_LINES = 300000;
async function streamJsonlEntries(full, onEntry, stats) {
  return new Promise((resolve) => {
    let lines = 0;
    let done = false;
    const finish = () => {
      if (!done) {
        done = true;
        resolve();
      }
    };
    let input;
    try {
      input = fs.createReadStream(full, { encoding: 'utf8' });
    } catch {
      stats.errors++;
      finish();
      return;
    }
    const rl = readline.createInterface({ input, crlfDelay: Infinity });
    rl.on('line', (line) => {
      if (++lines > MAX_STREAM_LINES) {
        stats.truncatedFiles = (stats.truncatedFiles || 0) + 1;
        rl.close();
        try {
          input.destroy();
        } catch {
          /* ignore */
        }
        return;
      }
      const t = line.trim();
      if (!t) return;
      try {
        onEntry(JSON.parse(t));
      } catch {
        /* skip malformed line */
      }
    });
    rl.on('close', finish);
    rl.on('error', finish);
    input.on('error', () => {
      try {
        rl.close();
      } catch {
        /* ignore */
      }
      finish();
    });
  });
}

function claudeTurnFromEntry(entry, sessionKey) {
  if (entry.isSidechain) return null;
  const text = capTurnText(claudeUserText(entry) || '');
  if (!text || looksLikeCommand(text) || SYSTEM_NOISE_RE.test(text) || text.length < 12) {
    return null;
  }
  let ts = Date.now();
  if (entry.timestamp) {
    const p = new Date(entry.timestamp).getTime();
    if (!Number.isNaN(p)) ts = p;
  }
  return { text, ts, source: 'claude-code', sessionKey, sessionTitle: null };
}

async function parseClaudeFileStreamed(full, stats) {
  const turns = [];
  const sessionKey = `claude-code:${path.basename(full, '.jsonl')}`;
  await streamJsonlEntries(
    full,
    (entry) => {
      const t = claudeTurnFromEntry(entry, sessionKey);
      if (t) turns.push(t);
    },
    stats
  );
  return turns;
}

function codexTurnFromEntry(entry, sessionKey) {
  const r = codexRoleText(entry);
  if (!r) return null;
  const text = capTurnText(r.text);
  if (!text || looksLikeCommand(text) || SYSTEM_NOISE_RE.test(text) || text.length < 12) {
    return null;
  }
  let ts = Date.now();
  if (entry.timestamp) {
    const p = new Date(entry.timestamp).getTime();
    if (!Number.isNaN(p)) ts = p;
  }
  return { text, ts, source: 'codex', sessionKey, sessionTitle: null };
}

async function parseCodexFileStreamed(full, stats) {
  const turns = [];
  const sessionKey = `codex:${path.basename(full, '.jsonl')}`;
  await streamJsonlEntries(
    full,
    (entry) => {
      const t = codexTurnFromEntry(entry, sessionKey);
      if (t) turns.push(t);
    },
    stats
  );
  return turns;
}

// --- Claude Code JSONL ---

function claudeUserText(entry) {
  if (entry.type !== 'user' || !entry.message) return '';
  const msg = entry.message;
  if (msg.role !== 'user') return '';
  const content = msg.content;
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .filter((b) => b && b.type === 'text' && typeof b.text === 'string')
      .map((b) => b.text)
      .join(' ');
  }
  return '';
}

function parseClaudeFile(full, stats) {
  const turns = [];
  let raw;
  try {
    raw = fs.readFileSync(full, 'utf8');
  } catch {
    stats.errors++;
    return turns;
  }
  const sessionKey = `claude-code:${path.basename(full, '.jsonl')}`;
  for (const line of raw.split('\n')) {
    const t = line.trim();
    if (!t) continue;
    let entry;
    try {
      entry = JSON.parse(t);
    } catch {
      continue;
    }
    if (entry.isSidechain) continue;
    const text = capTurnText(claudeUserText(entry) || '');
    if (!text) continue;
    if (looksLikeCommand(text)) continue;
    if (SYSTEM_NOISE_RE.test(text)) continue;
    if (text.length < 12) continue;
    let ts = Date.now();
    if (entry.timestamp) {
      const p = new Date(entry.timestamp).getTime();
      if (!Number.isNaN(p)) ts = p;
    }
    turns.push({ text, ts, source: 'claude-code', sessionKey, sessionTitle: null });
  }
  return turns;
}

// --- Codex rollout JSONL ---

function codexTextOfContent(content, kinds) {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content
    .filter((b) => b && kinds.includes(b.type) && typeof b.text === 'string')
    .map((b) => b.text)
    .join(' ');
}

function codexRoleText(entry) {
  if (!entry || typeof entry !== 'object') return null;
  if (entry.type === 'event_msg') {
    const p = entry.payload || {};
    if (p.type === 'user_message' && typeof p.message === 'string' && p.message.trim()) {
      return { role: 'user', text: p.message };
    }
    return null;
  }
  if (entry.type === 'response_item') {
    const p = entry.payload || {};
    if (p.type !== 'message') return null;
    if (p.role === 'user') {
      const t = codexTextOfContent(p.content, ['input_text', 'text']);
      return t.trim() ? { role: 'user', text: t } : null;
    }
  }
  return null;
}

function parseCodexFile(full, stats) {
  const turns = [];
  let raw;
  try {
    raw = fs.readFileSync(full, 'utf8');
  } catch {
    stats.errors++;
    return turns;
  }
  const sessionKey = `codex:${path.basename(full, '.jsonl')}`;
  for (const line of raw.split('\n')) {
    const t = line.trim();
    if (!t) continue;
    let entry;
    try {
      entry = JSON.parse(t);
    } catch {
      continue;
    }
    const r = codexRoleText(entry);
    if (!r) continue;
    const text = capTurnText(r.text);
    if (!text || looksLikeCommand(text) || SYSTEM_NOISE_RE.test(text)) continue;
    if (text.length < 12) continue;
    let ts = Date.now();
    if (entry.timestamp) {
      const p = new Date(entry.timestamp).getTime();
      if (!Number.isNaN(p)) ts = p;
    }
    turns.push({ text, ts, source: 'codex', sessionKey, sessionTitle: null });
  }
  return turns;
}

// --- Copilot / Cursor chatSessions (VSCode JSON schema) ---

function extractCopilotRequests(obj) {
  const out = [];
  const reqs = obj.requests;
  if (!Array.isArray(reqs)) return out;
  for (const r of reqs) {
    const text = r?.message?.text;
    if (typeof text !== 'string' || !text.trim()) continue;
    const ts = Number(r.timestamp) || Number(obj.creationDate) || Date.now();
    out.push({ text: capTurnText(text), ts });
  }
  return out;
}

function parseChatSessionFile(full, source, stats) {
  const turns = [];
  let sessionId = path.basename(full).replace(/\.(json|jsonl)$/, '');
  try {
    try {
      const st = fs.statSync(full);
      if (st.size > 8 * 1024 * 1024) {
        stats.truncatedFiles = (stats.truncatedFiles || 0) + 1;
        return turns;
      }
    } catch {
      /* ignore stat failure, try reading anyway */
    }
    const raw = fs.readFileSync(full, 'utf8');
    try {
      const obj = JSON.parse(raw);
      if (obj && Array.isArray(obj.requests)) {
        if (typeof obj.sessionId === 'string' && obj.sessionId) sessionId = obj.sessionId;
        const key = `${source}:${sessionId}`;
        for (const { text, ts } of extractCopilotRequests(obj)) {
          if (!text || looksLikeCommand(text) || text.length < 12) continue;
          if (SYSTEM_NOISE_RE.test(text)) continue;
          turns.push({ text, ts, source, sessionKey: key, sessionTitle: null });
        }
        return turns;
      }
    } catch {
      /* not whole-file JSON — try line-delimited below */
    }
    for (const line of raw.split('\n')) {
      const t = line.trim();
      if (!t) continue;
      let obj;
      try {
        obj = JSON.parse(t);
      } catch {
        continue;
      }
      const v = obj.v || obj;
      if (v && Array.isArray(v.requests) && v.requests.length) {
        const key = `${source}:${v.sessionId || sessionId}`;
        for (const { text, ts } of extractCopilotRequests(v)) {
          if (!text || looksLikeCommand(text) || text.length < 12) continue;
          turns.push({ text, ts, source, sessionKey: key, sessionTitle: null });
        }
      }
    }
  } catch {
    stats.errors++;
  }
  return turns;
}

// --- OpenCode SQLite ---

async function parseOpencodeDb(stats) {
  const turns = [];
  const dbPath = opencodeDbPath();
  if (!fs.existsSync(dbPath)) {
    stats.sources.opencode = { missing: true, dbPath };
    return turns;
  }
  let DatabaseSync;
  try {
    ({ DatabaseSync } = await import('node:sqlite'));
  } catch {
    stats.sources.opencode = { error: 'node:sqlite unavailable' };
    stats.errors++;
    return turns;
  }
  let db;
  try {
    db = new DatabaseSync(dbPath, { readOnly: true });
  } catch (e) {
    stats.sources.opencode = { error: `db open failed: ${e.message}` };
    stats.errors++;
    return turns;
  }
  try {
    const sessions = new Map();
    try {
      for (const row of db.prepare('SELECT id, title, slug FROM session').all()) {
        sessions.set(row.id, row);
      }
    } catch {
      /* older schema */
    }
    const sessOf = new Map();
    const roleOf = new Map();
    try {
      for (const m of db.prepare('SELECT id, session_id, data FROM message').all()) {
        sessOf.set(m.id, m.session_id);
        try {
          roleOf.set(m.id, JSON.parse(m.data)?.role || null);
        } catch {
          roleOf.set(m.id, null);
        }
      }
    } catch {
      /* ignore */
    }
    let rows = [];
    try {
      rows = db.prepare('SELECT message_id, time_created, data FROM part').all();
    } catch (e) {
      stats.sources.opencode = { error: `part query failed: ${e.message}` };
      return turns;
    }
    for (const p of rows) {
      const sessionId = sessOf.get(p.message_id);
      if (!sessionId) continue;
      if (roleOf.get(p.message_id) !== 'user') continue;
      let d;
      try {
        d = JSON.parse(p.data);
      } catch {
        continue;
      }
      if (!d || d.type !== 'text' || typeof d.text !== 'string') continue;
      const text = capTurnText(d.text);
      if (!text || text.length < 12) continue;
      if (looksLikeCommand(text) || SYSTEM_NOISE_RE.test(text)) continue;
      const sess = sessions.get(sessionId) || {};
      turns.push({
        text,
        ts: Number(p.time_created) || Date.now(),
        source: 'opencode',
        sessionKey: `opencode:${sessionId}`,
        sessionTitle:
          typeof sess.title === 'string' && sess.title.trim()
            ? sess.title.trim().slice(0, 120)
            : null,
      });
    }
    stats.sources.opencode = { sessions: sessions.size, userTurns: turns.length };
  } finally {
    try {
      db.close();
    } catch {
      /* ignore */
    }
  }
  return turns;
}

// ---------------------------------------------------------------------------
// Fact extraction
// ---------------------------------------------------------------------------

function extractFacts(turns, opts, stats) {
  const facts = [];
  for (const t of turns) {
    stats.turnsScanned++;
    const dateStr = toDateStr(t.ts);
    if (opts.since && dateStr < opts.since) {
      stats.filtered++;
      continue;
    }
    if (opts.until && dateStr > opts.until) {
      stats.filtered++;
      continue;
    }
    const cats = classifyTurn(t.text);
    if (cats.length === 0) {
      stats.noSignal++;
      continue;
    }
    if (looksLikePureCodeDump(t.text) && !cats.includes('decision')) {
      if (!(cats.includes('diet') || cats.includes('health'))) {
        stats.codeDumpsDropped++;
        continue;
      }
    }
    const importance = importanceOf(t.text, cats);
    if (importance < 1) {
      stats.belowThreshold++;
      continue;
    }
    const summary = makeSummary(t.text);
    const id = factId(dateStr, t.sessionKey, summary);
    facts.push({
      id,
      date: dateStr,
      ts: t.ts,
      time: toTimeStr(t.ts),
      source: t.source,
      sessionKey: t.sessionKey,
      sessionTitle: t.sessionTitle,
      categories: cats,
      importance,
      summary,
      text: cleanText(t.text).slice(0, MAX_TEXT_LEN),
    });
  }
  return facts;
}

// ---------------------------------------------------------------------------
// Facts cache (Tier 0, local only) — content-addressed, idempotent
// ---------------------------------------------------------------------------

function loadFactsCache(factsPath) {
  const byId = new Map();
  try {
    const raw = fs.readFileSync(factsPath, 'utf8');
    for (const line of raw.split('\n')) {
      const t = line.trim();
      if (!t) continue;
      try {
        const f = JSON.parse(t);
        if (f && f.id) byId.set(f.id, f);
      } catch {
        /* skip malformed */
      }
    }
  } catch {
    /* no cache yet */
  }
  return byId;
}

function appendFactsCache(factsPath, newFacts) {
  if (!newFacts.length) return 0;
  fs.mkdirSync(path.dirname(factsPath), { recursive: true });
  const lines = newFacts.map((f) => JSON.stringify(f)).join('\n') + '\n';
  fs.appendFileSync(factsPath, lines, 'utf8');
  return newFacts.length;
}

// ---------------------------------------------------------------------------
// Vault writers (Tier 1 + Tier 2) — AGENTS.md compliant
// ---------------------------------------------------------------------------

function yamlQuote(s) {
  return `"${String(s).replace(/"/g, "'").replace(/\n/g, ' ').trim()}"`;
}

function frontmatter(title, tags, created, updated, summary) {
  return (
    '---\n' +
    `title: ${yamlQuote(title)}\n` +
    `tags: [${tags.join(', ')}]\n` +
    `created: ${created}\n` +
    `updated: ${updated}\n` +
    `summary: ${yamlQuote(summary)}\n` +
    'privacy: private\n' +
    'filed_by: agent\n' +
    '---\n\n'
  );
}

// Where each output lives in the vault (routing table in the vault's AGENTS.md, 2026-10-09).
const DAILY_DIR = ['20_Areas', 'daily-logs'];
const ENTITY_DIRS = {
  'diet-and-nutrition': ['20_Areas', 'health'],
  'health-and-body': ['20_Areas', 'health'],
  'active-projects': ['20_Areas', 'career'],
  'personal-preferences': ['00_Inbox', 'entities'],
  'tier3-candidates': ['00_Inbox', 'entities'],
};
const BRAIN = 'C:\\Users\\Admin\\GitHub\\agent-config\\skills\\second-brain\\scripts\\brain.py';
const PYTHON = process.env.SB_PYTHON || 'C:\\Python314\\python.exe';

function shortSession(sessionKey) {
  const parts = String(sessionKey).split(':');
  const id = parts.length > 1 ? parts.slice(1).join(':') : parts[0];
  return id.slice(0, 8);
}

function bulletFor(f) {
  const src = `${f.source} ${shortSession(f.sessionKey)} ${f.time}`;
  return `- ${f.summary} *(${src})*`;
}

function writeDailyLogs(vault, factsByDate, stats, opts) {
  const dir = path.join(vault, ...DAILY_DIR);
  if (!opts.dryRun) fs.mkdirSync(dir, { recursive: true });
  const written = [];
  const dates = [...factsByDate.keys()].sort();
  for (const date of dates) {
    const list = factsByDate.get(date);
    const work = list.filter((f) => f.categories.includes('work'));
    const diet = list.filter((f) => f.categories.includes('diet'));
    const health = list.filter((f) => f.categories.includes('health'));
    const decisions = list.filter(
      (f) => f.categories.includes('decision') || f.importance >= 3
    );
    const sessions = [...new Set(list.map((f) => f.sessionKey))];

    const section = (title, items) => {
      const lines = [`## ${title}`, ''];
      const capped = items.slice(0, MAX_BULLETS_PER_SECTION);
      if (!capped.length) {
        lines.push('—', '');
      } else {
        for (const f of capped) lines.push(bulletFor(f));
        if (items.length > capped.length) {
          lines.push(
            `- … and ${items.length - capped.length} more turn(s) in the local archive (Tier 0).`
          );
        }
        lines.push('');
      }
      return lines;
    };

    const fm = frontmatter(
      `Daily Log — ${date}`,
      ['daily-log', 'timeline', 'life-stream'],
      date,
      date,
      `Activities, meals/diet, health, and engineering work recorded on ${date} — distilled from local AI session logs.`
    );
    const body = [
      `# Daily Log — ${date}`,
      '',
      ...section('Work & Projects', work),
      ...section('Diet & Nutrition', diet),
      ...section('Health & Lifestyle', health),
      ...section('Key Decisions', decisions),
      '## Source Sessions',
      '',
      ...(sessions.length ? sessions.map((s) => `- ${s}`) : ['—']),
      '',
      '_Distilled automatically from local session logs (Tier 0 stays on this machine). Promote what matters; ignore the rest._',
      '',
    ].join('\n');

    const file = path.join(dir, `${date}.md`);
    if (!opts.dryRun) fs.writeFileSync(file, fm + body, 'utf8');
    written.push({ date, file, bytes: Buffer.byteLength(fm + body, 'utf8') });
    stats.dailyLogs++;
  }
  // INDEX.md files are rebuilt by brain.py in updateInboxIndex().
  return written;
}

function writeEntities(vault, allFacts, stats, opts) {
  const groups = {
    'diet-and-nutrition': {
      title: 'Diet & Nutrition Chronicle',
      tags: ['diet', 'nutrition', 'health', 'entity'],
      summary:
        'Rolling timeline of meals, nutrition, fasting, and food mentions distilled from local AI session logs.',
      match: (f) => f.categories.includes('diet') && f.importance >= 2,
    },
    'health-and-body': {
      title: 'Health & Body Chronicle',
      tags: ['health', 'fitness', 'sleep', 'entity'],
      summary:
        'Rolling timeline of workouts, sleep, medical notes, supplements, and wellness mentions from local session logs.',
      match: (f) => f.categories.includes('health') && f.importance >= 2,
    },
    'active-projects': {
      title: 'Active Projects Status',
      tags: ['projects', 'work', 'status', 'entity'],
      summary:
        'Living snapshot of repos touched and engineering work recorded in local AI sessions, newest first.',
      match: (f) => f.categories.includes('work') && f.importance >= 1,
    },
    'personal-preferences': {
      title: 'Personal Preferences & Rules',
      tags: ['preferences', 'rules', 'decisions', 'entity'],
      summary:
        'Decisions, tooling preferences, and standing rules captured from local sessions — review before promoting.',
      match: (f) => f.categories.includes('decision') || f.importance >= 3,
    },
    'tier3-candidates': {
      title: 'Tier 3 Candidates (review before promoting)',
      tags: ['tier3', 'core-profile', 'review', 'entity'],
      summary:
        'High-importance standing-rule candidates for the core profile — human review required before promotion to 20_Areas.',
      match: (f) => f.importance >= 3,
    },
  };

  const written = [];
  for (const [slug, g] of Object.entries(groups)) {
    const facts = allFacts.filter(g.match).sort((a, b) => b.ts - a.ts).slice(0, 300);
    if (!facts.length) continue;
    const byDate = new Map();
    for (const f of facts) {
      if (!byDate.has(f.date)) byDate.set(f.date, []);
      byDate.get(f.date).push(f);
    }
    const dates = [...byDate.keys()].sort().reverse();
    const created = [...byDate.keys()].sort()[0];
    const updated = dates[0];
    const fm = frontmatter(g.title, g.tags, created, updated, g.summary);
    const lines = [`# ${g.title}`, ''];
    for (const d of dates) {
      lines.push(`## ${d}`, '');
      for (const f of byDate.get(d).slice(0, MAX_BULLETS_PER_SECTION)) {
        lines.push(`- **${f.time}** — ${f.summary} *(${f.source} ${shortSession(f.sessionKey)})*`);
      }
      lines.push('');
    }
    lines.push('_Auto-generated rollup. Newest first. Source transcripts stay local (Tier 0)._', '');
    const dir = path.join(vault, ...(ENTITY_DIRS[slug] || ['00_Inbox', 'entities']));
    if (!opts.dryRun) fs.mkdirSync(dir, { recursive: true });
    const file = path.join(dir, `${slug}.md`);
    if (!opts.dryRun) fs.writeFileSync(file, fm + lines.join('\n'), 'utf8');
    written.push({ slug, file, facts: facts.length });
    stats.entities++;
  }
  return written;
}

// Rebuild the INDEX.md of every folder the harvester writes to, with the second-brain skill (one format vault-wide).
function updateInboxIndex(vault, opts) {
  if (opts.dryRun) return;
  const folders = new Set([DAILY_DIR.join('/'), ...Object.values(ENTITY_DIRS).map((d) => d.join('/')), '00_Inbox']);
  for (const folder of folders) {
    try {
      execFileSync(PYTHON, [BRAIN, 'index', folder], { env: { ...process.env, SECOND_BRAIN: vault }, stdio: 'ignore' });
    } catch (e) {
      console.error(`index rebuild failed for ${folder}: ${e.message}`);
    }
  }
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

function parseArgs(argv) {
  const o = {
    dryRun: false,
    statsOnly: false,
    since: null,
    until: null,
    limit: 0,
    force: false,
    vault: defaultVault(),
    facts: defaultFacts(),
    state: defaultState(),
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--dry-run') o.dryRun = true;
    else if (a === '--stats-only') o.statsOnly = true;
    else if (a === '--force') o.force = true;
    else if (a === '--since') o.since = argv[++i];
    else if (a === '--until') o.until = argv[++i];
    else if (a === '--limit') o.limit = Number(argv[++i]) || 0;
    else if (a === '--vault') o.vault = argv[++i];
    else if (a === '--facts') o.facts = argv[++i];
    else if (a === '--state') o.state = argv[++i];
    else if (a === '--help' || a === '-h') {
      console.log(
        'Usage: node second_brain_harvester.mjs [--dry-run] [--stats-only] [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--limit N] [--vault PATH] [--facts PATH] [--state PATH] [--force]'
      );
      process.exit(0);
    }
  }
  return o;
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  const stats = {
    turnsScanned: 0,
    filtered: 0,
    noSignal: 0,
    codeDumpsDropped: 0,
    belowThreshold: 0,
    errors: 0,
    filesScanned: 0,
    filesSkippedHuge: 0,
    truncatedFiles: 0,
    byCategory: { diet: 0, health: 0, work: 0, decision: 0 },
    byImportance: { 1: 0, 2: 0, 3: 0 },
    bySource: {},
    dailyLogs: 0,
    entities: 0,
    sources: {},
  };

  const allTurns = [];

  // 1. Claude Code (streamed: huge transcripts contribute user turns too)
  try {
    const files = walkFiles(claudeProjectsDir(), ['.jsonl'], MAX_FILE_BYTES, true);
    stats.sources['claude-code'] = { files: files.length };
    let filesDone = 0;
    for (const f of files) {
      if (f.skipped) {
        stats.filesSkippedHuge++;
        continue;
      }
      if (opts.limit && filesDone >= opts.limit) break;
      const turns = await parseClaudeFileStreamed(f.full, stats);
      allTurns.push(...turns);
      stats.filesScanned++;
      filesDone++;
    }
  } catch (e) {
    stats.sources['claude-code'] = { error: e.message };
  }

  // 2. OpenCode
  try {
    const turns = await parseOpencodeDb(stats);
    allTurns.push(...turns);
  } catch (e) {
    stats.sources.opencode = { error: e.message };
  }

  // 3. Codex
  try {
    const files = [];
    const walkCodex = (dir) => {
      let entries;
      try {
        entries = fs.readdirSync(dir, { withFileTypes: true });
      } catch {
        return;
      }
      for (const e of entries) {
        const full = path.join(dir, e.name);
        if (e.isDirectory()) walkCodex(full);
        else if (e.isFile() && /^rollout-.*\.jsonl$/.test(e.name)) {
          files.push(full);
        }
      }
    };
    walkCodex(codexSessionsDir());
    stats.sources.codex = { files: files.length };
    for (const full of files) {
      allTurns.push(...(await parseCodexFileStreamed(full, stats)));
      stats.filesScanned++;
    }
  } catch (e) {
    stats.sources.codex = { error: e.message };
  }

  // 4+5. Copilot + Cursor
  for (const [name, dirFn] of [['copilot', copilotChatDir], ['cursor', cursorChatDir]]) {
    try {
      const files = walkFiles(dirFn(), ['.json', '.jsonl'], MAX_FILE_BYTES, true);
      stats.sources[name] = { files: files.length };
      for (const f of files) {
        if (f.skipped) {
          stats.filesSkippedHuge++;
          continue;
        }
        allTurns.push(...parseChatSessionFile(f.full, name, stats));
        stats.filesScanned++;
      }
    } catch (e) {
      stats.sources[name] = { error: e.message };
    }
  }

  const freshFacts = extractFacts(allTurns, opts, stats);
  for (const f of freshFacts) {
    for (const c of f.categories) stats.byCategory[c]++;
    stats.byImportance[f.importance] = (stats.byImportance[f.importance] || 0) + 1;
    stats.bySource[f.source] = (stats.bySource[f.source] || 0) + 1;
  }

  if (opts.statsOnly || opts.dryRun) {
    const byDate = new Map();
    for (const f of freshFacts) {
      if (!byDate.has(f.date)) byDate.set(f.date, []);
      byDate.get(f.date).push(f);
    }
    const dates = [...byDate.keys()].sort();
    const sample = freshFacts
      .filter((f) => f.categories.includes('diet') || f.categories.includes('health'))
      .slice(0, 10)
      .map((f) => `[${f.date} ${f.source}] (${f.categories.join('+')} i${f.importance}) ${f.summary.slice(0, 140)}`);
    console.log(
      JSON.stringify(
        {
          dryRun: true,
          turnsScanned: stats.turnsScanned,
          factsExtracted: freshFacts.length,
          filtered: stats.filtered,
          noSignal: stats.noSignal,
          codeDumpsDropped: stats.codeDumpsDropped,
          byCategory: stats.byCategory,
          byImportance: stats.byImportance,
          bySource: stats.bySource,
          filesScanned: stats.filesScanned,
          filesSkippedHuge: stats.filesSkippedHuge,
          truncatedFiles: stats.truncatedFiles,
          errors: stats.errors,
          dateRange: dates.length ? [dates[0], dates[dates.length - 1]] : [],
          activeDays: dates.length,
          dietHealthSample: sample,
          sources: stats.sources,
        },
        null,
        2
      )
    );
    return;
  }

  // Merge into facts cache (idempotent by fact id).
  const cache = loadFactsCache(opts.facts);
  const newOnes = freshFacts.filter((f) => !cache.has(f.id));
  appendFactsCache(opts.facts, newOnes);
  for (const f of newOnes) cache.set(f.id, f);
  const allFacts = [...cache.values()].sort((a, b) => a.ts - b.ts);

  const viewFacts = allFacts.filter(
    (f) => (!opts.since || f.date >= opts.since) && (!opts.until || f.date <= opts.until)
  );
  const factsByDate = new Map();
  for (const f of viewFacts) {
    if (!factsByDate.has(f.date)) factsByDate.set(f.date, []);
    factsByDate.get(f.date).push(f);
  }

  const dailyWritten = writeDailyLogs(opts.vault, factsByDate, stats, opts);
  const entitiesWritten = writeEntities(opts.vault, viewFacts, stats, opts);
  updateInboxIndex(opts.vault, opts);

  try {
    fs.mkdirSync(path.dirname(opts.state), { recursive: true });
    fs.writeFileSync(
      opts.state,
      JSON.stringify(
        {
          lastRun: new Date(Date.now() + 19800000).toISOString().replace(/\.\d+Z$/, "+05:30"),   // IST only
          turnsScanned: stats.turnsScanned,
          freshFacts: freshFacts.length,
          newFacts: newOnes.length,
          totalFacts: allFacts.length,
          dailyLogs: dailyWritten.length,
        },
        null,
        2
      ),
      'utf8'
    );
  } catch {
    /* non-fatal */
  }

  console.log(
    JSON.stringify(
      {
        turnsScanned: stats.turnsScanned,
        freshFacts: freshFacts.length,
        newFacts: newOnes.length,
        totalFacts: allFacts.length,
        byCategory: stats.byCategory,
        byImportance: stats.byImportance,
        bySource: stats.bySource,
        filesScanned: stats.filesScanned,
        filesSkippedHuge: stats.filesSkippedHuge,
        truncatedFiles: stats.truncatedFiles,
        errors: stats.errors,
        dailyLogs: dailyWritten.map((d) => ({ date: d.date, bytes: d.bytes })),
        entities: entitiesWritten,
        vault: opts.vault,
        factsCache: opts.facts,
      },
      null,
      2
    )
  );
}

const entryArg = process.argv[1] ? path.resolve(process.argv[1]) : '';
const selfPath = new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
if (path.resolve(selfPath) === entryArg || entryArg.endsWith('second_brain_harvester.mjs')) {
  main().catch((e) => {
    console.log(JSON.stringify({ error: e.message }));
    process.exitCode = 1;
  });
}
