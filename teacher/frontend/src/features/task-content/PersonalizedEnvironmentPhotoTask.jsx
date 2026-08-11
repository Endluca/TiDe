import { useEffect, useRef, useState } from "react";
import {
  ArrowClockwise,
  Camera,
  Check,
  CheckCircle,
  HourglassMedium,
  SealCheck,
  ShieldCheck,
  WarningCircle,
} from "@phosphor-icons/react";
import {
  localizeApiError,
  localizedValidationMessage,
} from "../../api-error-copy";
import { useI18n } from "../../i18n";
import ReadinessExampleGallery from "./ReadinessExampleGallery";
import {
  normalizeReadinessAnalysis,
  readinessPayloadFromValidation,
} from "./readiness-analysis";
import {
  TEACHING_ENVIRONMENT_REFERENCE_PHOTO,
  TEACHING_ENVIRONMENT_STANDARDS,
  teachingEnvironmentCameraErrors,
} from "./teaching-environment-photo";
import "./readiness-photo-task.css";

export default function PersonalizedEnvironmentPhotoTask({ task }) {
  const { language } = useI18n();
  const c = (en, zh) => (language === "zh" ? zh : en);
  const criteria = TEACHING_ENVIRONMENT_STANDARDS[language]
    || TEACHING_ENVIRONMENT_STANDARDS.en;
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const photoTakenRef = useRef(false);
  const reviewRequestRef = useRef(0);
  const executionReady = Boolean(task.execution?.live);
  const taskCompleted = task.status === "completed";
  const photoStep = task.execution?.findStep("ENVIRONMENT_PHOTO")
    || task.execution?.findStep("UPLOAD");
  const [cameraOpen, setCameraOpen] = useState(false);
  const [cameraReady, setCameraReady] = useState(false);
  const [opening, setOpening] = useState(false);
  const [photo, setPhoto] = useState("");
  const [photoFile, setPhotoFile] = useState(null);
  const [photoApproved, setPhotoApproved] = useState(taskCompleted);
  const [reviewChecks, setReviewChecks] = useState([]);
  const [analyzing, setAnalyzing] = useState(false);
  const [photoError, setPhotoError] = useState("");

  const stopCamera = () => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    setCameraOpen(false);
    setCameraReady(false);
  };

  const clearPhoto = () => {
    if (analyzing) return;
    reviewRequestRef.current += 1;
    if (photo.startsWith("blob:")) URL.revokeObjectURL(photo);
    setPhoto("");
    setPhotoFile(null);
    setPhotoApproved(false);
    setReviewChecks([]);
    setPhotoError("");
  };

  useEffect(() => () => stopCamera(), []);
  useEffect(() => () => {
    if (photo.startsWith("blob:")) URL.revokeObjectURL(photo);
  }, [photo]);
  useEffect(() => {
    if (taskCompleted) setPhotoApproved(true);
  }, [taskCompleted]);

  const applyValidation = (validation) => {
    const payload = readinessPayloadFromValidation(validation);
    if (!payload) return null;
    const analysis = normalizeReadinessAnalysis(
      payload,
      criteria.map(([id, title, detail]) => ({ id, title, detail })),
      c,
    );
    setReviewChecks(analysis.checks);
    setPhotoApproved(analysis.status === "approved");
    return analysis;
  };

  useEffect(() => {
    if (!executionReady || !task.execution?.loadValidation) return undefined;
    const controller = new AbortController();
    const requestId = ++reviewRequestRef.current;
    task.execution.loadValidation(controller.signal)
      .then((validation) => {
        if (requestId === reviewRequestRef.current) applyValidation(validation);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [executionReady, language, task.backendId]);

  useEffect(() => {
    if (!cameraOpen || !videoRef.current || !streamRef.current) return;
    videoRef.current.srcObject = streamRef.current;
    videoRef.current.play().catch(() => {
      task.execution?.track?.("CAMERA_FAILED", {
        stepKey: photoStep?.stepKey || "ENVIRONMENT_PHOTO",
        stepType: "CAMERA",
        errorCode: "CAMERA_PREVIEW_FAILED",
        result: "FAILURE",
      });
      setPhotoError(c(
        "The camera preview could not start. Refresh the page and try again.",
        "摄像头画面无法播放，请刷新页面后重新拍摄。",
      ));
      stopCamera();
    });
  }, [cameraOpen, language]);

  const openCamera = async () => {
    if (analyzing) return;
    setPhotoError("");
    if (!navigator.mediaDevices?.getUserMedia) {
      task.execution?.track?.("CAMERA_PERMISSION_RESULT", {
        stepKey: photoStep?.stepKey || "ENVIRONMENT_PHOTO",
        stepType: "CAMERA",
        permission: "UNSUPPORTED",
        result: "FAILURE",
      });
      setPhotoError(c(
        "This browser cannot open the camera. Use the latest Safari, Chrome or Edge.",
        "当前浏览器不支持网页摄像头，请改用最新版 Safari、Chrome 或 Edge。",
      ));
      return;
    }
    setOpening(true);
    try {
      stopCamera();
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: {
          facingMode: { ideal: "user" },
          width: { ideal: 1920 },
          height: { ideal: 1080 },
          aspectRatio: { ideal: 16 / 9 },
        },
      });
      streamRef.current = stream;
      setCameraOpen(true);
      task.execution?.track?.("CAMERA_PERMISSION_RESULT", {
        stepKey: photoStep?.stepKey || "ENVIRONMENT_PHOTO",
        stepType: "CAMERA",
        permission: "GRANTED",
        result: "SUCCESS",
      });
      task.execution?.track?.("CAMERA_OPENED", {
        stepKey: photoStep?.stepKey || "ENVIRONMENT_PHOTO",
        stepType: "CAMERA",
        result: "SUCCESS",
      });
    } catch (cameraError) {
      task.execution?.track?.("CAMERA_PERMISSION_RESULT", {
        stepKey: photoStep?.stepKey || "ENVIRONMENT_PHOTO",
        stepType: "CAMERA",
        permission: cameraError?.name === "NotAllowedError" ? "DENIED" : "UNAVAILABLE",
        errorCode: cameraError?.name || "CAMERA_OPEN_FAILED",
        result: "FAILURE",
      });
      task.execution?.track?.("CAMERA_FAILED", {
        stepKey: photoStep?.stepKey || "ENVIRONMENT_PHOTO",
        stepType: "CAMERA",
        errorCode: cameraError?.name || "CAMERA_OPEN_FAILED",
        result: "FAILURE",
      });
      const messages = teachingEnvironmentCameraErrors(c);
      setPhotoError(messages[cameraError?.name] || c(
        "The camera could not start. Check its permission and try again.",
        "摄像头暂时无法打开，请检查设备权限后重试。",
      ));
    } finally {
      setOpening(false);
    }
  };

  const capture = async () => {
    if (analyzing) return;
    const video = videoRef.current;
    if (!video?.videoWidth || !video?.videoHeight) {
      setPhotoError(c(
        "The camera is not ready yet. Wait a moment and try again.",
        "摄像头画面还没准备好，请稍等一秒再拍摄。",
      ));
      return;
    }
    if (video.videoHeight > video.videoWidth) {
      setPhotoError(c(
        "Rotate your device to landscape before taking the photo.",
        "请将设备横放，等画面切换为横向后再拍摄。",
      ));
      return;
    }
    const targetRatio = 16 / 9;
    const sourceRatio = video.videoWidth / video.videoHeight;
    let sourceX = 0;
    let sourceY = 0;
    let sourceWidth = video.videoWidth;
    let sourceHeight = video.videoHeight;
    if (sourceRatio > targetRatio) {
      sourceWidth = sourceHeight * targetRatio;
      sourceX = (video.videoWidth - sourceWidth) / 2;
    } else if (sourceRatio < targetRatio) {
      sourceHeight = sourceWidth / targetRatio;
      sourceY = (video.videoHeight - sourceHeight) / 2;
    }
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(640, Math.min(1920, Math.floor(sourceWidth)));
    canvas.height = Math.round(canvas.width * 9 / 16);
    canvas.getContext("2d")?.drawImage(
      video,
      sourceX,
      sourceY,
      sourceWidth,
      sourceHeight,
      0,
      0,
      canvas.width,
      canvas.height,
    );
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.9));
    if (!blob) {
      task.execution?.track?.("CAMERA_FAILED", {
        stepKey: photoStep?.stepKey || "ENVIRONMENT_PHOTO",
        stepType: "CAMERA",
        errorCode: "PHOTO_GENERATION_FAILED",
        result: "FAILURE",
      });
      setPhotoError(c("The photo could not be generated. Try again.", "照片生成失败，请重新拍摄。"));
      return;
    }
    task.execution?.track?.(
      photoTakenRef.current ? "CAMERA_PHOTO_RETAKEN" : "CAMERA_PHOTO_TAKEN",
      {
        stepKey: photoStep?.stepKey || "ENVIRONMENT_PHOTO",
        stepType: "CAMERA",
        result: "SUCCESS",
      },
    );
    photoTakenRef.current = true;
    reviewRequestRef.current += 1;
    if (photo.startsWith("blob:")) URL.revokeObjectURL(photo);
    setPhotoApproved(false);
    setReviewChecks([]);
    setPhotoError("");
    setPhotoFile(new File(
      [blob],
      `personalized-environment-${Date.now()}.jpg`,
      { type: "image/jpeg" },
    ));
    setPhoto(URL.createObjectURL(blob));
    stopCamera();
  };

  const submit = async () => {
    if (taskCompleted || analyzing || photoApproved) return;
    if (!executionReady || !photoFile || !photoStep || !task.execution) {
      setPhotoError(c(
        "This task is not connected to the photo-check service. Refresh and try again.",
        "当前任务尚未连接拍照检测服务，请刷新后重试。",
      ));
      return;
    }
    const requestId = ++reviewRequestRef.current;
    setAnalyzing(true);
    setPhotoError("");
    try {
      await task.execution.uploadStep(photoStep.stepKey, photoFile);
      const response = await task.execution.submit();
      const persistedValidation = task.execution.loadValidation
        ? await task.execution.loadValidation()
        : response?.validation;
      if (requestId !== reviewRequestRef.current) return;
      const analysis = applyValidation(persistedValidation);
      const passed = analysis?.status === "approved"
        || response?.status === "COMPLETED"
        || response?.validation?.status === "PASSED";
      if (passed) {
        stopCamera();
        setPhotoApproved(true);
      } else {
        const validation = persistedValidation ?? response?.validation;
        setPhotoError(analysis?.teacherMessage || localizedValidationMessage(
          validation,
          language,
          c(
            "The photo check did not finish. Review the result and retake the photo.",
            "照片检测未通过，请查看结果并重新拍摄。",
          ),
        ));
      }
    } catch (caught) {
      if (requestId === reviewRequestRef.current) {
        setPhotoError(localizeApiError(
          caught,
          language,
          c("The photo could not be submitted. Please retry.", "照片提交失败，请重试。"),
        ));
      }
    } finally {
      setAnalyzing(false);
    }
  };

  const refreshReview = async () => {
    if (!task.execution || analyzing) return;
    const requestId = ++reviewRequestRef.current;
    setAnalyzing(true);
    setPhotoError("");
    try {
      await task.execution.refresh?.();
      const validation = await task.execution.loadValidation?.();
      if (requestId !== reviewRequestRef.current) return;
      const analysis = applyValidation(validation);
      if (analysis?.status === "unavailable") {
        setPhotoError(analysis.teacherMessage);
      }
    } catch (caught) {
      if (requestId === reviewRequestRef.current) {
        setPhotoError(localizeApiError(
          caught,
          language,
          c("The review result could not be refreshed.", "审核结果刷新失败，请稍后重试。"),
        ));
      }
    } finally {
      setAnalyzing(false);
    }
  };

  const exampleFocusId = reviewChecks.find((check) => (
    ["fail", "uncertain"].includes(check.status)
  ))?.id || "camera_angle";
  const reviewPending = task.status === "verifying" && !photoApproved;

  return (
    <div className="readiness-photo-task g04-readiness-flow personalized-environment-photo-flow">
      <section className={`g04-part-card g04-photo-part personalized-environment-photo-card ${photoApproved ? "is-complete" : ""}`}>
        <header className="g04-part-header">
          <span className="g04-part-number">01</span>
          <div>
            <small>{c("PERSONALIZED IMPROVEMENT", "个性化改善")}</small>
            <h3>{c("Retake your teaching-view photo and pass the AI check", "重新拍摄授课画面并通过 AI 检测")}</h3>
          </div>
          <span className={`g04-part-status ${photoApproved ? "is-complete" : ""}`}>
            {photoApproved
              ? <CheckCircle size={17} weight="fill" />
              : reviewPending || analyzing
                ? <HourglassMedium size={17} weight="fill" />
                : <Camera size={17} />}
            {photoApproved
              ? c("Passed", "已通过")
              : reviewPending || analyzing
                ? c("Checking", "检测中")
                : c("To do", "待完成")}
          </span>
        </header>

        <div className="g04-part-body g04-photo-body">
          <div className="readiness-section-head compact-heading">
            <div>
              <span className="eyebrow">{c("TEACHING ENVIRONMENT PHOTO", "授课环境照片")}</span>
              <h3>{c("Use the same standard as the first-lesson check", "按首课检测同一标准拍照")}</h3>
              <p>{c(
                "One photo checks camera angle, lighting, background and dressing. All four items must pass.",
                "一张照片同时检测摄像头角度、光线、背景和着装，4 项全部通过才完成任务。",
              )}</p>
            </div>
            <Camera size={28} weight="fill" />
          </div>

          <div className="readiness-guidelines">
            {criteria.map(([id, title, detail], index) => (
              <article key={id}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <div><strong>{title}</strong><p>{detail}</p></div>
              </article>
            ))}
          </div>

          <ReadinessExampleGallery initialActiveId={exampleFocusId} />

          {reviewChecks.length > 0 && (
            <div className="readiness-result-list">
              {reviewChecks.map((check) => {
                const status = ["fail", "uncertain"].includes(check.status)
                  ? check.status
                  : "pass";
                return (
                  <article className={`result-${status}`} key={check.id || check.title}>
                    <span>{status === "pass"
                      ? <Check size={16} weight="bold" />
                      : <WarningCircle size={16} weight="fill" />}</span>
                    <div>
                      <strong>{check.title}</strong>
                      {check.message && <small>{check.message}</small>}
                      {check.suggestion && <p>{check.suggestion}</p>}
                    </div>
                    <em>{status === "pass"
                      ? c("Passed", "通过")
                      : status === "fail"
                        ? c("Adjust", "需调整")
                        : c("Uncertain", "无法判断")}</em>
                  </article>
                );
              })}
            </div>
          )}

          {photoApproved ? (
            <div className="readiness-complete" role="status">
              <SealCheck size={30} weight="fill" />
              <div>
                <strong>{c("Teaching-view photo check passed", "授课画面照片检测已通过")}</strong>
                <p>{c(
                  "The first qualified photo is saved as this personalized task's completion evidence.",
                  "首次合格照片已作为本次个性化任务的完成证据保存。",
                )}</p>
              </div>
            </div>
          ) : reviewPending ? (
            <div className="readiness-complete" role="status">
              <HourglassMedium size={30} weight="fill" />
              <div>
                <strong>{c("AI review is in progress", "AI 正在审核")}</strong>
                <p>{c("The photo is saved. Refresh shortly to view the four-item result.", "照片已保存，请稍后刷新查看四项检测结果。")}</p>
              </div>
              <button className="secondary-button" type="button" disabled={analyzing} onClick={refreshReview}>
                <ArrowClockwise size={17} />{c("Refresh result", "刷新结果")}
              </button>
            </div>
          ) : (
            <>
              {photoError && (
                <div className="readiness-error" role="alert">
                  <WarningCircle size={20} weight="fill" />{photoError}
                </div>
              )}

              <section className="readiness-capture">
                {cameraOpen ? (
                  <div className="readiness-live">
                    <div className="readiness-camera-stage">
                      <video ref={videoRef} autoPlay muted playsInline onCanPlay={() => setCameraReady(true)} />
                      <div className="readiness-person-guide" aria-hidden="true">
                        <svg viewBox="0 0 640 360"><ellipse cx="320" cy="105" rx="45" ry="58" /><path d="M190 310 C198 242 236 202 287 187 C296 184 302 177 304 166 M336 166 C338 177 344 184 353 187 C404 202 442 242 450 310" /></svg>
                        <span>{c("Face forward and align with the guide", "保持正脸并贴合辅助线")}</span>
                      </div>
                    </div>
                    <div className="readiness-camera-actions">
                      <button className="primary-button" type="button" disabled={!cameraReady || analyzing} onClick={capture}>
                        <Camera size={18} weight="fill" />
                        {cameraReady ? c("Take photo", "立即拍照") : c("Preparing camera…", "正在准备画面…")}
                      </button>
                      <button className="secondary-button" type="button" onClick={stopCamera}>{c("Cancel", "取消")}</button>
                    </div>
                  </div>
                ) : photo ? (
                  <div className="readiness-photo-preview">
                    <img src={photo} alt={c("Photo ready for the teaching-view check", "待检测的授课画面照片")} />
                    <div className="readiness-person-guide" aria-hidden="true">
                      <svg viewBox="0 0 640 360"><ellipse cx="320" cy="105" rx="45" ry="58" /><path d="M190 310 C198 242 236 202 287 187 C296 184 302 177 304 166 M336 166 C338 177 344 184 353 187 C404 202 442 242 450 310" /></svg>
                      <span>{c("Check: front-facing and aligned", "请确认：正脸且贴合辅助线")}</span>
                    </div>
                    <span><CheckCircle size={18} weight="fill" />{c("Photo ready", "照片已拍摄")}</span>
                    <button className="secondary-button" type="button" disabled={analyzing} onClick={clearPhoto}>{c("Retake", "重新拍摄")}</button>
                  </div>
                ) : (
                  <div className="readiness-empty">
                    <div className="readiness-example">
                      <img src={TEACHING_ENVIRONMENT_REFERENCE_PHOTO} alt={c("Qualified teaching-view example", "授课画面合格示例")} />
                      <span>{c("Qualified example", "合格示例")}</span>
                    </div>
                    <div className="readiness-empty-copy">
                      <Camera size={38} weight="duotone" />
                      <strong>{c("Take the photo in your real teaching position", "在真实授课位置拍摄")}</strong>
                      <small>{c(
                        "Use a clear 16:9 landscape view. Face forward and align your face and shoulders with the guide.",
                        "使用清晰的 16:9 横向画面；保持正脸，让脸部和肩部贴合辅助线。",
                      )}</small>
                      <button className="primary-button" type="button" disabled={opening || analyzing} onClick={openCamera}>
                        <Camera size={18} weight="fill" />
                        {opening ? c("Opening camera…", "正在打开摄像头…") : c("Open camera", "打开相机拍照")}
                      </button>
                    </div>
                  </div>
                )}
              </section>

              <button className="primary-button wide-button" type="button" disabled={!photoFile || analyzing} onClick={submit}>
                {analyzing
                  ? c("Checking the four items…", "正在检测 4 项画面标准…")
                  : c("Submit photo and start check", "提交照片并开始检测")}
              </button>
              <p className="readiness-retake-note">{c(
                "You can retake and resubmit until all four items pass. The first qualified photo becomes the completion evidence.",
                "4 项全部通过前可反复重拍并提交；首次合格照片将作为完成证据。",
              )}</p>
              <p className="readiness-privacy">
                <ShieldCheck size={17} weight="fill" />
                {c(
                  "Use your real teaching environment and keep private information out of view.",
                  "请使用真实授课环境拍摄，并避免在画面中暴露个人隐私信息。",
                )}
              </p>
            </>
          )}
        </div>
      </section>
    </div>
  );
}
