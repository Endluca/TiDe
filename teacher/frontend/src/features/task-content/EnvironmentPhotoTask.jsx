import { useEffect, useRef, useState } from "react";
import {
  ArrowClockwise,
  CalendarBlank,
  Camera,
  CaretDown,
  Check,
  CheckCircle,
  ClipboardText,
  ImagesSquare,
  SealCheck,
  Timer,
  WarningCircle,
} from "@phosphor-icons/react";
import { localizeApiError } from "../../api-error-copy";
import { useI18n } from "../../i18n";
import { publicAsset } from "../../public-assets";
import { Toki } from "../../components/UI";
import { downloadTeacherPhoto, getTeacherPhoto, submitTeacherPhoto } from "../../api/teacher-photo-api";
import { formatFirstLessonCountdown, formatFirstLessonTime, getFirstLessonReminder } from "./first-lesson-reminder";
import {
  isReadinessPendingStatus,
  normalizeReadinessAnalysis,
} from "./readiness-analysis";
import ReadinessExampleGallery from "./ReadinessExampleGallery";
import "./environment-photo-task.css";
import "./readiness-photo-task.css";

const CAMERA_REFERENCE_PHOTO = publicAsset("/readiness/lesson-preparation-examples/camera-angle-good-front.jpg");
const READINESS_RULESET_VERSION = "lesson-preparation-camera-view-2026-07-v3-strict";
const COURSEWARE_STATEMENT = "I have reviewed all the slides and finished preparing for this lesson.";

const readinessStandards = {
  en: [
    { id: "camera_angle", title: "Camera angle", detail: "Show exactly one teacher with the full unobstructed face and chest-up upper body clearly visible. Keep your head centered with a little space above it and the camera near eye level." },
    { id: "lighting", title: "Lighting", detail: "Both sides of your face and facial features must be clearly visible in even front light. Darkness, heavy shadows, overexposure, strong backlight or masking filters do not pass." },
    { id: "background", title: "Background", detail: "Use a clean, stable background with no unrelated people, animals, obvious clutter or identifiable private information. Virtual backgrounds must not break or cover you." },
    { id: "dressing", title: "Dressing", detail: "Your shoulders, neckline and neat professional top must be clearly visible. Sleepwear, loungewear, sleeveless or overly casual clothing and distracting accessories do not pass." },
  ],
  zh: [
    { id: "camera_angle", title: "摄像头角度", detail: "画面只保留一位老师，面部无遮挡且清晰完整，头顶留少量空间，胸部以上入镜；人物居中，镜头与眼睛大致平齐。" },
    { id: "lighting", title: "光线", detail: "面部两侧与五官都必须清楚可辨，正面光线均匀；过暗、重阴影、过曝、强逆光或遮盖五官的滤镜不通过。" },
    { id: "background", title: "背景", detail: "背景整洁稳定，无无关人员、动物、明显杂物或可识别的隐私信息；虚拟背景不得破损、穿帮或遮挡人物。" },
    { id: "dressing", title: "着装", detail: "肩颈和整洁、专业的上衣必须清楚可见；睡衣、居家服、无袖、明显过于休闲或干扰性强的服饰不通过。" },
  ],
};

const READINESS_POLL_INTERVAL_MS = 2_000;
const READINESS_POLL_ATTEMPTS = 20;

function waitForReadinessPoll() {
  return new Promise((resolve) => window.setTimeout(resolve, READINESS_POLL_INTERVAL_MS));
}

async function requestReadinessAnalysis(file, criteria, c, taskInstanceId, onPending) {
  if (taskInstanceId) {
    let payload = await submitTeacherPhoto(taskInstanceId, file);
    for (
      let attempt = 0;
      attempt < READINESS_POLL_ATTEMPTS && isReadinessPendingStatus(payload?.status);
      attempt += 1
    ) {
      onPending?.(normalizeReadinessAnalysis(
        { ...payload, provider: "tide-teacher-photo" },
        criteria,
        c,
      ));
      await waitForReadinessPoll();
      payload = await getTeacherPhoto(taskInstanceId);
    }
    return normalizeReadinessAnalysis({ ...payload, provider: "tide-teacher-photo" }, criteria, c);
  }
  const endpoint = import.meta.env.VITE_READINESS_AI_ENDPOINT;
  if (!endpoint) {
    throw new Error(c(
      "The real AI camera check is not connected yet. This photo cannot be marked as passed.",
      "真实 AI 画面检测服务尚未接入，本次照片不能判定为合格。",
    ));
  }
  const form = new FormData();
  form.append("photo", file, file.name);
  form.append("submissionId", `lesson-preparation-${Date.now()}`);
  form.append("rulesetVersion", READINESS_RULESET_VERSION);
  form.append("criteria", JSON.stringify(criteria));
  const response = await fetch(endpoint, { method: "POST", body: form });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.message || "";
    const retryMatch = detail.match(/Please retry in\s+([\d.]+)s/i);
    const retrySeconds = retryMatch ? Math.max(1, Math.ceil(Number(retryMatch[1]))) : null;
    if (/quota|rate.?limit|resource.?exhausted|too many requests/i.test(detail) || response.status === 429) {
      throw new Error(retrySeconds
        ? c(`AI check requests are too frequent. Try again in ${retrySeconds} seconds.`, `AI 检测请求过于频繁，请等待 ${retrySeconds} 秒后再重试。`)
        : c("AI check requests are too frequent. Try again shortly.", "AI 检测请求过于频繁，请稍后重试。"));
    }
    throw new Error(c("The AI check is temporarily unavailable. Try again shortly.", "AI 检测服务暂时不可用，请稍后重试。"));
  }
  return normalizeReadinessAnalysis(payload, criteria, c);
}

