import { useState } from "react";
import {
  Camera,
  CheckCircle,
  ClockCounterClockwise,
  Microphone,
  Monitor,
  WarningCircle,
  WifiHigh,
} from "@phosphor-icons/react";
import { localizeApiError } from "../../api-error-copy";
import { probeTeacherConnection } from "../../api/tide-api";
import { useI18n } from "../../i18n";
import "./readiness-photo-task.css";

const itemMeta = {
  camera: { icon: Camera, en: "Camera", zh: "摄像头" },
  microphone: { icon: Microphone, en: "Microphone", zh: "麦克风" },
  network: { icon: WifiHigh, en: "Network", zh: "网络" },
};

const mediaErrorCopy = (caught, c) => ({
  NotAllowedError: c(
    "Camera or microphone permission was not granted. Allow both permissions in the browser site settings, then retry.",
    "未获得摄像头或麦克风权限。请在浏览器地址栏的站点设置中允许这两项权限后重试。",
  ),
  NotFoundError: c(
    "No available camera or microphone was found. Connect the class device, then retry.",
    "没有检测到可用的摄像头或麦克风。请连接上课设备后重试。",
  ),
  NotReadableError: c(
    "The camera or microphone is being used by another app. Close that app, then retry.",
    "摄像头或麦克风正被其他程序占用。请关闭占用程序后重试。",
  ),
}[caught?.name]);

