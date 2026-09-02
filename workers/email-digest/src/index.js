const REDDIT_SENDER = "noreply@redditmail.com";
const HOST = "https://www.reddit.com";
const KEEP_DAYS = 10;
const ENTRY_LIMIT = 60;

export default {
  async email(message, env, ctx) {
    const sender = String(message.from || "").toLowerCase();
    if (!sender.includes(REDDIT_SENDER)) {
      console.log(`skip: non-digest sender ${sender}`);
      return;
    }
    const entries = extractEntries(message.raw);
    if (entries.length === 0) {
      console.log("digest parsed zero entries");
      return;
    }
    const now = new Date();
    const dateStr = now.toISOString().slice(0, 10);
    const key = `digest-${dateStr}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const payload = {
      date: now.toISOString(),
      sender: sender,
      entries: entries.slice(0, ENTRY_LIMIT),
    };
    await env.REDDIT_DIGEST.put(key, JSON.stringify(payload));
    await trimOld(env, now);
    console.log(`stored ${entries.length} digest entries -> ${key}`);
  },

  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname === "/latest") {
      const keys = await env.REDDIT_DIGEST.list({ prefix: "digest-" });
      const digests = [];
      for (const { name } of keys.keys) {
        const raw = await env.REDDIT_DIGEST.get(name);
        if (!raw) continue;
        try {
          digests.push(JSON.parse(raw));
        } catch (e) {
          console.log(`bad kv entry ${name}: ${e.message}`);
        }
      }
      return json({ digests });
    }
    return json({ ok: true, routes: ["/latest"] });
  },
};

async function trimOld(env, now) {
  const cutoff = new Date(now);
  cutoff.setDate(cutoff.getDate() - KEEP_DAYS);
  const cutoffStr = cutoff.toISOString().slice(0, 10);
  const keys = await env.REDDIT_DIGEST.list({ prefix: "digest-" });
  for (const { name } of keys.keys) {
    const dateStr = name.slice(7, 17); // digest-YYYY-MM-DD-...
    if (dateStr < cutoffStr) {
      await env.REDDIT_DIGEST.delete(name);
      console.log(`trimmed old digest ${name}`);
    }
  }
}

function json(obj) {
  return new Response(JSON.stringify(obj), {
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

// Parse Reddit digest MIME (HTML body). Returns [{title,url,subreddit,snippet}].
function extractEntries(raw) {
  const html = findHtmlPart(raw);
  const links = [];
  const re =
    /<a[^>]*href=(["'])(https:\/\/www\.reddit\.com\/r\/[^"']+)\1[^>]*>(.*?)<\/a>/gi;
  let m;
  while ((m = re.exec(html)) !== null) {
    const href = decodeHref(m[2]);
    const anchor = decodeHtml(stripTags(m[3])).trim();
    if (href.includes("/comments/") && anchor.length >= 3) {
      links.push({ url: href, anchor });
    }
  }
  // Pair each thread link with a short snippet from trailing HTML text up to
  // the next <a>. This captures the digest's excerpt of the conversation.
  const entries = [];
  for (let i = 0; i < links.length; i++) {
    const { url, anchor } = links[i];
    const subreddit = extractSubreddit(url);
    const h = encodedAnchor(links[i], html, links[i + 1]);
    const snippet = decodeHtml(stripTags(h)).replace(/\s+/g, " ").trim().slice(0, 200);
    entries.push({
      title: anchor,
      url,
      subreddit,
      snippet,
    });
  }
  // dedupe by url
  const seen = new Set();
  return entries.filter((e) => {
    if (seen.has(e.url)) return false;
    seen.add(e.url);
    return true;
  });
}

function encodedAnchor(link, html, nextLink) {
  const start = html.indexOf(link.url);
  if (start === -1) return "";
  const end = nextLink ? html.indexOf("http", start + 5) : start + 3000;
  const chunk = html.slice(start, end === -1 ? start + 3000 : end);
  return chunk;
}

function findHtmlPart(raw) {
  const boundary = extractBoundary(raw);
  if (boundary) {
    const parts = raw.split(`--${boundary}`);
    for (const part of parts) {
      if (part.toLowerCase().includes("text/html")) return part;
    }
    for (const part of parts) {
      if (part.toLowerCase().includes("text/plain")) return part;
    }
  }
  // single-part or fallback: strip headers
  const idx = raw.indexOf("\r\n\r\n");
  return idx === -1 ? raw : raw.slice(idx + 4);
}

function extractBoundary(raw) {
  const m = /boundary=(?:"([^"]+)"|([^;\s]+))/i.exec(raw);
  return m ? (m[1] || m[2]) : null;
}

function extractSubreddit(url) {
  const m = /reddit\.com\/r\/([^/]+)/.exec(url);
  return m ? m[1] : "";
}

function stripTags(text) {
  return text.replace(/<[^>]+>/g, "");
}

function decodeHtml(text) {
  return text
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&nbsp;/g, " ");
}

function decodeHref(href) {
  return decodeHtml(href);
}