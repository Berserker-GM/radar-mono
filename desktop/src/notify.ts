import { isPermissionGranted, requestPermission, sendNotification } from "@tauri-apps/plugin-notification";
import { SECTIONS, formatDiff, formatDuration, type FeedItem, type ParsedFeed } from "./feed.ts";
import { store } from "./settings.ts";

const SEEN_KEY = "importantSeen";
const REMINDERS_KEY = "deadlineReminders";
const LISTED = 3;
/** Escalating nudges for approaching deadlines, fired once per level. */
const REMINDER_LEVELS: readonly { key: string; withinMs: number }[] = [
  { key: "24h", withinMs: 24 * 3_600_000 },
  { key: "3h", withinMs: 3 * 3_600_000 },
  { key: "1h", withinMs: 1 * 3_600_000 },
];

/** The important-unseen ids from the previous check, tied to the server they came from. */
interface SeenRecord {
  apiUrl: string;
  ids: number[];
}

/** Thresholds already fired per event, tied to the server they came from. */
interface RemindersRecord {
  apiUrl: string;
  sent: Record<string, string[]>;
}

let permission: boolean | null = null;
let queue: Promise<void> = Promise.resolve();

export function importantItems(feed: ParsedFeed): FeedItem[] {
  const byId = new Map<number, FeedItem>();
  for (const section of SECTIONS) {
    for (const item of feed.sections[section.id]) {
      if (item.important_unseen && !byId.has(item.event_id)) byId.set(item.event_id, item);
    }
  }
  return [...byId.values()];
}

/**
 * Notifies about items that are important_unseen now but weren't at the previous
 * check, then records the current set. With `silent`, records without notifying.
 * Never throws.
 */
export function checkImportant(feed: ParsedFeed, apiUrl: string, silent: boolean): Promise<void> {
  queue = queue.then(() => runCheck(feed, apiUrl, silent)).catch(() => {});
  return queue;
}

async function runCheck(feed: ParsedFeed, apiUrl: string, silent: boolean): Promise<void> {
  const current = importantItems(feed);
  const previous = await readSeen(apiUrl);
  await writeSeen({ apiUrl, ids: current.map((item) => item.event_id) });
  // The first check for a server only records a baseline, so what's already there doesn't notify.
  if (previous === null) return;
  const fresh = current.filter((item) => !previous.has(item.event_id));
  if (fresh.length === 0 || silent) return;
  if (!(await allowed())) return;
  try {
    sendNotification(notificationFor(fresh));
  } catch {
    // Notifications are best effort.
  }
}

function notificationFor(items: FeedItem[]): { title: string; body: string } {
  if (items.length === 1) {
    const [item] = items;
    const diff = formatDiff(item.diff);
    return { title: "Radar: important update", body: diff ? `${item.title}\n${diff}` : item.title };
  }
  const lines = items.slice(0, LISTED).map((item) => item.title);
  if (items.length > LISTED) lines.push(`and ${items.length - LISTED} more`);
  return { title: `Radar: ${items.length} important updates`, body: lines.join("\n") };
}

async function allowed(): Promise<boolean> {
  if (permission !== null) return permission;
  try {
    let granted = await isPermissionGranted();
    if (!granted) granted = (await requestPermission()) === "granted";
    permission = granted;
  } catch {
    permission = false;
  }
  return permission;
}

/** Unique items with a future key date, collected across every section. */
export function deadlineItems(feed: ParsedFeed): FeedItem[] {
  const now = Date.now();
  const byId = new Map<number, FeedItem>();
  for (const section of SECTIONS) {
    for (const item of feed.sections[section.id]) {
      const at = item.key_at === null ? Number.NaN : Date.parse(item.key_at);
      if (Number.isNaN(at) || at <= now) continue;
      if (!byId.has(item.event_id)) byId.set(item.event_id, item);
    }
  }
  return [...byId.values()];
}

/**
 * Fires one notification per threshold as a deadline approaches (24h, 3h, 1h),
 * recording each level so it never repeats. Never throws.
 */
export function checkDeadlineReminders(feed: ParsedFeed, apiUrl: string): Promise<void> {
  queue = queue.then(() => runReminders(feed, apiUrl)).catch(() => {});
  return queue;
}

async function runReminders(feed: ParsedFeed, apiUrl: string): Promise<void> {
  const items = deadlineItems(feed);
  const now = Date.now();
  const previous = await readReminders(apiUrl);
  // Keep only entries the current feed still cares about, then add new levels.
  const sent: Record<string, string[]> = {};
  const due: { item: FeedItem; level: (typeof REMINDER_LEVELS)[number] }[] = [];
  for (const item of items) {
    const key = String(item.event_id);
    const at = item.key_at === null ? Number.NaN : Date.parse(item.key_at);
    const stored = previous.sent[key];
    const done = Array.isArray(stored) ? stored.filter((level): level is string => typeof level === "string") : [];
    const levels = REMINDER_LEVELS.filter(
      (level) => !done.includes(level.key) && at - now <= level.withinMs,
    );
    if (levels.length > 0) {
      sent[key] = [...done, ...levels.map((level) => level.key)];
      for (const level of levels) due.push({ item, level });
    } else if (done.length > 0) {
      sent[key] = done;
    }
  }
  if (due.length === 0) return;
  await writeReminders({ apiUrl, sent });
  if (!(await allowed())) return;
  try {
    sendNotification(reminderFor(due));
  } catch {
    // Notifications are best effort.
  }
}

function reminderFor(due: { item: FeedItem; level: (typeof REMINDER_LEVELS)[number] }[]): {
  title: string;
  body: string;
} {
  // One line per item even when several thresholds trip in the same check.
  const seen = new Set<number>();
  const items: FeedItem[] = [];
  for (const { item } of due) {
    if (seen.has(item.event_id)) continue;
    seen.add(item.event_id);
    items.push(item);
  }
  const lines = items.slice(0, LISTED).map((item) => {
    const at = item.key_at === null ? 0 : Date.parse(item.key_at);
    const left = formatDuration(at - Date.now());
    return `${item.title} — in ${left}`;
  });
  if (items.length > LISTED) lines.push(`and ${items.length - LISTED} more`);
  return { title: "Radar: deadline approaching", body: lines.join("\n") };
}

async function readReminders(apiUrl: string): Promise<RemindersRecord> {
  try {
    const record = await store.get<RemindersRecord>(REMINDERS_KEY);
    if (!record || record.apiUrl !== apiUrl || typeof record.sent !== "object" || record.sent === null) {
      return { apiUrl, sent: {} };
    }
    return record;
  } catch {
    return { apiUrl, sent: {} };
  }
}

async function writeReminders(record: RemindersRecord): Promise<void> {
  try {
    await store.set(REMINDERS_KEY, record);
    await store.save();
  } catch {
    // Worst case is a repeat reminder after a restart.
  }
}

/** Resolves to null when there is no usable record for this server yet. */
async function readSeen(apiUrl: string): Promise<Set<number> | null> {
  try {
    const record = await store.get<SeenRecord>(SEEN_KEY);
    if (!record || record.apiUrl !== apiUrl || !Array.isArray(record.ids)) return null;
    return new Set(record.ids.filter((id): id is number => typeof id === "number"));
  } catch {
    return null;
  }
}

async function writeSeen(record: SeenRecord): Promise<void> {
  try {
    await store.set(SEEN_KEY, record);
    await store.save();
  } catch {
    // Worst case is a repeat notification after a restart.
  }
}
