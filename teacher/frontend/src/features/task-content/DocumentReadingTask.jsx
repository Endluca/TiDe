import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BookOpenText, CheckCircle, SpinnerGap, WarningCircle } from "@phosphor-icons/react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  getTaskDocumentAsset,
  getTaskDocumentContent,
} from "../../api/task-api";
import { useI18n } from "../../i18n";
import {
  canPersistDocumentProgress,
  documentReadProgressFromStep,
  hasDocumentProgressAdvanced,
  measureDocumentReadProgress,
  mergeDocumentReadProgress,
  shouldPersistDocumentProgress,
} from "./document-reading-progress";
import {
  formatPolicyDocumentVersion,
  safePolicyDocumentUrl,
} from "./document-reading-safety";
import "./document-reading-task.css";

const SAVE_DELAY_MS = 500;

function normalizePolicyMarkdown(markdown, images) {
  let normalized = typeof markdown === "string"
    ? markdown.replace(/<\/?(?:span|u)(?:\s[^>]*)?>/giu, "")
    : "";
  for (const image of images) {
    normalized = normalized.replaceAll(image.sourcePath, image.assetUrl);
  }
  return normalized.trim();
}

function savedDocumentProgress(task) {
  return mergeDocumentReadProgress(null, {
    readPercent: Number.isInteger(task.documentReadPercent)
      ? task.documentReadPercent
      : 0,
    reachedEnd: task.documentReachedEnd === true,
  });
}

function externalLink({ href, children, title }) {
  const safeHref = safePolicyDocumentUrl(href);
  if (!safeHref) return <span>{children}</span>;
  return (
    <a href={safeHref} title={title} target="_blank" rel="noreferrer noopener">
      {children}
    </a>
  );
}

function controlledImageRenderer(controlledImages) {
  return function ControlledImage({ src, title }) {
    const image = controlledImages.get(src);
    if (!image) return null;
    return (
      <img
        src={image.assetUrl}
        alt={image.alt}
        title={title}
        width="1917"
        height="1073"
        loading="eager"
        decoding="async"
      />
    );
  };
}

