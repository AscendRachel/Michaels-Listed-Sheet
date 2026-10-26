import { ago, setServerOffset } from "../web/static/js/util.js";
const eq = (a, b, m) => { if (a !== b) { console.log("FAIL", m, JSON.stringify(a), "expected", JSON.stringify(b)); process.exitCode = 1; } };
// The server (New York, UTC-4 in October) stamped 10:00:00; it is now 14:30 UTC. The browser's own zone is set by the caller.
const now = Date.UTC(2026, 9, 26, 14, 30, 0);
Date.now = () => now;
setServerOffset(-240);
eq(ago("2026-10-26 10:00:00"), "30 min ago", "a server stamp from half an hour ago");
eq(ago("2026-10-25 10:00:00"), "25 h ago", "a day ago");
eq(ago("2026-10-26 10:25:00"), "5 min ago", "five minutes ago");
eq(ago("2026-10-26 10:29:30"), "just now", "half a minute ago");
eq(ago("2026-10-20 10:00:00"), "6 days ago", "last week");
setServerOffset(480);                                        // a server in Manila: 22:00 local = 14:00 UTC
eq(ago("2026-10-26 22:00:00"), "30 min ago", "a Manila-time server");
setServerOffset(null);                                       // unknown offset: the old behaviour (read in the browser's own zone) is kept
eq(typeof ago("2026-10-26 10:00:00"), "string", "no offset known");
eq(ago(""), "—", "empty");
