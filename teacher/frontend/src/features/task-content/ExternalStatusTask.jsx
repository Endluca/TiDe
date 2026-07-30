import { useState } from "react";
import {
  ArrowSquareOut,
  BookOpenText,
  CaretDown,
  CheckCircle,
  ClipboardText,
  ClockClockwise,
  HourglassMedium,
  Info,
  SealCheck,
  Tag,
  VideoCamera,
  WifiHigh,
} from "@phosphor-icons/react";
import { useI18n } from "../../i18n";
import "./external-status-task.css";

const itemIcons = {
  self_intro: VideoCamera,
  credential: SealCheck,
  device_check: WifiHigh,
  tag_callback: Tag,
};

const guideIcons = {
  document: BookOpenText,
  video: VideoCamera,
  credential: SealCheck,
  course: BookOpenText,
};

const statusMeta = {
  approved: {
    icon: CheckCircle,
    className: "is-approved",
    en: "Approved",
    zh: "已通过",
  },
  reviewing: {
    icon: HourglassMedium,
    className: "is-reviewing",
    en: "Under review",
    zh: "审核中",
  },
  waiting: {
    icon: ClockClockwise,
    className: "is-waiting",
    en: "Waiting for result",
    zh: "等待结果",
  },
  action_required: {
    icon: ClockClockwise,
    className: "is-action-required",
    en: "Not completed",
    zh: "未完成",
  },
  update_required: {
    icon: ClockClockwise,
    className: "is-action-required",
    en: "Needs attention",
    zh: "需要处理",
  },
  unavailable: {
    icon: ClockClockwise,
    className: "is-waiting",
    en: "Temporarily unavailable",
    zh: "暂不可用",
  },
};

function ProfileLearningCenter({ guides }) {
  const { language } = useI18n();
  const c = (en, zh) => language === "zh" ? zh : en;
  const local = (item, key) => language === "zh" ? item[`${key}Zh`] || item[key] : item[key];
  const [open, setOpen] = useState(false);
  const [activeId, setActiveId] = useState(guides[0]?.id || "");
  const activeGuide = guides.find((guide) => guide.id === activeId) || guides[0];

  if (!activeGuide) return null;

  const ActiveIcon = guideIcons[activeGuide.type] || BookOpenText;
  const steps = language === "zh" ? activeGuide.stepsZh || activeGuide.steps || [] : activeGuide.steps || [];

  return (
    <section className="profile-learning-center">
      <button className="profile-learning-toggle" type="button" aria-expanded={open} aria-controls="profile-learning-content" onClick={() => setOpen((current) => !current)}>
        <span><BookOpenText size={22} weight="duotone" /></span>
        <div>
          <strong>{c("Optional learning resources", "相关学习资料")}</strong>
          <small>{c("Review Self-intro guidance and TESOL learning content here", "可在这里查看自我介绍指南和 TESOL 学习内容")}</small>
        </div>
        <em>{open ? c("Collapse", "收起") : c("View learning", "查看学习内容")}</em>
        <CaretDown size={18} weight="bold" />
      </button>

      {open && (
        <div id="profile-learning-content" className="profile-learning-content">
          <div className="profile-learning-tabs" role="tablist" aria-label={c("Choose learning content", "选择学习内容")}>
            {guides.map((guide) => {
              const Icon = guideIcons[guide.type] || BookOpenText;
              return (
                <button className={guide.id === activeGuide.id ? "is-active" : ""} type="button" role="tab" aria-selected={guide.id === activeGuide.id} key={guide.id} onClick={() => setActiveId(guide.id)}>
                  <Icon size={18} weight={guide.id === activeGuide.id ? "fill" : "duotone"} />
                  {local(guide, "title")}
                </button>
              );
            })}
          </div>

          <article className="profile-learning-panel" role="tabpanel">
            <header>
              <span><ActiveIcon size={25} weight="duotone" /></span>
              <div>
                <small>{local(activeGuide, "eyebrow")}</small>
                <h3>{local(activeGuide, "title")}</h3>
                <p>{local(activeGuide, "summary")}</p>
              </div>
            </header>

            {activeGuide.mediaUrl && (
              <video src={activeGuide.mediaUrl} controls controlsList="nodownload noremoteplayback" disablePictureInPicture playsInline preload="metadata">
                {c("Your browser cannot play this training video.", "当前浏览器无法播放这段教程视频。")}
              </video>
            )}

            {steps.length > 0 && (
              <ol className="profile-learning-steps">
                {steps.map((step) => <li key={step}>{step}</li>)}
              </ol>
            )}

            {activeGuide.sections?.length > 0 && (
              <div className="profile-learning-sections">
                {activeGuide.sections.map((section) => {
                  const items = language === "zh" ? section.itemsZh || section.items : section.items;
                  return (
                    <section key={section.title}>
                      <strong>{local(section, "title")}</strong>
                      <ul>{items.map((item) => <li key={item}>{item}</li>)}</ul>
                    </section>
                  );
                })}
              </div>
            )}
          </article>

          <p className="profile-learning-boundary">
            <Info size={17} weight="fill" />
            {c(
              "Learning here does not change either review status. The latest review result appears automatically.",
              "这里的学习进度不会改变两项审核状态；最新审核结果会自动更新。",
            )}
          </p>
        </div>
      )}
    </section>
  );
}

