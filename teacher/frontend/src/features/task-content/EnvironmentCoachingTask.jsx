import { useState } from "react";
import { Camera, Check, CheckCircle, ShieldCheck, WarningCircle } from "@phosphor-icons/react";
import {
  localizeApiError,
  localizedValidationMessage,
} from "../../api-error-copy";
import { useI18n } from "../../i18n";
import "./readiness-photo-task.css";

export default function EnvironmentCoachingTask({ task }) {
  const { language } = useI18n();
  const c = (en, zh) => language === "zh" ? zh : en;
  const checklistStep = task.execution?.findStep("CHECKLIST");
  const items = (checklistStep?.config?.items || []).map((item) => typeof item === "string" ? { key: item, label: item } : item);
  const savedKeys = new Set(task.execution?.steps?.[checklistStep?.stepKey]?.details?.checkedItemKeys || []);
  const [checked, setChecked] = useState(items.map((item, index) => savedKeys.has(item.key) ? index : null).filter((value) => value !== null));
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const complete = task.status === "completed";

  const toggle = (index) => setChecked((current) => current.includes(index)
    ? current.filter((value) => value !== index)
    : [...current, index]);

  const chooseFile = (nextFile) => {
    if (preview.startsWith("blob:")) URL.revokeObjectURL(preview);
    setFile(nextFile);
    setPreview(nextFile ? URL.createObjectURL(nextFile) : "");
  };

  const submit = async () => {
    if (!task.execution || !file || checked.length !== items.length || submitting) return;
    setSubmitting(true);
    setError("");
    try {
      await task.execution.saveStep(checklistStep.stepKey, { checkedItemKeys: items.map((item) => item.key) });
      await task.execution.uploadStep("ENVIRONMENT_PHOTO", file);
      const response = await task.execution.submit();
      if (response?.status === "FAILED") {
        setError(localizedValidationMessage(
          response.validation,
          language,
          c("Please adjust the environment photo and submit again.", "请按提示调整授课环境照片后重新提交。"),
        ));
      }
    } catch (caught) {
      setError(localizeApiError(
        caught,
        language,
        c("The action failed. Please retry.", "操作失败，请重试。"),
      ));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="readiness-photo-task">
      <div className="readiness-section-head">
        <div><span className="eyebrow">{c("PERSONALIZED IMPROVEMENT", "个性化改善")}</span><h3>{task.name}</h3><p>{task.reason}</p></div>
        <ShieldCheck size={28} weight="fill" />
      </div>
      <div className="readiness-guidelines">
        {items.map((item, index) => (
          <article key={item.key} onClick={() => !complete && toggle(index)}>
            <span>{checked.includes(index) ? <Check size={17} weight="bold" /> : String(index + 1).padStart(2, "0")}</span>
            <div><strong>{language === "zh" ? item.labelZh || item.label : item.label}</strong><p>{checked.includes(index) ? c("Confirmed", "已确认") : c("Tap to confirm", "点击确认")}</p></div>
          </article>
        ))}
      </div>
      {complete ? (
        <div className="readiness-complete"><CheckCircle size={30} weight="fill" /><div><strong>{c("Improvement task complete", "改善任务已完成")}</strong><p>{c("The result has been saved.", "结果已经保存。")}</p></div></div>
      ) : (
        <>
          <label className="readiness-empty">
            <input type="file" accept="image/jpeg,image/png" capture="environment" hidden onChange={(event) => chooseFile(event.target.files?.[0] || null)} />
            {preview ? <img src={preview} alt={c("Selected environment", "已选择的授课环境照片")} /> : <Camera size={38} weight="duotone" />}
            <strong>{file?.name || c("Take or choose an environment photo", "拍摄或选择授课环境照片")}</strong>
          </label>
          {error && <div className="readiness-error" role="alert"><WarningCircle size={20} weight="fill" />{error}</div>}
          <button className="primary-button wide-button" type="button" disabled={checked.length !== items.length || !file || submitting} onClick={submit}>{submitting ? c("Submitting and checking…", "正在提交并检测…") : c("Submit for AI review", "提交 AI 审核")}</button>
        </>
      )}
    </div>
  );
}
