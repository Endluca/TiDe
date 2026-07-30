import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  Check,
  CheckCircle,
  Clock,
  Lock,
  Question,
  SealCheck,
  Sparkle,
  X,
} from "@phosphor-icons/react";
import { useI18n } from "../i18n";
import { publicAsset } from "../public-assets";

const tokiImages = {
  wave: publicAsset("/assets/toki/wave.png"),
  cheer: publicAsset("/assets/toki/cheer.png"),
  happy: publicAsset("/assets/toki/happy.png"),
  celebrate: publicAsset("/assets/toki/celebrate.png"),
  stars: publicAsset("/assets/toki/stars.png"),
  thumb: publicAsset("/assets/toki/thumb.png"),
};

const tokiMotions = {
  allDone: {
    mov: publicAsset("/assets/toki-motion/all-done.mov"),
    webm: publicAsset("/assets/toki-motion/all-done.webm"),
  },
  welcome: {
    mov: publicAsset("/assets/toki-motion/welcome.mov"),
    webm: publicAsset("/assets/toki-motion/welcome.webm"),
  },
  celebrate: {
    mov: publicAsset("/assets/toki-motion/celebrate.mov"),
    webm: publicAsset("/assets/toki-motion/celebrate.webm"),
  },
  encourage: {
    mov: publicAsset("/assets/toki-motion/encourage.mov"),
    webm: publicAsset("/assets/toki-motion/encourage.webm"),
  },
  scan: {
    mov: publicAsset("/assets/toki-motion/scan.mov"),
    webm: publicAsset("/assets/toki-motion/scan.webm"),
  },
  ticketReceived: {
    mov: publicAsset("/assets/toki-motion/ticket-received-clean.mov"),
    webm: publicAsset("/assets/toki-motion/ticket-received-clean.webm"),
  },
  unlock: {
    mov: publicAsset("/assets/toki-motion/unlock.mov"),
    webm: publicAsset("/assets/toki-motion/unlock.webm"),
  },
};

export function Toki({
  mood = "wave",
  motion,
  loop = false,
  className = "",
  alt,
}) {
  const { t } = useI18n();
  const videoRef = useRef(null);
  const hasStartedRef = useRef(false);
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  const poster = tokiImages[mood] || tokiImages.wave;
  const sources = tokiMotions[motion];
  const accessibleLabel = alt || t("toki.alt");

  useEffect(() => {
    const mediaQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    const updatePreference = () => setPrefersReducedMotion(mediaQuery.matches);
    updatePreference();
    mediaQuery.addEventListener?.("change", updatePreference);
    return () => mediaQuery.removeEventListener?.("change", updatePreference);
  }, []);

  useEffect(() => {
    hasStartedRef.current = false;
  }, [motion]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !sources || prefersReducedMotion) return undefined;

    const playWhenVisible = () => {
      if (!loop && video.ended) return;
      if (!hasStartedRef.current) {
        video.currentTime = 0;
        hasStartedRef.current = true;
      }
      video.play()?.catch(() => {});
    };

    if (!("IntersectionObserver" in window)) {
      playWhenVisible();
      return () => video.pause();
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && entry.intersectionRatio >= 0.65) {
          playWhenVisible();
        } else {
          video.pause();
        }
      },
      { threshold: [0, 0.65] },
    );

    observer.observe(video);
    return () => {
      observer.disconnect();
      video.pause();
    };
  }, [loop, motion, prefersReducedMotion, sources]);

  if (!sources || prefersReducedMotion) {
    return <img className={`toki ${className}`} src={poster} alt={accessibleLabel} />;
  }

  return (
    <video
      ref={videoRef}
      key={motion}
      className={`toki toki-motion ${className}`}
      muted
      playsInline
      loop={loop}
      preload="none"
      poster={poster}
      role="img"
      aria-label={accessibleLabel}
    >
      <source src={sources.mov} type='video/quicktime; codecs="hvc1"' />
      <source src={sources.webm} type='video/webm; codecs="vp9"' />
    </video>
  );
}

export function StatusPill({ status }) {
  const { t } = useI18n();
  const Icon = status === "completed" ? CheckCircle : ["submitted", "verifying"].includes(status) ? Clock : Sparkle;
  return (
    <span className={`status-pill status-${status}`}>
      <Icon size={14} weight="bold" aria-hidden="true" />
      {t(`status.${status}`)}
    </span>
  );
}

export function TaskRow({ task, compact = false }) {
  const { t } = useI18n();
  return (
    <Link
      className={`task-row ${compact ? "task-row-compact" : ""}`}
      to={`/task/${task.id}`}
      {...(task.backendId ? {
        "data-analytics-task-card": "true",
        "data-analytics-task-assignment-id": task.backendId,
        "data-analytics-task-code": task.taskCode,
        "data-analytics-task-type": task.taskCategory,
        "data-analytics-entry-source": "TASK_LIST",
        "data-analytics-display-position": compact ? "COMPACT" : "LIST",
      } : {})}
    >
      <span className={`task-state-mark state-${task.status}`} aria-hidden="true">
        {task.status === "completed" ? <Check size={16} weight="bold" /> : task.locked ? <Lock size={14} /> : null}
      </span>
      <span className="task-row-copy">
        <span className="task-row-topline">
          <strong>{task.name}</strong>
          <span className="mock-label">{t("task.required")}</span>
        </span>
        <span className="task-row-meta">
          {t(`method.${task.method}`)} <span aria-hidden="true">·</span> {task.duration}
        </span>
      </span>
      <span className="task-row-end">
        <StatusPill status={task.status} />
        <ArrowRight size={18} weight="bold" aria-hidden="true" />
      </span>
    </Link>
  );
}

export function ProgressSteps({ tasks, label }) {
  const { t } = useI18n();
  const progressLabel = label || t("progress.label");
  const done = tasks.filter((task) => task.status === "completed").length;
  return (
    <div className="progress-steps" role="img" aria-label={t("progress.aria", { label: progressLabel, done, total: tasks.length })}>
      {tasks.map((task) => (
        <span key={task.id} className={task.status === "completed" ? "step-done" : task.locked ? "step-locked" : "step-active"} />
      ))}
    </div>
  );
}

export function EmptyResult({ children }) {
  return <div className="empty-result">{children}</div>;
}

export function HelpDialog({ open, onClose, onReset }) {
  const { t } = useI18n();
  useEffect(() => {
    if (!open) return undefined;
    const closeOnEscape = (event) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="help-dialog" role="dialog" aria-modal="true" aria-labelledby="help-title" onMouseDown={(event) => event.stopPropagation()}>
        <div className="dialog-head">
          <div>
            <span className="eyebrow">{t("help.eyebrow")}</span>
            <h2 id="help-title">{t("help.title")}</h2>
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label={t("help.close")}>
            <X size={20} />
          </button>
        </div>
        <p className="dialog-intro">{t("help.intro")}</p>
        <div className="help-options">
          <button type="button" onClick={onClose}>
            <Question size={22} />
            <span><strong>{t("help.understand")}</strong><small>{t("help.understandCopy")}</small></span>
          </button>
          <button type="button" onClick={onClose}>
            <SealCheck size={22} />
            <span><strong>{t("help.rules")}</strong><small>{t("help.rulesCopy")}</small></span>
          </button>
        </div>
        <div className="dialog-foot">
          <button className="text-button" type="button" onClick={onReset}>{t("help.reset")}</button>
          <button className="primary-button" type="button" onClick={onClose}>{t("help.gotIt")}</button>
        </div>
      </section>
    </div>
  );
}
