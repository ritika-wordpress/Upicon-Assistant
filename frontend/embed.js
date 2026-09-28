(function () {
  "use strict";

  // ---------------------------------------------------------------------
  // UPICON Assistant — embeddable widget
  // Single-file version of index.html + style.css + script.js, bundled so
  // it can be dropped into any site with one <script> tag. Everything is
  // scoped under #upicon-assistant-root to avoid colliding with host-page
  // styles/ids.
  // ---------------------------------------------------------------------

  const API_BASE = "https://apis.upicon.in";

  // Avoid double-injecting if the script tag is accidentally included twice
  if (document.getElementById("upicon-assistant-root")) return;

  // ---------------- Site-wide translation (self-contained) ----------------
  // The widget itself sets up Google's Website Translator so the language
  // toggle can drive the WHOLE page, not just the chat — no other change
  // needed anywhere else on the site.
  function injectGoogleTranslate() {
    if (document.getElementById("google_translate_element")) return; // already present

    const gtStyle = document.createElement("style");
    gtStyle.textContent = `
      .goog-te-banner-frame.skiptranslate{ display:none !important; }
      body{ top:0px !important; }
      .goog-te-gadget{ display:none !important; }
    `;
    document.head.appendChild(gtStyle);

    const gtDiv = document.createElement("div");
    gtDiv.id = "google_translate_element";
    gtDiv.style.display = "none";
    document.body.appendChild(gtDiv);

    window.googleTranslateElementInit = function () {
      new window.google.translate.TranslateElement(
        { pageLanguage: "en", includedLanguages: "en,hi", autoDisplay: false },
        "google_translate_element"
      );
    };

    const gtScript = document.createElement("script");
    gtScript.src = "https://translate.google.com/translate_a/element.js?cb=googleTranslateElementInit";
    document.body.appendChild(gtScript);
  }
  injectGoogleTranslate();

  // ---------------- Fonts ----------------
  const fontPreconnect = document.createElement("link");
  fontPreconnect.rel = "preconnect";
  fontPreconnect.href = "https://fonts.googleapis.com";
  document.head.appendChild(fontPreconnect);

  const fontLink = document.createElement("link");
  fontLink.rel = "stylesheet";
  fontLink.href =
    "https://fonts.googleapis.com/css2?family=Poppins:wght@500;600;700&family=Inter:wght@400;500;600&family=Noto+Sans+Devanagari:wght@400;500;600&display=swap";
  document.head.appendChild(fontLink);

  // ---------------- Styles ----------------
  const style = document.createElement("style");
  style.textContent = `
#upicon-assistant-root{
  --navy-950:#0E2E2F;
  --navy-800:#14403F;
  --navy-700:#1C5453;
  --teal-500:#2FAFAF;
  --teal-400:#57C4C4;
  --marigold-500:#DA7F28;
  --marigold-400:#E89A52;
  --paper:#FBF7F0;
  --paper-dim:#F1EBDD;
  --ink:#21242C;
  --ink-soft:#5B6270;
  --line:#E4DCC9;
  --white:#FFFFFF;
  --success:#3F8F6F;
  --font-display:'Poppins','Noto Sans Devanagari',sans-serif;
  --font-body:'Inter','Noto Sans Devanagari',sans-serif;
  --radius-lg:20px;
  --radius-md:14px;
  --radius-sm:10px;
  --shadow-panel: 0 24px 60px -20px rgba(14,46,47,0.35), 0 4px 14px rgba(14,46,47,0.12);
  all: initial;
}
#upicon-assistant-root *{ box-sizing:border-box; font-family: var(--font-body); }
#upicon-assistant-root button{ font-family: var(--font-body); }

@media (prefers-reduced-motion: reduce){
  #upicon-assistant-root *{ animation-duration:0.001ms !important; transition-duration:0.001ms !important; }
}

#upicon-launcher{
  position:fixed; left:24px; bottom:24px; width:64px; height:64px;
  border-radius: 50%; border:none; cursor:pointer; padding:0; overflow:hidden;
  background: linear-gradient(145deg, var(--navy-800), var(--navy-950));
  color: var(--marigold-400);
  display:flex; align-items:center; justify-content:center;
  box-shadow: 0 14px 30px -10px rgba(14,46,47,0.55), 0 0 0 3px var(--marigold-500);
  transition: transform .18s ease, box-shadow .18s ease, opacity .18s ease, visibility .18s ease;
  z-index:2147483000;
}
#upicon-launcher:hover{ transform: translateY(-2px) scale(1.03); box-shadow: 0 18px 36px -8px rgba(14,46,47,0.6), 0 0 0 3px var(--marigold-500); }
#upicon-launcher:focus-visible{ outline:3px solid var(--marigold-400); outline-offset:3px; }
#upicon-launcher.launcher-hidden{ opacity:0; visibility:hidden; transform:scale(.85); pointer-events:none; }
.upicon-launcher-icon{ width:100%; height:100%; object-fit:cover; border-radius:50%; pointer-events:none; }
.upicon-launcher-dot{
  position:absolute; top:4px; right:4px; width:10px; height:10px; border-radius:50%;
  background:var(--marigold-500); border:2px solid var(--white);
}

#upicon-panel{
  position:fixed; left:24px; bottom:98px; width:380px; max-width:calc(100vw - 32px);
  height:min(620px, calc(100vh - 140px));
  background:var(--paper); border-radius: var(--radius-lg); box-shadow: var(--shadow-panel);
  display:flex; flex-direction:column; overflow:hidden;
  z-index:2147483000; transform-origin: bottom left;
  transition: opacity .18s ease, transform .18s ease;
}
#upicon-panel.hidden{ opacity:0; transform: scale(.92) translateY(12px); pointer-events:none; }

.upicon-panel-header{
  background: linear-gradient(120deg, var(--navy-950), var(--navy-700));
  color:var(--white); padding:16px 14px 16px 16px;
  display:flex; align-items:center; justify-content:space-between; gap:10px;
}
.upicon-header-id{ display:flex; align-items:center; gap:10px; min-width:0; }
.upicon-header-avatar{
  width:36px; height:36px; border-radius:50%;
  background: linear-gradient(145deg, var(--marigold-400), var(--marigold-500));
  color:var(--navy-950); font-family:var(--font-display); font-weight:700;
  display:flex; align-items:center; justify-content:center; flex-shrink:0;
}
.upicon-header-title{ margin:0; font-family:var(--font-display); font-weight:600; font-size:15px; color:var(--white); }
.upicon-header-sub{ margin:2px 0 0; font-size:12px; color:#C9D2E6; display:flex; align-items:center; gap:5px; }
.upicon-header-sub::before{ content:""; width:6px; height:6px; border-radius:50%; background:var(--success); display:inline-block; }
.upicon-header-actions{ display:flex; align-items:center; gap:8px; flex-shrink:0; }

.upicon-lang-toggle{
  position:relative; display:flex; background:rgba(255,255,255,0.12);
  border-radius:999px; padding:3px; width:78px; flex-shrink:0;
}
.upicon-lang-btn{
  position:relative; z-index:2; flex:1; border:none; background:transparent;
  color:#DDE3F1; font-family:var(--font-body); font-weight:600; font-size:12px;
  padding:5px 0; cursor:pointer; border-radius:999px;
}
.upicon-lang-btn.active{ color:var(--navy-950); }
.upicon-lang-pill{
  position:absolute; top:3px; left:3px; width:36px; height:calc(100% - 6px);
  background:var(--marigold-400); border-radius:999px; transition: transform .2s ease; z-index:1;
}
.upicon-lang-toggle[data-active="hi"] .upicon-lang-pill{ transform: translateX(36px); }

#upicon-closeBtn{
  width:30px; height:30px; border-radius:50%; border:none;
  background:rgba(255,255,255,0.12); color:var(--white); cursor:pointer;
  display:flex; align-items:center; justify-content:center;
}
#upicon-closeBtn:hover{ background:rgba(255,255,255,0.22); }

.upicon-saffron-rule{
  height:3px;
  background: linear-gradient(90deg, var(--marigold-500), var(--marigold-400), var(--marigold-500));
  flex-shrink:0;
}

.upicon-messages{
  flex:1; overflow-y:auto; padding:16px 14px; display:flex; flex-direction:column; gap:10px;
  background: radial-gradient(circle at 100% 0%, rgba(232,151,59,0.06), transparent 40%), var(--paper);
}
.upicon-msg{
  max-width:84%; padding:10px 13px; border-radius: var(--radius-md); font-size:14px;
  line-height:1.5; white-space:pre-wrap; word-wrap:break-word;
}
.upicon-msg.bot{ align-self:flex-start; background:var(--white); border:1px solid var(--line); border-top-left-radius:4px; color:var(--ink); }
.upicon-msg.user{ align-self:flex-end; background: linear-gradient(135deg, var(--navy-700), var(--navy-950)); color:var(--white); border-top-right-radius:4px; }
.upicon-msg.typing{ display:flex; gap:4px; align-items:center; padding:12px 14px; }
.upicon-msg.typing span{ width:6px; height:6px; border-radius:50%; background:var(--ink-soft); animation: upicon-bounce 1.1s infinite ease-in-out; }
.upicon-msg.typing span:nth-child(2){ animation-delay:.15s; }
.upicon-msg.typing span:nth-child(3){ animation-delay:.3s; }
@keyframes upicon-bounce{ 0%,60%,100%{ transform:translateY(0); opacity:.5; } 30%{ transform:translateY(-4px); opacity:1; } }

.upicon-suggestions{ display:flex; gap:6px; padding:0 14px 10px; flex-wrap:wrap; flex-shrink:0; }
.upicon-chip{
  border:1px solid var(--line); background:var(--white); color:var(--navy-800);
  font-size:12px; font-weight:500; padding:6px 11px; border-radius:999px; cursor:pointer;
  transition: background .15s ease, border-color .15s ease, transform .12s ease, box-shadow .12s ease;
}
.upicon-chip:hover{ background:var(--paper-dim); border-color:var(--marigold-400); transform: translateY(-2px) scale(1.04); box-shadow: 0 4px 10px -4px rgba(14,46,47,0.3); }
.upicon-chip:active{ transform: scale(0.96); }
.upicon-chip:disabled{ opacity:.5; cursor:default; pointer-events:none; transform:none; box-shadow:none; }

.upicon-composer{
  display:flex; align-items:center; gap:6px; padding:10px 12px;
  border-top:1px solid var(--line); background:var(--white); flex-shrink:0;
}
#upicon-textInput{
  flex:1; border:1px solid var(--line); background:var(--paper); border-radius:999px;
  padding:10px 14px; font-size:14px; color:var(--ink); outline:none;
}
#upicon-textInput:focus{ border-color:var(--marigold-500); box-shadow:0 0 0 3px rgba(232,151,59,0.15); }

.upicon-icon-btn{
  position:relative; width:36px; height:36px; border-radius:50%; border:1px solid var(--line);
  background:var(--paper); color:var(--navy-800); display:flex; align-items:center; justify-content:center;
  cursor:pointer; flex-shrink:0; transition: background .15s ease, transform .12s ease;
}
.upicon-icon-btn:hover{ background:var(--paper-dim); transform: scale(1.08); }
.upicon-icon-btn:active{ transform: scale(0.92); }
.upicon-icon-btn:disabled{ opacity:.5; cursor:default; pointer-events:none; transform:none; }
.upicon-icon-btn[data-state="speaking"],
.upicon-icon-btn[data-state="paused"]{ background:var(--navy-800); color:var(--white); border-color:var(--navy-800); }
#upicon-speakerCross{ stroke:currentColor; }

.upicon-mic-ring{ position:absolute; inset:-4px; border-radius:50%; border:2px solid var(--marigold-500); opacity:0; transform:scale(0.85); }
.upicon-icon-btn.listening .upicon-mic-ring{ opacity:1; animation: upicon-pulse-ring 1.2s infinite ease-out; }
@keyframes upicon-pulse-ring{ 0%{ transform:scale(0.85); opacity:.9; } 100%{ transform:scale(1.35); opacity:0; } }

.upicon-send-btn{
  width:38px; height:38px; border-radius:50%; border:none;
  background: linear-gradient(135deg, var(--marigold-400), var(--marigold-500));
  color:var(--navy-950); display:flex; align-items:center; justify-content:center;
  cursor:pointer; flex-shrink:0; transition: transform .12s ease;
}
.upicon-send-btn:hover{ transform: scale(1.1) rotate(-4deg); }
.upicon-send-btn:active{ transform: scale(0.92); }
.upicon-send-btn:disabled{ opacity:.5; cursor:default; transform:none; }

.upicon-footnote{ margin:0; padding:6px 14px 10px; font-size:10.5px; color:var(--ink-soft); text-align:center; flex-shrink:0; }

.upicon-messages::-webkit-scrollbar{ width:6px; }
.upicon-messages::-webkit-scrollbar-thumb{ background:var(--line); border-radius:6px; }

.upicon-messages.hidden, .upicon-suggestions.hidden, .upicon-composer.hidden{ display:none !important; }

@media (max-width:420px){
  #upicon-panel{ right:12px; left:12px; bottom:88px; width:auto; height:min(70vh, 620px); }
  #upicon-launcher{ left:16px; bottom:16px; }
}
`;
  document.head.appendChild(style);

  // ---------------- Markup ----------------
  const root = document.createElement("div");
  root.id = "upicon-assistant-root";
  root.innerHTML = `
<button id="upicon-launcher" aria-label="Open UPICON Mitra">
  <video class="upicon-launcher-icon" src="assets/launcher-icon.mp4" autoplay loop muted playsinline aria-hidden="true"></video>
  <span class="upicon-launcher-dot" id="upicon-launcherDot" hidden></span>
</button>

<section id="upicon-panel" class="hidden" role="dialog" aria-label="UPICON Mitra chat">
  <header class="upicon-panel-header">
    <div class="upicon-header-id">
      <div class="upicon-header-avatar">U</div>
      <div>
        <p class="upicon-header-title" id="upicon-headerTitle">UPICON Mitra</p>
        <p class="upicon-header-sub" id="upicon-headerSub">Online</p>
      </div>
    </div>
    <div class="upicon-header-actions">
      <div class="upicon-lang-toggle" id="upicon-langToggle" data-active="en" role="group" aria-label="Choose language">
        <span class="upicon-lang-pill"></span>
        <button type="button" class="upicon-lang-btn active" data-lang="en">EN</button>
        <button type="button" class="upicon-lang-btn" data-lang="hi">हि</button>
      </div>
      <button id="upicon-closeBtn" aria-label="Close chat">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M6 6l12 12M18 6L6 18"/>
        </svg>
      </button>
    </div>
  </header>

  <div class="upicon-saffron-rule"></div>

  <main id="upicon-messages" class="upicon-messages" aria-live="polite"></main>

  <div id="upicon-suggestions" class="upicon-suggestions">
    <button class="upicon-chip" data-chip="initiatives" data-q-en="What initiatives does UPICON run?" data-q-hi="UPICON की मुख्य पहलें कौन सी हैं?">Initiatives</button>
    <button class="upicon-chip" data-chip="careers" data-q-en="Are there any job openings right now?" data-q-hi="क्या अभी कोई नौकरी के अवसर उपलब्ध हैं?">Careers</button>
    <button class="upicon-chip" data-chip="schemes" data-q-en="Tell me about current schemes" data-q-hi="वर्तमान योजनाओं के बारे में बताएं">Schemes</button>
    <button class="upicon-chip" data-chip="news" data-q-en="Latest news updates" data-q-hi="नवीनतम समाचार अपडेट बताएं">News</button>
  </div>

  <form id="upicon-composer" class="upicon-composer">
    <button type="button" id="upicon-micBtn" class="upicon-icon-btn" aria-label="Speak your question">
      <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8">
        <path d="M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Z"/>
        <path d="M19 11a7 7 0 0 1-14 0M12 18v3"/>
      </svg>
      <span class="upicon-mic-ring" id="upicon-micRing"></span>
    </button>

    <input id="upicon-textInput" type="text" autocomplete="off" placeholder="Type your question…" aria-label="Message" />

    <button type="button" id="upicon-speakerBtn" class="upicon-icon-btn" aria-label="Read reply aloud" data-state="idle">
      <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8">
        <path d="M4 9v6h4l5 4V5L8 9H4Z"/>
        <path id="upicon-speakerWaves" d="M16 9a4 4 0 0 1 0 6" opacity="0.35"/>
        <path id="upicon-speakerCross" d="M3 3L21 21" stroke-linecap="round" style="display:none;"/>
      </svg>
    </button>

    <button type="submit" id="upicon-sendBtn" class="upicon-send-btn" aria-label="Send">
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M4 12h15M13 6l6 6-6 6"/>
      </svg>
    </button>
  </form>
</section>
`;
  document.body.appendChild(root);

  // ---------------- Logic (same behavior as script.js, re-scoped ids) ----------------
  const el = (id) => document.getElementById(id);

  const launcher = el("upicon-launcher");
  const launcherDot = el("upicon-launcherDot");
  const panel = el("upicon-panel");
  const closeBtn = el("upicon-closeBtn");
  const messagesEl = el("upicon-messages");
  const composer = el("upicon-composer");
  const textInput = el("upicon-textInput");
  const sendBtn = el("upicon-sendBtn");
  const micBtn = el("upicon-micBtn");
  const speakerBtn = el("upicon-speakerBtn");
  const headerSub = el("upicon-headerSub");
  const headerTitle = el("upicon-headerTitle");
  const suggestionsEl = el("upicon-suggestions");
  const langToggle = el("upicon-langToggle");

  let sessionId = localStorage.getItem("upicon_session_id") || null;
  let currentLang =
    localStorage.getItem("upicon_site_lang") ||
    (navigator.language && navigator.language.toLowerCase().startsWith("hi") ? "hi" : "en");
  // Read-aloud state machine: "idle" (nothing playing), "speaking", or
  // "paused" (cross shown over the speaker icon, click resumes).
  let speechState = "idle";
  let lastBotReply = null;
  let speechChunks = [];
  let speechChunkIndex = 0;
  let hasOpenedOnce = sessionStorage.getItem("upicon_panel_open") === "1";
  let messageLog = JSON.parse(sessionStorage.getItem("upicon_messages") || "[]");

  function applyLangToUI(lang) {
    langToggle.dataset.active = lang;
    langToggle.querySelectorAll(".upicon-lang-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.lang === lang);
    });
    headerTitle.textContent = COPY[lang].name;
    panel.setAttribute("aria-label", COPY[lang].name + (lang === "hi" ? " चैट" : " chat"));
    el("upicon-launcher")?.setAttribute("aria-label", (lang === "hi" ? "खोलें: " : "Open ") + COPY[lang].name);
    headerSub.textContent = COPY[lang].online;
    textInput.placeholder = COPY[lang].placeholder;
    suggestionsEl.querySelectorAll(".upicon-chip[data-chip]").forEach((chip) => {
      const key = chip.dataset.chip;
      if (COPY[lang].chips[key]) chip.textContent = COPY[lang].chips[key];
    });
  }

  function setLanguage(lang) {
    if (lang === currentLang) return;
    currentLang = lang;
    applyLangToUI(lang);
    translateSite(lang);
  }

  function translateSite(lang) {
    // Shared with the rest of upicon.in: any page (including this one, on
    // reload) can read this to know the visitor's chosen language.
    localStorage.setItem("upicon_site_lang", lang);

    // Lets the host page's own script react without a reload, if it has
    // one listening (e.g. a React/Vue i18n setup, or a translations swap).
    window.dispatchEvent(new CustomEvent("upicon:languagechange", { detail: { lang } }));

    // If the page has Google's Website Translator widget embedded, the
    // standard way to drive it programmatically is the "googtrans" cookie
    // plus a reload. Only do this when that widget is actually present,
    // so pages without it don't get an unnecessary reload.
    const hasGoogleTranslate =
      !!document.getElementById("google_translate_element") || !!window.google?.translate;
    if (hasGoogleTranslate) {
      document.cookie = `googtrans=/en/${lang}; path=/`;
      document.cookie = `googtrans=/en/${lang}; domain=.${location.hostname}; path=/`;
      location.reload();
    }
  }

  const COPY = {
    en: {
      name: "UPICON Mitra",
      online: "Online",
      placeholder: "Type your question…",
      listening: "Listening…",
      greetOnOpen: "Hi! 👋 I'm UPICON Mitra. Ask me anything about UPICON — initiatives, schemes, news, careers and more.",
      chips: { initiatives: "Initiatives", careers: "Careers", schemes: "Schemes", news: "News" },
    },
    hi: {
      name: "UPICON मित्र",
      online: "ऑनलाइन",
      placeholder: "अपना सवाल लिखें…",
      listening: "सुन रहा हूँ…",
      greetOnOpen: "नमस्ते! 👋 मैं UPICON मित्र हूँ। UPICON से जुड़ी पहलों, योजनाओं, समाचार, करियर या किसी भी जानकारी के बारे में पूछें।",
      chips: { initiatives: "पहल", careers: "करियर", schemes: "योजनाएँ", news: "समाचार" },
    },
  };

  applyLangToUI(currentLang);

  function openPanel() {
    panel.classList.remove("hidden");
    launcher.classList.add("launcher-hidden");
    launcherDot.hidden = true;
    sessionStorage.setItem("upicon_panel_open", "1");

    if (messageLog.length > 0) {
      // Restore prior conversation (e.g. after a language-toggle reload)
      // instead of greeting again — render-only, so it doesn't duplicate
      // itself in messageLog.
      messageLog.forEach((m) => addMessageDOM(m.role, m.text));
    } else if (!hasOpenedOnce) {
      hasOpenedOnce = true;
      addMessage("bot", COPY[currentLang].greetOnOpen);
    }
    textInput.focus();
  }

  launcher.addEventListener("click", openPanel);

  // ---------------- Hover voiceover on the launcher ----------------
  // Note: browsers only allow audio-with-sound to autoplay after the
  // visitor has interacted with the page at least once - a hover alone
  // doesn't count in Chrome - so the very first hover on a fresh page
  // load may be silently blocked until after a click/keypress anywhere.
  const _voiceClips = {
    en: new Audio("assets/launcher-voice-en.mp3"),
    hi: new Audio("assets/launcher-voice-hi.mp3"),
  };

  launcher.addEventListener("mouseenter", () => {
    const clip = _voiceClips[currentLang] || _voiceClips.en;
    Object.values(_voiceClips).forEach((a) => {
      if (a !== clip) {
        a.pause();
        a.currentTime = 0;
      }
    });
    clip.currentTime = 0;
    clip.play().catch(() => {
      /* blocked by autoplay policy until the visitor interacts once */
    });
  });

  launcher.addEventListener("mouseleave", () => {
    const clip = _voiceClips[currentLang] || _voiceClips.en;
    clip.pause();
    clip.currentTime = 0;
  });

  closeBtn.addEventListener("click", () => {
    panel.classList.add("hidden");
    launcher.classList.remove("launcher-hidden");
    sessionStorage.setItem("upicon_panel_open", "0");
  });

  // Re-open automatically if we're back after a translateSite() reload so
  // switching language doesn't look like the widget closing on the visitor.
  if (sessionStorage.getItem("upicon_panel_open") === "1") {
    openPanel();
  }

  langToggle.addEventListener("click", (e) => {
    const btn = e.target.closest(".upicon-lang-btn");
    if (!btn) return;
    setLanguage(btn.dataset.lang);
  });

  suggestionsEl.addEventListener("click", (e) => {
    const chip = e.target.closest(".upicon-chip");
    if (!chip) return;
    const q = currentLang === "hi" ? chip.dataset.qHi : chip.dataset.qEn;
    sendMessage(q || chip.dataset.qEn);
  });

  // Renders only — does NOT persist. Used by addMessage() below and when
  // replaying messageLog after a language-toggle reload.
  function addMessageDOM(role, text) {
    const bubble = document.createElement("div");
    bubble.className = `upicon-msg ${role}`;
    bubble.textContent = text;
    messagesEl.appendChild(bubble);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return bubble;
  }

  // Renders AND persists to sessionStorage, so a language-toggle reload
  // (see translateSite()) can restore the conversation instead of the
  // panel coming back empty.
  function addMessage(role, text) {
    messageLog.push({ role, text });
    sessionStorage.setItem("upicon_messages", JSON.stringify(messageLog));
    return addMessageDOM(role, text);
  }

  // Swaps a still-visible typing indicator for the real bot reply, in the
  // exact same spot in the message list - never appended at the end. That
  // way, if a second question was asked while the first was still loading,
  // the first answer still lands directly under its own question instead of
  // under whichever question happens to be newest.
  function resolveTypingBubble(typingBubble, text) {
    messageLog.push({ role: "bot", text });
    sessionStorage.setItem("upicon_messages", JSON.stringify(messageLog));
    const bubble = document.createElement("div");
    bubble.className = "upicon-msg bot";
    bubble.textContent = text;
    typingBubble.replaceWith(bubble);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return bubble;
  }

  function addTypingIndicator() {
    const bubble = document.createElement("div");
    bubble.className = "upicon-msg bot typing";
    bubble.innerHTML = "<span></span><span></span><span></span>";
    messagesEl.appendChild(bubble);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return bubble;
  }

  // Buttons (send/mic/speaker/chips) stay live even while a reply is
  // loading - you can ask another question right away. Requests still go
  // to the backend one at a time, in the order asked, so answers can never
  // come back interleaved; each one replaces its own typing indicator the
  // moment it's ready (see resolveTypingBubble), so it lands right under
  // its own question even if a later question is still waiting below it.
  const sendQueue = [];
  let isProcessingQueue = false;

  async function processSendQueue() {
    if (isProcessingQueue) return;
    isProcessingQueue = true;
    while (sendQueue.length > 0) {
      const { trimmed, typingBubble } = sendQueue.shift();
      await deliverReply(trimmed, typingBubble);
    }
    isProcessingQueue = false;
  }

  async function deliverReply(trimmed, typingBubble) {
    try {
      const resp = await fetch(`${API_BASE}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: trimmed,
          session_id: sessionId,
          lang: currentLang,
        }),
      });

      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();

      sessionId = data.session_id;
      localStorage.setItem("upicon_session_id", sessionId);

      resolveTypingBubble(typingBubble, data.reply);
      lastBotReply = data.reply;

      // If a read-aloud session was already active (playing or paused)
      // when this new reply arrived, jump straight to reading it instead
      // of the old message - otherwise stay silent until speaker is clicked.
      if (speechState === "speaking") {
        speakText(data.reply);
      } else if (speechState === "paused") {
        // Paused, then a new reply arrived: drop the old paused position so the
        // next speaker click reads the NEW reply from the start.
        speechChunks = [];
        speechChunkIndex = 0;
        speechState = "idle";
        updateSpeakerUI();
      }
    } catch (err) {
      const fallback =
        currentLang === "hi"
          ? "माफ़ कीजिए, अभी जवाब देने में दिक्कत आ रही है। कृपया थोड़ी देर बाद पुनः प्रयास करें।"
          : "Sorry, I'm having trouble responding right now. Please try again in a moment.";
      resolveTypingBubble(typingBubble, fallback);
      console.error(err);
    }
  }

  function sendMessage(text) {
    const trimmed = text.trim();
    if (!trimmed) return;

    addMessage("user", trimmed);
    textInput.value = "";

    const typingBubble = addTypingIndicator();
    sendQueue.push({ trimmed, typingBubble });
    processSendQueue();
  }

  composer.addEventListener("submit", (e) => {
    e.preventDefault();
    sendMessage(textInput.value);
  });

  // ---------------- Voice input ----------------
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  let recognizer = null;
  let isListening = false;

  if (SpeechRecognition) {
    recognizer = new SpeechRecognition();
    recognizer.continuous = false;
    recognizer.interimResults = false;

    recognizer.onresult = (event) => {
      const transcript = event.results[0][0].transcript;
      textInput.value = transcript;
      sendMessage(transcript);
    };

    recognizer.onerror = () => stopListening();
    recognizer.onend = () => stopListening();
  } else {
    micBtn.hidden = true;
  }

  function startListening() {
    if (!recognizer || isListening) return;
    recognizer.lang = currentLang === "hi" ? "hi-IN" : "en-IN";
    isListening = true;
    micBtn.classList.add("listening");
    textInput.placeholder = COPY[currentLang].listening;
    try {
      recognizer.start();
    } catch {
      stopListening();
    }
  }

  function stopListening() {
    isListening = false;
    micBtn.classList.remove("listening");
    textInput.placeholder = COPY[currentLang].placeholder;
    try {
      recognizer && recognizer.stop();
    } catch {
      /* no-op */
    }
  }

  micBtn.addEventListener("click", () => {
    if (isListening) stopListening();
    else startListening();
  });

  // ---------------- Voice output ----------------
  // Speaks in sentence-sized chunks rather than one long utterance so a
  // manual pause can reliably "resume" from the sentence it stopped at -
  // native SpeechSynthesis pause()/resume() is flaky in Chrome on long text.
  const speakerCross = el("upicon-speakerCross");
  const hasSpeechSynthesis = "speechSynthesis" in window;
  if (!hasSpeechSynthesis) speakerBtn.hidden = true;

  let currentUtteranceInfo = null;
  let speechCharOffset = 0; // chars already read of the current sentence
  const SPEECH_CHARS_PER_SEC = 14; // fallback pace estimate when no boundary events
  function splitIntoSpeechChunks(text) {
    return text
      .split(/(?<=[.!?।])\s+/)
      .map((s) => s.trim())
      .filter(Boolean);
  }

  function updateSpeakerUI() {
    speakerBtn.dataset.state = speechState;
    speakerCross.style.display = speechState === "paused" ? "block" : "none";
  }

  function playCurrentSpeechChunk() {
    if (speechChunkIndex >= speechChunks.length) {
      speechCharOffset = 0;
      speechState = "idle";
      updateSpeakerUI();
      return;
    }
    const full = speechChunks[speechChunkIndex];
    const startAt = speechCharOffset; // where in this sentence we (re)start
    const remaining = full.slice(startAt);
    if (!remaining.trim()) {
      speechChunkIndex += 1;
      speechCharOffset = 0;
      playCurrentSpeechChunk();
      return;
    }

    const utterance = new SpeechSynthesisUtterance(remaining);
    utterance.lang = currentLang === "hi" ? "hi-IN" : "en-IN";

    let gotBoundary = false;
    let startedAt = 0;
    utterance.onstart = () => { startedAt = Date.now(); };
    // Track how far into the sentence the voice has actually read, so a
    // pause -> resume continues from there instead of repeating the sentence.
    utterance.onboundary = (e) => {
      gotBoundary = true;
      speechCharOffset = startAt + e.charIndex;
    };
    // Some voices (e.g. network/Hindi ones) never fire boundary events, so
    // remember a rough time-based estimate to fall back on when pausing.
    currentUtteranceInfo = () => {
      if (gotBoundary || !startedAt) return;
      const elapsed = (Date.now() - startedAt) / 1000;
      const est = Math.min(remaining.length, Math.floor(elapsed * SPEECH_CHARS_PER_SEC));
      const nextSpace = remaining.indexOf(" ", est);
      speechCharOffset = startAt + (nextSpace === -1 ? remaining.length : nextSpace + 1);
    };

    utterance.onend = () => {
      // Only auto-advance if still actively speaking - a manual pause (or a
      // new message replacing this one) already changed speechState.
      if (speechState === "speaking") {
        speechChunkIndex += 1;
        speechCharOffset = 0;
        playCurrentSpeechChunk();
      }
    };
    window.speechSynthesis.speak(utterance);
  }

  function speakText(text) {
    window.speechSynthesis.cancel();
    speechChunks = splitIntoSpeechChunks(text);
    speechChunkIndex = 0;
    speechCharOffset = 0;
    speechState = "speaking";
    updateSpeakerUI();
    playCurrentSpeechChunk();
  }

  function pauseSpeech() {
    if (currentUtteranceInfo) currentUtteranceInfo();
    speechState = "paused";
    updateSpeakerUI();
    window.speechSynthesis.cancel();
  }

  function resumeSpeech() {
    if (speechChunkIndex >= speechChunks.length) return;
    speechState = "speaking";
    updateSpeakerUI();
    playCurrentSpeechChunk();
  }

  speakerBtn.addEventListener("click", () => {
    if (!hasSpeechSynthesis) return;
    if (speechState === "idle") {
      if (lastBotReply) speakText(lastBotReply);
    } else if (speechState === "speaking") {
      pauseSpeech();
    } else if (speechState === "paused") {
      resumeSpeech();
    }
  });
})();