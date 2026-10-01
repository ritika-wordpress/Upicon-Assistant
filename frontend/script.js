// ---------------------------------------------------------------------
// UPICON Assistant — frontend logic
// Talks to the FastAPI backend's /chat endpoint. Voice input uses the
// browser's native Web Speech API (SpeechRecognition) — no audio upload
// needed for the common case; voice output uses SpeechSynthesis.
// ---------------------------------------------------------------------

const API_BASE = "http://127.0.0.1:8000"; // change to your deployed backend URL

// ---------------- Site-wide translation (self-contained) ----------------
// Same approach as embed.js: the widget sets this up itself so the
// language toggle can drive the whole page.
(function injectGoogleTranslate() {
  if (document.getElementById("google_translate_element")) return;

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
})();

const el = (id) => document.getElementById(id);

const launcher = el("launcher");
const launcherDot = el("launcherDot");
const panel = el("panel");
const closeBtn = el("closeBtn");
const messagesEl = el("messages");
const composer = el("composer");
const textInput = el("textInput");
const sendBtn = el("sendBtn");
const micBtn = el("micBtn");
const micRing = el("micRing");
const speakerBtn = el("speakerBtn");
const headerSub = el("headerSub");
const headerTitle = el("headerTitle");
const suggestionsEl = el("suggestions");
const langToggle = el("langToggle");