export default function EnvironmentPhotoTask({ task, onUpdate }) {
  const { language } = useI18n();
  const c = (en, zh) => language === "zh" ? zh : en;
  const criteria = readinessStandards[language] || readinessStandards.en;
  const validCriterionIds = new Set(readinessStandards.en.map((item) => item.id));
  const savedAnalysisIsLegacy = Boolean(task.readinessAnalysis?.checks?.some((check) => !validCriterionIds.has(check.id || check.code)));
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const countdownTimerRef = useRef(null);
  const mountedRef = useRef(true);
  const photoTakenRef = useRef(Boolean(task.readinessAnalysis));
  const uploadAttemptRef = useRef(0);
  const [now, setNow] = useState(() => Date.now());
  const [coursewareConfirmed, setCoursewareConfirmed] = useState(task.coursewarePrepared === true);
  const [proof, setProof] = useState(null);
  const [proofPreview, setProofPreview] = useState("");
  const [cameraOpen, setCameraOpen] = useState(false);
  const [cameraReady, setCameraReady] = useState(false);
  const [cameraPortrait, setCameraPortrait] = useState(false);
  const [openingCamera, setOpeningCamera] = useState(false);
  const [cameraError, setCameraError] = useState("");
  const [captureDelay, setCaptureDelay] = useState(0);
  const [cameraCountdown, setCameraCountdown] = useState(0);
  const [analyzing, setAnalyzing] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [examplesOpen, setExamplesOpen] = useState(true);
  const [exampleFocusId, setExampleFocusId] = useState("camera_angle");
  const [analysis, setAnalysis] = useState(savedAnalysisIsLegacy ? null : task.readinessAnalysis || null);
  const deviceStep = task.execution?.findStep("DEVICE_CHECK");
  const deviceRequired = Boolean(deviceStep);
  const deviceProgress = task.execution?.steps?.[deviceStep?.stepKey];
  const [deviceCheckPassed, setDeviceCheckPassed] = useState(!deviceRequired || deviceProgress?.status === "COMPLETED");
  const [deviceResults, setDeviceResults] = useState(deviceProgress?.details?.results || {});
  const [checkingDevice, setCheckingDevice] = useState(false);
  const [deviceError, setDeviceError] = useState("");
  const photoApproved = analysis?.status === "approved";
  const photoSaved = photoApproved && analysis?.beautyStatus === "READY" && Boolean(analysis?.finalPhoto?.fileId);
  const photoComplete = photoApproved && (!task.backendId || photoSaved);
  const photoProcessing = analysis?.status === "processing" || (photoApproved && task.backendId && !photoSaved);
  const taskCompleted = task.status === "completed" && deviceCheckPassed && coursewareConfirmed && photoComplete;
  const firstLessonReminder = getFirstLessonReminder(task, now);

  const progressFor = ({ confirmed = coursewareConfirmed, photoStatus = analysis?.status, saved = photoSaved } = {}) => {
    const deviceProgressValue = deviceCheckPassed ? 15 : 0;
    const confirmationProgress = confirmed ? 15 : 0;
    const photoProgress = saved || photoStatus === "approved" ? 70 : photoStatus === "changes_requested" ? 40 : 0;
    return Math.min(95, deviceProgressValue + confirmationProgress + photoProgress);
  };

  const toggleCourseware = () => {
    if (taskCompleted) return;
    const next = !coursewareConfirmed;
    setCoursewareConfirmed(next);
    const completed = deviceCheckPassed && next && photoComplete;
    onUpdate(task.id, completed ? "completed" : analysis?.status === "changes_requested" ? "retry_required" : photoApproved ? "verifying" : "started", {
      coursewarePrepared: next,
      coursewarePreparedAt: next ? new Date().toISOString() : null,
      environmentChecks: next ? [0] : [],
      progress: completed ? 100 : progressFor({ confirmed: next }),
    });
  };

  const runDeviceCheck = async () => {
    if (checkingDevice || !deviceRequired) return;
    setCheckingDevice(true);
    setDeviceError("");
    const results = {
      camera: "FAILED",
      microphone: "FAILED",
      network: navigator.onLine ? "PASSED" : "FAILED",
    };
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        task.execution?.track?.("CAMERA_PERMISSION_RESULT", {
          stepKey: deviceStep.stepKey,
          stepType: "DEVICE_CHECK",
          permission: "UNSUPPORTED",
          result: "FAILURE",
        });
        throw new Error(c("This browser cannot check the camera and microphone.", "当前浏览器无法检测摄像头和麦克风。"));
      }
      const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
      task.execution?.track?.("CAMERA_PERMISSION_RESULT", {
        stepKey: deviceStep.stepKey,
        stepType: "DEVICE_CHECK",
        permission: "GRANTED",
        result: "SUCCESS",
      });
      task.execution?.track?.("CAMERA_OPENED", {
        stepKey: deviceStep.stepKey,
        stepType: "DEVICE_CHECK",
        result: "SUCCESS",
      });
      results.camera = stream.getVideoTracks().length > 0 ? "PASSED" : "FAILED";
      results.microphone = stream.getAudioTracks().length > 0 ? "PASSED" : "FAILED";
      stream.getTracks().forEach((track) => track.stop());
      const response = await task.execution.saveStep(deviceStep.stepKey, { results });
      const passed = response?.step?.status === "COMPLETED";
      setDeviceResults(results);
      setDeviceCheckPassed(passed);
      if (!passed) {
        setDeviceError(c("Check the device permission and network, then try again.", "请检查摄像头、麦克风权限和网络后重试。"));
      }
    } catch (caught) {
      if (navigator.mediaDevices?.getUserMedia) {
        task.execution?.track?.("CAMERA_PERMISSION_RESULT", {
          stepKey: deviceStep.stepKey,
          stepType: "DEVICE_CHECK",
          permission: caught?.name === "NotAllowedError" ? "DENIED" : "UNAVAILABLE",
          errorCode: caught?.code || caught?.name || "DEVICE_CHECK_FAILED",
          result: "FAILURE",
        });
      }
      task.execution?.track?.("CAMERA_FAILED", {
        stepKey: deviceStep.stepKey,
        stepType: "DEVICE_CHECK",
        errorCode: caught?.code || caught?.name || "DEVICE_CHECK_FAILED",
        result: "FAILURE",
      });
      setDeviceResults(results);
      setDeviceCheckPassed(false);
      setDeviceError(caught.message);
    } finally {
      setCheckingDevice(false);
    }
  };

  const clearCameraCountdown = () => {
    if (countdownTimerRef.current) window.clearInterval(countdownTimerRef.current);
    countdownTimerRef.current = null;
    if (mountedRef.current) setCameraCountdown(0);
  };

  const stopCamera = () => {
    clearCameraCountdown();
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    if (mountedRef.current) {
      setCameraOpen(false);
      setCameraReady(false);
      setCameraPortrait(false);
    }
  };

  useEffect(() => {
    mountedRef.current = true;
    const reminderTimer = window.setInterval(() => setNow(Date.now()), 60000);
    return () => {
      mountedRef.current = false;
      window.clearInterval(reminderTimer);
      if (countdownTimerRef.current) window.clearInterval(countdownTimerRef.current);
      streamRef.current?.getTracks().forEach((track) => track.stop());
    };
  }, []);

  useEffect(() => {
    if (!savedAnalysisIsLegacy) return;
    onUpdate(task.id, "started", {
      readinessPhotoName: null,
      readinessPhotoSize: null,
      readinessPhotoSubmittedAt: null,
      readinessAnalysis: null,
      readinessChecks: [],
      readinessCheckedAt: null,
      progress: progressFor({ photoStatus: null, saved: false }),
    });
  }, []);

  useEffect(() => {
    if (!task.backendId || analysis) return undefined;
    const controller = new AbortController();
    const restore = async () => {
      let payload = await getTeacherPhoto(task.backendId, controller.signal);
      for (
        let attempt = 0;
        attempt < READINESS_POLL_ATTEMPTS &&
          isReadinessPendingStatus(payload?.status) &&
          !controller.signal.aborted;
        attempt += 1
      ) {
        const pending = normalizeReadinessAnalysis(
          { ...payload, provider: "tide-teacher-photo" },
          criteria,
          c,
        );
        setAnalysis(pending);
        await waitForReadinessPoll();
        payload = await getTeacherPhoto(task.backendId, controller.signal);
      }
      if (controller.signal.aborted) return;
      const restored = normalizeReadinessAnalysis(
        { ...payload, provider: "tide-teacher-photo" },
        criteria,
        c,
      );
      setAnalysis(restored);
      if (restored.beautyStatus !== "READY") return;
      const blob = await downloadTeacherPhoto(task.backendId, controller.signal);
      if (controller.signal.aborted) return;
      if (proofPreview.startsWith("blob:")) URL.revokeObjectURL(proofPreview);
      setProofPreview(URL.createObjectURL(blob));
    };
    restore()
      .catch(() => undefined);
    return () => controller.abort();
  }, [task.backendId]);

  useEffect(() => {
    if (!deviceCheckPassed || !coursewareConfirmed || !photoComplete || task.status === "completed") return;
    onUpdate(task.id, "completed", { progress: 100 });
  }, [deviceCheckPassed, coursewareConfirmed, photoComplete, task.status]);

  useEffect(() => () => {
    if (proofPreview.startsWith("blob:")) URL.revokeObjectURL(proofPreview);
  }, [proofPreview]);

  useEffect(() => {
    if (!cameraOpen || !videoRef.current || !streamRef.current) return;
    videoRef.current.srcObject = streamRef.current;
    videoRef.current.play().catch(() => {
      task.execution?.track?.("CAMERA_FAILED", {
        stepKey: "ENVIRONMENT_PHOTO",
        stepType: "CAMERA",
        errorCode: "CAMERA_PREVIEW_FAILED",
        result: "FAILURE",
      });
      setCameraError(c("The camera preview could not start. Refresh the page and try again.", "摄像头画面无法播放，请刷新页面后重新拍摄。"));
      stopCamera();
    });
  }, [cameraOpen, language]);

  const openCamera = async () => {
    setCameraError("");
    if (!window.isSecureContext) {
      task.execution?.track?.("CAMERA_PERMISSION_RESULT", {
        stepKey: "ENVIRONMENT_PHOTO",
        stepType: "CAMERA",
        permission: "INSECURE_CONTEXT",
        result: "FAILURE",
      });
      task.execution?.track?.("CAMERA_FAILED", {
        stepKey: "ENVIRONMENT_PHOTO",
        stepType: "CAMERA",
        errorCode: "CAMERA_INSECURE_CONTEXT",
        result: "FAILURE",
      });
      setCameraError(c("The web camera requires HTTPS. Use the provided HTTPS address on mobile, or localhost on a computer.", "网页摄像头需要 HTTPS。手机请使用提供的 HTTPS 测试地址，电脑可使用 localhost。"));
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      task.execution?.track?.("CAMERA_PERMISSION_RESULT", {
        stepKey: "ENVIRONMENT_PHOTO",
        stepType: "CAMERA",
        permission: "UNSUPPORTED",
        result: "FAILURE",
      });
      task.execution?.track?.("CAMERA_FAILED", {
        stepKey: "ENVIRONMENT_PHOTO",
        stepType: "CAMERA",
        errorCode: "CAMERA_UNSUPPORTED",
        result: "FAILURE",
      });
      setCameraError(c("This browser does not support the web camera. Use the latest Safari, Chrome or Edge.", "当前浏览器不支持网页摄像头，请改用最新版 Safari、Chrome 或 Edge。"));
      return;
    }
    setOpeningCamera(true);
    try {
      stopCamera();
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { facingMode: { ideal: "user" }, width: { ideal: 1920 }, height: { ideal: 1080 }, aspectRatio: { ideal: 16 / 9 } },
      });
      streamRef.current = stream;
      setCameraOpen(true);
      task.execution?.track?.("CAMERA_PERMISSION_RESULT", {
        stepKey: "ENVIRONMENT_PHOTO",
        stepType: "CAMERA",
        permission: "GRANTED",
        result: "SUCCESS",
      });
      task.execution?.track?.("CAMERA_OPENED", {
        stepKey: "ENVIRONMENT_PHOTO",
        stepType: "CAMERA",
        result: "SUCCESS",
      });
    } catch (error) {
      task.execution?.track?.("CAMERA_PERMISSION_RESULT", {
        stepKey: "ENVIRONMENT_PHOTO",
        stepType: "CAMERA",
        permission: error?.name === "NotAllowedError" ? "DENIED" : "UNAVAILABLE",
        errorCode: error?.name || "CAMERA_OPEN_FAILED",
        result: "FAILURE",
      });
      task.execution?.track?.("CAMERA_FAILED", {
        stepKey: "ENVIRONMENT_PHOTO",
        stepType: "CAMERA",
        errorCode: error?.name || "CAMERA_OPEN_FAILED",
        result: "FAILURE",
      });
      const messages = {
        NotAllowedError: c("Camera permission was not granted. Allow camera access in the browser site settings, then try again.", "未获得摄像头权限。请在浏览器地址栏的站点设置中允许使用摄像头后重试。"),
        NotFoundError: c("No camera was found. Check that a camera is available, then try again.", "没有检测到可用摄像头，请确认设备摄像头可用后重试。"),
        NotReadableError: c("The camera is being used by another app. Close that app and try again.", "摄像头正被其他程序占用，请关闭后重试。"),
        OverconstrainedError: c("This camera does not support the required image size. Try a different camera.", "当前摄像头不支持所需画面规格，请换一个摄像头后重试。"),
      };
      setCameraError(messages[error?.name] || c("The camera could not start. Check its permission and try again.", "摄像头暂时无法打开，请检查权限后重试。"));
    } finally {
      setOpeningCamera(false);
    }
  };

  const capturePhoto = async () => {
    const video = videoRef.current;
    if (!video?.videoWidth || !video?.videoHeight) {
      setCameraError(c("The camera is not ready yet. Wait a moment and try again.", "摄像头画面还没准备好，请稍等一秒再拍摄。"));
      return;
    }
    if (video.videoHeight > video.videoWidth) {
      setCameraPortrait(true);
      setCameraError(c("Rotate your device to landscape before taking the photo.", "请将设备横放，等画面切换为横向后再拍摄。"));
      return;
    }
    const targetRatio = 16 / 9;
    const sourceRatio = video.videoWidth / video.videoHeight;
    let sx = 0;
    let sy = 0;
    let sw = video.videoWidth;
    let sh = video.videoHeight;
    if (sourceRatio > targetRatio) {
      sw = sh * targetRatio;
      sx = (video.videoWidth - sw) / 2;
    } else if (sourceRatio < targetRatio) {
      sh = sw / targetRatio;
      sy = (video.videoHeight - sh) / 2;
    }
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(16, Math.floor(Math.min(1920, sw) / 16) * 16);
    canvas.height = canvas.width * 9 / 16;
    const context = canvas.getContext("2d");
    if (!context) {
      setCameraError(c("This browser could not generate the photo. Refresh the page or try another browser.", "当前浏览器无法生成照片，请刷新页面或更换浏览器重试。"));
      return;
    }
    context.drawImage(video, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.92));
    if (!blob) {
      task.execution?.track?.("CAMERA_FAILED", {
        stepKey: "ENVIRONMENT_PHOTO",
        stepType: "CAMERA",
        errorCode: "PHOTO_GENERATION_FAILED",
        result: "FAILURE",
      });
      setCameraError(c("The photo could not be generated. Reopen the camera and try again.", "照片生成失败，请重新打开摄像头后拍摄。"));
      return;
    }
    task.execution?.track?.(
      photoTakenRef.current ? "CAMERA_PHOTO_RETAKEN" : "CAMERA_PHOTO_TAKEN",
      {
        stepKey: "ENVIRONMENT_PHOTO",
        stepType: "CAMERA",
        result: "SUCCESS",
      },
    );
    photoTakenRef.current = true;
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
    setProof(new File([blob], `camera-view-${timestamp}.jpg`, { type: "image/jpeg" }));
    if (proofPreview.startsWith("blob:")) URL.revokeObjectURL(proofPreview);
    setProofPreview(URL.createObjectURL(blob));
    setAnalysis(null);
    setCameraError("");
    stopCamera();
  };

  const startTimedCapture = () => {
    if (countdownTimerRef.current) {
      clearCameraCountdown();
      return;
    }
    if (captureDelay <= 0) {
      void capturePhoto();
      return;
    }
    let remaining = captureDelay;
    setCameraCountdown(remaining);
    countdownTimerRef.current = window.setInterval(() => {
      remaining -= 1;
      if (remaining <= 0) {
        window.clearInterval(countdownTimerRef.current);
        countdownTimerRef.current = null;
        if (mountedRef.current) setCameraCountdown(0);
        void capturePhoto();
      } else if (mountedRef.current) {
        setCameraCountdown(remaining);
      }
    }, 1000);
  };

  const syncCameraFrame = () => {
    const video = videoRef.current;
    if (!video?.videoWidth || !video?.videoHeight) return;
    const isPortrait = video.videoHeight > video.videoWidth;
    setCameraReady(true);
    setCameraPortrait(isPortrait);
    if (isPortrait && cameraCountdown > 0) clearCameraCountdown();
  };

  const clearProof = () => {
    if (proofPreview.startsWith("blob:")) URL.revokeObjectURL(proofPreview);
    setProof(null);
    setProofPreview("");
    setAnalysis(null);
    setCameraError("");
  };

  const submitPhoto = async () => {
    if (!proof || analyzing) return;
    setAnalyzing(true);
    setCameraError("");
    uploadAttemptRef.current += 1;
    const uploadAttempt = uploadAttemptRef.current;
    const uploadStartedAt = performance.now();
    task.execution?.track?.(uploadAttempt > 1 ? "UPLOAD_RETRIED" : "UPLOAD_STARTED", {
      stepKey: "ENVIRONMENT_PHOTO",
      stepType: "CAMERA",
      retryNo: uploadAttempt,
      contentType: proof.type || "image/jpeg",
      result: "STARTED",
    });
    try {
      const result = await requestReadinessAnalysis(
        proof,
        criteria,
        c,
        task.backendId,
        setAnalysis,
      );
      task.execution?.track?.("UPLOAD_SUCCEEDED", {
        stepKey: "ENVIRONMENT_PHOTO",
        stepType: "CAMERA",
        retryNo: uploadAttempt,
        contentType: proof.type || "image/jpeg",
        durationMs: Math.round(performance.now() - uploadStartedAt),
        result: "SUCCESS",
      });
      setAnalysis(result);
      if (result.status === "changes_requested") {
        const firstIssue = result.checks.find((check) => check.status !== "pass");
        setExampleFocusId(firstIssue?.id || "camera_angle");
        setExamplesOpen(true);
      }
      const saved = result.status === "approved" && result.beautyStatus === "READY" && Boolean(result.finalPhoto?.fileId);
      const completed = deviceCheckPassed && result.status === "approved" && (saved || !task.backendId) && coursewareConfirmed;
      onUpdate(task.id, completed ? "completed" : result.status === "changes_requested" ? "retry_required" : result.status === "approved" ? "verifying" : "started", {
        coursewarePrepared: coursewareConfirmed,
        environmentChecks: coursewareConfirmed ? [0] : [],
        readinessPhotoName: proof.name,
        readinessPhotoSize: proof.size,
        readinessPhotoSubmittedAt: new Date().toISOString(),
        readinessAnalysis: result,
        readinessChecks: result.checks,
        readinessCheckedAt: result.checkedAt,
        teacherPhotoRunId: result.photoRunId,
        teacherPhotoProcessingStatus: result.beautyStatus,
        teacherPhotoFinalFileId: result.finalPhoto?.fileId || null,
        teacherPhotoFilterPreset: result.finalPhoto?.filterPreset || null,
        teacherPhotoFilterStrength: result.finalPhoto?.filterStrength || null,
        teacherPhotoSavedAt: result.finalPhoto?.savedAt || null,
        progress: completed ? 100 : progressFor({ photoStatus: result.status, saved }),
      });
      if (saved && task.backendId) {
        downloadTeacherPhoto(task.backendId)
          .then((blob) => {
            if (!mountedRef.current) return;
            if (proofPreview.startsWith("blob:")) URL.revokeObjectURL(proofPreview);
            setProofPreview(URL.createObjectURL(blob));
          })
          .catch(() => undefined);
      }
    } catch (error) {
      task.execution?.track?.("UPLOAD_FAILED", {
        stepKey: "ENVIRONMENT_PHOTO",
        stepType: "CAMERA",
        retryNo: uploadAttempt,
        contentType: proof.type || "image/jpeg",
        durationMs: Math.round(performance.now() - uploadStartedAt),
        errorCode: error?.code || error?.name || "UPLOAD_FAILED",
        result: "FAILURE",
      });
      setCameraError(localizeApiError(
        error,
        language,
        c("The photo could not be checked. Please try again later.", "照片暂时无法检测，请稍后重试。"),
      ));
    } finally {
      if (mountedRef.current) setAnalyzing(false);
    }
  };

  const resetDetection = () => {
    setResetting(true);
    stopCamera();
    clearProof();
    onUpdate(task.id, "started", {
      readinessPhotoName: null,
      readinessPhotoSize: null,
      readinessPhotoSubmittedAt: null,
      readinessAnalysis: null,
      readinessChecks: [],
      readinessCheckedAt: null,
      progress: progressFor({ photoStatus: null, saved: false }),
    });
    window.setTimeout(() => {
      if (mountedRef.current) setResetting(false);
    }, 250);
  };

  const totalSteps = deviceRequired ? 3 : 2;
  const completedSteps = Number(deviceRequired && deviceCheckPassed) + Number(photoComplete) + Number(coursewareConfirmed);

  return (
    <div className="environment-photo-task">
      <header className="lesson-focus-intro">
        <div>
          <span className="eyebrow">{c("FIRST-LESSON PREPARATION", "首课准备")}</span>
          <h2>{c("Complete your pre-class setup", "完成首课前的设备与画面准备")}</h2>
          <p>{c("Check your device and network, then use one camera frame to review four visible items.", "先检查设备与网络，再用同一张摄像头画面完成四项可见内容检测。")}</p>
        </div>
        <div className="lesson-completion-count" aria-label={c(`${completedSteps} of ${totalSteps} steps complete`, `已完成 ${completedSteps} / ${totalSteps} 项`)}>
          <strong>{completedSteps}/{totalSteps}</strong>
          <small>{c("complete", "已完成")}</small>
        </div>
      </header>

      {firstLessonReminder && !taskCompleted && (
        <section className={`first-lesson-inline-reminder level-${firstLessonReminder.level}`} role="status">
          <CalendarBlank size={20} weight="duotone" />
          <div>
            <strong>{c("First lesson", "首课时间")} · {formatFirstLessonTime(firstLessonReminder.startsAt, language)}</strong>
            <small>{c("Finish all preparation items before class.", "请在上课前完成全部准备项目。")}</small>
          </div>
          <span><Timer size={16} weight="fill" />{formatFirstLessonCountdown(firstLessonReminder.minutesRemaining, language)}</span>
        </section>
      )}

      {deviceRequired && (
        <section className={`lesson-device-check ${deviceCheckPassed ? "is-complete" : ""}`}>
          <span className="lesson-module-number">01</span>
          <div className="lesson-device-check-copy">
            <small>{c("DEVICE & NETWORK", "设备与网络")}</small>
            <strong>{c("Check camera, microphone and connection", "检查摄像头、麦克风与网络连接")}</strong>
            <p>{c("The browser will request camera and microphone permission only for this check.", "浏览器只会为本次检测申请摄像头和麦克风权限。")}</p>
            {Object.keys(deviceResults).length > 0 && (
              <div className="lesson-device-results">
                {[
                  ["camera", c("Camera", "摄像头")],
                  ["microphone", c("Microphone", "麦克风")],
                  ["network", c("Network", "网络")],
                ].map(([key, label]) => (
                  <span className={deviceResults[key] === "PASSED" ? "passed" : "failed"} key={key}>
                    {deviceResults[key] === "PASSED" ? <Check size={13} weight="bold" /> : <WarningCircle size={13} weight="fill" />}
                    {label}
                  </span>
                ))}
              </div>
            )}
            {deviceError && <div className="readiness-error" role="alert"><WarningCircle size={18} weight="fill" />{deviceError}</div>}
          </div>
          <button className={deviceCheckPassed ? "secondary-button" : "primary-button"} type="button" disabled={checkingDevice} onClick={runDeviceCheck}>
            {deviceCheckPassed
              ? <><CheckCircle size={17} weight="fill" />{c("Check passed", "检测已通过")}</>
              : checkingDevice
                ? c("Checking…", "正在检测…")
                : c("Run device check", "开始设备检测")}
          </button>
        </section>
      )}

      <section className="lesson-camera-module">
        {deviceRequired && (
          <div className="lesson-camera-module-heading">
            <span className="lesson-module-number">02</span>
            <div><small>{c("CAMERA VIEW", "上课画面")}</small><strong>{c("Check four visible teaching-readiness items", "检测四项可见的授课准备内容")}</strong></div>
          </div>
        )}
        <p className="readiness-boundary-note">
          <WarningCircle size={18} weight="fill" />
          {c(
            "Strict check: the photo must be at least 640×360 in a 16:9 landscape frame. All four items must pass with at least 85% confidence; an unclear item requires a retake.",
            "严格标准：照片至少 640×360、16:9 横向；四项必须全部通过，且每项判断置信度不低于 85%，看不清或判断不稳都会要求重拍。",
          )}
        </p>
        {photoApproved ? (
          <div className="readiness-complete" role="status">
            <SealCheck size={30} weight="fill" />
            <div>
              <small>{c("Camera check complete", "画面检测已完成")}</small>
              <strong>{c("All four visible items passed", "四项画面内容均已通过")}</strong>
              <p>{photoProcessing
                ? c("The evidence is being saved. No further action is needed.", "正在保存检测证据，你不需要再操作。")
                : coursewareConfirmed
                  ? c("Both first-lesson preparation items are complete.", "首课准备的两项内容都已完成。")
                  : c("Confirm your courseware below to finish.", "再确认下方的课件准备即可完成。")}</p>
              {photoSaved && proofPreview && <img className="readiness-final-photo" src={proofPreview} alt={c("Saved camera-check evidence", "已保存的画面检测证据")} />}
            </div>
          </div>
        ) : (
          <>
            <section className={cameraOpen ? "readiness-capture camera-active" : proofPreview ? "readiness-capture has-photo" : "readiness-capture"}>
              {cameraOpen ? (
                <div className="readiness-live">
                  <div className="readiness-camera-stage">
                    <video ref={videoRef} autoPlay muted playsInline aria-label={c("Live camera preview", "摄像头实时预览")} aria-describedby="lesson-readiness-camera-guide" onLoadedMetadata={syncCameraFrame} onCanPlay={syncCameraFrame} onResize={syncCameraFrame} />
                    <div className="readiness-person-guide" aria-hidden="true">
                      <svg viewBox="0 0 640 360"><ellipse cx="320" cy="105" rx="45" ry="58" /><path d="M190 310 C198 242 236 202 287 187 C296 184 302 177 304 166 M336 166 C338 177 344 184 353 187 C404 202 442 242 450 310" /></svg>
                    </div>
                    <span id="lesson-readiness-camera-guide" className="readiness-visually-hidden">{c("Use a landscape frame, keep your head and upper body centered, and place the camera near eye level.", "使用横向画面，头部和上半身大致居中，并让镜头尽量与眼睛平齐。")}</span>
                    {cameraPortrait && <span className="readiness-orientation-warning" role="status">{c("Rotate the device before taking the photo", "请横放设备后再拍照")}</span>}
                    {cameraCountdown > 0 && <div className="readiness-countdown" role="status" aria-live="assertive"><strong>{cameraCountdown}</strong><span>{c("Look at the camera and keep a natural posture", "看向镜头，保持自然姿势")}</span></div>}
                  </div>
                  <div className="readiness-camera-actions readiness-camera-controls">
                    <label className="readiness-camera-timer" htmlFor="lesson-readiness-camera-delay"><span>{c("Timer", "倒计时")}</span><select id="lesson-readiness-camera-delay" value={captureDelay} disabled={cameraCountdown > 0} onChange={(event) => setCaptureDelay(Number(event.target.value))}><option value={0}>{c("Now", "立即")}</option><option value={3}>3 {c("sec", "秒")}</option><option value={5}>5 {c("sec", "秒")}</option><option value={10}>10 {c("sec", "秒")}</option></select></label>
                    <button className="primary-button" type="button" disabled={!cameraReady || cameraPortrait} onClick={startTimedCapture}><Camera size={18} weight="fill" />{cameraCountdown > 0 ? c("Cancel timer", "取消倒计时") : cameraPortrait ? c("Rotate device", "请横放设备") : cameraReady ? captureDelay > 0 ? c(`Take photo in ${captureDelay} sec`, `${captureDelay} 秒后拍照`) : c("Take photo now", "立即拍照") : c("Preparing camera…", "正在准备画面…")}</button>
                    <button className="secondary-button" type="button" onClick={stopCamera}>{c("Cancel", "取消")}</button>
                  </div>
                </div>
              ) : proofPreview ? (
                <div className="readiness-photo-preview">
                  <img src={proofPreview} alt={c("Photo ready for the camera-view check", "待检测的摄像头画面")} />
                  <span><CheckCircle size={18} weight="fill" />{c("Photo ready", "照片已拍摄")}</span>
                  <button className="secondary-button" type="button" onClick={clearProof}>{c("Retake", "重新拍摄")}</button>
                </div>
              ) : (
                <div className="readiness-empty">
                  <div className="readiness-example">
                    <img src={CAMERA_REFERENCE_PHOTO} alt={c("Camera framing reference", "摄像头画面参考")} />
                    <span>{c("Recommended camera view", "合格画面参考")}</span>
                  </div>
                  <div className="readiness-empty-copy">
                    <div>
                      <strong>{c("Compare the reference, then check your camera", "对照参考画面，检测当前上课环境")}</strong>
                      <small>{c("No screenshots to upload — open the camera and take one photo here.", "无需自己截图或上传，直接打开摄像头拍一张即可。")}</small>
                    </div>
                    <button className="primary-button" type="button" disabled={openingCamera} onClick={openCamera}><Camera size={18} weight="fill" />{openingCamera ? c("Opening camera…", "正在打开摄像头…") : c("Open camera and start", "打开摄像头开始检测")}</button>
                  </div>
                </div>
              )}
            </section>

            {proof && <div className="readiness-selected-photo"><CheckCircle size={17} weight="fill" /><span><strong>{proof.name}</strong>{(proof.size / 1024 / 1024).toFixed(1)} MB</span></div>}
            {proof && <button className="primary-button wide-button" type="button" disabled={analyzing} onClick={submitPhoto}>{analyzing ? c("Reviewing… usually 10-15 sec", "正在审核，预计 10-15 秒") : c("Submit and check 4 items", "提交并检测四项内容")}</button>}
          </>
        )}

        {cameraError && <div className="readiness-error" role="alert"><WarningCircle size={20} weight="fill" />{cameraError}</div>}

        {analysis?.status === "processing" && (
          <div className="readiness-retry-note readiness-processing-note" role="status">
            <Timer size={20} weight="fill" />
            <span>
              <strong>{c("Checking the camera view", "正在检测上课画面")}</strong>
              {analysis.teacherMessage}
            </span>
            <Toki
              mood="happy"
              motion="scan"
              loop
              className="readiness-processing-toki"
              alt={c("Toki is checking the camera view", "Toki 正在检测上课画面")}
            />
          </div>
        )}

        {analysis?.status === "unavailable" && (
          <div className="readiness-error" role="alert"><WarningCircle size={20} weight="fill" />{analysis.teacherMessage}</div>
        )}

        {analysis?.status === "changes_requested" && (
          <div className="readiness-retry-note readiness-retry-animation">
            <WarningCircle size={20} weight="fill" />
            <span>
              <strong>{c("Adjust the highlighted items, then retake", "请按未通过项调整后重拍")}</strong>
              {c("The matching examples have been opened below.", "对应的判断示例已在下方展开。")}
            </span>
            <Toki
              mood="thumb"
              motion="encourage"
              className="readiness-retry-toki"
              alt={c("Toki encourages you to adjust and retake", "Toki 鼓励你调整后重拍")}
            />
          </div>
        )}

        {analysis?.checks?.length > 0 && (
          <div className="readiness-result-list">
            {analysis.checks.map((check) => (
              <article className={`result-${check.status}`} key={check.id}>
                <span>{check.status === "pass" ? <Check size={16} weight="bold" /> : <WarningCircle size={16} weight="fill" />}</span>
                <div><strong>{check.title}</strong><small>{check.message}</small>{check.suggestion && <p>{check.suggestion}</p>}</div>
                <em>{check.status === "pass" ? c("Passed", "通过") : check.status === "fail" ? c("Adjust", "需调整") : c("Uncertain", "无法判断")}</em>
              </article>
            ))}
          </div>
        )}

        {(analysis || proofPreview) && (
          <div className="readiness-toolbar">
            <button className="secondary-button" type="button" disabled={analyzing || resetting || openingCamera} onClick={resetDetection}>
              <ArrowClockwise size={17} />{resetting ? c("Resetting…", "正在重置…") : c("Run the check again", "重新检测")}
            </button>
          </div>
        )}

        <button
          className={`lesson-example-toggle ${examplesOpen ? "is-open" : ""}`}
          type="button"
          aria-expanded={examplesOpen}
          aria-controls="lesson-camera-examples"
          onClick={() => setExamplesOpen((current) => !current)}
        >
          <ImagesSquare size={20} weight="duotone" />
          <span><strong>{c("View four judging examples", "查看四项判断示例")}</strong><small>{c("Recommended and needs-adjustment comparisons", "合格与需调整画面对照")}</small></span>
          <CaretDown size={18} weight="bold" />
        </button>

        {examplesOpen && (
          <div id="lesson-camera-examples" className="lesson-example-drawer">
            <ReadinessExampleGallery compact initialActiveId={exampleFocusId} />
          </div>
        )}
      </section>

      <section className={`lesson-courseware-confirmation ${coursewareConfirmed ? "is-complete" : ""}`}>
        <span className="lesson-module-number">{deviceRequired ? "03" : "02"}</span>
        <label>
          <input type="checkbox" checked={coursewareConfirmed} disabled={taskCompleted} onChange={toggleCourseware} />
          <span className="lesson-courseware-checkbox">{coursewareConfirmed ? <Check size={17} weight="bold" /> : null}</span>
          <div>
            <small>{c("COURSEWARE CONFIRMATION", "课件准备确认")}</small>
            <strong>{c(COURSEWARE_STATEMENT, "我已浏览全部课件，并完成本节课备课。")}</strong>
            <p>{c("Self-confirmation only. No screenshot or file upload is required.", "主动勾选确认即可，无需截图或上传文件。")}</p>
          </div>
        </label>
        <span className="lesson-step-status">
          {coursewareConfirmed ? c("Confirmed", "已确认") : c("To do", "待确认")}
        </span>
      </section>
    </div>
  );
}
