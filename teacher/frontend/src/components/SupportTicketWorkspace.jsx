import { useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Camera,
  CheckCircle,
  CircleNotch,
  Clock,
  Headset,
  Image as ImageIcon,
  PaperPlaneTilt,
  UserCircle,
  X,
} from "@phosphor-icons/react";
import {
  getSupportTicketImage,
  markSupportTicketRead,
  replySupportTicket,
  resolveSupportTicket,
} from "../api/support-ticket-api";
import { localizeApiError } from "../api-error-copy";
import { trackProductEvent } from "../analytics/product-analytics";

const copy = (language, english, chinese) => language === "zh" ? chinese : english;

const categoryCopy = {
  TASK_RULES: ["Task rules or completion", "任务规则或完成问题"],
  LESSON_INFO: ["Lesson information", "课程信息"],
  SCORE_OR_REVIEW: ["Score or review result", "积分或审核结果"],
  PRODUCT_FUNCTION: ["Product function", "产品功能"],
  ACCOUNT_LOGIN: ["Account or sign-in", "账号或登录"],
  MEDIA_UPLOAD_CAMERA: ["Upload, video, or camera", "上传、视频或摄像头"],
  OTHER: ["Other", "其他"],
};

function TicketImage({ ticketId, image, language }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    if (!image.fileId || image.deleted) return undefined;
    const controller = new AbortController();
    let active = true;
    getSupportTicketImage(ticketId, image.fileId, controller.signal)
      .then((blob) => {
        if (!active) return;
        setUrl(URL.createObjectURL(blob));
      })
      .catch(() => undefined);
    return () => {
      active = false;
      controller.abort();
    };
  }, [image.deleted, image.fileId, ticketId]);
  useEffect(() => () => url && URL.revokeObjectURL(url), [url]);

  if (image.deleted) {
    return (
      <span className="ticket-image-deleted">
        <ImageIcon size={18} />
        {copy(language, "Screenshot deleted after closure", "截图已在关单后删除")}
      </span>
    );
  }
  return url ? <img src={url} alt={image.filename} /> : (
    <span className="ticket-image-loading"><CircleNotch className="faq-spinner" size={18} /></span>
  );
}

const time = (value, language) =>
  new Date(value).toLocaleString(language === "zh" ? "zh-CN" : "en-US");

