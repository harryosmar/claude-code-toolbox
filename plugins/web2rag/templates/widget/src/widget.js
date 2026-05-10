// web2rag chat widget — vanilla Web Component, Shadow DOM, no framework.
// Bilingual EN + ID. Streams SSE tokens live; swaps in guard-amended text
// with a "(corrected)" badge on guard_amend events. Production user
// feedback (👍/👎 + free-text) is sent to /feedback after each answer.
//
// Embed:
//   <script src=".../widget.js"
//           data-api-url="https://api.example.com/chat"
//           data-feedback-url="https://api.example.com/feedback"
//           data-site-id="docs.acme.com"
//           data-theme="auto" data-accent-color="#6B5BFF"
//           data-launcher-position="bottom-right"
//           data-locale="auto"></script>

(function () {
  "use strict";

  // i18n bundles are inlined at server-render time (the api substitutes them
  // when serving widget.js). Keeping both locales in one bundle avoids a
  // second network round-trip.
  const I18N = {
    en: __I18N_EN__,
    id: __I18N_ID__,
  };

  const DEFAULTS = {
    apiUrl: "/chat",
    feedbackUrl: "/feedback",
    siteId: "",
    theme: "auto",
    accentColor: "#6B5BFF",
    launcherPosition: "bottom-right",
    headerText: "",
    locale: "auto",
  };

  function readConfig() {
    const tag = document.currentScript;
    if (!tag) return DEFAULTS;
    const c = { ...DEFAULTS };
    for (const k of Object.keys(DEFAULTS)) {
      const dataKey = "data-" + k.replace(/[A-Z]/g, (m) => "-" + m.toLowerCase());
      const v = tag.getAttribute(dataKey);
      if (v != null) c[k] = v;
    }
    return c;
  }

  function resolveLocale(requested) {
    if (requested && requested !== "auto") return I18N[requested] ? requested : "en";
    const nav = (navigator.language || "en").toLowerCase();
    if (nav.startsWith("id")) return "id";
    return "en";
  }

  // SSE-over-fetch reader — we don't use EventSource because it can't POST.
  async function* readSSE(response) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) return;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const block = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        let event = "message";
        let data = "";
        for (const line of block.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) data += line.slice(5).trim();
        }
        if (data) {
          try { yield { event, data: JSON.parse(data) }; } catch { /* ignore */ }
        }
      }
    }
  }

  function uuid() {
    // Crypto-strong on modern browsers; fallback OK for older.
    if (crypto && crypto.randomUUID) return crypto.randomUUID();
    return "id-" + Math.random().toString(36).slice(2) + "-" + Date.now();
  }

  class RagChatbot extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._cfg = readConfig();
      this._locale = resolveLocale(this._cfg.locale);
      this._t = I18N[this._locale];
      this._open = false;
      this._messages = []; // {role, text, citations?, corrected?}
      this._busy = false;
      this._sessionId = uuid();
      this._render();
    }

    _render() {
      const t = this._t;
      const css = this._css();
      const launcherSide = this._cfg.launcherPosition === "bottom-left" ? "left" : "right";
      this.shadowRoot.innerHTML = `
        <style>${css}</style>
        <button class="launcher" part="launcher"
                aria-label="${t.launcher_aria_label}"
                style="${launcherSide}: 24px;">
          <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
            <path fill="currentColor" d="M4 4h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H8l-4 4V6a2 2 0 0 1 2-2z"/>
          </svg>
        </button>
        <section class="panel" role="dialog" aria-modal="false" aria-labelledby="w2r-title" hidden
                 style="${launcherSide}: 24px;">
          <header>
            <h2 id="w2r-title">${this._cfg.headerText || t.header_default}</h2>
            <button class="close" aria-label="${t.close_aria_label}">×</button>
          </header>
          <div class="log" role="log" aria-live="polite" aria-atomic="false"></div>
          <form class="composer" autocomplete="off">
            <input type="text" name="msg" placeholder="${t.input_placeholder}" aria-label="${t.input_placeholder}" required />
            <button type="submit" aria-label="${t.send_aria_label}">→</button>
          </form>
          <footer><span>${t.powered_by}</span></footer>
        </section>
      `;
      this._launcher = this.shadowRoot.querySelector(".launcher");
      this._panel = this.shadowRoot.querySelector(".panel");
      this._log = this.shadowRoot.querySelector(".log");
      this._form = this.shadowRoot.querySelector(".composer");
      this._input = this._form.querySelector("input[name=msg]");
      this._closeBtn = this.shadowRoot.querySelector(".close");

      this._launcher.addEventListener("click", () => this._toggle(true));
      this._closeBtn.addEventListener("click", () => this._toggle(false));
      this._form.addEventListener("submit", (e) => this._onSubmit(e));
      this.shadowRoot.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && this._open) this._toggle(false);
      });
    }

    _toggle(open) {
      this._open = open;
      this._panel.hidden = !open;
      this._launcher.style.display = open ? "none" : "";
      if (open) this._input.focus();
      else this._launcher.focus();
    }

    async _onSubmit(e) {
      e.preventDefault();
      if (this._busy) return;
      const text = this._input.value.trim();
      if (!text) return;
      this._input.value = "";
      this._busy = true;

      this._appendMessage({ role: "user", text });
      const bubble = this._appendMessage({ role: "assistant", text: "", thinking: true });
      bubble.dataset.messageId = uuid();
      bubble.dataset.question = text;

      const history = this._messages
        .slice(0, -1) // exclude the just-added empty assistant bubble
        .filter((m) => m.text)
        .map((m) => ({ role: m.role, content: m.text }));

      try {
        const resp = await fetch(this._cfg.apiUrl, {
          method: "POST",
          headers: { "content-type": "application/json", "accept": "text/event-stream" },
          body: JSON.stringify({
            message: text,
            site_id: this._cfg.siteId || null,
            session_id: this._sessionId,
            history,
          }),
        });
        if (!resp.ok || !resp.body) throw new Error("api error " + resp.status);
        let answer = "";
        let amended = false;
        let citations = [];
        let guards = {};
        for await (const evt of readSSE(resp)) {
          if (evt.event === "token") {
            if (bubble.dataset.thinking) {
              bubble.dataset.thinking = "";
              bubble.querySelector(".body").textContent = "";
            }
            answer += evt.data.text;
            bubble.querySelector(".body").textContent = answer;
            this._scrollToBottom();
          } else if (evt.event === "guard_amend") {
            answer = evt.data.text;
            amended = true;
            bubble.querySelector(".body").textContent = answer;
          } else if (evt.event === "citations") {
            citations = evt.data.citations || [];
          } else if (evt.event === "blocked") {
            bubble.querySelector(".body").textContent = this._t.blocked_message;
          } else if (evt.event === "no_context") {
            bubble.querySelector(".body").textContent = this._t.no_context_message;
          } else if (evt.event === "done") {
            guards = evt.data.guards || {};
          }
        }
        if (amended) this._renderCorrectedBadge(bubble);
        if (citations.length) this._renderCitations(bubble, citations);
        // Final answer (or amended) is in bubble; cache the snapshot for feedback.
        this._renderFeedback(bubble, {
          question: text,
          answer,
          citations,
          guards,
        });
      } catch (err) {
        bubble.querySelector(".body").textContent = this._t.error_generic;
        // eslint-disable-next-line no-console
        console.error("[web2rag]", err);
      } finally {
        this._busy = false;
        this._input.focus();
      }
    }

    _appendMessage({ role, text, thinking = false }) {
      const wrap = document.createElement("div");
      wrap.className = "msg msg-" + role;
      wrap.dataset.thinking = thinking ? "1" : "";
      const body = document.createElement("div");
      body.className = "body";
      body.textContent = thinking ? this._t.thinking : text;
      wrap.appendChild(body);
      this._log.appendChild(wrap);
      this._messages.push({ role, text });
      this._scrollToBottom();
      return wrap;
    }

    _renderCorrectedBadge(bubble) {
      const badge = document.createElement("div");
      badge.className = "badge";
      badge.textContent = this._t.corrected_badge;
      bubble.appendChild(badge);
    }

    _renderCitations(bubble, citations) {
      const wrap = document.createElement("div");
      wrap.className = "citations";
      const label = document.createElement("span");
      label.className = "citations-label";
      label.textContent = this._t.sources_label + ": ";
      wrap.appendChild(label);
      citations.forEach((c, i) => {
        const a = document.createElement("a");
        a.href = c.url;
        a.target = "_blank";
        a.rel = "noopener";
        a.className = "chip";
        a.textContent = "[" + (i + 1) + "] " + (c.title || c.url);
        if (c.snippet) a.title = c.snippet;
        wrap.appendChild(a);
      });
      bubble.appendChild(wrap);
    }

    _renderFeedback(bubble, snapshot) {
      const wrap = document.createElement("div");
      wrap.className = "feedback";
      const up = document.createElement("button");
      up.className = "fb-btn";
      up.setAttribute("aria-label", this._t.feedback_up_label);
      up.innerHTML = "👍";
      const down = document.createElement("button");
      down.className = "fb-btn";
      down.setAttribute("aria-label", this._t.feedback_down_label);
      down.innerHTML = "👎";
      wrap.appendChild(up);
      wrap.appendChild(down);
      bubble.appendChild(wrap);

      let submitted = false;
      const sendVote = (vote, comment = "") => {
        if (submitted) return;
        submitted = true;
        wrap.classList.add("submitted");
        wrap.innerHTML = `<span class="fb-thanks">${this._t.feedback_thanks}</span>`;
        fetch(this._cfg.feedbackUrl, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            session_id: this._sessionId,
            message_id: bubble.dataset.messageId,
            vote,
            site_id: this._cfg.siteId || null,
            comment,
            snapshot,
          }),
        }).catch((e) => console.warn("[web2rag] feedback post failed", e));
      };

      up.addEventListener("click", () => sendVote("up"));
      down.addEventListener("click", () => {
        // 👎 expands a short comment textarea (optional).
        const form = document.createElement("form");
        form.className = "fb-form";
        form.innerHTML = `
          <textarea rows="2" maxlength="2000" placeholder="${this._t.feedback_down_prompt}"></textarea>
          <button type="submit" class="fb-submit">${this._t.feedback_submit}</button>
        `;
        wrap.replaceWith(form);
        const ta = form.querySelector("textarea");
        ta.focus();
        form.addEventListener("submit", (e) => {
          e.preventDefault();
          const comment = ta.value.trim();
          form.replaceWith(wrap);
          sendVote("down", comment);
        });
      });
    }

    _scrollToBottom() {
      this._log.scrollTop = this._log.scrollHeight;
    }

    _css() {
      const accent = this._cfg.accentColor;
      const themeOverride = this._cfg.theme === "dark" ? ":host" : (this._cfg.theme === "light" ? ":host[never-dark]" : "@media (prefers-color-scheme: dark)");
      return `
        :host {
          --accent: ${accent};
          --bg: #fff;
          --fg: #111;
          --bg-alt: #f3f4f6;
          --border: #e5e7eb;
          --shadow: 0 8px 24px rgba(0,0,0,.18);
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        }
        ${themeOverride === "@media (prefers-color-scheme: dark)" ? "@media (prefers-color-scheme: dark)" : ":host"} {
          --bg: #0f172a; --fg: #e5e7eb; --bg-alt: #1e293b; --border: #334155;
        }
        .launcher {
          position: fixed; bottom: 24px;
          width: 56px; height: 56px; border-radius: 28px;
          background: var(--accent); color: #fff; border: none;
          box-shadow: var(--shadow); cursor: pointer; z-index: 2147483647;
          display: flex; align-items: center; justify-content: center;
        }
        .launcher:focus-visible { outline: 3px solid #fff; outline-offset: 2px; }
        .panel {
          position: fixed; bottom: 24px;
          width: min(400px, calc(100vw - 32px));
          height: min(640px, calc(100vh - 48px));
          background: var(--bg); color: var(--fg); border: 1px solid var(--border);
          border-radius: 16px; box-shadow: var(--shadow);
          display: flex; flex-direction: column; overflow: hidden;
          z-index: 2147483647;
        }
        header {
          display: flex; align-items: center; justify-content: space-between;
          padding: 14px 16px; border-bottom: 1px solid var(--border);
          background: var(--bg-alt);
        }
        h2 { margin: 0; font-size: 15px; font-weight: 600; }
        .close {
          background: none; border: none; color: var(--fg); font-size: 22px;
          cursor: pointer; line-height: 1; padding: 4px 8px; border-radius: 6px;
        }
        .close:hover { background: var(--border); }
        .log {
          flex: 1; overflow-y: auto; padding: 12px 16px;
          display: flex; flex-direction: column; gap: 12px;
        }
        .msg { max-width: 85%; padding: 10px 12px; border-radius: 12px; line-height: 1.4; font-size: 14px; word-wrap: break-word; }
        .msg-user { align-self: flex-end; background: var(--accent); color: #fff; }
        .msg-assistant { align-self: flex-start; background: var(--bg-alt); }
        .msg[data-thinking="1"] .body { opacity: .65; font-style: italic; }
        .badge {
          margin-top: 6px; font-size: 11px; opacity: .75;
          padding: 2px 6px; border: 1px solid var(--border); border-radius: 6px;
          display: inline-block;
        }
        .citations { margin-top: 8px; font-size: 12px; line-height: 1.6; }
        .citations-label { opacity: .7; }
        .chip {
          display: inline-block; margin: 2px 4px 2px 0; padding: 2px 8px;
          background: var(--bg); border: 1px solid var(--border); border-radius: 999px;
          color: var(--fg); text-decoration: none; max-width: 200px;
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap; vertical-align: middle;
        }
        .chip:hover { border-color: var(--accent); color: var(--accent); }
        .feedback {
          margin-top: 8px; display: flex; gap: 8px; align-items: center;
        }
        .fb-btn {
          background: var(--bg); border: 1px solid var(--border); border-radius: 6px;
          padding: 4px 8px; cursor: pointer; font-size: 14px;
        }
        .fb-btn:hover { border-color: var(--accent); }
        .fb-thanks { font-size: 11px; opacity: .7; }
        .fb-form {
          margin-top: 8px; display: flex; flex-direction: column; gap: 6px;
        }
        .fb-form textarea {
          width: 100%; padding: 6px 8px; border: 1px solid var(--border);
          border-radius: 6px; resize: vertical; font: inherit; background: var(--bg); color: var(--fg);
        }
        .fb-submit {
          align-self: flex-end; background: var(--accent); color: #fff;
          border: none; border-radius: 6px; padding: 4px 12px; cursor: pointer;
        }
        .composer {
          display: flex; gap: 8px; padding: 12px 16px;
          border-top: 1px solid var(--border); background: var(--bg);
        }
        .composer input {
          flex: 1; padding: 10px 12px; border: 1px solid var(--border);
          border-radius: 8px; background: var(--bg); color: var(--fg); font-size: 14px;
          outline-offset: 2px;
        }
        .composer input:focus { border-color: var(--accent); }
        .composer button {
          background: var(--accent); color: #fff; border: none; border-radius: 8px;
          width: 40px; cursor: pointer; font-size: 18px;
        }
        .composer button:disabled { opacity: .5; cursor: not-allowed; }
        footer {
          padding: 6px 16px; font-size: 11px; opacity: .55; text-align: right;
          border-top: 1px solid var(--border);
        }
      `;
    }
  }

  if (!customElements.get("rag-chatbot")) {
    customElements.define("rag-chatbot", RagChatbot);
  }
  if (!document.querySelector("rag-chatbot")) {
    const el = document.createElement("rag-chatbot");
    document.body.appendChild(el);
  }
})();