// Session id lives in localStorage (persists across page loads). Language
// choice is also remembered in localStorage now (shared with the rest of
// the site — see setLanguage/translateSite below), falling back to the
// visitor's browser language, then English.
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
  langToggle.querySelectorAll(".lang-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.lang === lang);
  });
  headerTitle.textContent = COPY[lang].name;
  panel.setAttribute("aria-label", COPY[lang].name + (lang === "hi" ? " चैट" : " chat"));
  launcher.setAttribute("aria-label", (lang === "hi" ? "खोलें: " : "Open ") + COPY[lang].name);
  document.title = COPY[lang].name;
  headerSub.textContent = COPY[lang].online;
  textInput.placeholder = COPY[lang].placeholder;
  suggestionsEl.querySelectorAll(".chip[data-chip]").forEach((chip) => {
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

  // Lets the host page's own script react without a reload, if it has one
  // listening (e.g. a React/Vue i18n setup, or a translations JSON swap).
  window.dispatchEvent(new CustomEvent("upicon:languagechange", { detail: { lang } }));

  // If the page has Google's Website Translator widget embedded, the
  // standard way to drive it programmatically is the "googtrans" cookie
  // plus a reload. Only do this when that widget is actually present, so
  // sites without it don't get an unnecessary reload.
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

// ---------------- Panel open/close ----------------
applyLangToUI(currentLang);

function openPanel() {
  panel.classList.remove("hidden");
  launcher.classList.add("launcher-hidden"); // clicking the launcher hides it while the chat is open
  launcherDot.hidden = true;
  sessionStorage.setItem("upicon_panel_open", "1");

  // Stop the launcher's hover voiceover if it's still playing when the
  // chat is opened, so it doesn't keep talking over the opened panel.
  stopLauncherVoiceover();

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
// Plays a short "what is this?" voiceover in whichever language the page
// is currently in. Note: browsers only allow audio-with-sound to autoplay
// after the visitor has interacted with the page at least once (a hover
// alone doesn't count in Chrome) - so on a fresh page load the very first
// hover may be silently blocked until after a click/keypress anywhere.
const _voiceClips = {
  en: new Audio("assets/launcher-voice-en.mp3"),
  hi: new Audio("assets/launcher-voice-hi.mp3"),
};

function stopLauncherVoiceover() {
  Object.values(_voiceClips).forEach((a) => {
    a.pause();
    a.currentTime = 0;
  });
}

launcher.addEventListener("mouseenter", () => {
  // Don't play the "what is this?" voiceover once the chat is already
  // open - the launcher is visually hidden then, but guard anyway in
  // case it's still reachable (e.g. focus/hover during the close
  // animation).
  if (!panel.classList.contains("hidden")) return;

  const clip = _voiceClips[currentLang] || _voiceClips.en;
  Object.values(_voiceClips).forEach((a) => {
    if (a !== clip) {
      a.pause();
      a.currentTime = 0;
    }
  });
  clip.currentTime = 0;
  clip.play().catch(() => {
    /* blocked by autoplay policy until the visitor interacts once - no-op */
  });
});

launcher.addEventListener("mouseleave", () => {
  const clip = _voiceClips[currentLang] || _voiceClips.en;
  clip.pause();
  clip.currentTime = 0;
});

closeBtn.addEventListener("click", () => {
  panel.classList.add("hidden");
  launcher.classList.remove("launcher-hidden"); // bring the launcher back on close
  sessionStorage.setItem("upicon_panel_open", "0");
  stopSpeech(); // don't keep reading a reply aloud once the chat is closed
});

// Re-open automatically if we're back after a translateSite() reload
// (see setLanguage) so switching language doesn't look like the widget
// closing on the visitor.
if (sessionStorage.getItem("upicon_panel_open") === "1") {
  openPanel();
}

// ---------------- Header language toggle ----------------
langToggle.addEventListener("click", (e) => {
  const btn = e.target.closest(".lang-btn");
  if (!btn) return;
  setLanguage(btn.dataset.lang);
});

// ---------------- Suggestion chips ----------------
suggestionsEl.addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  const q = currentLang === "hi" ? chip.dataset.qHi : chip.dataset.qEn;
  sendMessage(q || chip.dataset.qEn);
});

// ---------------- Message rendering ----------------
// Renders a bubble only — does NOT persist. Used both by addMessage() below
// and when replaying messageLog after a language-toggle reload, so restored
// history doesn't get re-appended to the log on every restore.
function addMessageDOM(role, text) {
  const bubble = document.createElement("div");
  bubble.className = `msg ${role}`;

  if (role === "bot" && window.marked && window.DOMPurify) {
    // Bot replies come back as Markdown (bold, bullet lists, etc.) - parse
    // it to HTML and sanitize before inserting, since this text originates
    // from an LLM and must never be trusted as-is.
    const rawHtml = marked.parse(text);
    bubble.innerHTML = DOMPurify.sanitize(rawHtml);
  } else {
    // User messages (and bot messages if the Markdown libs failed to load)
    // stay as plain text - no parsing needed/safe either way.
    bubble.textContent = text;
  }

  messagesEl.appendChild(bubble);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return bubble;
}

// Renders AND persists — this is what every real send/reply should call.
// Persisting to sessionStorage means a language-toggle reload (see
// translateSite()) can restore the conversation instead of the panel
// coming back empty.
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
  bubble.className = "msg bot";
  if (window.marked && window.DOMPurify) {
    bubble.innerHTML = DOMPurify.sanitize(marked.parse(text));
  } else {
    bubble.textContent = text;
  }
  typingBubble.replaceWith(bubble);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return bubble;
}

function addTypingIndicator() {
  const bubble = document.createElement("div");
  bubble.className = "msg bot typing";
  bubble.innerHTML = "<span></span><span></span><span></span>";
  messagesEl.appendChild(bubble);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return bubble;
}