export default function DeviceCheckTask({ task, embedded = false, forceGrandfathered = false, onPassed }) {
  const { language } = useI18n();
  const c = (en, zh) => language === "zh" ? zh : en;
  const step = task.execution?.findStep("DEVICE_CHECK");
  const saved = task.execution?.steps?.[step?.stepKey];
  const [results, setResults] = useState(saved?.details?.results || {});
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");
  const configuredItems = Array.isArray(step?.config?.items) ? step.config.items : ["camera", "microphone", "network"];
  const hasCurrentEvidence = Boolean(
    step?.config?.version
    && saved?.details?.checkVersion === step.config.version
    && saved?.details?.source === "BROWSER_LOCAL",
  );
  const grandfatheredCompleted = forceGrandfathered || (
    task.taskCode === "G04"
    && task.status === "completed"
    && !hasCurrentEvidence
  );
  const completed = task.status === "completed" || saved?.status === "COMPLETED";

  const run = async () => {
    if (!task.execution || checking || completed) return;
    if (!step) {
      setError(c(
        "The device-check configuration is still syncing. Refresh and try again.",
        "设备检测配置正在同步，请刷新后重试。",
      ));
      return;
    }
    setChecking(true);
    setError("");
    const next = Object.fromEntries(configuredItems.map((key) => [key, "FAILED"]));
    let networkDurationMs = null;
    let stream = null;
    let mediaFailure = null;
    let networkFailure = null;
    try {
      const needsMedia = configuredItems.some((key) => ["camera", "microphone"].includes(key));
      const mediaCheck = needsMedia
        ? (async () => {
            if (!navigator.mediaDevices?.getUserMedia) {
              const unsupported = Object.assign(new Error("Media devices are not supported"), {
                code: "CAMERA_UNSUPPORTED",
              });
              task.execution?.track?.("CAMERA_PERMISSION_RESULT", {
                stepKey: step.stepKey,
                stepType: "DEVICE_CHECK",
                permission: "UNSUPPORTED",
                result: "FAILURE",
              });
              throw unsupported;
            }
            stream = await navigator.mediaDevices.getUserMedia({
              video: configuredItems.includes("camera"),
              audio: configuredItems.includes("microphone"),
            });
            task.execution?.track?.("CAMERA_PERMISSION_RESULT", {
              stepKey: step.stepKey,
              stepType: "DEVICE_CHECK",
              permission: "GRANTED",
              result: "SUCCESS",
            });
            if (configuredItems.includes("camera")) {
              next.camera = stream.getVideoTracks().some((track) => track.readyState === "live")
                ? "PASSED"
                : "FAILED";
              task.execution?.track?.("CAMERA_OPENED", {
                stepKey: step.stepKey,
                stepType: "DEVICE_CHECK",
                result: next.camera === "PASSED" ? "SUCCESS" : "FAILURE",
              });
            }
            if (configuredItems.includes("microphone")) {
              next.microphone = stream.getAudioTracks().some((track) => track.readyState === "live")
                ? "PASSED"
                : "FAILED";
            }
          })().catch((caught) => {
            mediaFailure = caught;
            if (caught?.code !== "CAMERA_UNSUPPORTED") {
              task.execution?.track?.("CAMERA_PERMISSION_RESULT", {
                stepKey: step.stepKey,
                stepType: "DEVICE_CHECK",
                permission: caught?.name === "NotAllowedError" ? "DENIED" : "UNAVAILABLE",
                errorCode: caught?.code || caught?.name || "DEVICE_CHECK_FAILED",
                result: "FAILURE",
              });
            }
            task.execution?.track?.("CAMERA_FAILED", {
              stepKey: step.stepKey,
              stepType: "DEVICE_CHECK",
              errorCode: caught?.code || caught?.name || "DEVICE_CHECK_FAILED",
              result: "FAILURE",
            });
          })
        : Promise.resolve();

      const networkCheck = configuredItems.includes("network")
        ? (async () => {
            const networkStartedAt = performance.now();
            try {
              await probeTeacherConnection();
              next.network = "PASSED";
            } catch (caught) {
              networkFailure = caught;
              next.network = "FAILED";
            } finally {
              networkDurationMs = Math.round(performance.now() - networkStartedAt);
            }
          })()
        : Promise.resolve();

      await Promise.all([mediaCheck, networkCheck]);
      const checkedAt = new Date().toISOString();
      const response = await task.execution.saveStep(step.stepKey, {
        results: next,
        source: "BROWSER_LOCAL",
        checkedAt,
        measurements: {
          network: { durationMs: networkDurationMs ?? 0 },
        },
      });
      setResults(next);
      if (response?.step?.status === "COMPLETED") {
        if (onPassed) await onPassed(response);
        else await task.execution.submit();
      } else if (mediaFailure) {
        setError(mediaErrorCopy(mediaFailure, c) || c(
          "The camera or microphone check did not finish. Check the device and retry.",
          "摄像头或麦克风检测未完成，请检查设备后重试。",
        ));
      } else if (networkFailure) {
        setError(localizeApiError(
          networkFailure,
          language,
          c(
            "The connection check could not reach TIDE. Check the network and retry.",
            "连接检测暂时无法访问 TIDE，请检查网络后重试。",
          ),
        ));
      } else {
        setError(c(
          "One or more checks did not pass. Check the class device and retry.",
          "有检测项未通过，请检查上课设备后重试。",
        ));
      }
    } catch (caught) {
      setResults(next);
      setError(localizeApiError(
        caught,
        language,
        c("The device check failed. Please retry.", "设备检测失败，请重试。"),
      ));
    } finally {
      stream?.getTracks().forEach((track) => track.stop());
      setChecking(false);
    }
  };

  return (
    <div className={embedded ? "device-check-panel is-embedded" : "readiness-photo-task device-check-panel"}>
      {!embedded && (
        <div className="readiness-section-head">
          <div>
            <span className="eyebrow">{c("DEVICE READINESS", "设备准备")}</span>
            <h3>{c("Check your class device and network", "检查上课设备与网络")}</h3>
            <p>{c("The browser requests real camera and microphone permission, then sends one real request to TIDE to verify the connection.", "浏览器会真实申请摄像头和麦克风权限，并向 TIDE 发起一次真实请求确认连接。")}</p>
          </div>
          <Monitor size={28} weight="fill" />
        </div>
      )}

      {embedded && <p className="device-check-intro">{c(
        "Allow camera and microphone access. The connection item uses a real request to TIDE; it does not estimate bandwidth or latency.",
        "请允许摄像头和麦克风权限。网络项会真实连接 TIDE，但不评估带宽或延迟。",
      )}</p>}

      {grandfatheredCompleted ? (
        <div className="device-check-grandfathered" role="status">
          <ClockCounterClockwise size={28} weight="duotone" />
          <div>
            <strong>{c("Completed under the earlier G04 version", "已按 G04 早期版本完成")}</strong>
            <p>{c(
              "Your completed status is preserved. The current three-item browser check was not part of that version, so no item result is shown and no recheck is required.",
              "已完成状态已保留。当时版本不包含当前三项浏览器预检，因此不展示单项结果，也无需重新检测。",
            )}</p>
          </div>
        </div>
      ) : (
        <div className="device-check-items">
          {configuredItems.map((key, index) => {
            const meta = itemMeta[key] || { icon: Monitor, en: key, zh: key };
            const Icon = meta.icon;
            const passed = results[key] === "PASSED";
            const failed = results[key] === "FAILED";
            return (
              <article className={passed ? "is-passed" : failed ? "is-failed" : ""} key={key}>
                <span>{passed ? <CheckCircle size={18} weight="fill" /> : failed ? <WarningCircle size={18} weight="fill" /> : String(index + 1).padStart(2, "0")}</span>
                <div><strong><Icon size={18} /> {c(meta.en, meta.zh)}</strong><p>{passed ? c("Passed", "已通过") : failed ? c("Did not pass", "未通过") : c("Waiting for check", "等待检测")}</p></div>
              </article>
            );
          })}
        </div>
      )}

      {error && <div className="readiness-error" role="alert"><WarningCircle size={20} weight="fill" />{error}</div>}
      {grandfatheredCompleted ? null : completed ? (
        <div className="readiness-complete compact" role="status"><CheckCircle size={26} weight="fill" /><div><strong>{c("Device and connection check passed", "设备与连接检测已通过")}</strong><p>{c("The browser-local result has been saved. No repeated check is needed.", "浏览器本地检测结果已保存，无需重复操作。")}</p></div></div>
      ) : (
        <button className="primary-button wide-button" type="button" disabled={checking} onClick={run}>
          {checking ? c("Checking camera, microphone and connection…", "正在检测摄像头、麦克风与连接…") : c("Start device and connection check", "开始设备与连接检测")}
        </button>
      )}
    </div>
  );
}