export default function DocumentReadingTask({ task }) {
  const { language } = useI18n();
  const c = (en, zh) => language === "zh" ? zh : en;
  const documentConfig = task.documentContent || {};
  const taskInstanceId = task.backendId || task.id || "";
  const contentVersion = typeof documentConfig.contentVersion === "string"
    ? documentConfig.contentVersion
    : "";
  const contentHash = typeof documentConfig.contentHash === "string"
    ? documentConfig.contentHash
    : "";
  const [loadedContent, setLoadedContent] = useState(null);
  const [contentLoadFailed, setContentLoadFailed] = useState(false);
  const [contentRequestVersion, setContentRequestVersion] = useState(0);
  useEffect(() => {
    if (!taskInstanceId || !contentVersion || !contentHash) {
      setLoadedContent(null);
      setContentLoadFailed(true);
      return undefined;
    }
    const controller = new AbortController();
    const objectUrls = [];
    setLoadedContent(null);
    setContentLoadFailed(false);
    getTaskDocumentContent(taskInstanceId, controller.signal)
      .then(async (content) => {
        const images = Array.isArray(content?.images) ? content.images : [];
        const hydratedImages = await Promise.all(images.map(async (image) => {
          if (
            !image
            || typeof image.key !== "string"
            || typeof image.sourcePath !== "string"
            || typeof image.sha256 !== "string"
            || !/^[a-f0-9]{64}$/u.test(image.sha256)
          ) throw new Error("INVALID_DOCUMENT_IMAGE");
          const blob = await getTaskDocumentAsset(
            taskInstanceId,
            image.key,
            image.sha256,
            controller.signal,
          );
          const assetUrl = URL.createObjectURL(blob);
          objectUrls.push(assetUrl);
          return { ...image, assetUrl };
        }));
        if (!controller.signal.aborted) {
          setLoadedContent({ ...content, images: hydratedImages });
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) setContentLoadFailed(true);
      });
    return () => {
      controller.abort();
      for (const objectUrl of objectUrls) URL.revokeObjectURL(objectUrl);
    };
  }, [contentHash, contentRequestVersion, contentVersion, taskInstanceId]);
  const controlledImages = useMemo(() => {
    if (!Array.isArray(loadedContent?.images)) return new Map();
    return new Map(loadedContent.images.flatMap((image) => {
      if (
        !image
        || typeof image.key !== "string"
        || typeof image.sourcePath !== "string"
        || typeof image.sha256 !== "string"
        || !/^[a-f0-9]{64}$/u.test(image.sha256)
      ) return [];
      if (typeof image.assetUrl !== "string" || !image.assetUrl.startsWith("blob:")) return [];
      return [[image.assetUrl, image]];
    }));
  }, [loadedContent, taskInstanceId]);
  const controlledImage = useMemo(
    () => controlledImageRenderer(controlledImages),
    [controlledImages],
  );
  const controlledImageList = useMemo(
    () => [...controlledImages.values()],
    [controlledImages],
  );
  const policyDocumentMarkdown = useMemo(
    () => normalizePolicyMarkdown(
      language === "zh" ? loadedContent?.markdown?.zh : loadedContent?.markdown?.en,
      controlledImageList,
    ),
    [controlledImageList, language, loadedContent],
  );
  const contentCompatible = Boolean(
    loadedContent
    && loadedContent.contentVersion === contentVersion
    && loadedContent.contentHash === contentHash
    && loadedContent.completionMode === "SCROLL_TO_END"
    && policyDocumentMarkdown,
  );
  const sourceUpdatedAt = loadedContent?.sourceUpdatedAt || documentConfig.sourceUpdatedAt;
  const versionLabel = formatPolicyDocumentVersion(sourceUpdatedAt, contentVersion, language);
  const taskIdentity = `${taskInstanceId || task.taskCode || "document"}:${contentVersion}:${contentHash}`;
  const savedProgress = useMemo(
    () => savedDocumentProgress(task),
    [task.documentReadPercent, task.documentReachedEnd],
  );
  const assignmentCompleted = task.status === "completed";
  const documentCompleted = task.documentCompleted === true;
  const historicalCompletion = assignmentCompleted && !documentCompleted;
  const [progress, setProgress] = useState(savedProgress);
  const [saving, setSaving] = useState(false);
  const [completionError, setCompletionError] = useState("");
  const readerRef = useRef(null);
  const progressRef = useRef(savedProgress);
  const confirmedProgressRef = useRef(savedProgress);
  const pendingProgressRef = useRef(null);
  const saveTimerRef = useRef(null);
  const frameRef = useRef(null);
  const persistPromiseRef = useRef(null);
  const forceFlushRef = useRef(false);
  const submitPromiseRef = useRef(null);
  const submitAttemptedRef = useRef(false);
  const assignmentFinishedRef = useRef(assignmentCompleted);
  const documentReceiptCompleteRef = useRef(documentCompleted);
  const mountedRef = useRef(false);
  const busyCountRef = useRef(0);
  const errorKindRef = useRef(null);
  const identityRef = useRef(taskIdentity);
  const latestRef = useRef(null);

  latestRef.current = {
    assignmentCompleted,
    contentHash,
    contentCompatible,
    contentVersion,
    documentCompleted,
    documentStepKey: task.documentStepKey,
    execution: task.execution,
    language,
    readOnly: task.previewReadOnly === true,
  };

  const changeBusy = useCallback((change) => {
    busyCountRef.current = Math.max(0, busyCountRef.current + change);
    if (mountedRef.current) setSaving(busyCountRef.current > 0);
  }, []);

  const showError = useCallback((kind, en, zh) => {
    errorKindRef.current = kind;
    if (mountedRef.current) {
      setCompletionError(latestRef.current?.language === "zh" ? zh : en);
    }
  }, []);

  const clearError = useCallback((kind = null) => {
    if (kind && errorKindRef.current !== kind) return;
    errorKindRef.current = null;
    if (mountedRef.current) setCompletionError("");
  }, []);

  const finishAssignment = useCallback(async ({ force = false } = {}) => {
    const latest = latestRef.current;
    if (
      !canPersistDocumentProgress({
        readOnly: latest?.readOnly,
        assignmentCompleted: latest?.assignmentCompleted,
        documentCompleted: false,
        contentCompatible: latest?.contentCompatible,
      })
      || assignmentFinishedRef.current
      || !documentReceiptCompleteRef.current
    ) return null;
    if (submitPromiseRef.current) return submitPromiseRef.current;
    if (submitAttemptedRef.current && !force) return null;
    if (!latest.execution?.live || typeof latest.execution.submit !== "function") {
      showError(
        "submit",
        "Your reading is saved, but task completion is still syncing. Retry shortly.",
        "阅读进度已保存，但任务完成能力仍在同步，请稍后重试。",
      );
      return null;
    }

    submitAttemptedRef.current = true;
    clearError();
    const operation = (async () => {
      changeBusy(1);
      try {
        const response = await latest.execution.submit({ keepalive: true });
        if (response?.status === "COMPLETED") {
          assignmentFinishedRef.current = true;
        }
        return response;
      } catch {
        showError(
          "submit",
          "Your reading progress is saved. Retry to finish the task.",
          "阅读进度已保存，请重试完成任务。",
        );
        return null;
      } finally {
        changeBusy(-1);
      }
    })();
    submitPromiseRef.current = operation;
    try {
      return await operation;
    } finally {
      if (submitPromiseRef.current === operation) submitPromiseRef.current = null;
    }
  }, [changeBusy, clearError, showError]);

  const flushProgress = useCallback(({ force = false } = {}) => {
    if (force) forceFlushRef.current = true;
    if (persistPromiseRef.current) return persistPromiseRef.current;

    const operation = (async () => {
      changeBusy(1);
      let candidate = null;
      try {
        while (true) {
          const latest = latestRef.current;
          if (!canPersistDocumentProgress({
            readOnly: latest?.readOnly,
            assignmentCompleted: latest?.assignmentCompleted,
            documentCompleted: documentReceiptCompleteRef.current,
            contentCompatible: latest?.contentCompatible,
          })) {
            pendingProgressRef.current = null;
            if (documentReceiptCompleteRef.current) await finishAssignment();
            break;
          }
          if (assignmentFinishedRef.current) {
            pendingProgressRef.current = null;
            break;
          }

          candidate = pendingProgressRef.current;
          const confirmed = confirmedProgressRef.current;
          if (!candidate || !hasDocumentProgressAdvanced(confirmed, candidate)) {
            pendingProgressRef.current = null;
            break;
          }
          if (
            !forceFlushRef.current
            && !candidate.reachedEnd
            && !shouldPersistDocumentProgress(
              confirmed.readPercent,
              candidate.readPercent,
            )
          ) break;
          if (
            !latest.execution?.live
            || typeof latest.execution.saveStep !== "function"
            || !latest.documentStepKey
          ) {
            showError(
              "progress",
              "Reading progress is still syncing. Keep this page open and retry shortly.",
              "阅读进度能力仍在同步，请保持页面打开并稍后重试。",
            );
            break;
          }

          pendingProgressRef.current = null;
          const response = await latest.execution.saveStep(
            latest.documentStepKey,
            {
              contentVersion: latest.contentVersion,
              contentHash: latest.contentHash,
              readPercent: candidate.readPercent,
              reachedEnd: candidate.reachedEnd,
            },
            0,
            { keepalive: true },
          );
          const acknowledged = documentReadProgressFromStep(response?.step, {
            stepKey: latest.documentStepKey,
            contentVersion: latest.contentVersion,
            contentHash: latest.contentHash,
          });
          confirmedProgressRef.current = mergeDocumentReadProgress(
            confirmedProgressRef.current,
            acknowledged,
          );
          progressRef.current = mergeDocumentReadProgress(
            progressRef.current,
            confirmedProgressRef.current,
          );
          if (mountedRef.current) {
            setProgress((current) => mergeDocumentReadProgress(current, acknowledged));
          }
          clearError("progress");

          if (response?.step?.status === "COMPLETED") {
            documentReceiptCompleteRef.current = true;
            if (saveTimerRef.current !== null) {
              window.clearTimeout(saveTimerRef.current);
              saveTimerRef.current = null;
            }
            pendingProgressRef.current = null;
            if (response?.status === "COMPLETED") {
              assignmentFinishedRef.current = true;
            } else {
              await finishAssignment();
            }
            break;
          }
          candidate = null;
        }
      } catch {
        if (candidate) {
          pendingProgressRef.current = mergeDocumentReadProgress(
            pendingProgressRef.current,
            candidate,
          );
        }
        showError(
          "progress",
          "Reading progress could not be saved. Keep this page open and try again.",
          "阅读进度保存失败，请保持页面打开并重试。",
        );
      } finally {
        forceFlushRef.current = false;
        changeBusy(-1);
      }
    })();
    persistPromiseRef.current = operation;
    operation.finally(() => {
      if (persistPromiseRef.current === operation) persistPromiseRef.current = null;
    });
    return operation;
  }, [changeBusy, clearError, finishAssignment, showError]);

  const stageProgress = useCallback((measured) => {
    const latest = latestRef.current;
    if (
      !latest?.contentCompatible
      || latest?.assignmentCompleted
      || assignmentFinishedRef.current
      || documentReceiptCompleteRef.current
    ) return;
    const nextProgress = mergeDocumentReadProgress(progressRef.current, measured);
    if (!hasDocumentProgressAdvanced(progressRef.current, nextProgress)) return;

    progressRef.current = nextProgress;
    if (mountedRef.current) setProgress(nextProgress);
    if (!canPersistDocumentProgress({ readOnly: latest.readOnly })) return;
    pendingProgressRef.current = mergeDocumentReadProgress(
      pendingProgressRef.current,
      nextProgress,
    );
    if (nextProgress.reachedEnd) {
      if (saveTimerRef.current !== null) {
        window.clearTimeout(saveTimerRef.current);
        saveTimerRef.current = null;
      }
      void flushProgress();
      return;
    }
    if (!shouldPersistDocumentProgress(
      confirmedProgressRef.current.readPercent,
      nextProgress.readPercent,
    )) return;
    if (saveTimerRef.current !== null) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(() => {
      saveTimerRef.current = null;
      void flushProgress();
    }, SAVE_DELAY_MS);
  }, [flushProgress]);

  const measure = useCallback(() => {
    frameRef.current = null;
    const latest = latestRef.current;
    const reader = readerRef.current;
    if (
      !reader
      || latest?.assignmentCompleted
      || !latest?.contentCompatible
      || assignmentFinishedRef.current
      || documentReceiptCompleteRef.current
      || progressRef.current.reachedEnd
    ) return;
    stageProgress(measureDocumentReadProgress(reader));
  }, [stageProgress]);

  const scheduleMeasure = useCallback(() => {
    if (frameRef.current !== null) return;
    frameRef.current = window.requestAnimationFrame(measure);
  }, [measure]);

  useEffect(() => {
    mountedRef.current = true;
    const flushBeforeLeaving = () => {
      if (saveTimerRef.current !== null) {
        window.clearTimeout(saveTimerRef.current);
        saveTimerRef.current = null;
      }
      void flushProgress({ force: true });
    };
    const flushWhenHidden = () => {
      if (document.visibilityState === "hidden") flushBeforeLeaving();
    };
    window.addEventListener("pagehide", flushBeforeLeaving);
    document.addEventListener("visibilitychange", flushWhenHidden);
    return () => {
      mountedRef.current = false;
      window.removeEventListener("pagehide", flushBeforeLeaving);
      document.removeEventListener("visibilitychange", flushWhenHidden);
      if (frameRef.current !== null) {
        window.cancelAnimationFrame(frameRef.current);
        frameRef.current = null;
      }
      if (saveTimerRef.current !== null) {
        window.clearTimeout(saveTimerRef.current);
        saveTimerRef.current = null;
      }
      void flushProgress({ force: true });
    };
  }, [flushProgress]);

  useEffect(() => {
    if (identityRef.current === taskIdentity) return;
    identityRef.current = taskIdentity;
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
    }
    progressRef.current = savedProgress;
    confirmedProgressRef.current = savedProgress;
    pendingProgressRef.current = null;
    forceFlushRef.current = false;
    submitAttemptedRef.current = false;
    assignmentFinishedRef.current = assignmentCompleted;
    documentReceiptCompleteRef.current = documentCompleted;
    errorKindRef.current = null;
    setProgress(savedProgress);
    setCompletionError("");
  }, [assignmentCompleted, documentCompleted, savedProgress, taskIdentity]);

  useEffect(() => {
    let incoming = savedProgress;
    if (documentCompleted) {
      incoming = mergeDocumentReadProgress(incoming, {
        readPercent: 100,
        reachedEnd: true,
      });
      documentReceiptCompleteRef.current = true;
    }
    confirmedProgressRef.current = mergeDocumentReadProgress(
      confirmedProgressRef.current,
      incoming,
    );
    progressRef.current = mergeDocumentReadProgress(progressRef.current, incoming);
    setProgress((current) => mergeDocumentReadProgress(current, incoming));
    if (
      pendingProgressRef.current
      && !hasDocumentProgressAdvanced(
        confirmedProgressRef.current,
        pendingProgressRef.current,
      )
    ) pendingProgressRef.current = null;
  }, [documentCompleted, savedProgress]);

  useEffect(() => {
    if (!assignmentCompleted) return;
    assignmentFinishedRef.current = true;
    pendingProgressRef.current = null;
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
    }
  }, [assignmentCompleted]);

  useEffect(() => {
    if (!contentCompatible || assignmentCompleted || documentCompleted) return undefined;
    const reader = readerRef.current;
    if (!reader) return undefined;
    scheduleMeasure();
    window.addEventListener("resize", scheduleMeasure);
    const observer = typeof ResizeObserver === "function"
      ? new ResizeObserver(scheduleMeasure)
      : null;
    observer?.observe(reader);
    if (reader.firstElementChild) observer?.observe(reader.firstElementChild);
    return () => {
      window.removeEventListener("resize", scheduleMeasure);
      observer?.disconnect();
      if (frameRef.current !== null) {
        window.cancelAnimationFrame(frameRef.current);
        frameRef.current = null;
      }
    };
  }, [assignmentCompleted, contentCompatible, documentCompleted, language, scheduleMeasure, taskIdentity]);

  useEffect(() => {
    if (documentCompleted && !assignmentCompleted) void finishAssignment();
  }, [
    assignmentCompleted,
    documentCompleted,
    finishAssignment,
    task.execution?.live,
    task.execution?.submit,
  ]);

  const retry = () => {
    clearError();
    if (!contentCompatible) {
      setContentRequestVersion((current) => current + 1);
      return;
    }
    if (assignmentCompleted) return;
    if (documentReceiptCompleteRef.current || documentCompleted) {
      submitAttemptedRef.current = false;
      void finishAssignment({ force: true });
      return;
    }
    pendingProgressRef.current = mergeDocumentReadProgress(
      pendingProgressRef.current,
      progressRef.current,
    );
    void flushProgress({ force: true });
  };

  return (
    <section className="document-reading-task">
      <header className="document-reading-header">
        <span><BookOpenText size={26} weight="duotone" /></span>
        <div>
          <small>{c("POLICY DOCUMENT", "政策文档")}</small>
          <h3>{loadedContent?.title || documentConfig.sourceTitle || "Overseas NT Policies"}</h3>
          <div className="document-reading-version">
            {c("Policy version", "政策版本")} · {versionLabel}
          </div>
          <p>{contentCompatible
            ? c(
              "Read the full document below. Reaching the end saves 100% and completes this task automatically.",
              "请完整阅读下方文档；到达末尾后会保存为 100%，并自动完成本任务。",
            )
            : c(
              contentLoadFailed
                ? "The current policy content could not be loaded. Retry shortly."
                : "The latest policy content is loading.",
              contentLoadFailed
                ? "当前政策内容加载失败，请稍后重试。"
                : "正在加载最新政策内容。",
            )}</p>
        </div>
        <span className={contentCompatible && documentCompleted ? "document-read-status is-complete" : "document-read-status"}>
          {contentCompatible && documentCompleted ? <CheckCircle size={16} weight="fill" /> : null}
          {!contentCompatible
            ? c("Syncing", "同步中")
            : documentCompleted
            ? c("Read", "已读完")
            : c(`${progress.readPercent}% read`, `已读 ${progress.readPercent}%`)}
        </span>
      </header>

      <div className="document-reading-progress" aria-hidden="true">
        <span style={{ width: `${contentCompatible ? progress.readPercent : 0}%` }} />
      </div>

      {contentCompatible ? (
        <div
          key={language}
          className="document-reading-scroll"
          ref={readerRef}
          onScroll={scheduleMeasure}
          role="region"
          tabIndex="0"
          aria-label={c("Overseas NT Policies document", "Overseas NT Policies 文档")}
        >
          <article className="document-reading-markdown">
            <Markdown
              remarkPlugins={[remarkGfm]}
              skipHtml
              components={{ a: externalLink, img: controlledImage }}
            >
              {policyDocumentMarkdown}
            </Markdown>
            <div className="document-reading-end" aria-label={c("End of document", "文档末尾")}>
              <CheckCircle size={22} weight="duotone" />
              <strong>{c("End of document", "文档已到底")}</strong>
            </div>
          </article>
        </div>
      ) : (
        <div className="document-reading-sync" role="status">
          <SpinnerGap className="document-reading-spinner" size={24} />
          <div>
            <strong>{contentLoadFailed
              ? c("Policy content is unavailable", "政策内容暂不可用")
              : c("Policy content is loading", "正在加载政策内容")}</strong>
            <span>{c(
              "Reading progress stays disabled until the server returns the exact document version assigned to this task.",
              "服务端返回与本任务匹配的精确文档版本前，阅读进度不会记录。",
            )}</span>
            {contentLoadFailed ? (
              <button type="button" onClick={retry}>{c("Retry", "重试")}</button>
            ) : null}
          </div>
        </div>
      )}

      <footer className="document-reading-footer">
        <div>
          {saving ? <SpinnerGap className="document-reading-spinner" size={18} /> : null}
          <span>{!contentCompatible
            ? c("No reading progress or task completion is sent before the assigned content loads.", "指定版本内容加载完成前，不会保存阅读进度，也不会提交任务完成。")
            : saving
            ? progress.reachedEnd || documentCompleted
              ? c("Saving your reading and finishing the task…", "正在保存阅读结果并完成任务…")
              : c("Saving progress…", "正在保存进度…")
            : task.previewReadOnly
              ? c(
                "Read-only preview: scrolling changes only this local preview and is not saved.",
                "只读预览：滚动进度仅在本地预览中显示，不会保存。",
              )
              : historicalCompletion
              ? c(
                "This task was completed previously. The current document is available for review; its reading receipt remains unchanged.",
                "该任务此前已完成；当前文档可供回看，文档阅读记录保持原样。",
              )
              : assignmentCompleted && documentCompleted
                ? c("Reading complete. You can review this document anytime.", "阅读任务已完成，之后可随时回看。")
                : documentCompleted
                  ? c("Reading is saved. Task completion is being finalized.", "阅读记录已保存，正在完成任务。")
                  : c("Progress is saved as you read.", "阅读过程中会持续保存进度。")}</span>
        </div>
        {completionError ? (
          <div className="document-reading-error" role="alert">
            <WarningCircle size={18} weight="fill" />
            <span>{completionError}</span>
            <button type="button" onClick={retry}>{c("Retry", "重试")}</button>
          </div>
        ) : null}
      </footer>
    </section>
  );
}
