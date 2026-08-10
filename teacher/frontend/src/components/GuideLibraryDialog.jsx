import { useEffect, useRef } from "react";
import {
  ArrowRight,
  CheckCircle,
  Circle,
  LockSimple,
  Sparkle,
  X,
} from "@phosphor-icons/react";
import "./guide-library.css";

const copy = (language, english, chinese) => language === "zh" ? chinese : english;

const focusableSelector = [
  "button:not([disabled])",
  "[href]",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

const GUIDE_LIBRARY_COPY = Object.freeze({
  FIRST_LOGIN: {
    title: { en: "Tour the whole camp", zh: "认识整个客户端" },
    description: { en: "Understand My TIDE, Tasks, Messages and Help before you begin.", zh: "开始任务前，先看懂我的成长、我的任务、消息和帮助。" },
  },
  MY_TIDE_OVERVIEW: {
    title: { en: "Understand My TIDE", zh: "认识 My TIDE" },
    description: { en: "See your current stage, Toki's recommendation and growth dimensions.", zh: "了解当前阶段、Toki 建议和成长维度。" },
  },
  SCORE_DETAILS: {
    title: { en: "Points and milestones", zh: "积分与里程碑" },
    description: { en: "Understand current and available points, milestones and update time.", zh: "看懂已获得积分、可获得积分、里程碑和更新时间。" },
  },
  TASK_PATH: {
    title: { en: "Start your first task", zh: "开始第一项任务" },
    description: { en: "Open the recommended task, read its instructions and find the real workspace.", zh: "打开推荐任务，看懂任务说明并找到真实工作区。" },
  },
  TASK_RESULT: {
    title: { en: "Results and next steps", zh: "结果与下一步" },
    description: { en: "Know where task status, point syncing and the next task appear.", zh: "了解任务状态、积分同步和下一项任务会在哪里出现。" },
  },
  MESSAGES_TICKETS: {
    title: { en: "Messages and tickets", zh: "消息与工单" },
    description: { en: "Know where system updates and submitted-ticket replies are kept, even when the list is empty.", zh: "即使列表为空，也能先看懂系统消息和已提交工单分别在哪里。" },
  },
  HELP_ROUTES: {
    title: { en: "Get the right help", zh: "选择合适的帮助" },
    description: { en: "Choose between approved AI answers and an operations ticket.", zh: "区分 AI 知识库问答与提交运营工单。" },
  },
  PERSONALIZED_TASK_FIRST: {
    title: { en: "Personalized improvement tasks", zh: "个性化改善任务" },
    description: { en: "Understand why a personalized task was assigned and how it differs from required tasks.", zh: "了解个性化任务的分配原因，以及它与必修任务的区别。" },
  },
});

export const GUIDE_LIBRARY_ORDER = Object.freeze(Object.keys(GUIDE_LIBRARY_COPY));

function localized(value, language) {
  return value?.[language === "zh" ? "zh" : "en"] || "";
}

export default function GuideLibraryDialog({
  open,
  language = "en",
  guides = [],
  returnFocusElement = null,
  onClose,
  onPlay,
}) {
  const dialogRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const previousFocus = document.activeElement;
    const frame = window.requestAnimationFrame(() => dialogRef.current?.focus());
    const handleKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose?.();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = [...(dialogRef.current?.querySelectorAll(focusableSelector) || [])];
      if (focusable.length === 0) {
        event.preventDefault();
        dialogRef.current?.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable.at(-1);
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialogRef.current)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("keydown", handleKeyDown);
      window.requestAnimationFrame(() => {
        const target = returnFocusElement?.isConnected ? returnFocusElement : previousFocus;
        target?.focus?.();
      });
    };
  }, [onClose, open, returnFocusElement]);

  if (!open) return null;

  const states = new Map(guides.map((guide) => [guide.guideCode, guide]));

  return (
    <div className="guide-library-backdrop" onMouseDown={onClose}>
      <section
        ref={dialogRef}
        className="guide-library-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="guide-library-title"
        tabIndex={-1}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="guide-library-header">
          <span className="guide-library-mark"><Sparkle size={24} weight="fill" /></span>
          <span>
            <small>{copy(language, "FEATURE GUIDES", "功能引导")}</small>
            <h2 id="guide-library-title">{copy(language, "Learn one feature at a time", "按需了解每项功能")}</h2>
            <p>{copy(language, "Each guide is independent. Replaying one never changes task progress or points.", "每项引导相互独立；重新播放不会改变任务进度或积分。")}</p>
          </span>
          <button type="button" onClick={onClose} aria-label={copy(language, "Close feature guides", "关闭功能引导")}>
            <X size={20} />
          </button>
        </header>

        <div className="guide-library-list">
          {GUIDE_LIBRARY_ORDER.map((guideCode) => {
            const state = states.get(guideCode) || {};
            const content = GUIDE_LIBRARY_COPY[guideCode];
            const available = state.available !== false;
            const completed = Boolean(state.status);
            return (
              <article className={`guide-library-item ${available ? "is-available" : "is-locked"}`} key={guideCode}>
                <span className="guide-library-state" aria-hidden="true">
                  {completed
                    ? <CheckCircle size={22} weight="fill" />
                    : available
                      ? <Circle size={22} weight="duotone" />
                      : <LockSimple size={20} weight="duotone" />}
                </span>
                <span className="guide-library-copy">
                  <strong>{localized(content.title, language)}</strong>
                  <small>{localized(content.description, language)}</small>
                  <em>
                    {completed
                      ? copy(language, "Viewed · replay anytime", "已看过 · 可随时重播")
                      : available
                        ? copy(language, "Ready to view", "可以查看")
                        : state.unavailableLabel || copy(language, "Available when related real data appears", "出现相关真实数据后可查看")}
                  </em>
                </span>
                <button
                  type="button"
                  disabled={!available}
                  onClick={() => onPlay?.(guideCode)}
                  aria-label={copy(language, `Play ${localized(content.title, language)}`, `播放${localized(content.title, language)}`)}
                >
                  {copy(language, completed ? "Replay" : "Start", completed ? "重播" : "开始")}
                  <ArrowRight size={17} weight="bold" />
                </button>
              </article>
            );
          })}
        </div>
      </section>
    </div>
  );
}
