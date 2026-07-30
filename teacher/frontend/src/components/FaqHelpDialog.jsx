import { useEffect, useRef, useState } from "react";
import "../faq-help.css";
import {
  ChatCircleDots,
  Check,
  CircleNotch,
  Headset,
  PaperPlaneTilt,
  Question,
  Sparkle,
  ThumbsDown,
  ThumbsUp,
  UserCircle,
  X,
} from "@phosphor-icons/react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  askFaqQuestion,
  createFaqConversation,
  getFaqConversation,
  newFaqMessageKey,
  saveFaqFeedback,
} from "../api/faq-api";
import { localizeApiError } from "../api-error-copy";
import { toFaqDisplayMarkdown } from "../faq-message-format";
import { trackProductEvent } from "../analytics/product-analytics";
import SupportTicketForm from "./SupportTicketForm";
import { Toki } from "./UI";

const copy = (language, english, chinese) => language === "zh" ? chinese : english;

const mergeMessages = (current, incoming) => {
  const byId = new Map(current.map((message) => [message.id, message]));
  incoming.forEach((message) => byId.set(message.id, message));
  return [...byId.values()].sort((left, right) =>
    new Date(left.createdAt).getTime() - new Date(right.createdAt).getTime()
  );
};

function AssistantAnswer({ body }) {
  const displayBody = toFaqDisplayMarkdown(body);
  return (
    <div className="faq-message-rich-text">
      <Markdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
        }}
      >
        {displayBody}
      </Markdown>
    </div>
  );
}

function Feedback({ message, language, saving, onFeedback, onEscalate }) {
  if (message.feedback) {
    return (
      <div className="faq-feedback-recorded">
        <Check size={14} weight="bold" />
        {message.feedback.resolved
          ? copy(language, "Marked as helpful", "已标记为有帮助")
          : copy(language, "Feedback received", "反馈已收到")}
        {!message.feedback.resolved && (
          <button type="button" onClick={onEscalate}>
            {copy(language, "Submit a ticket", "提交工单")}
          </button>
        )}
      </div>
    );
  }
  return (
    <div className="faq-feedback">
      <span>{copy(language, "Did this answer help?", "这个回答有帮助吗？")}</span>
      <button type="button" disabled={saving} onClick={() => onFeedback(message.id, true)}>
        <ThumbsUp size={15} />
        {copy(language, "Yes", "有")}
      </button>
      <button type="button" disabled={saving} onClick={() => onFeedback(message.id, false)}>
        <ThumbsDown size={15} />
        {copy(language, "Not yet", "暂时没有")}
      </button>
    </div>
  );
}

