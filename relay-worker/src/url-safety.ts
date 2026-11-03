// Port of src/sound_vault/url_safety.py :: is_safe_public_url (SSRF defense).
//
// A submitted link travels relay -> paired desktop, where it is fetched. Reject
// non-web schemes, over-long URLs, and hosts that are literal private/reserved IPs
// or known internal hostnames. No DNS lookup (the desktop does that at fetch time).

export const MAX_URL_LENGTH = 2048;

const BLOCKED_HOSTNAMES = new Set([
  "localhost",
  "ip6-localhost",
  "metadata",
  "metadata.google.internal",
  "metadata.google.com",
  "instance-data",
  "instance-data.ec2.internal",
]);

// Python ipaddress: is_private | is_loopback | is_link_local | is_reserved |
// is_multicast | is_unspecified, flattened into CIDR lists.
const BLOCKED_V4: Array<[string, number]> = [
  ["0.0.0.0", 8],
  ["10.0.0.0", 8],
  ["127.0.0.0", 8],
  ["169.254.0.0", 16],
  ["172.16.0.0", 12],
  ["192.0.0.0", 29],
  ["192.0.0.170", 31],
  ["192.0.2.0", 24],
  ["192.168.0.0", 16],
  ["198.18.0.0", 15],
  ["198.51.100.0", 24],
  ["203.0.113.0", 24],
  ["224.0.0.0", 4], // multicast
  ["240.0.0.0", 4], // reserved
  ["255.255.255.255", 32],
];

const BLOCKED_V6: Array<[string, number]> = [
  ["::", 128], // unspecified
  ["::1", 128], // loopback
  ["::", 8], // reserved (IETF) — also covers ::ffff:0:0/96 and ::/128
  ["64:ff9b:1::", 48],
  ["100::", 8], // reserved (covers discard-only 100::/64)
  ["200::", 7],
  ["400::", 6],
  ["800::", 5],
  ["1000::", 4],
  ["2001::", 23],
  ["2001:db8::", 32],
  ["4000::", 3],
  ["6000::", 3],
  ["8000::", 3],
  ["a000::", 3],
  ["c000::", 3],
  ["e000::", 4],
  ["f000::", 5],
  ["f800::", 6],
  ["fc00::", 7], // unique local
  ["fe00::", 9],
  ["fe80::", 10], // link-local
  ["fec0::", 10], // site-local (deprecated)
  ["ff00::", 8], // multicast
];

function parseV4(host: string): number | null {
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(host);
  if (!m) return null;
  let value = 0;
  for (let i = 1; i <= 4; i++) {
    const octet = Number(m[i]);
    if (octet > 255) return null;
    value = value * 256 + octet;
  }
  return value;
}

function parseV6(host: string): bigint | null {
  if (!host.includes(":")) return null;
  let text = host;
  // Embedded dotted IPv4 tail (WHATWG URL serialises without it, but be safe).
  const v4tail = /(\d+\.\d+\.\d+\.\d+)$/.exec(text);
  if (v4tail) {
    const v4 = parseV4(v4tail[1]);
    if (v4 === null) return null;
    text = text.slice(0, -v4tail[1].length) + ((v4 >>> 16) & 0xffff).toString(16) + ":" + (v4 & 0xffff).toString(16);
  }
  const halves = text.split("::");
  if (halves.length > 2) return null;
  const head = halves[0] ? halves[0].split(":") : [];
  const tail = halves.length === 2 && halves[1] ? halves[1].split(":") : [];
  const missing = 8 - head.length - tail.length;
  if (halves.length === 1 ? missing !== 0 : missing < 1) return null;
  const groups = [...head, ...Array(halves.length === 2 ? missing : 0).fill("0"), ...tail];
  let value = 0n;
  for (const g of groups) {
    if (!/^[0-9a-f]{1,4}$/i.test(g)) return null;
    value = (value << 16n) | BigInt(parseInt(g, 16));
  }
  return value;
}

function inV4(ip: number, [base, bits]: [string, number]): boolean {
  const b = parseV4(base)!;
  if (bits === 0) return true;
  const mask = bits === 32 ? 0xffffffff : (~((1 << (32 - bits)) - 1)) >>> 0;
  return ((ip & mask) >>> 0) === ((b & mask) >>> 0);
}

function inV6(ip: bigint, [base, bits]: [string, number]): boolean {
  const b = parseV6(base)!;
  const shift = BigInt(128 - bits);
  return ip >> shift === b >> shift;
}

export function ipIsBlocked(host: string): boolean {
  const v4 = parseV4(host);
  if (v4 !== null) return BLOCKED_V4.some((net) => inV4(v4, net));
  const v6 = parseV6(host);
  if (v6 !== null) {
    // IPv4-mapped (::ffff:a.b.c.d): judge by the embedded IPv4 address too.
    if (v6 >> 32n === 0xffffn && BLOCKED_V4.some((net) => inV4(Number(v6 & 0xffffffffn), net))) return true;
    return BLOCKED_V6.some((net) => inV6(v6, net));
  }
  return false; // not an IP literal
}

function hostIsBlocked(rawHost: string): boolean {
  let host = rawHost.toLowerCase();
  if (host.startsWith("[") && host.endsWith("]")) host = host.slice(1, -1);
  host = host.replace(/^\.+|\.+$/g, "");
  if (!host) return true;
  if (BLOCKED_HOSTNAMES.has(host) || host.endsWith(".localhost")) return true;
  return ipIsBlocked(host);
}

export function isSafePublicUrl(url: string): boolean {
  if (!url || url.length > MAX_URL_LENGTH) return false;
  let parsed: URL;
  try {
    parsed = new URL(url.trim());
  } catch {
    return false;
  }
  const scheme = parsed.protocol.replace(/:$/, "").toLowerCase();
  if (scheme !== "http" && scheme !== "https") return false;
  if (!parsed.hostname) return false;
  return !hostIsBlocked(parsed.hostname);
}
