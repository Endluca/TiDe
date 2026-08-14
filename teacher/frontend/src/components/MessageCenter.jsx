import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  Bell,
  CheckCircle,
  Clock,
  EnvelopeOpen,
  ListChecks,
} from "@phosphor-icons/react";
import { resolveMessageActionRoute } from "../message-action.js";
import SupportTicketWorkspace from "./SupportTicketWorkspace.jsx";

const copy = (language, english, chinese) =>
  language === "zh" ? chinese : english;

const messageTitle = (message, language) =>
  language === "zh" ? message.titleZh : message.title;

const messageBody = (message, language) =>
  language === "zh" ? message.bodyZh : message.body;

const messageTime = (message, language) =>
  language === "zh" ? message.issuedAtZh : message.issuedAt;

function MessageType({ message, language }) {
  const isTask = message.type === "TASK";
  return (
    <span className={`message-type ${isTask ? "is-task" : "is-text"}`}>
      {isTask ? <ListChecks size={15} weight="fill" /> : <EnvelopeOpen size={15} weight="fill" />}
      {copy(language, isTask ? "Task" : "Update", isTask ? "任务提醒" : "文字通知")}
    </span>
  );
}

export default function MessageCenter({
  messages,
  tasks,
  language,
  onRead,
  onAction,
  filter,
  onFilterChange,
  totalCount,
  unreadCount,
  nextCursor,
  loading,
  onLoadMore,
  error,
  mobileNav,
  supportTickets = [],
  supportTicketsLoading = false,
  supportTicketsError = "",
  onSupportTicketsRefresh,
  onSupportTicketUpdated,
}) {
  const [view, setView] = useState("SYSTEM");
  const [selectedId, setSelectedId] = useState("");
  const [mobileDetailOpen, setMobileDetailOpen] = useState(false);
  const selected = messages.find((message) => message.id === selectedId);
  const filteredMessages = useMemo(
    () => messages.filter((message) => filter === "ALL" || !message.read),
    [filter, messages],
  );

  const openMessage = (message) => {
    setSelectedId(message.id);
    setMobileDetailOpen(true);
    onRead(message.id, "BODY");
  };

  const relatedTask = selected?.relatedTaskId || selected?.relatedTaskInstanceId
    ? tasks.find(
      (task) =>
        task.id === selected.relatedTaskId ||
        task.backendId === selected.relatedTaskInstanceId,
    )
    : null;
  const actionLabels = {
    TASK_DETAIL: copy(language, "View task", "查看任务"),
    MY_TIDE: copy(language, "Go to My TIDE", "前往 My TIDE"),
    TASKS: copy(language, "View tasks", "查看任务列表"),
    HELP: copy(language, "Open Help", "打开帮助"),
    ACCOUNT: copy(language, "Open Account", "打开账号"),
  };
  const actionRoute = resolveMessageActionRoute(selected, relatedTask);
  const actionIsAvailable = Boolean(
    selected?.actionAvailable &&
      (actionRoute || ["HELP", "ACCOUNT"].includes(selected?.actionType)),
  );
  const actionTitle =
    selected?.actionType === "TASK_DETAIL"
      ? relatedTask?.name || actionLabels.TASK_DETAIL
      : selected?.typeCode === "GROWTH_STAGE_AVAILABLE"
        ? copy(language, "Your newly opened stage", "新开放的成长阶段")
      : actionLabels[selected?.actionType];
  const actionButtonLabel =
    selected?.typeCode === "GROWTH_STAGE_AVAILABLE"
      ? copy(language, "View new stage", "查看新阶段")
      : actionLabels[selected?.actionType];

  const changeFilter = (nextFilter) => {
    if (nextFilter === filter) return;
    setSelectedId("");
    setMobileDetailOpen(false);
    onFilterChange(nextFilter);
  };

  const supportUnreadCount = supportTickets.filter((ticket) => ticket.unread).length;
  const combinedUnreadCount = unreadCount + supportUnreadCount;

  return (
    <main className={`ref-page messages-screen ${mobileDetailOpen ? "mobile-detail-open" : ""}`}>
      <header className="messages-hero">
        <div>
          <span className="messages-kicker"><Bell size={17} weight="fill" />{copy(language, "YOUR CAMP UPDATES", "训练营消息")}</span>
          <h1>{copy(language, "Messages", "消息")}</h1>
          <p>{copy(language, "Revisit your complete notification history.", "查看账号下的全部历史通知。")}</p>
        </div>
        <div className="messages-summary" aria-label={copy(language, "Message summary", "消息概览")}>
          <strong>{combinedUnreadCount}</strong>
          <span>{copy(language, "unread", "条未读")}</span>
        </div>
      </header>

      <nav
        className="message-view-switch"
        aria-label={copy(language, "Message views", "消息视图")}
        data-onboarding-target="messages-tabs"
      >
        <button className={view === "SYSTEM" ? "active" : ""} type="button" onClick={() => setView("SYSTEM")}>
          {copy(language, "System messages", "系统消息")}
          {unreadCount > 0 && <em>{unreadCount}</em>}
        </button>
        <button
          className={view === "TICKETS" ? "active" : ""}
          type="button"
          onClick={() => {
            setView("TICKETS");
            onSupportTicketsRefresh?.();
          }}
        >
          {copy(language, "My submitted tickets", "我提交的工单")}
          {supportUnreadCount > 0 && <em>{supportUnreadCount}</em>}
        </button>
      </nav>

      {view === "TICKETS" ? (
        <SupportTicketWorkspace
          tickets={supportTickets}
          language={language}
          loading={supportTicketsLoading}
          error={supportTicketsError}
          onRefresh={onSupportTicketsRefresh}
          onUpdated={onSupportTicketUpdated}
        />
      ) : (
      <section
        className="messages-workspace"
        data-onboarding-target="messages-overview"
        data-onboarding-max-height="420"
        data-onboarding-scroll-block="start"
      >
        <div className="message-list-panel">
          <div className="message-list-toolbar">
            <div>
              <h2>{copy(language, "Notification history", "历史通知")}</h2>
              <span>{copy(language, `${totalCount} messages`, `共 ${totalCount} 条`)}</span>
            </div>
            <div className="message-filters" aria-label={copy(language, "Filter messages", "筛选消息")}>
              <button className={filter === "ALL" ? "active" : ""} type="button" disabled={loading} onClick={() => changeFilter("ALL")}>
                {copy(language, "All", "全部")}
              </button>
              <button className={filter === "UNREAD" ? "active" : ""} type="button" disabled={loading} onClick={() => changeFilter("UNREAD")}>
                {copy(language, "Unread", "未读")}{unreadCount > 0 && <em>{unreadCount}</em>}
              </button>
            </div>
          </div>

          <div className="message-list">
            {filteredMessages.map((message, index) => (
              <button
                className={`message-list-item ${message.read ? "is-read" : "is-unread"} ${selectedId === message.id ? "is-selected" : ""}`}
                key={message.id}
                type="button"
                onClick={() => openMessage(message)}
                data-analytics-message="true"
                data-analytics-message-id={message.id}
                data-analytics-notification-type={message.typeCode || message.type}
                data-analytics-entry-source="MESSAGES"
                data-analytics-display-position={`LIST_${index + 1}`}
                {...(message.relatedTaskInstanceId
                  ? { "data-analytics-task-assignment-id": message.relatedTaskInstanceId }
                  : {})}
              >
                <span className="message-status-dot" aria-label={copy(language, message.read ? "Read" : "Unread", message.read ? "已读" : "未读")} />
                <span className="message-list-copy">
                  <span className="message-list-meta"><MessageType message={message} language={language} /></span>
                  <strong>{messageTitle(message, language)}</strong>
                  <p>{messageBody(message, language)}</p>
                  <time>
                    <Clock size={14} />{messageTime(message, language)}
                    {message.expired && <span className="message-expired-badge">{copy(language, "Expired", "已过期")}</span>}
                  </time>
                </span>
                <ArrowRight className="message-list-arrow" size={20} />
              </button>
            ))}
            {filteredMessages.length > 0 && nextCursor && (
              <button className="message-load-more" type="button" disabled={loading} onClick={onLoadMore}>
                {copy(language, loading ? "Loading…" : "Load more", loading ? "加载中…" : "加载更多")}
              </button>
            )}
            {filteredMessages.length === 0 && (error ? (
              <div className="message-empty-state is-error" role="alert">
                <Bell size={42} weight="duotone" />
                <strong>{copy(language, "Messages are temporarily unavailable", "消息暂时无法加载")}</strong>
                <p>{copy(language, "Your message history has not been cleared. Please reload and try again.", "你的历史消息没有被清空，请重新加载后再试。")}</p>
                <button type="button" onClick={() => window.location.reload()}>
                  {copy(language, "Try again", "重新加载")}
                </button>
              </div>
            ) : (
              <div className="message-empty-state">
                <CheckCircle size={42} weight="duotone" />
                <strong>{copy(language, "You’re all caught up", "消息都看完啦")}</strong>
                <p>{copy(language, "New unread messages will appear here.", "新的未读消息会显示在这里。")}</p>
              </div>
            ))}
          </div>
        </div>

        <article className={`message-detail-panel ${selected ? "has-message" : "is-empty"}`}>
          {selected ? (
            <>
              <button className="message-mobile-back" type="button" onClick={() => setMobileDetailOpen(false)}>
                <ArrowLeft size={20} />{copy(language, "Back to messages", "返回消息列表")}
              </button>
              <div className="message-detail-heading">
                <div className="message-detail-icon">
                  {selected.type === "TASK" ? <ListChecks size={28} weight="duotone" /> : <EnvelopeOpen size={28} weight="duotone" />}
                </div>
                <div>
                  <span>
                    <MessageType message={selected} language={language} />
                    {selected.expired && <small>{copy(language, "EXPIRED", "已过期")}</small>}
                  </span>
                  <h2>{messageTitle(selected, language)}</h2>
                  <time>{messageTime(selected, language)}</time>
                </div>
              </div>
              <div className="message-detail-body">
                <p>{messageBody(selected, language)}</p>
              </div>
              {selected.actionType && (
                <div className={`message-task-action ${actionIsAvailable ? "is-available" : "is-unavailable"}`}>
                  <div>
                    <span className="message-task-icon"><ListChecks size={23} weight="fill" /></span>
                    <span>
                      <small>{copy(language, "RELATED ACTION", "相关操作")}</small>
                      <strong>{actionIsAvailable ? actionTitle : copy(language, "This action is no longer available", "该操作当前已失效")}</strong>
                    </span>
                  </div>
                  {actionIsAvailable && actionRoute ? (
                    <Link
                      to={actionRoute}
                      onClick={() => onAction?.(selected)}
                      data-analytics-entry-source="MESSAGES"
                      data-analytics-display-position="MESSAGE_ACTION"
                    >
                      {actionButtonLabel}<ArrowRight size={18} />
                    </Link>
                  ) : actionIsAvailable ? (
                    <button type="button" onClick={() => onAction?.(selected)}>
                      {actionButtonLabel}<ArrowRight size={18} />
                    </button>
                  ) : (
                    <p>{copy(language, "This message remains in your history, but its action is no longer available.", "消息仍会保留在历史记录中，但操作入口已不可用。")}</p>
                  )}
                </div>
              )}
              <footer className="message-read-note">
                <CheckCircle size={18} weight="fill" />
                {copy(language, "Opened and saved as read", "已打开并保存为已读")}
              </footer>
            </>
          ) : (
            <div className="message-detail-placeholder">
              <span><EnvelopeOpen size={34} weight="duotone" /></span>
              <h2>{copy(language, "Open a message when you’re ready", "选择一条消息查看详情")}</h2>
              <p>{copy(language, "Messages stay unread until you open them. Simply seeing this list does not change their status.", "消息只有在你主动打开后才会变为已读，仅浏览列表不会改变状态。")}</p>
            </div>
          )}
        </article>
      </section>
      )}
      {mobileNav}
    </main>
  );
}
import "../message-center.css";
