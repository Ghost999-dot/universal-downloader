// ==UserScript==
// @name         Universal Downloader — Smart Grabber + Twitter
// @namespace    local.universal.downloader
// @author       ELO (Ghost999-dot)
// @icon         data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCA2NCA2NCIgd2lkdGg9IjY0IiBoZWlnaHQ9IjY0Ij4KICA8ZGVmcz4KICAgIDxsaW5lYXJHcmFkaWVudCBpZD0iZyIgeDE9IjAiIHkxPSIwIiB4Mj0iMSIgeTI9IjEiPgogICAgICA8c3RvcCBvZmZzZXQ9IjAiIHN0b3AtY29sb3I9IiNiMDZiZmYiLz4KICAgICAgPHN0b3Agb2Zmc2V0PSIxIiBzdG9wLWNvbG9yPSIjNmQyOGQ5Ii8+CiAgICA8L2xpbmVhckdyYWRpZW50PgogIDwvZGVmcz4KICA8cmVjdCB4PSIyIiB5PSIyIiB3aWR0aD0iNjAiIGhlaWdodD0iNjAiIHJ4PSIxNiIgZmlsbD0idXJsKCNnKSIvPgogIDwhLS0gb3JiaXQgcmluZzogdGhlICJ1bml2ZXJzYWwiIG5vZCAtLT4KICA8Y2lyY2xlIGN4PSIzMiIgY3k9IjI5IiByPSIxNyIgZmlsbD0ibm9uZSIgc3Ryb2tlPSIjZmZmZmZmIiBzdHJva2Utb3BhY2l0eT0iMC4yMCIgc3Ryb2tlLXdpZHRoPSIzIi8+CiAgPCEtLSBkb3dubG9hZCBhcnJvdyAtLT4KICA8cGF0aCBkPSJNMzIgMTQgVjMzIiBzdHJva2U9IiNmZmZmZmYiIHN0cm9rZS13aWR0aD0iNSIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIi8+CiAgPHBhdGggZD0iTTIyIDI1IGwxMCAxMCBsMTAgLTEwIiBmaWxsPSJub25lIiBzdHJva2U9IiNmZmZmZmYiIHN0cm9rZS13aWR0aD0iNSIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIi8+CiAgPCEtLSB0cmF5IC8gaW5ib3ggLS0+CiAgPHBhdGggZD0iTTE3IDQxIHY0IGE1IDUgMCAwIDAgNSA1IGgyMCBhNSA1IDAgMCAwIDUgLTUgdi00IiBmaWxsPSJub25lIiBzdHJva2U9IiNmZmZmZmYiIHN0cm9rZS13aWR0aD0iNSIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIi8+Cjwvc3ZnPgo=
// @version      1.6.1
// @description  Grabber sites AND X/Twitter → your local Universal Downloader app. On X, media is captured passively from X's own traffic and the resolved URLs are sent to the app. Plus absolute timestamps and a simplify (narrow-feed) mode on X.
// @match        *://*/*
// @updateURL    http://127.0.0.1:9898/userscript.user.js
// @downloadURL  http://127.0.0.1:9898/userscript.user.js
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_deleteValue
// @grant        GM_addStyle
// @grant        GM_registerMenuCommand
// @connect      127.0.0.1
// @connect      localhost
// @run-at       document-idle
// @noframes
// ==/UserScript==