export default function ExternalStatusTask({ task, embedded = false }) {
  const { language } = useI18n();
  const c = (en, zh) => language === "zh" ? zh : en;
  const local = (item, key) => language === "zh" ? item[`${key}Zh`] || item[key] : item[key];
  const items = task.externalStatusItems || [];
  const resources = task.resources || [];
  const copy = task.externalStatusCopy || {};
  const profileStatusOnly = task.statusPageVariant === "profile_credentials";
  const profileGuides = profileStatusOnly ? task.profileGuides || [] : [];
  const approvedCount = items.filter((item) => item.status === "approved").length;

  return (
    <div className={[
      "external-status-task",
      profileStatusOnly ? "profile-credentials-status" : "",
      embedded ? "is-embedded" : "",
    ].filter(Boolean).join(" ")}>
      <div className="external-status-boundary">
        <ClipboardText size={25} weight="duotone" />
        <span>
          <strong>
            {profileStatusOnly
              ? c(copy.title || "Self-intro and TESOL status", copy.titleZh || "自我介绍与 TESOL 状态")
              : c(copy.title || "Your submission status", copy.titleZh || "你的资料状态")}
          </strong>
          {profileStatusOnly
            ? c(
                copy.description || "Review the latest status of these two items. There is nothing to upload or submit on this page.",
                copy.descriptionZh || "在这里查看这两项的最新状态，不需要上传或提交任何材料。",
              )
            : c(
                copy.description || "Check the latest available status.",
                copy.descriptionZh || "查看最新状态。",
              )}
        </span>
        {profileStatusOnly && (
          <em className="external-status-count">{c(`${approvedCount} of ${items.length} approved`, `已通过 ${approvedCount} / ${items.length} 项`)}</em>
        )}
      </div>

      {!profileStatusOnly && resources.length > 0 && (
        <section className="external-resource-section">
          <header><div><strong>{c("Guides and course links", "标准与课程入口")}</strong><small>{c(`${resources.length} links`, `${resources.length} 个入口`)}</small></div></header>
          <div className="external-resource-grid">
            {resources.map((resource) => (
              <a href={resource.url} target="_blank" rel="noreferrer" key={resource.url}>
                <span><BookOpenText size={21} weight="duotone" /></span>
                <div><strong>{local(resource, "title")}</strong><small>{local(resource, "meta")}</small></div>
                <ArrowSquareOut size={17} weight="bold" />
              </a>
            ))}
          </div>
        </section>
      )}

      <div className={`external-status-list ${profileStatusOnly ? "profile-status-pair" : ""}`.trim()}>
        {items.map((item) => {
          const Icon = itemIcons[item.type] || ClipboardText;
          const status = statusMeta[item.status] || statusMeta.waiting;
          const StatusIcon = status.icon;
          return (
            <article key={item.id} className={`external-status-item ${status.className}`}>
              <span className="external-item-icon"><Icon size={25} weight="duotone" /></span>
              <div className="external-item-copy">
                <h3>{local(item, "label")}</h3>
                <p>{local(item, "source")}</p>
                <small>{c("Last status update: ", "最近更新：")}{local(item, "updatedAt")}</small>
              </div>
              <span className={`external-item-status ${status.className}`}>
                <StatusIcon size={17} weight="fill" />
                {c(status.en, status.zh)}
              </span>
            </article>
          );
        })}
      </div>

      <div className="external-status-note">
        <Info size={19} weight="fill" />
        <span>
          <strong>
            {profileStatusOnly
              ? c(copy.completionTitle || "This task completes automatically", copy.completionTitleZh || "本任务会自动完成")
              : c(copy.completionTitle || "When this task is complete", copy.completionTitleZh || "什么时候算完成")}
          </strong>
          {profileStatusOnly
            ? c(
                copy.completion || "When both statuses show Approved, the task completes automatically. If a status has not updated, contact Training Support about the source record.",
                copy.completionZh || "两项均显示“已通过”后任务会自动完成；状态长时间未更新时，请联系培训支持核对来源记录。",
              )
            : c(
                copy.completion || "The task completes when the required source status passes.",
                copy.completionZh || "必备状态通过后，这项任务即完成。",
              )}
        </span>
      </div>

      {profileGuides.length > 0 && <ProfileLearningCenter guides={profileGuides} />}
    </div>
  );
}
