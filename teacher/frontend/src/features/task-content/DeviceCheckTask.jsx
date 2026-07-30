import { useState } from "react";
import {
  Camera,
  CheckCircle,
  Microphone,
  Monitor,
  WarningCircle,
  WifiHigh,
} from "@phosphor-icons/react";
import { localizeApiError } from "../../api-error-copy";
import { useI18n } from "../../i18n";
import "./readiness-photo-task.css";

const itemMeta = {
  camera: { icon: Camera, en: "Camera", zh: "摄像头" },
  microphone: { icon: Microphone, en: "Microphone", zh: "麦克风" },
  network: { icon: WifiHigh, en: "Network", zh: "网络" },
};

export default function DeviceCheckTask({ task }) {
  const { language } = useI18n();
  const c = (en, zh) => language === "zh" ? zh : en;
  const step = task.execution?.findStep("DEVICE_CHECK");
  const saved = task.execution?.steps?.[step?.stepKey];
  const [results, setResults] = useState(saved?.details?.results || {});
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");
  const configuredItems = Array.isArray(step?.config?.items) ? step.config.items : ["camera", "microphone", "network"];
  const completed = task.status === "completed" || saved?.status === "COMPLETED";

  const run = async () => {
    if (!task.execution || checking) return;
    setChecking(true);
    setError("");
    const next = Object.fromEntries(configuredItems.map((key) => [key, key === "network" && navigator.onLine ? "PASSED" : "FAILED"]));
    let stream = null;
    try {
      if (configuredItems.some((key) => ["camera", "microphone"].includes(key))) {
        if (!navigator.mediaDevices?.getUserMedia) {
          task.execution?.track?.("CAMERA_PERMISSION_RESULT", {
            stepKey: step?.stepKey || "DEVICE_CHECK",
            stepType: "DEVICE_CHECK",
            permission: "UNSUPPORTED",
            result: "FAILURE",
          });
          throw Object.assign(new Error("Media devices are not supported"), {
            code: "CAMERA_UNSUPPORTED",
          });
        }
        stream = await navigator.mediaDevices?.getUserMedia({
          video: configuredItems.includes("camera"),
          audio: configuredItems.includes("microphone"),
        });
        task.execution?.track?.("CAMERA_PERMISSION_RESULT", {
          stepKey: step?.stepKey || "DEVICE_CHECK",
          stepType: "DEVICE_CHECK",
          permission: "GRANTED",
          result: "SUCCESS",
        });
        if (configuredItems.includes("camera")) {
          task.execution?.track?.("CAMERA_OPENED", {
            stepKey: step?.stepKey || "DEVICE_CHECK",
            stepType: "DEVICE_CHECK",
            result: "SUCCESS",
          });
        }
        if (configuredItems.includes("camera")) next.camera = stream?.getVideoTracks().length ? "PASSED" : "FAILED";
        if (configuredItems.includes("microphone")) next.microphone = stream?.getAudioTracks().length ? "PASSED" : "FAILED";
      }
      const response = await task.execution.saveStep(step?.stepKey, { results: next });
      setResults(next);
      if (response?.step?.status === "COMPLETED") await task.execution.submit();
      else setError(c("One or more checks did not pass. Check permissions and network, then retry.", "有检测项未通过，请检查设备权限和网络后重试。"));
    } catch (caught) {
      if (caught?.code !== "CAMERA_UNSUPPORTED") {
        task.execution?.track?.("CAMERA_PERMISSION_RESULT", {
          stepKey: step?.stepKey || "DEVICE_CHECK",
          stepType: "DEVICE_CHECK",
          permission: caught?.name === "NotAllowedError" ? "DENIED" : "UNAVAILABLE",
          errorCode: caught?.code || caught?.name || "DEVICE_CHECK_FAILED",
          result: "FAILURE",
        });
      }
      task.execution?.track?.("CAMERA_FAILED", {
        stepKey: step?.stepKey || "DEVICE_CHECK",
        stepType: "DEVICE_CHECK",
        errorCode: caught?.code || caught?.name || "DEVICE_CHECK_FAILED",
        result: "FAILURE",
      });
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
    <div className="readiness-photo-task">
      <div className="readiness-section-head">
        <div>
          <span className="eyebrow">{c("DEVICE READINESS", "设备准备")}</span>
          <h3>{c("Check your class device and network", "检查上课设备与网络")}</h3>
          <p>{c("The browser requests real camera and microphone permission and records the result in the system.", "浏览器会真实申请摄像头和麦克风权限，检测结果会保存到系统。")}</p>
        </div>
        <Monitor size={28} weight="fill" />
      </div>

      <div className="readiness-guidelines">
        {configuredItems.map((key, index) => {
          const meta = itemMeta[key] || { icon: Monitor, en: key, zh: key };
          const Icon = meta.icon;
          const passed = results[key] === "PASSED";
          return (
            <article key={key}>
              <span>{passed ? <CheckCircle size={18} weight="fill" /> : String(index + 1).padStart(2, "0")}</span>
              <div><strong><Icon size={18} /> {c(meta.en, meta.zh)}</strong><p>{passed ? c("Passed", "已通过") : c("Waiting for check", "等待检测")}</p></div>
            </article>
          );
        })}
      </div>

      {error && <div className="readiness-error" role="alert"><WarningCircle size={20} weight="fill" />{error}</div>}
      {completed ? (
        <div className="readiness-complete" role="status"><CheckCircle size={30} weight="fill" /><div><strong>{c("Device check complete", "设备检测已完成")}</strong><p>{c("The result has been saved. No repeated check is needed.", "检测结果已保存，无需重复操作。")}</p></div></div>
      ) : (
        <button className="primary-button wide-button" type="button" disabled={checking} onClick={run}>
          {checking ? c("Checking…", "正在检测…") : c("Start real device check", "开始真实设备检测")}
        </button>
      )}
    </div>
  );
}