(function () {
  "use strict";

  // ═══════════════════════════ Shared config ═══════════════════════════
  const PORT    = 9898;
  const API     = "http://127.0.0.1:" + PORT + "/api/download";
  const QUALITY = "best";                                         // best | 1080 | 720 | 480 | 360
  const HOTKEY  = { alt: true, shift: true, ctrl: false, code: "KeyD" };  // Alt+Shift+D toggles a site

  if (location.port === String(PORT)) return;                     // never run on the app's own page

  const host = location.hostname.replace(/^www\./, "").toLowerCase();
  const IS_TWITTER = /(^|\.)x\.com$/.test(host) || /(^|\.)twitter\.com$/.test(host) || host.indexOf("tweetdeck") >= 0;

  // ═══════════════════════════ Shared: toast ═══════════════════════════
  function toast(msg, color) {
    const t = document.createElement("div");
    t.textContent = msg;
    Object.assign(t.style, {
      position: "fixed", right: "18px", bottom: "78px", zIndex: 2147483647,
      background: "#131313", color: color || "#e0e0e0",
      border: "1px solid #2a2a2a", borderLeft: "3px solid " + (color || "#a855f7"),
      padding: "10px 14px", borderRadius: "8px",
      font: "13px 'Segoe UI', system-ui, sans-serif",
      boxShadow: "0 6px 24px rgba(0,0,0,.5)", maxWidth: "330px",
      pointerEvents: "none", opacity: "0",
      transition: "opacity .2s, transform .2s", transform: "translateY(8px)"
    });
    (document.body || document.documentElement).appendChild(t);
    requestAnimationFrame(function () { t.style.opacity = "1"; t.style.transform = "translateY(0)"; });
    setTimeout(function () { t.style.opacity = "0"; setTimeout(function () { t.remove(); }, 250); }, 2600);
  }

  // ═══════════════════════ Shared: send to the app ═══════════════════════
  // onDone(ok) is optional; `quiet` suppresses this function's own toasts (callers that
  // fire many sends at once show a single aggregate toast instead).
  function send(url, fmt, audio, onDone, quiet) {
    if (!url) { toast("✕ Couldn't find a link there", "#ef4444"); if (onDone) onDone(false); return; }
    if (!quiet) toast("⬇ Sending…", "#a855f7");
    GM_xmlhttpRequest({
      method: "POST", url: API,
      headers: { "Content-Type": "application/json" },
      data: JSON.stringify({ url: url, format: fmt || "mp4", audio_only: !!audio, quality: QUALITY }),
      timeout: 9000,
      onload: function (r) {
        const ok = r.status >= 200 && r.status < 300;
        if (!quiet) toast(ok ? "✓ Sent to downloader" : "✕ App rejected it (" + r.status + ")", ok ? "#a855f7" : "#ef4444");
        if (onDone) onDone(ok);
      },
      onerror:   function () { if (!quiet) toast("✕ App not running — open Universal_Downloader.exe", "#ef4444"); if (onDone) onDone(false); },
      ontimeout: function () { if (!quiet) toast("✕ Server timed out", "#ef4444"); if (onDone) onDone(false); }
    });
  }

  // A menu shortcut available on every page: push the current URL to the app.
  try {
    GM_registerMenuCommand("⬇ Send THIS page to Universal Downloader", function () { send(location.href, "mp4", false); });
  } catch (e) { /* menu API unavailable */ }

  // ════════════════════════════════════════════════════════════════════
  //  TWITTER / X  — inject buttons, resolve media in-browser, route to app
  // ════════════════════════════════════════════════════════════════════
  const Twitter = (function () {
    const AUTH = "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA";
    const isTweetDeck = host.indexOf("tweetdeck") >= 0;
    let history = [];

    const CSS = `
      .tmd-down {margin-left: 12px; order: 99; position: relative;}
      .tmd-down:hover svg {color: #a855f7;}
      .tmd-down:hover div:first-child:not(:last-child) {background-color: rgba(168,85,247,0.1);}
      .tmd-down:active div:first-child:not(:last-child) {background-color: rgba(168,85,247,0.2);}
      .tmd-down.tmd-media {position: absolute; right: 0;}
      .tmd-down.tmd-media > div {display: flex; border-radius: 99px; margin: 2px;}
      .tmd-down.tmd-media > div > div {display: flex; margin: 6px; color: #fff;}
      .tmd-down.tmd-media:hover > div {background-color: rgba(255,255,255,0.6);}
      .tmd-down.tmd-media:hover > div > div {color: #a855f7;}
      .tmd-down.tmd-media:not(:hover) > div > div {filter: drop-shadow(0 0 1px #000);}
      .tmd-down g {display: none;}
      .tmd-down.download g.download, .tmd-down.completed g.completed, .tmd-down.exist g.completed, .tmd-down.loading g.loading, .tmd-down.failed g.failed {display: unset;}
      .tmd-down.exist svg {color: #a855f7;}
      .tmd-down.completed svg {color: #00ba7c;}
      .tmd-down.loading svg {animation: tmdspin 1s linear infinite; color: #a855f7;}
      @keyframes tmdspin {0% {transform: rotate(0deg);} 100% {transform: rotate(360deg);}}
      .tmd-down.tmd-img {position: absolute; right: 0; bottom: 0; display: none !important;}
      .tmd-down.tmd-img > div {display: flex; border-radius: 99px; margin: 2px; background-color: rgba(255,255,255,0.6);}
      .tmd-down.tmd-img > div > div {display: flex; margin: 6px; color: #fff !important;}
      .tmd-down.tmd-img:not(:hover) > div > div {filter: drop-shadow(0 0 1px #000);}
      .tmd-down.tmd-img:hover > div > div {color: #a855f7;}
      :hover > .tmd-down.tmd-img, .tmd-img.loading, .tmd-img.completed, .tmd-img.exist, .tmd-img.failed {display: block !important;}
      .tweet-detail-action-item {width: 20% !important;}
    `;

    const SVG = `
      <g class="download"><path d="M7 11l5 5 5-5M12 4v12" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/></g>
      <g class="completed"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/><polyline points="8 11 11 14 17 7" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></g>
      <g class="loading"><circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" stroke-width="2" opacity="0.3"/><path d="M12 2a10 10 0 0 1 10 10" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"/></g>
      <g class="failed"><circle cx="12" cy="12" r="11" fill="#f33" stroke="currentColor" stroke-width="2" opacity="0.8"/><path d="M14.5 7.5l-5 9M9.5 7.5l5 9" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round"/></g>
    `;

    function getCookie() {
      let c = {};
      document.cookie.split(";").filter(n => n.includes("=")).forEach(n => {
        n.replace(/^([^=]+)=(.+)$/, (_, k, v) => { c[k.trim()] = v.trim(); });
      });
      return c;
    }

    async function fetchJson(status_id) {
      const ck = getCookie();
      const variables = { tweetId: status_id, with_rux_injections: false, includePromotedContent: true, withCommunity: true, withQuickPromoteEligibilityTweetFields: true, withBirdwatchNotes: true, withVoice: true, withV2Timeline: true };
      const features = { "articles_preview_enabled": true, "c9s_tweet_anatomy_moderator_badge_enabled": true, "communities_web_enable_tweet_community_results_fetch": false, "creator_subscriptions_quote_tweet_preview_enabled": false, "creator_subscriptions_tweet_preview_api_enabled": false, "freedom_of_speech_not_reach_fetch_enabled": true, "graphql_is_translatable_rweb_tweet_is_translatable_enabled": true, "longform_notetweets_consumption_enabled": false, "longform_notetweets_inline_media_enabled": true, "longform_notetweets_rich_text_read_enabled": false, "premium_content_api_read_enabled": false, "profile_label_improvements_pcf_label_in_post_enabled": true, "responsive_web_edit_tweet_api_enabled": false, "responsive_web_enhance_cards_enabled": false, "responsive_web_graphql_exclude_directive_enabled": false, "responsive_web_graphql_skip_user_profile_image_extensions_enabled": false, "responsive_web_graphql_timeline_navigation_enabled": false, "responsive_web_grok_analysis_button_from_backend": false, "responsive_web_grok_analyze_button_fetch_trends_enabled": false, "responsive_web_grok_analyze_post_followups_enabled": false, "responsive_web_grok_image_annotation_enabled": false, "responsive_web_grok_share_attachment_enabled": false, "responsive_web_grok_show_grok_translated_post": false, "responsive_web_jetfuel_frame": false, "responsive_web_media_download_video_enabled": false, "responsive_web_twitter_article_tweet_consumption_enabled": true, "rweb_tipjar_consumption_enabled": true, "rweb_video_screen_enabled": false, "standardized_nudges_misinfo": true, "tweet_awards_web_tipping_enabled": false, "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": true, "tweetypie_unmention_optimization_enabled": false, "verified_phone_label_enabled": false, "view_counts_everywhere_api_enabled": true };
      const url = encodeURI(`https://${location.hostname}/i/api/graphql/2ICDjqPd81tulZcYrtpTuQ/TweetResultByRestId?variables=${JSON.stringify(variables)}&features=${JSON.stringify(features)}`);
      const headers = { "authorization": AUTH, "x-twitter-active-user": "yes", "x-twitter-client-language": ck.lang || "en" };
      if (ck.ct0) headers["x-csrf-token"] = ck.ct0;
      if (ck.gt)  headers["x-guest-token"] = ck.gt;
      const res = await fetch(url, { headers });
      if (!res.ok) throw new Error("API " + res.status);
      const j = await res.json();
      const r = j.data?.tweetResult?.result;
      return r?.tweet || r;
    }

    function setStatus(btn, css, title) {
      if (css) { btn.classList.remove("download", "completed", "exist", "loading", "failed"); btn.classList.add(css); }
      if (title) btn.title = title;
    }

    // ── Passive media capture from X's OWN API responses (XEnhancer trick) ──
    // Hook XHR, watch the JSON X already fetches, and remember each tweet's media.
    // A click then downloads from this cache (GraphQL fetch is only a fallback).
    const mediaMap = new Map();   // statusId -> { text, items:[{url,fmt}] }
    function findParent(obj, key, out) {
      out = out || [];
      if (Array.isArray(obj)) { for (const it of obj) findParent(it, key, out); }
      else if (obj && typeof obj === "object") {
        for (const k in obj) {
          if (!Object.prototype.hasOwnProperty.call(obj, k)) continue;
          if (k === key) out.push(obj);
          findParent(obj[k], key, out);
        }
      }
      return out;
    }
    function pickMedia(m) {
      if (m.type === "photo") return { url: (m.media_url_https || "") + ":orig", fmt: "jpg" };
      const mp4 = (m.video_info?.variants || []).filter(v => v.content_type === "video/mp4")
        .sort((a, b) => (b.bitrate || 0) - (a.bitrate || 0))[0];
      return mp4 ? { url: mp4.url, fmt: "mp4" } : null;
    }
    function extractMedia(text) {
      let data; try { data = JSON.parse(text); } catch (e) { return; }
      for (const ent of findParent(data, "extended_entities")) {
        if (!ent.extended_entities) continue;
        const id = ent.id_str || ent.conversation_id_str;
        if (!id) continue;
        const items = (ent.extended_entities.media || [])
          .filter(m => ["video", "animated_gif", "photo"].includes(m.type))
          .map(pickMedia).filter(Boolean);
        if (!items.length) continue;
        const t = ((ent.full_text || "").split("https://t.co")[0] || "").trim().slice(0, 50);
        mediaMap.set(String(id), { text: t, items });
      }
    }
    function hookXHR() {
      const O = XMLHttpRequest.prototype.open, S = XMLHttpRequest.prototype.send;
      XMLHttpRequest.prototype.open = function (m, u) { this._udUrl = u; return O.apply(this, arguments); };
      XMLHttpRequest.prototype.send = function () {
        this.addEventListener("load", function () {
          try { if (this._udUrl && (this.responseType === "" || this.responseType === "text") && this.responseText) extractMedia(this.responseText); } catch (e) {}
        });
        return S.apply(this, arguments);
      };
    }
    const getExt = u => { try { return new URL(u).pathname.split(".").pop() || null; } catch (e) { return null; } };
    const sanitize = s => String(s || "").replace(/[\/\\?%*:|"<>\r\n]/g, "_").trim();

    function mediaUrls(json, index) {
      let leg = json?.legacy;
      let medias = leg?.extended_entities?.media || [];
      if (!medias.length) {                                    // fall back to a quoted tweet's media
        const q = json?.quoted_status_result?.result?.legacy?.extended_entities?.media;
        if (q) medias = q;
      }
      if (index) { const m = medias[parseInt(index, 10) - 1]; medias = m ? [m] : []; }
      return medias.map(m => {
        if (m.type === "photo") return { url: m.media_url_https + ":orig", fmt: "jpg" };
        const mp4 = (m.video_info?.variants || []).filter(v => v.content_type === "video/mp4")
          .sort((a, b) => (b.bitrate || 0) - (a.bitrate || 0))[0];
        return mp4 ? { url: mp4.url, fmt: "mp4" } : null;
      }).filter(Boolean);
    }

    async function click(btn, status_id, index) {
      if (btn.classList.contains("loading")) return;
      setStatus(btn, "loading", "Preparing…");

      // 1) prefer media X already handed us (captured passively via the XHR hook)
      let items = [], name = "";
      const cap = mediaMap.get(String(status_id));
      if (cap && cap.items.length) { items = cap.items.slice(); name = cap.text; }

      // 2) fallback: ask the GraphQL API directly (uses your logged-in session)
      if (!items.length) {
        try {
          const json = await fetchJson(status_id);
          if (json && json.legacy) {
            items = mediaUrls(json, null);
            name = ((json.legacy.full_text || "").split("https://t.co")[0] || "").trim().slice(0, 50);
          }
        } catch (e) {}
      }

      if (index) { const m = items[parseInt(index, 10) - 1]; items = m ? [m] : []; }
      if (!items.length) { setStatus(btn, "failed", "No media captured yet — scroll the tweet into view and retry"); return; }

      // 3) hand the resolved URLs to the local app, so X media lands in downloads/<type>/
      toast("⬇ Sending " + items.length + " item" + (items.length > 1 ? "s" : "") + " to downloader…", "#a855f7");
      let left = items.length, failed = 0;
      items.forEach(it => send(it.url, it.fmt, false, function (ok) {
        if (!ok) failed++;
        if (--left === 0) {
          if (failed === items.length) { setStatus(btn, "failed", "Send failed — is the app running?"); toast("✕ Nothing sent — open Universal_Downloader.exe", "#ef4444"); }
          else {
            setStatus(btn, "completed", "Sent ✓");
            toast("✓ Sent " + (items.length - failed) + "/" + items.length + " to downloader", "#a855f7");
            if (history.indexOf(status_id) < 0) { history.push(status_id); GM_setValue("ud_tw_history", history); }
          }
        }
      }, true));
    }

    function addToArticle(article) {
      if (article.dataset.udDetected) return;
      article.dataset.udDetected = "true";

      const media = article.querySelector([
        'a[href*="/photo/1"]', 'div[role="progressbar"]', 'button[data-testid="playButton"]',
        'a[href="/settings/content_you_see"]', 'div.media-image-container', 'div.media-preview-container',
        'div[aria-labelledby]>div:first-child>div[role="button"][tabindex="0"]'
      ].join(","));

      if (media) {
        const link = article.querySelector('a[href*="/status/"]');
        if (!link) return;
        const status_id = link.href.split("/status/").pop().split("/").shift();
        const group = article.querySelector('div[role="group"]:last-of-type, ul.tweet-actions, ul.tweet-detail-actions');
        if (!group) return;
        const shareArr = Array.from(group.querySelectorAll(':scope>div>div, li.tweet-action-item>a, li.tweet-detail-action-item>a'));
        if (!shareArr.length) return;
        const btn_share = shareArr.pop().parentNode;
        const btn = btn_share.cloneNode(true);
        const b = btn.querySelector("button"); if (b) b.removeAttribute("disabled");
        if (isTweetDeck) {
          btn.firstElementChild.innerHTML = '<svg viewBox="0 0 24 24" style="width:18px;height:18px;">' + SVG + '</svg>';
          btn.firstElementChild.removeAttribute("rel");
          btn.classList.replace("pull-left", "pull-right");
        } else {
          const svg = btn.querySelector("svg"); if (svg) svg.innerHTML = SVG;
        }
        const exist = history.indexOf(status_id) >= 0;
        setStatus(btn, "tmd-down");
        setStatus(btn, exist ? "exist" : "download", exist ? "Already sent" : "Send to Universal Downloader");
        btn_share.parentNode.insertBefore(btn, btn_share.nextSibling);
        btn.onclick = () => click(btn, status_id, null);
      }

      const imgs = article.querySelectorAll('a[href*="/photo/"]');
      if (imgs.length > 1) {
        const link = article.querySelector('a[href*="/status/"]');
        if (!link) return;
        const status_id = link.href.split("/status/").pop().split("/").shift();
        imgs.forEach(img => {
          const index = img.href.split("/status/").pop().split("/").pop();
          const btn = document.createElement("div");
          btn.innerHTML = '<div><div><svg viewBox="0 0 24 24" style="width:18px;height:18px;">' + SVG + '</svg></div></div>';
          btn.classList.add("tmd-down", "tmd-img");
          setStatus(btn, "download", "Send this image");
          img.parentNode.appendChild(btn);
          btn.onclick = e => { e.preventDefault(); click(btn, status_id, index); };
        });
      }
    }

    function addToMediaList(items) {
      items.forEach(li => {
        if (li.dataset.udDetected === "true") return;
        const link = li.querySelector('a[href*="/status/"]');
        if (!link || !link.href) return;
        li.dataset.udDetected = "true";
        const status_id = link.href.split("/status/").pop().split(/[\/?#]/).shift();
        const exist = history.indexOf(status_id) >= 0;
        const btn = document.createElement("div");
        btn.innerHTML = '<div><div><svg viewBox="0 0 24 24" style="width:18px;height:18px;">' + SVG + '</svg></div></div>';
        btn.classList.add("tmd-down", "tmd-media");
        setStatus(btn, exist ? "exist" : "download", exist ? "Already sent" : "Send to Universal Downloader");
        li.appendChild(btn);
        btn.onclick = e => { e.preventDefault(); e.stopPropagation(); click(btn, status_id, null); };
      });
    }

    function detect(node) {
      const article = (node.tagName === "ARTICLE" && node) ||
        (node.tagName === "DIV" && (node.querySelector("article") || node.closest("article")));
      if (article) addToArticle(article);
      const items = (node.tagName === "LI" && node.getAttribute("role") === "listitem" && [node]) ||
        (node.tagName === "DIV" && node.querySelectorAll('li[role="listitem"]'));
      if (items && items.length) addToMediaList(items);
    }

    return {
      init: async function () {
        hookXHR();                       // start capturing X's media traffic immediately
        history = await GM_getValue("ud_tw_history", []);
        document.head.insertAdjacentHTML("beforeend", "<style>" + CSS + "</style>");
        new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(n => { if (n.nodeType === 1) detect(n); })))
          .observe(document.body, { childList: true, subtree: true });
        // sweep anything already on the page
        document.querySelectorAll("article").forEach(a => addToArticle(a));
        toast("⬇ X downloader active — sends media to the app", "#a855f7");
      }
    };
  })();

  // ════════════════════════════════════════════════════════════════════
  //  TWITTER EXTRAS — absolute timestamps + simplify (narrow-feed) mode
  //  Adapted from XEnhancer (PeterParker / Levivi, MIT). Downloading stays
  //  routed to the local app via the Twitter module above; this only adds
  //  the timestamp reformatter and the optional narrow layout.
  // ════════════════════════════════════════════════════════════════════
  const TwitterExtras = (function () {
    const L = { settings:"Time format settings", titleDateFormat:"Time format settings:", buttonClose:"Close", simplifyMode:"Simplify Mode", turnOn:"Turn on", turnOff:"Turn off" };
    const WEEK_FULL=["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"];
    const MONTH_SHORT=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    const MONTH_FULL=["January","February","March","April","May","June","July","August","September","October","November","December"];
    const FMT_DEFAULT=16;
    const FORMATS=[
      {format:"Do nothing",example:"N/A"},
      {format:"ISO 8601 T",example:"2025-07-09T22:57:30"},
      {format:"ISO 8601 (space + s)",example:"2025-07-09 22:57:30"},
      {format:"ISO 8601 (space, no s)",example:"2025-07-09 22:57"},
      {format:"US: MMM d, yyyy h:mm A",example:"Jul 9, 2025, 10:57 PM"},
      {format:"US: EEE, MMM d, yyyy h:mm A",example:"Wed, Jul 9, 2025, 10:57 PM"},
      {format:"US: MM/dd/yyyy h:mm A",example:"07/09/2025 10:57 PM"},
      {format:"US: MM/dd/yyyy HH:mm",example:"07/09/2025 22:57"},
      {format:"EU/UK: dd/MM/yyyy HH:mm",example:"09/07/2025 22:57"},
      {format:"DE: dd.MM.yyyy, HH:mm",example:"09.07.2025, 22:57"},
      {format:"EU long: d MMMM yyyy, HH:mm",example:"9 July 2025, 22:57"},
      {format:"CN: yyyy年M月d日 HH:mm",example:"2025年7月9日 22:57"},
      {format:"East Asia: yyyy/MM/dd HH:mm",example:"2025/07/09 22:57"},
      {format:"UK short: EEE d MMM yyyy HH:mm",example:"Wed 9 Jul 2025 22:57"},
      {format:"Unix ctime (en)",example:"Wed Jul  9 22:57:30 2025"},
      {format:"US full: EEEE, MMMM d, yyyy h:mm:ss A",example:"Wednesday, July 9, 2025, 10:57:30 PM"},
      {format:"Compact: hh.mm A·mmm d,yy",example:"10.57 PM·Jul 9,25"},
      {format:"TW ROC: Myyy-MM-dd HH:mm",example:"M114-07-09 22:57"}
    ];
    let fmt = GM_getValue("fmt", FMT_DEFAULT);
    (function(){ const max=FORMATS.length-1; const v=parseInt(String(fmt),10);
      if(Number.isNaN(v)||v<0||v>max){ fmt=String(Math.min(Math.max(parseInt(String(FMT_DEFAULT),10),0),max)); GM_setValue("fmt",fmt); } else fmt=String(v); })();

    function df(date,f){
      const pad=n=>("0"+n).slice(-2);
      const YE=date.getFullYear(), YE2=YE.toString().slice(-2), YM=YE-1911;
      const MO=pad(date.getMonth()+1), MO_IDX=date.getMonth(), MO_NAME=MONTH_SHORT[MO_IDX], MO_NAME_FULL=MONTH_FULL[MO_IDX];
      const DA=pad(date.getDate()), dNum=parseInt(DA,10);
      const weekAbbr=()=>WEEK_FULL[date.getDay()].slice(0,3);
      const HO=pad(date.getHours()), MI=pad(date.getMinutes()), SE=pad(date.getSeconds());
      const h12=date.getHours()%12||12, HO12=pad(h12), AMPM=date.getHours()>=12?"PM":"AM";
      const F=[
        `${YE}-${MO}-${DA}T${HO}:${MI}:${SE}`,
        `${YE}-${MO}-${DA} ${HO}:${MI}:${SE}`,
        `${YE}-${MO}-${DA} ${HO}:${MI}`,
        `${MO_NAME} ${dNum}, ${YE}, ${HO12}:${MI} ${AMPM}`,
        `${weekAbbr()}, ${MO_NAME} ${dNum}, ${YE}, ${HO12}:${MI} ${AMPM}`,
        `${MO}/${DA}/${YE} ${HO12}:${MI} ${AMPM}`,
        `${MO}/${DA}/${YE} ${HO}:${MI}`,
        `${DA}/${MO}/${YE} ${HO}:${MI}`,
        `${DA}.${MO}.${YE}, ${HO}:${MI}`,
        `${dNum} ${MO_NAME_FULL} ${YE}, ${HO}:${MI}`,
        `${YE}年${MO_IDX+1}月${dNum}日 ${HO}:${MI}`,
        `${YE}/${MO}/${DA} ${HO}:${MI}`,
        `${weekAbbr()} ${dNum} ${MO_NAME} ${YE} ${HO}:${MI}`,
        `${weekAbbr()} ${MO_NAME} ${String(dNum).padStart(2," ")} ${HO}:${MI}:${SE} ${YE}`,
        `${WEEK_FULL[date.getDay()]}, ${MO_NAME_FULL} ${dNum}, ${YE}, ${HO12}:${MI}:${SE} ${AMPM}`,
        `${HO12}.${MI} ${AMPM}·${MO_NAME} ${dNum},${YE2}`,
        `M${YM}-${MO}-${DA} ${HO}:${MI}`
      ];
      return F[f] ?? F[0];
    }

    const MYNAME="ud_ts";
    function repldatetime(){
      const SEL='main div[data-testid="primaryColumn"] section article time[datetime*=":"]';
      const SEL_2='div[aria-labelledby="modal-header"] div[data-testid^="User-Name"] time[datetime]';
      const SEL_3='div[aria-labelledby="modal-header"] div[aria-label] time[datetime]';
      const SEL_4='main section[aria-labelledby="detail-header"] article div[data-testid^="User-Name"] time[datetime]';
      const SEL_5='main section div[data-testid="conversation"] div[aria-label] time[datetime]';
      document.querySelectorAll([SEL,SEL_2,SEL_3,SEL_4,SEL_5].join(", ")).forEach(e=>{
        if(fmt==0) return;
        const SEL_ADD="span.us-"+MYNAME;
        const d=e.getAttribute("datetime");
        const s=df(new Date(d), fmt-1);
        const pe=e.parentNode, old=pe.querySelectorAll(SEL_ADD);
        if(!old.length){
          const span=document.createElement("span");
          span.className="us-"+MYNAME; span.setAttribute("datetime",d); span.setAttribute("local-datetime",s);
          span.textContent=s; span.style=e.style; e.style.setProperty("display","none"); pe.appendChild(span);
        } else if(old[0].getAttribute("local-datetime")!=s){
          old[0].setAttribute("local-datetime",s); old[0].textContent=s; old[0].style=e.style;
        }
      });
    }

    const dlg = {
      number: Math.ceil(Math.random()*1e8),
      make(){
        const dialog=document.createElement("div");
        dialog.className="dialog_u_"+this.number;
        dialog.style.cssText="all:initial;background:#fff;border:1px solid #e1e8ed;border-radius:10px;box-shadow:0 16px 48px rgba(15,20,25,.14),0 4px 16px rgba(15,20,25,.08);font-family:monospace;font-size:12px;width:640px;max-width:calc(100vw - 24px);box-sizing:border-box;padding:8px;position:fixed;right:8px;top:8px;z-index:2147483647;overflow:auto;display:none;";
        const escH=s=>String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
        let rows='<table style="width:100%;border:1px solid #c0bfbf;border-collapse:collapse;">';
        for(let i=1;i<=FORMATS.length;i++){
          if(i%2!==0) rows+='<tr style="width:100%;border:1px solid #c0bfbf;">';
          const it=FORMATS[i-1], exT=String(it.example).replace(/"/g,"&quot;");
          rows+=`<td width="50%" style="border:1px solid #c0bfbf;padding:5px;vertical-align:top;" title="${exT}"><div><div style="color:#000;font-size:13px;"><input type="radio" name="fmt" value="${i-1}"/><b>【${i}】${escH(it.format)}</b></div><div style="color:#555;font-size:11px;margin-top:4px;padding-left:20px;">${escH(it.example)}</div></div></td>`;
          if(i%2===0) rows+='</tr>';
        }
        if(FORMATS.length%2!==0) rows+='</tr>';
        rows+='</table>';
        dialog.innerHTML=`<div style="font-size:17px;font-weight:700;margin:15px auto;padding:0 4px;text-align:center;font-family:system-ui,sans-serif;color:#0f1419;">${escH(L.titleDateFormat)}</div><div>${rows}</div><div style="margin-top:15px;text-align:center;"><button type="button" name="closex" style="border:1px solid #1d9bf0;border-radius:999px;padding:5px 18px;font-size:14px;font-weight:600;font-family:system-ui,sans-serif;background:linear-gradient(180deg,#1d9bf0,#1a8cd8);color:#fff;cursor:pointer;box-shadow:0 2px 10px rgba(29,155,240,.35);">${escH(L.buttonClose)}</button></div>`;
        return dialog;
      },
      init(){
        const dialog=this.make();
        document.body.appendChild(dialog);
        dialog.querySelector("button[name='closex']").addEventListener("click",()=>{
          for(const e of dialog.querySelectorAll('input[name="fmt"]')){ if(e.checked){ fmt=e.value; break; } }
          GM_setValue("fmt",fmt); dialog.style.display="none";
        },false);
        GM_registerMenuCommand(L.settings,()=>{
          if(dialog.style.display!=="none") return;
          const input=dialog.querySelector(`input[name="fmt"][value="${String(fmt)}"]`);
          if(input) input.checked=true;
          dialog.style.display="block";
        });
      }
    };

    function initSimplify(){
      const enabled=GM_getValue("x_simplify_mode","")==="true";
      GM_registerMenuCommand(L.simplifyMode+` (${enabled?L.turnOff:L.turnOn})`,()=>{
        if(enabled) GM_deleteValue("x_simplify_mode"); else GM_setValue("x_simplify_mode","true");
        location.reload();
      });
      if(!enabled) return;
      function update(){
        const width=Math.min(document.documentElement.offsetWidth||800,800);
        if(window.innerWidth===width && document.documentElement.clientWidth===width) return;
        window.__defineGetter__("innerWidth",()=>width);
        document.documentElement.__defineGetter__("clientWidth",()=>width);
        if(window.visualViewport) window.visualViewport.__defineGetter__("width",()=>width);
        window.dispatchEvent(new Event("resize"));
        if(window.visualViewport) window.visualViewport.dispatchEvent(new Event("resize"));
      }
      window.addEventListener("load",update);
      window.addEventListener("resize",update);
      if(window.visualViewport) window.visualViewport.addEventListener("resize",update);
      document.addEventListener("visibilitychange",update);
      GM_addStyle("#react-root main{-webkit-flex-grow:1!important;flex-grow:1!important}@media (min-width:800px){[role='listbox']{max-width:500px!important}}");
      update();
    }

    return {
      init(){
        try { dlg.init(); } catch(e){}
        try { initSimplify(); } catch(e){}
        let pending=false;
        const schedule=()=>{ if(pending)return; pending=true; setTimeout(()=>{ pending=false; try{repldatetime();}catch(e){} },300); };
        new MutationObserver(schedule).observe(document.body,{childList:true,subtree:true});
        schedule();
      }
    };
  })();

  // ════════════════════════════════════════════════════════════════════
  //  GRABBER (main) — bubble / right-click / hotkey on allow-listed sites
  // ════════════════════════════════════════════════════════════════════
  const Grabber = (function () {
    const DEFAULT_ALLOW = ["erome.com", "eporner.com", "ebonybaddies.com"];

    function getAllow() { try { return JSON.parse(GM_getValue("allow", JSON.stringify(DEFAULT_ALLOW))); } catch (e) { return DEFAULT_ALLOW.slice(); } }
    function setAllow(list) { GM_setValue("allow", JSON.stringify(list)); }
    function norm(d) { return String(d).replace(/^www\./, "").trim().toLowerCase(); }
    function isAllowed() { return getAllow().some(function (d) { d = norm(d); return d && (host === d || host.endsWith("." + d)); }); }

    function grab(el, kind) {
      if (!el) return location.href;
      if (kind === "image") {
        const img = el.closest("img") || (el.querySelector && el.querySelector("img"));
        if (img && img.src) return img.src;
        const bg = el.style && el.style.backgroundImage;
        if (bg && bg.indexOf("url(") !== -1) return bg.slice(bg.indexOf("(") + 1, bg.indexOf(")")).replace(/['"]/g, "");
      }
      const a = el.closest("a[href]");
      if (a && a.href && a.href.indexOf("javascript") !== 0) return a.href;
      const v = el.closest("video");
      if (v) { if (v.currentSrc) return v.currentSrc; const s = v.querySelector("source"); if (s && s.src) return s.src; }
      return location.href;
    }

    function onContextMenu(e) {
      let fmt = "mp4", audio = false, kind = "video";
      if      (e.shiftKey) { fmt = "mp4"; kind = "video"; }
      else if (e.altKey)   { fmt = "mp3"; audio = true; kind = "audio"; }
      else if (e.ctrlKey)  { fmt = "mp4"; kind = "image"; }
      else return;
      e.preventDefault(); e.stopPropagation();
      send(grab(e.target, kind), fmt, audio);
    }

    const bubble = document.createElement("div");
    bubble.innerHTML =
      '<svg viewBox="0 0 64 64" width="26" height="26" fill="none" style="display:block">' +
      '<circle cx="32" cy="29" r="17" stroke="#fff" stroke-opacity="0.25" stroke-width="3"/>' +
      '<path d="M32 14 V33" stroke="#fff" stroke-width="5" stroke-linecap="round"/>' +
      '<path d="M22 25 l10 10 l10 -10" stroke="#fff" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>' +
      '<path d="M17 41 v4 a5 5 0 0 0 5 5 h20 a5 5 0 0 0 5 -5 v-4" stroke="#fff" stroke-width="5" stroke-linecap="round"/>' +
      '</svg>';
    bubble.title =
      "Universal Downloader\n" +
      "Click: download THIS page (video)\n" +
      "Shift+Click: this page as audio (MP3)\n\n" +
      "On any thumbnail / link / video:\n" +
      "Shift + Right-Click  →  Video\n" +
      "Alt + Right-Click    →  Audio (MP3)\n" +
      "Ctrl + Right-Click   →  Image\n\n" +
      "Alt+Shift+D  →  toggle the downloader off for this site";
    Object.assign(bubble.style, {
      position: "fixed", right: "18px", bottom: "18px", zIndex: 2147483647,
      width: "44px", height: "44px", borderRadius: "50%",
      background: "#a855f7", color: "#fff", font: "20px 'Segoe UI', system-ui, sans-serif",
      display: "flex", alignItems: "center", justifyContent: "center",
      cursor: "pointer", boxShadow: "0 4px 16px rgba(0,0,0,.5)", userSelect: "none", transition: "transform .1s"
    });
    bubble.addEventListener("mouseenter", function () { bubble.style.transform = "scale(1.08)"; });
    bubble.addEventListener("mouseleave", function () { bubble.style.transform = "scale(1)"; });
    bubble.addEventListener("click", function (e) { send(location.href, e.shiftKey ? "mp3" : "mp4", e.shiftKey); });

    let active = false;
    function mountBubble() { if (active && document.body && !document.getElementById("ud-bubble")) { bubble.id = "ud-bubble"; document.body.appendChild(bubble); } }
    function activate() { if (!active) { active = true; document.addEventListener("contextmenu", onContextMenu, true); } mountBubble(); }
    function deactivate() { if (!active) return; active = false; document.removeEventListener("contextmenu", onContextMenu, true); const b = document.getElementById("ud-bubble"); if (b) b.remove(); }
    function toggleSite() {
      const list = getAllow();
      const i = list.findIndex(function (d) { return norm(d) === host; });
      let on;
      if (i >= 0) { list.splice(i, 1); on = false; } else { list.push(host); on = true; }
      setAllow(list);
      if (on) { activate();  toast("✓ Downloader ON for " + host, "#a855f7"); }
      else    { deactivate(); toast("✕ Downloader OFF for " + host + " (Alt+Shift+D to re-enable)", "#ef4444"); }
    }

    return {
      init: function () {
        window.addEventListener("keydown", function (e) {
          if (!!e.altKey === HOTKEY.alt && !!e.shiftKey === HOTKEY.shift && !!e.ctrlKey === HOTKEY.ctrl
              && (e.code === HOTKEY.code || (e.key || "").toLowerCase() === "d")) {
            e.preventDefault(); e.stopPropagation(); toggleSite();
          }
        }, true);
        try {
          GM_registerMenuCommand((isAllowed() ? "✕ Disable downloader on this site (" : "✓ Enable downloader on this site (") + host + ")", toggleSite);
          GM_registerMenuCommand("⚙ Edit allowed sites…", function () {
            const next = prompt("Sites the downloader works on (comma-separated domains):", getAllow().join(", "));
            if (next !== null) { setAllow(next.split(",").map(norm).filter(Boolean)); location.reload(); }
          });
        } catch (e) { /* menu API unavailable */ }
        if (isAllowed()) { activate(); window.addEventListener("load", mountBubble); }
      }
    };
  })();

  // ═══════════════════════════════ Start ═══════════════════════════════
  if (IS_TWITTER) { Twitter.init(); TwitterExtras.init(); }   // X: app-download buttons + timestamp/simplify extras
  else            Grabber.init();                             // everywhere else: allow-listed bubble/right-click → app
})();