export default function SupportTicketWorkspace({
  tickets,
  language,
  loading,
  error,
  onRefresh,
  onUpdated,
}) {
  const [selectedId, setSelectedId] = useState("");
  const [mobileDetailOpen, setMobileDetailOpen] = useState(false);
  const [replyOpen, setReplyOpen] = useState(false);
  const [reply, setReply] = useState("");
  const [images, setImages] = useState([]);
  const [saving, setSaving] = useState(false);
  const [actionError, setActionError] = useState("");
  const selected = useMemo(
    () => tickets.find((ticket) => ticket.ticketId === selectedId) || null,
    [selectedId, tickets],
  );

  const openTicket = async (ticket) => {
    setSelectedId(ticket.ticketId);
    setMobileDetailOpen(true);
    setReplyOpen(false);
    setActionError("");
    trackProductEvent("SUPPORT_TICKET_OPENED", {
      properties: {
        supportCategory: ticket.secondaryCategory,
        supportStatus: ticket.status,
        result: "OPENED",
      },
    });
    if (!ticket.unread) return;
    onUpdated?.({ ...ticket, unread: false });
    try {
      const updated = await markSupportTicketRead(ticket.ticketId);
      onUpdated?.(updated);
      trackProductEvent("SUPPORT_TICKET_REPLY_READ", {
        properties: { supportStatus: updated.status, result: "SUCCESS" },
      });
    } catch {
      onUpdated?.(ticket);
    }
  };

  const resolved = async () => {
    if (!selected || saving) return;
    setSaving(true);
    setActionError("");
    try {
      const updated = await resolveSupportTicket(selected.ticketId, selected.rowVersion);
      onUpdated?.(updated);
      trackProductEvent("SUPPORT_TICKET_RESOLUTION_CONFIRMED", {
        properties: { supportStatus: "CLOSED", result: "RESOLVED" },
      });
    } catch (caught) {
      setActionError(localizeApiError(
        caught,
        language,
        copy(language, "Unable to close this ticket.", "暂时无法关闭工单，请刷新后重试。"),
      ));
    } finally {
      setSaving(false);
    }
  };

  const sendReply = async (event) => {
    event.preventDefault();
    if (!selected || !reply.trim() || saving) return;
    setSaving(true);
    setActionError("");
    try {
      const updated = await replySupportTicket(selected.ticketId, {
        description: reply.trim(),
        rowVersion: selected.rowVersion,
        images,
      });
      onUpdated?.(updated);
      setReply("");
      setImages([]);
      setReplyOpen(false);
      trackProductEvent("SUPPORT_TICKET_FOLLOWUP_SUBMITTED", {
        properties: {
          supportCategory: selected.secondaryCategory,
          attachmentCount: images.length,
          result: "SUCCESS",
        },
      });
    } catch (caught) {
      setActionError(localizeApiError(
        caught,
        language,
        copy(language, "Unable to send your follow-up.", "补充内容暂时无法提交，请刷新后重试。"),
      ));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className={`messages-workspace ticket-workspace ${mobileDetailOpen ? "mobile-detail-open" : ""}`}>
      <div className="message-list-panel">
        <div className="message-list-toolbar">
          <div>
            <h2>{copy(language, "My submitted tickets", "我提交的工单")}</h2>
            <span>{copy(language, `${tickets.length} tickets`, `共 ${tickets.length} 条`)}</span>
          </div>
          <button className="ticket-refresh" type="button" onClick={onRefresh} disabled={loading}>
            {loading ? <CircleNotch className="faq-spinner" size={16} /> : <Clock size={16} />}
            {copy(language, "Refresh", "刷新")}
          </button>
        </div>
        <div className="message-list">
          {tickets.map((ticket) => {
            const category = categoryCopy[ticket.secondaryCategory] || categoryCopy.OTHER;
            const lastMessage = ticket.messages.at(-1);
            return (
              <button
                className={`message-list-item ${ticket.unread ? "is-unread" : "is-read"} ${selectedId === ticket.ticketId ? "is-selected" : ""}`}
                key={ticket.ticketId}
                type="button"
                onClick={() => openTicket(ticket)}
              >
                <span className="message-status-dot" />
                <span className="message-list-copy">
                  <span className={`ticket-status is-${ticket.status.toLowerCase()}`}>
                    {ticket.status === "CLOSED"
                      ? copy(language, "Closed", "已关闭")
                      : ticket.status === "WAITING_TEACHER"
                        ? copy(language, "Replied", "运营已回复")
                        : copy(language, "Pending reply", "等待运营回复")}
                  </span>
                  <strong>{copy(language, category[0], category[1])}</strong>
                  <p>{lastMessage?.content || ""}</p>
                  <time><Clock size={14} />{time(lastMessage?.createdAt || ticket.updatedAt, language)}</time>
                </span>
                <ArrowRight className="message-list-arrow" size={20} />
              </button>
            );
          })}
          {tickets.length === 0 && (
            <div className={`message-empty-state ${error ? "is-error" : ""}`}>
              <Headset size={42} weight="duotone" />
              <strong>
                {error
                  ? copy(language, "Tickets are temporarily unavailable", "工单暂时无法加载")
                  : copy(language, "No tickets yet", "还没有提交过工单")}
              </strong>
              <p>
                {error
                  ? error
                  : copy(language, "Use Help to contact support.", "需要运营协助时，可以从“帮助”提交工单。")}
              </p>
            </div>
          )}
        </div>
      </div>

      <article className={`message-detail-panel ticket-detail ${selected ? "has-message" : "is-empty"}`}>
        {selected ? (
          <>
            <button className="message-mobile-back" type="button" onClick={() => setMobileDetailOpen(false)}>
              <ArrowLeft size={20} />{copy(language, "Back to tickets", "返回工单列表")}
            </button>
            <div className="ticket-detail-header">
              <span><Headset size={25} weight="duotone" /></span>
              <div>
                <small>{copy(language, "SUPPORT TICKET", "工单")}</small>
                <h2>{copy(language, ...(categoryCopy[selected.secondaryCategory] || categoryCopy.OTHER))}</h2>
                <time>{time(selected.createdAt, language)}</time>
              </div>
            </div>
            <div className="ticket-thread">
              {selected.messages.map((message) => (
                <article className={`ticket-message is-${message.sender.toLowerCase()}`} key={message.messageId}>
                  <span>{message.sender === "TEACHER" ? <UserCircle size={20} /> : <Headset size={19} />}</span>
                  <div>
                    <small>{message.sender === "TEACHER" ? copy(language, "You", "我") : copy(language, "Support", "运营")}</small>
                    <p>{message.content}</p>
                    {message.images.length > 0 && (
                      <div className="ticket-message-images">
                        {message.images.map((image) => (
                          <TicketImage key={image.fileId || image.filename} ticketId={selected.ticketId} image={image} language={language} />
                        ))}
                      </div>
                    )}
                    <time>{time(message.createdAt, language)}</time>
                  </div>
                </article>
              ))}
            </div>

            {selected.status === "WAITING_TEACHER" && !replyOpen && (
              <div className="ticket-resolution">
                <strong>{copy(language, "Has your issue been resolved?", "问题是否已经解决？")}</strong>
                <p>{copy(language, "If not, add more details and the ticket will return to Support.", "如果未解决，可以继续补充，工单会重新交给运营处理。")}</p>
                <div>
                  <button type="button" onClick={resolved} disabled={saving}>
                    <CheckCircle size={18} weight="fill" />{copy(language, "Resolved", "已解决")}
                  </button>
                  <button type="button" onClick={() => setReplyOpen(true)} disabled={saving}>
                    {copy(language, "Not resolved", "未解决")}
                  </button>
                </div>
              </div>
            )}
            {selected.status === "WAITING_OPERATOR" && (
              <div className="ticket-waiting"><Clock size={18} />{copy(language, "Pending reply", "正在等待运营回复")}</div>
            )}
            {selected.status === "CLOSED" && (
              <div className="ticket-closed"><CheckCircle size={18} weight="fill" />{copy(language, "This ticket is closed", "该工单已关闭")}</div>
            )}
            {replyOpen && (
              <form className="ticket-reply-form" onSubmit={sendReply}>
                <div>
                  <strong>{copy(language, "Add more details", "继续补充")}</strong>
                  <button type="button" onClick={() => setReplyOpen(false)}><X size={17} /></button>
                </div>
                <textarea
                  rows={4}
                  maxLength={5000}
                  value={reply}
                  onChange={(event) => setReply(event.target.value)}
                  placeholder={copy(language, "Describe what still needs to be resolved.", "请说明仍未解决的问题。")}
                  required
                />
                <div className="ticket-reply-actions">
                  <label>
                    <Camera size={17} />{copy(language, `${images.length}/3 screenshots`, `${images.length}/3 张截图`)}
                    <input
                      type="file"
                      accept="image/jpeg,image/png,image/webp"
                      multiple
                      onChange={(event) => {
                        setImages((current) => [...current, ...(event.target.files || [])].slice(0, 3));
                        event.target.value = "";
                      }}
                    />
                  </label>
                  <button type="submit" disabled={saving || !reply.trim()}>
                    {saving ? <CircleNotch className="faq-spinner" size={17} /> : <PaperPlaneTilt size={17} weight="fill" />}
                    {copy(language, "Send", "提交")}
                  </button>
                </div>
              </form>
            )}
            {actionError && <div className="faq-error" role="alert">{actionError}</div>}
          </>
        ) : (
          <div className="message-detail-placeholder">
            <span><Headset size={34} weight="duotone" /></span>
            <h2>{copy(language, "Open a ticket", "选择一条工单查看")}</h2>
            <p>{copy(language, "Replies remain here with the full conversation.", "运营回复和后续沟通会完整保留在这里。")}</p>
          </div>
        )}
      </article>
    </section>
  );
}