export default function FaqHelpDialog({
  open,
  onClose,
  language,
  entrySource = "UNKNOWN",
  supportContext,
  taskOptions,
  lessonOptions,
  onTicketCreated,
}) {
  const [mode, setMode] = useState("CHOICE");
  const [ticketCreated, setTicketCreated] = useState(null);
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [error, setError] = useState("");
  const [feedbackSaving, setFeedbackSaving] = useState({});
  const conversationIdRef = useRef("");
  const pendingRequestRef = useRef(null);
  const endRef = useRef(null);
  const formRef = useRef(null);
  const textareaRef = useRef(null);
  const previewTicket = import.meta.env.DEV
    && new URLSearchParams(window.location.search).get("tokiPreview") === "ticketReceived";

  useEffect(() => {
    if (!open) return undefined;
    setMode(previewTicket ? "TICKET" : "CHOICE");
    setTicketCreated(previewTicket ? { ticketCode: "TIDE-PREVIEW" } : null);
    const close = (event) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [entrySource, open, onClose, previewTicket]);

  useEffect(() => {
    if (!open || mode !== "FAQ") return undefined;
    trackProductEvent("FAQ_OPENED", {
      properties: { entrySource },
    });
    const controller = new AbortController();
    let active = true;
    setHistoryLoading(true);
    setError("");
    createFaqConversation()
      .then((conversation) => {
        conversationIdRef.current = conversation.conversationId;
        return getFaqConversation(conversation.conversationId, controller.signal);
      })
      .then((conversation) => {
        if (!active) return;
        setMessages(conversation.messages || []);
        window.setTimeout(() => textareaRef.current?.focus(), 0);
      })
      .catch((caught) => {
        if (!active || caught.name === "AbortError") return;
        setError(localizeApiError(
          caught,
          language,
          copy(language, "Unable to load FAQ history.", "FAQ 历史记录暂时无法加载。"),
        ));
      })
      .finally(() => active && setHistoryLoading(false));
    return () => {
      active = false;
      controller.abort();
    };
  }, [entrySource, open, language, mode]);

  useEffect(() => {
    if (!open || mode !== "FAQ") return;
    endRef.current?.scrollIntoView({ behavior: messages.length > 2 ? "smooth" : "auto" });
  }, [messages, open, historyLoading, mode]);

  if (!open) return null;

  const ask = async (event) => {
    event.preventDefault();
    const trimmed = question.trim();
    if (trimmed.length < 2 || loading || historyLoading) return;
    setLoading(true);
    setError("");
    trackProductEvent("FAQ_QUESTION_SUBMITTED", {
      properties: {
        entrySource,
        faqCategory: "UNCLASSIFIED",
        itemCount: trimmed.length,
        result: "SUBMITTED",
      },
    });
    try {
      if (!conversationIdRef.current) {
        const conversation = await createFaqConversation();
        conversationIdRef.current = conversation.conversationId;
      }
      let pending = pendingRequestRef.current;
      if (
        !pending ||
        pending.question !== trimmed ||
        pending.conversationId !== conversationIdRef.current
      ) {
        pending = {
          conversationId: conversationIdRef.current,
          question: trimmed,
          idempotencyKey: newFaqMessageKey(),
        };
        pendingRequestRef.current = pending;
      }
      const response = await askFaqQuestion(
        pending.conversationId,
        pending.question,
        pending.idempotencyKey,
      );
      setMessages((current) => mergeMessages(current, [response.teacherMessage, response.answer]));
      pendingRequestRef.current = null;
      setQuestion("");
    } catch (caught) {
      setError(localizeApiError(
        caught,
        language,
        copy(language, "Unable to get an answer. Try again.", "暂时无法获取答案，请重试。"),
      ));
    } finally {
      setLoading(false);
    }
  };

  const submitFeedback = async (messageId, resolved) => {
    setFeedbackSaving((current) => ({ ...current, [messageId]: true }));
    setError("");
    try {
      const feedback = await saveFaqFeedback(
        messageId,
        resolved,
        resolved ? undefined : "ANSWER_NOT_HELPFUL",
      );
      setMessages((current) => current.map((message) =>
        message.id === messageId ? { ...message, feedback } : message
      ));
      trackProductEvent("FAQ_FEEDBACK_SUBMITTED", {
        properties: {
          entrySource,
          feedbackResolved: resolved,
          result: "SUCCESS",
        },
      });
    } catch (caught) {
      setError(localizeApiError(
        caught,
        language,
        copy(language, "Unable to save feedback.", "反馈保存失败，请稍后重试。"),
      ));
    } finally {
      setFeedbackSaving((current) => ({ ...current, [messageId]: false }));
    }
  };

  const suggestions = language === "zh"
    ? ["遇到课堂技术问题应该怎么办？", "在哪里可以获取 TESOL 证书？", "如何提交 Lesson Memo？"]
    : [
      "What should I do if I have technical issues?",
      "Where can I get my TESOL certificate?",
      "How do I post a lesson memo?",
    ];
  const startFaq = () => {
    trackProductEvent("HELP_ROUTE_SELECTED", {
      properties: { entrySource, interaction: "AI_FAQ", result: "OPENED" },
    });
    setMode("FAQ");
  };
  const startTicket = (prefill = "") => {
    trackProductEvent("HELP_ROUTE_SELECTED", {
      properties: { entrySource, interaction: "SUPPORT_TICKET", result: "OPENED" },
    });
    if (prefill) setQuestion(prefill);
    setMode("TICKET");
  };

  return (
    <div className="help-backdrop faq-help-backdrop" onMouseDown={onClose}>
      <section
        className={`faq-help-dialog is-${mode.toLowerCase()}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="faq-help-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="faq-help-header">
          <span className="faq-help-mark"><ChatCircleDots size={23} weight="duotone" /></span>
          <span>
            <small>
              {mode === "FAQ"
                ? copy(language, "APPROVED KNOWLEDGE", "已审核知识库")
                : copy(language, "TIDE SUPPORT", "TIDE 帮助")}
            </small>
            <h2 id="faq-help-title">
              {mode === "CHOICE"
                ? copy(language, "How can we help?", "你需要哪种帮助？")
                : mode === "TICKET"
                  ? copy(language, "Contact operations", "联系运营")
                  : copy(language, "TIDE FAQ assistant", "TIDE FAQ 助手")}
            </h2>
          </span>
          <button type="button" onClick={onClose} aria-label={copy(language, "Close FAQ", "关闭 FAQ")}>
            <X size={20} />
          </button>
        </header>

        {mode === "CHOICE" ? (
          <div className="help-route-choice">
            <div className="help-route-intro">
              <h3>{copy(language, "Choose the quickest route", "选择最合适的求助方式")}</h3>
              <p>{copy(language, "Try the FAQ for common questions, or send a ticket when you need an operations reply.", "常见问题可先问 AI；需要运营介入时，可直接提交工单。")}</p>
            </div>
            <button type="button" onClick={startFaq}>
              <span><ChatCircleDots size={25} weight="duotone" /></span>
              <strong>{copy(language, "Ask the AI FAQ", "询问 AI 助手")}</strong>
              <small>{copy(language, "Instant answers from approved content", "从已审核知识库获取即时回答")}</small>
            </button>
            <button type="button" onClick={() => startTicket()}>
              <span><Headset size={25} weight="duotone" /></span>
              <strong>{copy(language, "Submit a support ticket", "提交工单")}</strong>
              <small>{copy(language, "Send screenshots and wait for an operations reply", "可附截图，等待运营回复")}</small>
            </button>
          </div>
        ) : mode === "TICKET" ? (
          ticketCreated ? (
            <div className="support-ticket-success">
              <div className={`support-ticket-success-motion${previewTicket ? " is-preview" : ""}`}>
                <Toki
                  mood="thumb"
                  motion="ticketReceived"
                  loop={previewTicket}
                  alt={copy(language, "Toki confirms the ticket was received", "Toki 确认工单已收到")}
                />
              </div>
              <h3>{copy(language, "Ticket submitted", "工单已提交")}</h3>
              <p>{copy(language, "You can follow replies under Messages → My submitted tickets.", "你可以在“消息 → 我提交的工单”中查看运营回复。")}</p>
              <button type="button" onClick={onClose}>{copy(language, "Done", "完成")}</button>
            </div>
          ) : (
            <SupportTicketForm
              language={language}
              entrySource={entrySource}
              initialDescription={question}
              supportContext={supportContext}
              taskOptions={taskOptions}
              lessonOptions={lessonOptions}
              onBack={() => setMode("CHOICE")}
              onCreated={(ticket) => {
                setTicketCreated(ticket);
                onTicketCreated?.(ticket);
              }}
            />
          )
        ) : (
          <>
        <div className="faq-conversation" aria-live="polite" aria-busy={historyLoading}>
          {historyLoading ? (
            <div className="faq-loading-state">
              <CircleNotch size={25} className="faq-spinner" />
              {copy(language, "Restoring your conversation…", "正在恢复你的对话…")}
            </div>
          ) : messages.length === 0 ? (
            <div className="faq-empty-state">
              <span><Sparkle size={25} weight="duotone" /></span>
              <h3>{copy(language, "Ask about your 51Talk teacher journey", "询问你的 51Talk 教师相关问题")}</h3>
              <p>{copy(
                language,
                "Answers use approved FAQ content only. If there is no reliable match, the question is recorded for review.",
                "回答只依据已审核 FAQ；没有可靠依据时会明确说明，并记录问题供后续补充。",
              )}</p>
              <div className="faq-suggestions">
                {suggestions.map((suggestion) => (
                  <button key={suggestion} type="button" onClick={() => {
                    setQuestion(suggestion);
                    window.setTimeout(() => textareaRef.current?.focus(), 0);
                  }}>
                    <Question size={15} />
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="faq-message-list">
              {messages.map((message) => (
                <article key={message.id} className={`faq-message ${message.role === "TEACHER" ? "is-teacher" : "is-assistant"}`}>
                  <span className="faq-message-avatar">
                    {message.role === "TEACHER" ? <UserCircle size={20} /> : <Sparkle size={18} weight="duotone" />}
                  </span>
                  <div>
                    {message.role === "ASSISTANT"
                      ? <AssistantAnswer body={message.body} />
                      : <p>{message.body}</p>}
                    {message.role === "ASSISTANT" && (
                      <>
                        {message.reasonCode !== "FAQ_CLARIFICATION_NEEDED" && (
                          <Feedback
                            message={message}
                            language={language}
                            saving={feedbackSaving[message.id] === true}
                            onFeedback={submitFeedback}
                            onEscalate={() => startTicket(
                              [...messages].reverse().find((item) => item.role === "TEACHER")?.body || "",
                            )}
                          />
                        )}
                      </>
                    )}
                  </div>
                </article>
              ))}
            </div>
          )}
          <div ref={endRef} />
        </div>

        <form ref={formRef} className="faq-composer" onSubmit={ask}>
          {error && <div className="faq-error" role="alert">{error}</div>}
          <label>
            <span className="sr-only">{copy(language, "Your FAQ question", "你的 FAQ 问题")}</span>
            <textarea
              ref={textareaRef}
              value={question}
              minLength={2}
              maxLength={2000}
              rows={2}
              disabled={historyLoading}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  formRef.current?.requestSubmit();
                }
              }}
              placeholder={copy(language, "Type your question…", "输入你的问题…")}
            />
          </label>
          <button
            type="submit"
            disabled={historyLoading || loading || question.trim().length < 2}
            aria-label={copy(language, "Send question", "发送问题")}
          >
            {loading ? <CircleNotch size={21} className="faq-spinner" /> : <PaperPlaneTilt size={21} weight="fill" />}
          </button>
          <small>{copy(language, "Enter to send · Shift + Enter for a new line", "按 Enter 发送 · Shift + Enter 换行")}</small>
        </form>
          </>
        )}
      </section>
    </div>
  );
}