// ---------------- Sending messages ----------------
// Buttons (send/mic/speaker/chips) stay live even while a reply is loading -
// you can ask another question right away. Requests still go to the backend
// one at a time, in the order asked, so answers can never come back
// interleaved; each one replaces its own typing indicator the moment it's
// ready (see resolveTypingBubble), so it lands right under its own question
// even if a later question is still waiting below it.
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
  const fallbackText =
    currentLang === "hi"
      ? "माफ़ कीजिए, अभी जवाब देने में दिक्कत आ रही है। कृपया थोड़ी देर बाद पुनः प्रयास करें।"
      : "Sorry, I'm having trouble responding right now. Please try again in a moment.";

  const render = (bubble, text) => {
    if (window.marked && window.DOMPurify) {
      bubble.innerHTML = window.DOMPurify.sanitize(window.marked.parse(text));
    } else {
      bubble.textContent = text;
    }
    messagesEl.scrollTop = messagesEl.scrollHeight;
  };

  let bubble = null;   // created on first streamed token
  let fullText = "";
  let extras = []; // follow-up messages (top item, link) sent as separate bubbles
  // Reveal the reply at a steady, readable pace instead of dumping each network
  // chunk the moment it arrives. Lower STREAM_CHARS_PER_SEC = slower typing.
  const STREAM_CHARS_PER_SEC = 30;
  let shown = 0;
  let pacer = null;
  let carry = 0;
  const pacerTick = () => {
    if (!bubble) return;
    const backlog = fullText.length - shown;
    if (backlog <= 0) return;
    // steady pace, plus a gentle catch-up so a long reply never lags far behind
    carry += STREAM_CHARS_PER_SEC * 0.04;
    let step = Math.floor(carry);
    carry -= step;
    step += Math.floor(backlog / 200);
    if (step < 1) return;
    let next = Math.min(fullText.length, shown + step);
    // finish the current word so text doesn't appear cut mid-word
    while (next < fullText.length && !/\s/.test(fullText[next - 1]) && next - shown < step + 12) next++;
    shown = next;
    render(bubble, fullText.slice(0, shown));
  };
  const startPacer = () => { if (!pacer) pacer = setInterval(pacerTick, 40); };
  const stopPacer = () => { if (pacer) { clearInterval(pacer); pacer = null; } };
  const drainPacer = () => new Promise((resolve) => {
    if (!bubble) return resolve();
    startPacer();
    const wait = setInterval(() => {
      if (shown >= fullText.length) { clearInterval(wait); stopPacer(); resolve(); }
    }, 40);
  });

  try {
    const resp = await fetch(`${API_BASE}/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: trimmed, session_id: sessionId, lang: currentLang }),
    });
    if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let streamError = false;

    const handleLine = (line) => {
      if (!line.trim()) return;
      let evt;
      try { evt = JSON.parse(line); } catch { return; }

      if (evt.session_id) {
        sessionId = evt.session_id;
        localStorage.setItem("upicon_session_id", sessionId);
      }
      if (evt.error) streamError = true;
      if (evt.new_message) { extras.push(""); return; }
      if (evt.delta && extras.length) { extras[extras.length - 1] += evt.delta; return; }
      if (evt.delta) {
        fullText += evt.delta;
        if (!bubble) {
          bubble = document.createElement("div");
          bubble.className = "msg bot";
          typingBubble.replaceWith(bubble); // swap dots -> live bubble, same spot
        }
        startPacer();
      }
    };

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop(); // keep any partial line for the next chunk
      lines.forEach(handleLine);
    }
    if (buf) handleLine(buf);

    if (!fullText) throw new Error(streamError ? "stream error" : "empty reply");

    await drainPacer(); // let the typing finish at its steady pace
    render(bubble, fullText); // final render with the complete text
    messageLog.push({ role: "bot", text: fullText });
    sessionStorage.setItem("upicon_messages", JSON.stringify(messageLog));
    lastBotReply = fullText;
    // Follow-up messages (top item, then link): each in its own bubble,
    // shown after a short "typing" pause so they read as separate messages.
    for (const extra of extras) {
      const extraText = extra.trim();
      if (!extraText) continue;
      const typing = addTypingIndicator();
      await new Promise((r) => setTimeout(r, 700));
      const extraBubble = document.createElement("div");
      extraBubble.className = "msg bot";
      typing.replaceWith(extraBubble);
      render(extraBubble, extraText);
      messageLog.push({ role: "bot", text: extraText });
      sessionStorage.setItem("upicon_messages", JSON.stringify(messageLog));
    }

    if (speechState === "speaking") {
      speakText(fullText);
    } else if (speechState === "paused") {
      speechChunks = [];
      speechChunkIndex = 0;
      speechState = "idle";
      updateSpeakerUI();
    }
  } catch (err) {
    console.error(err);
    if (bubble && fullText) {
      // Stream broke midway: keep what arrived instead of wiping it.
      stopPacer();
      render(bubble, fullText);
      messageLog.push({ role: "bot", text: fullText });
      sessionStorage.setItem("upicon_messages", JSON.stringify(messageLog));
      lastBotReply = fullText;
    } else {
      resolveTypingBubble(typingBubble, fallbackText);
    }
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

// ---------------- Voice input (Web Speech API) ----------------
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
  micBtn.hidden = true; // gracefully hide mic button on unsupported browsers
}

function startListening() {
  if (!recognizer || isListening) return;
  // Stop the bot talking the moment the user taps the mic - otherwise
  // the recognizer would also pick up the bot's own voice.
  if (hasSpeechSynthesis && speechState !== "idle") stopSpeech();
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

// ---------------- Voice output (SpeechSynthesis) ----------------
// Speaks in sentence-sized chunks rather than one long utterance so a
// manual pause can reliably "resume" from the sentence it stopped at -
// native SpeechSynthesis pause()/resume() is flaky in Chrome on long text.
const speakerCross = document.getElementById("speakerCross");
const hasSpeechSynthesis = "speechSynthesis" in window;
if (!hasSpeechSynthesis) speakerBtn.hidden = true;

// Bot replies are Markdown (the chat bubble renders them via marked.js).
// Speech should read the actual content, not the Markdown syntax around it
// (**bold**, `code`, [links](url), # headers, - bullets, etc.) - but must
// leave alone any character that's part of real content (emails, phone
// numbers, ₹/%, etc.), so this only targets Markdown's own punctuation.
let currentUtteranceInfo = null;
let speechCharOffset = 0; // chars already read of the current sentence
const SPEECH_CHARS_PER_SEC = 14; // fallback pace estimate when no boundary events
function stripMarkdownForSpeech(text) {
  return text
    // Emoji: some TTS voices (esp. Hindi ones) narrate a description
    // instead of skipping them (👋 -> "hath hilana"/"waving hand") - strip
    // them (and any attached variation-selector/ZWJ/skin-tone sequence)
    // before speaking. Visual chat bubble is untouched - only the audio.
    .replace(/\p{Extended_Pictographic}(\uFE0F|\u200D\p{Extended_Pictographic})*/gu, "")
    .replace(/[\u{1F1E6}-\u{1F1FF}]/gu, "") // regional-indicator flag letters
    .replace(/```[\s\S]*?```/g, " ") // fenced code blocks
    .replace(/`([^`]+)`/g, "$1") // inline code
    .replace(/!\[([^\]]*)\]\([^)]+\)/g, "$1") // images -> alt text
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1") // links -> link text
    .replace(/(\*\*\*|___)(.+?)\1/g, "$2") // bold+italic
    .replace(/(\*\*|__)(.+?)\1/g, "$2") // bold
    .replace(/(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)/g, "$1") // *italic*
    .replace(/^#{1,6}\s+/gm, "") // headers
    .replace(/^>\s?/gm, "") // blockquotes
    .replace(/^([-*_]){3,}\s*$/gm, "") // horizontal rules
    .replace(/^[ \t]*[-*+]\s+/gm, "") // bullet list markers
    .replace(/[*_~`]/g, "") // any leftover stray Markdown punctuation
    .replace(/[ \t]{2,}/g, " ")
    .trim();
}

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
  speechChunks = splitIntoSpeechChunks(stripMarkdownForSpeech(text));
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
  window.speechSynthesis.cancel(); // stops the in-progress chunk; resume replays it
}

function stopSpeech() {
  // Full stop (vs. pauseSpeech): resets back to idle so a later reopen
  // doesn't try to resume mid-reply.
  if (!hasSpeechSynthesis) return;
  window.speechSynthesis.cancel();
  speechState = "idle";
  speechChunkIndex = 0;
  speechCharOffset = 0;
  updateSpeakerUI();
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